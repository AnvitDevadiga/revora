from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
import os
import time
from collections import defaultdict, deque
from typing import Literal
from secrets import compare_digest
from .engine import build_engine
from .laboratory import Laboratory, SCENARIOS
from .runtime import Runtime, evaluate
from .groq_agent import GroqAgent
from .deployment import evaluate_deployments

app = FastAPI(title="Revora API", version="0.1.0")
cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if origin.strip()]
engine = build_engine()
laboratory = Laboratory(engine.graph)
runtime = Runtime(engine.graph)
groq_agent = GroqAgent(engine.graph)
API_KEY = os.getenv("REVORA_API_KEY", "")
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
request_log: dict[str, deque[float]] = defaultdict(deque)

@app.middleware("http")
async def protect(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/health":
        return await call_next(request)
    if API_KEY and not compare_digest(request.headers.get("x-api-key", "").encode(), API_KEY.encode()):
        return JSONResponse({"detail": "Missing or invalid API key"}, status_code=401)
    now_ts = time.monotonic()
    for host in list(request_log):
        if not request_log[host] or now_ts - request_log[host][-1] > 60:
            del request_log[host]
    if len(request_log) >= 10000:
        return JSONResponse({"detail": "Server busy"}, status_code=429)
    bucket = request_log[request.client.host if request.client else "unknown"]
    while bucket and now_ts - bucket[0] > 60: bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    bucket.append(now_ts)
    return await call_next(request)

# Register CORS last so even authentication/rate-limit responses carry headers.
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET", "POST"], allow_headers=["content-type", "x-api-key"])

class EpisodeIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    text: str = Field(min_length=3, max_length=2000)
    outcome: str = Field(min_length=2, max_length=500)
    success: bool
    agent: str = "demo-agent"
    source: str = "operator"
    impact: int = Field(default=3, ge=1, le=5)
    tenant_id: Literal["demo"] = "demo"

class AgentRunIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    goal: str = Field(min_length=3, max_length=500)

class SandboxRunIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    service: str = Field(default="contacts", min_length=1, max_length=64)
    environment: Literal["sandbox"] = "sandbox"
    workload: str = Field(default="imported-contacts", min_length=1, max_length=64)
    values: list[str] = Field(min_length=1, max_length=1000)

class GroqRunIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    goal: str = Field(default="Safely evaluate this deployment", min_length=3, max_length=300)
    service: str = Field(default="payments", min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    scenario: Literal["healthy", "readiness_failure", "canary_regression", "slow_release"] = "canary_regression"

@app.get("/api/llm/status")
def llm_status():
    return {"configured": groq_agent.configured, "provider": "Groq", "model": groq_agent.model}

@app.post("/api/llm/run")
def llm_run(payload: GroqRunIn):
    try:
        return groq_agent.run(**payload.model_dump())
    except (RuntimeError, ValueError) as error:
        raise HTTPException(503 if isinstance(error, RuntimeError) else 422, str(error)) from error

@app.post("/api/runtime/run")
def sandbox_run(payload: SandboxRunIn):
    if any(not value or len(value) > 200 for value in payload.values):
        raise HTTPException(422, "Values must contain 1–200 characters")
    try:
        return runtime.run(payload.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.get("/api/runtime/history")
def runtime_history():
    return runtime.history()[-50:]

@app.get("/api/evaluation")
def evaluation():
    return {"database": evaluate(), "deployments": evaluate_deployments()}

class ReplayIn(BaseModel):
    scenario: Literal["deployment", "migration"] = "deployment"
    replay_id: str = Field(default="judge-demo", min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")

@app.get("/api/lab/scenarios")
def scenarios():
    return [{"id": key, "title": value["title"]} for key, value in SCENARIOS.items()]

@app.post("/api/lab/replay")
def replay(payload: ReplayIn):
    return laboratory.run(payload.scenario, payload.replay_id)

@app.get("/health")
def health():
    try:
        engine._run("RETURN 1")
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok", "database": "falkordb" if engine.graph is not None else "memory", "graph": engine.graph_name}

@app.get("/api/memory")
def memory(): return engine.snapshot()

@app.post("/api/episodes")
def episodes(payload: EpisodeIn): return engine.ingest(**payload.model_dump())

@app.post("/api/agent/run")
def agent_run(payload: AgentRunIn): return engine.run_agent(payload.goal)

@app.post("/api/demo/seed")
def seed():
    engine.ingest("Friday deployment caused a production incident", "rollback required", False, source="production", impact=5)
    engine.ingest("Friday deployment completed cleanly with a canary", "no incident", True, source="production", impact=4)
    engine.ingest("Friday deployment completed cleanly with staged rollout", "no incident", True, source="production", impact=4)
    return engine.snapshot()

@app.post("/api/demo/reset")
def reset():
    if os.getenv("ENABLE_DEMO_RESET") != "true":
        return JSONResponse({"detail": "Reset disabled; enable only for a disposable demo graph"}, status_code=403)
    with engine.lock:
        if engine.graph is not None:
            engine._run("MATCH (n) WHERE n:Episode OR n:Belief OR n:Procedure DETACH DELETE n")
        engine.episodes.clear()
        engine.beliefs.clear()
        engine.procedures.clear()
        return engine.snapshot()

@app.get("/api/graph")
def graph():
    if engine.graph is None:
        nodes = []
        edges = []
        for episode in engine.episodes:
            nodes.append({"id": episode["id"], "type": "Episode", "label": episode["text"][:42]})
        for belief in engine.beliefs.values():
            nodes.append({"id": belief["id"], "type": "Belief", "label": belief["claim"]})
            for evidence in belief["evidence"]:
                edges.append({"source": evidence["episode_id"], "target": belief["id"], "relation": evidence["relation"], "weight": evidence["weight"]})
        return {"nodes": nodes, "edges": edges}
    rows = engine._result_rows(engine._run("MATCH (a)-[r]->(b) RETURN a.id, labels(a)[0], b.id, labels(b)[0], type(r), r.weight"))
    nodes = {}
    edges = []
    for source, source_type, target, target_type, relation, weight in rows:
        nodes[source] = {"id": source, "type": source_type}
        nodes[target] = {"id": target, "type": target_type}
        edges.append({"source": source, "target": target, "relation": relation, "weight": weight})
    return {"nodes": list(nodes.values()), "edges": edges}
