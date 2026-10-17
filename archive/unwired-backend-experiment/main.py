"""Revora API — FastAPI entrypoint.

Endpoints
---------
POST /api/agent/run                trigger a deployment agent run (202, async)
GET  /api/agent/stream/{run_id}    Server-Sent Events: reasoning, tool calls, belief updates
GET  /api/agent/runs[/{run_id}]    run list / full replayable run record
GET  /api/graph/state              whole graph (or ?node_id=&depth= neighborhood) for the visualizer
GET  /api/memories/history         audit log: experiences, evidence, beliefs, revision chains
GET  /api/llm/status               whether Groq reasoning is configured
POST /api/demo/seed                load the three-act belief-evolution story
POST /api/demo/reset               clear graph memory (ENABLE_DEMO_RESET)
GET  /health                       FalkorDB ping + mode

Operational posture: optional API key (x-api-key header, or `api_key` query
parameter for EventSource clients), per-host rate limiting, bounded concurrent
agent runs. FalkorDB is the persistent memory; with no FALKORDB_URL the engine
falls back to an in-memory mirror that the UI labels clearly.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from secrets import compare_digest
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent import DeploymentAgent, GroqClient, RunRegistry
from engine import BeliefEngine, GroqReflector
from memory import build_engine
from memory import queries, schema
from memory.seed import seed_demo_story

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("revora.api")

SCENARIO_CHOICES = ("healthy", "readiness_failure", "canary_regression", "slow_release")
MAX_CONCURRENT_RUNS = 3


# ---------------------------------------------------------------------------
# Settings & application state
# ---------------------------------------------------------------------------
@dataclass
class Settings:
    falkordb_url: str = ""
    graph_name: str = "revora"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    api_key: str = ""
    rate_limit_per_minute: int = 120
    enable_demo_reset: bool = True
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])

    @classmethod
    def from_env(cls) -> "Settings":
        origins = [
            origin.strip()
            for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
            if origin.strip()
        ]
        return cls(
            falkordb_url=os.getenv("FALKORDB_URL", ""),
            graph_name=os.getenv("FALKORDB_GRAPH", "revora"),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
            api_key=os.getenv("REVORA_API_KEY", ""),
            rate_limit_per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "120")),
            enable_demo_reset=os.getenv("ENABLE_DEMO_RESET", "true").lower() in ("1", "true", "yes"),
            cors_origins=origins,
        )


class AppState:
    """Everything a request needs; swappable wholesale in tests."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.memory = build_engine(settings.falkordb_url, settings.graph_name, logger)
        self.groq = GroqClient(settings.groq_api_key, settings.groq_model) if settings.groq_api_key else None
        self.reflector = GroqReflector(self.groq, settings.groq_model)
        self.belief_engine = BeliefEngine(self.memory, self.reflector)
        self.agent = DeploymentAgent(self.memory, self.belief_engine, self.groq, settings.groq_model)
        self.registry = RunRegistry(capacity=64)
        self.run_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)
        logger.info(
            "Revora ready · memory=%s · Groq=%s (%s)",
            self.memory.mode,
            "enabled" if self.groq else "disabled (deterministic planner)",
            settings.groq_model,
        )


state = AppState(Settings.from_env())

app = FastAPI(title="Revora API", version="1.0.0", description="Graph-based memory and belief evolution for AI agents.")

