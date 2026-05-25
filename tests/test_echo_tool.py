from app.services.tools.echo_tools import build_get_echo_tool


def test_get_echo_returns_input():
    tool = build_get_echo_tool()
    result = tool.invoke({"text": "你好世界"})
    assert result["echo"] == "你好世界"
