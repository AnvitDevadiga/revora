from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, ConfigDict
import os
import time
import json
import queue
import threading
from queue import Empty
from threading import Thread, Lock
from collections import defaultdict, deque
from typing import Literal
from secrets import compare_digest
from contextlib import asynccontextmanager
from hashlib import sha256
import logging
import math
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from .database import connect_graph
from .laboratory import Laboratory, SCENARIOS
from .runtime import Runtime, evaluate
from .evaluation import evaluate_adversarial
from .groq_agent import AgentCapacityError, GroqAgent
from .deployment import evaluate_deployments
from .memory import MemoryStore
from .release_controller import ReleaseController
from .coordination import CoordinationWorkflow
from .llm_planner import LLMPlanner

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}')
logger = logging.getLogger("revora.api")
_started_controller = None

class BodyLimitMiddleware:
    """Bound streamed request bodies without buffering untrusted sizes."""
    def __init__(self, app, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        length = next((value.decode() for name, value in scope.get("headers", [])
                       if name.lower() == b"content-length"), None)
        max_body_bytes = min(self.max_body_bytes, MAX_BODY_BYTES)
        if length:
            try:
                if int(length) > max_body_bytes:
                    return await self._too_large(send)
            except ValueError:
                return await self._bad_length(send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > max_body_bytes:
                return await self._too_large(send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        await self.app(scope, replay, send)

    @staticmethod
    async def _too_large(send):
        body = b'{"detail":"Request body too large"}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _bad_length(send):
        body = b'{"detail":"Invalid content length"}'
        await send({"type": "http.response.start", "status": 400,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

@asynccontextmanager
async def lifespan(_app):
    global _started_controller, release_controller
    yield
    if _started_controller is not None:
        _started_controller.close()
        _started_controller = None
        release_controller = None

app = FastAPI(title="Revora API", version="0.1.0", lifespan=lifespan)
app.add_middleware(BodyLimitMiddleware, max_body_bytes=int(os.getenv("MAX_BODY_BYTES", "1048576")))
app.add_middleware(TrustedHostMiddleware,
                   allowed_hosts=[host.strip() for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if host.strip()])
cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if origin.strip()]
graph_client, graph, graph_name = connect_graph()
laboratory = Laboratory(graph)
runtime = Runtime(graph)
memory_store = MemoryStore(graph)
coordination = CoordinationWorkflow(graph, planner=LLMPlanner.from_env())
release_controller = None
release_controller_lock = Lock()

def get_release_controller():
    global release_controller, _started_controller
    with release_controller_lock:
        if release_controller is None:
            release_controller = ReleaseController(graph)
            _started_controller = release_controller
            app.state.release_controller = release_controller
        return release_controller
groq_agent = GroqAgent(graph, memory=memory_store)
API_KEY = os.getenv("REVORA_API_KEY", "")
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
request_log: dict[str, deque[float]] = defaultdict(deque)
MAX_RATE_BUCKETS = 4096

if RATE_LIMIT < 1:
    raise RuntimeError("RATE_LIMIT_PER_MINUTE must be positive")
if API_KEY == "" and os.getenv("ALLOW_ANONYMOUS", "false").lower() != "true":
    if os.getenv("APP_ENV", "development").lower() in {"production", "prod"}:
        raise RuntimeError("REVORA_API_KEY is required in production; set ALLOW_ANONYMOUS=true only for an intentionally public API")

def _rate_identity(request: Request) -> str:
    key = request.headers.get("x-api-key", "")
    if key and API_KEY and compare_digest(key.encode(), API_KEY.encode()):
        return "key:" + sha256(key.encode()).hexdigest()
    return "ip:" + (request.client.host if request.client else "unknown")

def _prune_rate_buckets(now_ts: float):
    expired = [identity for identity, entries in request_log.items()
               if not entries or now_ts - entries[-1] > 60]
    for identity in expired:
        request_log.pop(identity, None)

@app.middleware("http")
async def protect(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/health":
        return await call_next(request)
    now_ts = time.monotonic()
    _prune_rate_buckets(now_ts)
    bucket_id = _rate_identity(request)
    bucket = request_log.get(bucket_id)
    if bucket is None:
        if len(request_log) >= MAX_RATE_BUCKETS:
            # Bound memory even under identity churn. Evict least recently used bucket.
            oldest = min(request_log, key=lambda identity: request_log[identity][-1] if request_log[identity] else 0)
            del request_log[oldest]
        bucket = request_log[bucket_id] = deque()
    while bucket and now_ts - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        retry_after = max(1, math.ceil(60 - (now_ts - bucket[0])))
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429,
                            headers={"Retry-After": str(retry_after)})
    bucket.append(now_ts)
    if API_KEY and not compare_digest(request.headers.get("x-api-key", "").encode(), API_KEY.encode()):
        logger.warning("request_denied path=%s client=%s", request.url.path, request.client.host if request.client else "unknown")
        return JSONResponse({"detail": "Missing or invalid API key"}, status_code=401,
                            headers={"X-Request-ID": request.headers.get("x-request-id", "")[:64] or __import__("uuid").uuid4().hex})
    request.state.request_id = request.headers.get("x-request-id", "")[:64] or __import__("uuid").uuid4().hex
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info("request_complete id=%s method=%s path=%s status=%d latency_ms=%.2f",
                    request.state.request_id, request.method, request.url.path,
                    response.status_code, (time.perf_counter() - started) * 1000)
        return response
    except Exception:
        logger.exception("request_failed id=%s method=%s path=%s", request.state.request_id,
                         request.method, request.url.path)
        raise

# Register CORS last so even authentication/rate-limit responses carry headers.
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET", "POST"], allow_headers=["content-type", "x-api-key", "x-request-id"])

MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", "1048576"))

class SandboxRunIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    service: str = Field(default="contacts", min_length=1, max_length=64)
    environment: Literal["sandbox"] = "sandbox"
    workload: str = Field(default="imported-contacts", min_length=1, max_length=64)
    values: list[str] = Field(min_length=1, max_length=1000)
    schema_version: str = Field(default="customers-v1", min_length=1, max_length=64)
    merge_authorized: bool = False

class GroqRunIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    goal: str = Field(default="Safely evaluate this deployment", min_length=3, max_length=300)
    service: str = Field(default="payments", min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    scenario: Literal["healthy", "readiness_failure", "canary_regression", "slow_release"] = "canary_regression"

class ReleaseRunIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    version: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    mode: Literal["healthy", "errors", "slow", "mixed"] = "mixed"
    force_recheck: bool = False
    components: list[str] = Field(default_factory=list, max_length=8)

class ReleaseInjectIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    version: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    mode: Literal["errors", "slow", "mixed"] = "errors"
    components: list[str] = Field(default_factory=list, max_length=8)

class CoordinationIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    incident_id: str = Field(min_length=1, max_length=64)
    use_memory: bool = True

@app.get("/api/release/live")
def release_live():
    """Probe the currently routed service through the live HTTP gateway."""
    return JSONResponse(get_release_controller().status(), headers={"Cache-Control": "no-store"})

@app.get("/api/release/traffic")
def release_traffic():
    """Expose the gateway's current response and HTTP status to callers."""
    status = get_release_controller().status()
    observed = status["gateway_probe"]
    return JSONResponse({"service": status["service"], "version": observed["version"],
                         "route": observed["route"], "latency_ms": observed["latency_ms"]},
                        status_code=observed["status"], headers={"Cache-Control": "no-store"})

@app.post("/api/release/run")
def release_run(payload: ReleaseRunIn):
    try:
        return get_release_controller().run(**payload.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.post("/api/release/inject")
def release_inject(payload: ReleaseInjectIn):
    try:
        return get_release_controller().inject(**payload.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.post("/api/release/recover")
def release_recover():
    try:
        return get_release_controller().recover()
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.post("/api/release/park")
def release_park():
    return get_release_controller().park()

@app.get("/api/release/history")
def release_history():
    return {"items": get_release_controller().history()}

@app.post("/api/coordination/run")
def coordination_run(payload: CoordinationIn):
    incident = next((item for item in get_release_controller().history(100)
                     if item.get("id") == payload.incident_id), None)
    if incident is None:
        raise HTTPException(404, "Incident not found in release history")
    try:
        return coordination.run(incident, use_memory=payload.use_memory)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error

class HandoffExecuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handoff_id: str = Field(min_length=1, max_length=64)

@app.post("/api/coordination/diagnose")
def coordination_diagnose():
    try:
        return coordination.publish(get_release_controller().diagnose())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.post("/api/coordination/execute")
def coordination_execute(payload: HandoffExecuteIn):
    try:
        return coordination.consume(payload.handoff_id, executor=get_release_controller())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.post("/api/coordination/tool/recover")
def recovery_tool(payload: HandoffExecuteIn):
    try:
        return coordination.execute_recovery_tool(payload.handoff_id, get_release_controller())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.post("/api/coordination/recover")
def coordination_recover():
    try:
        handoff = coordination.publish(get_release_controller().diagnose())
        return coordination.consume(handoff["id"], executor=get_release_controller())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.get("/api/coordination/graph")
def coordination_graph(limit: int = Query(default=20, ge=1, le=100)):
    """Live multi-agent provenance graph read directly from FalkorDB."""
    return coordination.graph_view(get_release_controller().service, limit)

@app.get("/api/coordination/history")
def coordination_history():
    return {"items": coordination.history()}

@app.get("/api/llm/status")
def llm_status():
    return {"configured": groq_agent.configured, "provider": "Groq", "model": groq_agent.model}

@app.post("/api/agent/run")
def agent_run_live(payload: GroqRunIn):
    try:
        return groq_agent.run(**payload.model_dump())
    except AgentCapacityError as error:
        raise HTTPException(429, str(error), headers={"Retry-After": "1"}) from error
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error

@app.post("/api/agent/stream")
def agent_stream(payload: GroqRunIn):
    """Stream ordered tool events; disconnect stops delivery, not the bounded run."""
    events: queue.Queue = queue.Queue(maxsize=32)
    sentinel = object()
    cancelled = threading.Event()

    def emit(event):
        while not cancelled.is_set():
            try:
                events.put(event, timeout=0.25)
                return
            except queue.Full:
                continue

    def work():
        try:
            result = groq_agent.run(**payload.model_dump(), on_event=emit)
            emit({"type": "complete", "result": result})
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            emit({"type": "error", "message": str(error)[:500],
                  "status": 429 if isinstance(error, AgentCapacityError) else 503 if isinstance(error, RuntimeError) else 422,
                  "retry_after": 1 if isinstance(error, AgentCapacityError) else None})
        finally:
            emit(sentinel)

    def stream():
        emit({"type": "started", "service": payload.service, "scenario": payload.scenario})
        Thread(target=work, daemon=True).start()
        try:
            while True:
                try:
                    event = events.get(timeout=10)
                except Empty:
                    yield ": keep-alive\n\n"
                    continue
                if event is sentinel:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            cancelled.set()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })

@app.exception_handler(RedisConnectionError)
@app.exception_handler(RedisTimeoutError)
async def graph_unavailable(_request: Request, error: Exception):
    """Surface a lost FalkorDB connection as a retryable 503 instead of a bare 500."""
    logger.error("graph_unavailable error=%s", type(error).__name__)
    return JSONResponse({"detail": "FalkorDB is unavailable; retry shortly", "error": "graph_unavailable"},
                        status_code=503, headers={"Retry-After": "2"})

@app.get("/api/graph/state")
def graph_state():
    return memory_store.graph_state()

@app.get("/api/graph/neighborhood")
def graph_neighborhood(query: str = Query(min_length=1, max_length=200)):
    return memory_store.neighborhood(query)

@app.get("/api/memories/history")
def memory_history(limit: int = Query(default=100, ge=1, le=500)):
    items = memory_store.history(limit)
    return {"items": items, "count": len(items)}

@app.post("/api/runtime/run")
def sandbox_run(payload: SandboxRunIn):
    if any(not value or len(value) > 200 for value in payload.values):
        raise HTTPException(422, "Values must contain 1–200 characters")
    try:
        return runtime.run(payload.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.post("/api/runtime/proof")
def runtime_learning_proof():
    """Run a fresh two-round, invoice-preserving learning demonstration."""
    try:
        return runtime.learning_proof()
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.post("/api/runtime/ablation")
def runtime_memory_ablation():
    try:
        return runtime.memory_ablation()
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.get("/api/runtime/history")
def runtime_history():
    return runtime.history()[-50:]

@app.post("/api/evaluation/adversarial")
def adversarial_evaluation():
    return evaluate_adversarial(graph)

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
        if graph is not None:
            graph.query("RETURN 1")
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok", "database": "falkordb" if graph is not None else "memory", "graph": graph_name}

@app.get("/metrics")
def metrics():
    """Small process-local operational snapshot; use one worker as documented."""
    return {"requests": {"rate_limit_buckets": len(request_log),
                         "recent_requests": sum(len(bucket) for bucket in request_log.values())},
            "graph": {"connected": graph is not None, "name": graph_name},
            "controller": (release_controller.snapshot() if release_controller is not None else None)}
