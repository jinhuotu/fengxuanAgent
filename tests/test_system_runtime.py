from fastapi.testclient import TestClient

from app.main import app


def test_system_runtime_exposes_safe_config():
    client = TestClient(app)
    response = client.get("/api/v1/system/runtime")
    assert response.status_code == 200
    body = response.json()
    assert body.get("code") == 0
    data = body.get("data") or {}
    assert "app_env" in data
    assert "app_name" in data
    assert "app_port" in data
    assert "app_debug" in data
