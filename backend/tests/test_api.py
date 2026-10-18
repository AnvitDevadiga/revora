from fastapi.testclient import TestClient
from app import main
from app.runtime import Runtime
from app.memory import MemoryStore

def test_protected_api_and_preflight(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "test-key")
    monkeypatch.setattr(main, "runtime", Runtime())
    main.request_log.clear()
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        denied = client.get("/api/runtime/history", headers={"Origin": "http://localhost:3000"})
        assert denied.status_code == 401
        assert denied.headers["access-control-allow-origin"] == "http://localhost:3000"
        response = client.options("/api/runtime/run", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-api-key,content-type"})
        assert response.status_code == 200
        headers = {"x-api-key": "test-key"}
        body = {"request_id": "api-test", "values": ["a", "a", "b"], "merge_authorized": True}
        first = client.post("/api/runtime/run", headers=headers, json=body)
        assert first.status_code == 200 and first.json()["success"]
        assert client.post("/api/runtime/run", headers=headers, json=body).json() == first.json()
        assert client.post("/api/runtime/run", headers=headers, json={**body, "values": ["x"]}).status_code == 409
        assert client.post("/api/runtime/run", headers=headers, json={**body, "values": []}).status_code == 422
        assert client.post("/api/runtime/run", headers=headers, json={**body, "environment": "production"}).status_code == 422

def test_learning_proof_endpoint_returns_verified_two_round_result(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main, "runtime", Runtime())
    with TestClient(main.app) as client:
        response = client.post("/api/runtime/proof", json={})
    assert response.status_code == 200
    proof = response.json()
    assert proof["proof"]["passed"] is True
    assert proof["proof"]["learned_first_action"] == "deduplicate_then_index"


def test_graph_state_and_belief_history_routes(monkeypatch):
    store = MemoryStore()
    store.record_deployment("payments", "healthy", "promote", True, "Gates passed", {
        "scenario": "healthy", "decision": "promote", "success": True, "http_status": 200,
        "probe_ms": 1, "synthetic_metrics": {"latency_ms": 10, "error_rate": 0},
        "gates": {"health": True, "latency": True, "errors": True},
    })
    monkeypatch.setattr(main, "memory_store", store)
    with TestClient(main.app) as client:
        graph = client.get("/api/graph/state")
        history = client.get("/api/memories/history")
    assert {node["type"] for node in graph.json()["nodes"]} == {"Experience", "Evidence", "Belief"}
    assert {edge["relation"] for edge in graph.json()["edges"]} == {"LED_TO", "SUPPORTS"}
    assert history.json()["count"] == 1


def test_agent_stream_forwards_live_execution_events(monkeypatch):
    class StubAgent:
        def run(self, goal, service, scenario, on_event=None):
            on_event({"type": "tool_start", "tool": "search_memory", "arguments": {"service": service}})
            on_event({"type": "tool_result", "tool": "search_memory", "result": []})
            return {"answer": "Memory searched", "trace": [], "canary": {"decision": "rollback"}}

    monkeypatch.setattr(main, "groq_agent", StubAgent())
    with TestClient(main.app) as client:
        response = client.post("/api/agent/stream", json={"goal": "Deploy safely", "service": "payments", "scenario": "healthy"})
    assert response.status_code == 200
    assert '"type": "tool_start"' in response.text
    assert '"type": "complete"' in response.text

def test_coordination_api_requires_saved_release_incident(monkeypatch):
    from app.coordination import CoordinationWorkflow
    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main, "coordination", CoordinationWorkflow())
    class History:
        def history(self, limit=12):
            return [{"id": "incident-api", "service": "checkout", "version": "v1", "decision": "rollback",
                     "failed": True, "measurement_mode": "live_gateway", "during": {"status": 503},
                     "after": {"status": 200}, "metrics": {"sample_count": 12}}]
    monkeypatch.setattr(main, "get_release_controller", lambda: History())
    with TestClient(main.app) as client:
        result = client.post("/api/coordination/run", json={"incident_id": "incident-api"})
        missing = client.post("/api/coordination/run", json={"incident_id": "missing"})
    assert result.status_code == 200
    assert result.json()["plan"]["action"] == "keep_stable_and_verify"
    assert missing.status_code == 404


def test_health_uses_active_graph_connection_and_legacy_routes_are_removed(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main, "graph", None)
    monkeypatch.setattr(main, "graph_name", "test-graph")
    with TestClient(main.app) as client:
        assert client.get("/health").json() == {"status": "ok", "database": "memory", "graph": "test-graph"}
        assert client.get("/api/memory").status_code == 404
        assert client.post("/api/episodes", json={}).status_code == 404
        assert client.post("/api/agent/plan", json={}).status_code == 404
        assert client.post("/api/llm/run", json={}).status_code == 404
        assert client.post("/api/demo/seed").status_code == 404
        assert client.post("/api/demo/reset").status_code == 404
        assert client.get("/api/graph").status_code == 404


def test_auth_failures_are_rate_limited_and_return_retry_after(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "secret")
    monkeypatch.setattr(main, "RATE_LIMIT", 1)
    main.request_log.clear()
    with TestClient(main.app) as client:
        assert client.get("/api/runtime/history").status_code == 401
        limited = client.get("/api/runtime/history")
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0


def test_large_request_body_rejected(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main, "MAX_BODY_BYTES", 32)
    with TestClient(main.app) as client:
        response = client.post("/api/runtime/run", content=b" " * 33,
                               headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_metrics_snapshot_does_not_probe_live_gateway(monkeypatch):
    class Controller:
        def snapshot(self):
            return {"active_route": "stable", "incident_active": False}
        def status(self):
            raise AssertionError("metrics must not issue a gateway probe")

    monkeypatch.setattr(main, "release_controller", Controller())
    with TestClient(main.app) as client:
        result = client.get("/metrics")
    assert result.status_code == 200
    assert result.json()["controller"] == {"active_route": "stable", "incident_active": False}


def test_agent_capacity_returns_retryable_http_429(monkeypatch):
    from app.groq_agent import AgentCapacityError

    class SaturatedAgent:
        configured = True
        model = "test"
        def run(self, **_):
            raise AgentCapacityError("Agent capacity reached; retry shortly")

    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main, "groq_agent", SaturatedAgent())
    with TestClient(main.app) as client:
        response = client.post("/api/agent/run", json={"goal": "Deploy safely"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"


def test_agent_stream_reports_capacity_as_retryable_sse_event(monkeypatch):
    from app.groq_agent import AgentCapacityError

    class SaturatedAgent:
        def run(self, **_):
            raise AgentCapacityError("Agent capacity reached; retry shortly")

    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main, "groq_agent", SaturatedAgent())
    with TestClient(main.app) as client:
        response = client.post("/api/agent/stream", json={"goal": "Deploy safely"})
    assert response.status_code == 200
    assert '"status": 429' in response.text
    assert '"retry_after": 1' in response.text


def test_root_describes_the_api():
    from fastapi.testclient import TestClient
    from app.main import app
    body = TestClient(app).get("/").json()
    assert body["service"] == "revora-api" and body["endpoints"]["health"] == "/health"
