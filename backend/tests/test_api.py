from fastapi.testclient import TestClient
from app import main
from app.runtime import Runtime

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
        body = {"request_id": "api-test", "values": ["a", "a", "b"]}
        first = client.post("/api/runtime/run", headers=headers, json=body)
        assert first.status_code == 200 and first.json()["success"]
        assert client.post("/api/runtime/run", headers=headers, json=body).json() == first.json()
        assert client.post("/api/runtime/run", headers=headers, json={**body, "values": ["x"]}).status_code == 409
        assert client.post("/api/runtime/run", headers=headers, json={**body, "values": []}).status_code == 422
        assert client.post("/api/runtime/run", headers=headers, json={**body, "environment": "production"}).status_code == 422
