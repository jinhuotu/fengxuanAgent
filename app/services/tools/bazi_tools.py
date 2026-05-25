"""内置工具：china-testing/bazi 八字排盘、生肖合婚、罗喉日。"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.tools.bazi_runner import run_bazi_script


class BaziChartArgs(BaseModel):
    """公历/农历出生时刻八字排盘（调用本地 bazi.py）。"""

    year: int = Field(..., ge=1, le=3000, description="年")
    month: int = Field(..., ge=1, le=12, description="月")
    day: int = Field(..., ge=1, le=31, description="日")
    time: int = Field(
        ...,
        ge=0,
        le=23,
        description="时辰（小时，0–23；与 bazi.py 一致，如 19 表示戌时附近）",
    )
    use_gregorian: bool = Field(
        default=True,
        description="True 表示输入为公历并加 -g；False 表示农历（不加 -g）",
    )
    is_female: bool = Field(default=False, description="是否为女命（-n）")
    is_leap_month: bool = Field(default=False, description="农历闰月（-r，仅农历有效）")
    simple_mode: bool = Field(default=True, description="简单模式（-s），输出较短，适合对话")


class ShengxiaoMatchArgs(BaseModel):
    """生肖合婚参考（合/冲/刑/害等，非完整八字合婚）。"""

    shengxiao: str = Field(
        ...,
        min_length=1,
        max_length=4,
        description="生肖汉字，如 虎、龙、鼠",
    )


class LuohouArgs(BaseModel):
    """罗喉日与杀师时（风水罗盘慎用时段）。"""

    start_year: int | None = Field(default=None, ge=1900, le=2100, description="起始公历年，默认今天")
    start_month: int | None = Field(default=None, ge=1, le=12, description="起始公历月")
    start_day: int | None = Field(default=None, ge=1, le=31, description="起始公历日")
    days: int = Field(default=7, ge=1, le=62, description="从起始日起连续计算的天数（luohou.py -n）")


def _format_result(result: dict[str, Any], *, disclaimer: str) -> str:
    if not result.get("ok"):
        err = result.get("error") or "未知错误"
        return f"[BAZI_TOOL_ERROR] {err}"
    body = str(result.get("output") or "")
    parts = [body, "", "---", disclaimer]
    if result.get("truncated"):
        parts.append("（输出已截断，可开启 simple_mode 或缩小查询范围）")
    return "\n".join(parts)


_BAZI_DISCLAIMER = "仅供参考与娱乐，不构成专业命理或医疗建议。"


def build_compute_bazi_chart_tool() -> Callable[..., str]:
    @tool("compute_bazi_chart", args_schema=BaziChartArgs)
    def compute_bazi_chart(
        year: int,
        month: int,
        day: int,
        time: int,
        use_gregorian: bool = True,
        is_female: bool = False,
        is_leap_month: bool = False,
        simple_mode: bool = True,
    ) -> str:
        """
        根据出生年月日时进行八字排盘（五行、十神、刑冲合会、《三命通会》片段等）。
        需要用户明确的出生日期与时辰；默认按公历输入。
        """
        args = [str(year), str(month), str(day), str(time)]
        if use_gregorian:
            args.append("-g")
        if is_female:
            args.append("-n")
        if is_leap_month:
            args.append("-r")
        if simple_mode:
            args.append("-s")

        result = run_bazi_script("bazi.py", args)
        return _format_result(result, disclaimer=_BAZI_DISCLAIMER)

    return compute_bazi_chart


def build_match_shengxiao_tool() -> Callable[..., str]:
    @tool("match_shengxiao", args_schema=ShengxiaoMatchArgs)
    def match_shengxiao(shengxiao: str) -> str:
        """
        按生肖查询三合、六合、相冲、相刑、相害、相破等合婚参考（非完整八字合婚）。
        """
        result = run_bazi_script("shengxiao.py", [shengxiao.strip()])
        return _format_result(result, disclaimer=_BAZI_DISCLAIMER)

    return match_shengxiao


def build_compute_luohou_tool() -> Callable[..., str]:
    @tool("compute_luohou", args_schema=LuohouArgs)
    def compute_luohou(
        start_year: int | None = None,
        start_month: int | None = None,
        start_day: int | None = None,
        days: int = 7,
    ) -> str:
        """
        计算罗喉日与杀师时，供风水师判断何时慎用罗盘；可指定起始公历日期与连续天数。
        """
        args: list[str] = []
        if start_year is not None and start_month is not None and start_day is not None:
            args.extend(["-d", f"{start_year} {start_month} {start_day}"])
        args.extend(["-n", str(days)])

        result = run_bazi_script("luohou.py", args, max_output_chars=24_000)
        return _format_result(result, disclaimer=_BAZI_DISCLAIMER)

    return compute_luohou

