import pytest

from app.services.tools.bazi_runner import resolve_bazi_home, run_bazi_script
from app.services.tools.bazi_tools import (
    build_compute_bazi_chart_tool,
    build_match_shengxiao_tool,
)


@pytest.fixture
def bazi_home():
    home = resolve_bazi_home()
    if home is None:
        pytest.skip("BAZI_HOME 未配置或目录不存在")
    return home


def test_match_shengxiao_tool(bazi_home):
    tool = build_match_shengxiao_tool()
    out = tool.invoke({"shengxiao": "虎"})
    assert "[BAZI_TOOL_ERROR]" not in out
    assert len(out) > 80


def test_compute_bazi_chart_simple_mode(bazi_home):
    tool = build_compute_bazi_chart_tool()
    out = tool.invoke(
        {
            "year": 1990,
            "month": 5,
            "day": 15,
            "time": 12,
            "use_gregorian": True,
            "simple_mode": True,
        }
    )
    assert "[BAZI_TOOL_ERROR]" not in out
    assert len(out) > 100


def test_run_bazi_script_missing_script():
    result = run_bazi_script("__missing__.py", [])
    assert result["ok"] is False