# -- rate limiting ----------------------------------------------------------
request_log: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def protect(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/health":
        return await call_next(request)

    api_key = state.settings.api_key
    if api_key:
        supplied = request.headers.get("x-api-key", "") or request.query_params.get("api_key", "")
        if not compare_digest(supplied.encode(), api_key.encode()):
            return JSONResponse({"detail": "Missing or invalid API key"}, status_code=401)

    now_ts = time.monotonic()
    for host in list(request_log):
        if not request_log[host] or now_ts - request_log[host][-1] > 60:
            del request_log[host]
    if len(request_log) >= 10_000:
        return JSONResponse({"detail": "Server busy"}, status_code=429)
    bucket = request_log[request.client.host if request.client else "unknown"]
    while bucket and now_ts - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= state.settings.rate_limit_per_minute:
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    bucket.append(now_ts)
    return await call_next(request)


# Register CORS last so even authentication/rate-limit responses carry headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=state.settings.cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "x-api-key"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class AgentRunIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    goal: str = Field(
        default="Safely evaluate this release and make the deployment decision",
        min_length=3,
        max_length=300,
    )
    target: str = Field(default="payments", min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    scenario: Literal["healthy", "readiness_failure", "canary_regression", "slow_release"] = "canary_regression"
    mode: Literal["auto", "groq", "deterministic"] = "auto"


class SeedIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    target: str = Field(default="payments", min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


# ---------------------------------------------------------------------------
# Health & status
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return {
        "name": "Revora",
        "tagline": "AI remembers. Revora learns.",
        "docs": "/docs",
        "endpoints": [
            "/api/agent/run", "/api/agent/stream/{run_id}", "/api/agent/runs",
            "/api/graph/state", "/api/memories/history", "/api/llm/status",
            "/api/demo/seed", "/api/demo/reset", "/health",
        ],
    }


@app.get("/health")
def health():
    try:
        state.memory.backend.ping()
    except Exception as error:
        logger.error("health check failed: %s", error)
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {
        "status": "ok",
        "database": "falkordb" if state.memory.mode == "falkordb" else "memory",
        "graph": state.settings.graph_name,
        "mode": state.memory.mode,
    }


@app.get("/api/llm/status")
def llm_status():
    return {
        "configured": state.groq is not None,
        "provider": "Groq",
        "model": state.settings.groq_model,
        "reflection": state.reflector.available,
        "fallback": "deterministic planner (same tools, templated wording)" if state.groq is None else None,
    }


# ---------------------------------------------------------------------------
# Agent runs
# ---------------------------------------------------------------------------
@app.post("/api/agent/run", status_code=202)
def agent_run(payload: AgentRunIn):
    try:
        resolved = state.agent.resolve_mode(payload.mode)
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error
    if not state.run_slots.acquire(blocking=False):
        raise HTTPException(429, f"Too many concurrent agent runs (max {MAX_CONCURRENT_RUNS}); retry shortly.")

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    session = state.registry.create(run_id, payload.model_dump())

    def worker():
        try:
            result = state.agent.run(
                run_id=run_id,
                goal=payload.goal,
                target=payload.target,
                scenario=payload.scenario,
                mode=payload.mode,
                emit=session.emit,
            )
            session.finish(result=result)
        except Exception as error:  # noqa: BLE001 — every failure is surfaced to the stream
            logger.exception("agent run %s failed", run_id)
            session.finish(error=str(error))
        finally:
            state.run_slots.release()

    threading.Thread(target=worker, name=f"agent-{run_id}", daemon=True).start()
    return {
        "run_id": run_id,
        "status": "running",
        "requested_mode": payload.mode,
        "resolved_mode": resolved,
        "stream": f"/api/agent/stream/{run_id}",
        "target": payload.target,
        "scenario": payload.scenario,
    }


def _sse_frame(event: dict) -> str:
    payload = json.dumps(event, default=str)
    return f"id: {event['seq']}\nevent: {event.get('type', 'message')}\ndata: {payload}\n\n"


@app.get("/api/agent/stream/{run_id}")
def agent_stream(run_id: str, after: int = 0):
    session = state.registry.get(run_id)
    if session is None:
        raise HTTPException(404, f"Unknown run {run_id!r}")

    def frames():
        index = max(0, after)
        while True:
            batch = session.wait_after(index, timeout=12.0)
            if batch is None:  # keep proxies from closing an idle stream
                yield ": heartbeat\n\n"
                continue
            if not batch:  # terminal event already delivered
                return
            for event in batch:
                index = event["seq"] + 1
                yield _sse_frame(event)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/agent/runs")
def agent_runs(limit: int = 20):
    limit = max(1, min(64, limit))
    return {"runs": state.registry.summaries(limit=limit)}


@app.get("/api/agent/runs/{run_id}")
def agent_run_record(run_id: str):
    session = state.registry.get(run_id)
    if session is None:
        raise HTTPException(404, f"Unknown run {run_id!r}")
    return session.snapshot()


# ---------------------------------------------------------------------------
# Graph & memory
# ---------------------------------------------------------------------------
@app.get("/api/graph/state")
def graph_state(node_id: str | None = None, depth: int = 2):
    depth = max(1, min(3, depth))
    if node_id:
        try:
            return state.memory.neighborhood(node_id, depth=depth)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
    return state.memory.graph_state()


@app.get("/api/memories/history")
def memories_history(target: str | None = None, limit: int = 200):
    if target is not None and not target.strip():
        target = None
    limit = max(1, min(500, limit))
    payload = state.memory.history(target=target, limit=limit)
    payload["risk_posture"] = queries.risk_posture(state.memory, target)
    payload["contradictions_index"] = queries.contradiction_index(state.memory, target)
    return payload


@app.get("/api/beliefs/{belief_id}/chain")
def belief_chain(belief_id: str):
    chain = queries.revision_chain(state.memory, belief_id)
    if not chain:
        raise HTTPException(404, f"No belief chain for {belief_id!r}")
    return {
        "chain": chain,
        "reasons": {belief["id"]: queries.revision_reason(state.memory, belief["id"]) for belief in chain},
    }


# ---------------------------------------------------------------------------
# Demo controls
# ---------------------------------------------------------------------------
@app.post("/api/demo/seed")
def demo_seed(payload: SeedIn):
    outcome = seed_demo_story(state.memory, target=payload.target)
    return {**outcome, "graph": state.memory.graph_state()["counts"]}


@app.post("/api/demo/reset")
def demo_reset():
    if not state.settings.enable_demo_reset:
        raise HTTPException(403, "Reset disabled (ENABLE_DEMO_RESET=false)")
    state.memory.reset()
    return {"reset": True, "graph": state.memory.graph_state()["counts"]}


# ---------------------------------------------------------------------------
# Schema documentation endpoint (judge-friendly)
# ---------------------------------------------------------------------------
@app.get("/api/schema")
def schema_doc():
    return {
        "nodes": {label: True for label in schema.NODE_LABELS},
        "relationships": list(schema.EDGE_TYPES),
        "belief_statuses": list(schema.BELIEF_STATUSES),
        "pattern": [
            "(:Experience)-[:LED_TO]->(:Evidence)",
            "(:Evidence)-[:SUPPORTS|:CONTRADICTS]->(:Belief)",
            "(:Belief)-[:UPDATES {reason}]->(:Belief)",
        ],
        "engine": state.memory.mode,
    }
