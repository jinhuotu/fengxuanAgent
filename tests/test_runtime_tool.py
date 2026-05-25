from app.services.tools.runtime_tools import build_get_python_runtime_tool


def test_get_python_runtime_tool_returns_shape():
    tool = build_get_python_runtime_tool()
    out = tool.invoke({})
    assert isinstance(out, dict)
    assert "version" in out
    assert "full_version" in out
    assert "implementation" in out
    assert isinstance(out["version"], str)
