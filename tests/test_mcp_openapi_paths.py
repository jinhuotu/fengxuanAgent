from fastapi.testclient import TestClient

from app.main import app


def test_mcp_routes_are_registered():
    """轻量级 MCP 路由注册检查（RAG/MCP 学习路径）。"""
    client = TestClient(app)
    openapi = client.get("/openapi.json").json()
    paths = openapi.get("paths") or {}
    mcp_paths = [p for p in paths if p.startswith("/api/v1/mcp")]
    assert mcp_paths, "expected at least one /api/v1/mcp/* route"
