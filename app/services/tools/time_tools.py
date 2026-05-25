"""内置工具：查询服务器时间（供需要「当前时刻」的 Agent 回答使用）。"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Callable

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class CurrentTimeArgs(BaseModel):
    """可选 IANA 时区，用于展示当地民用时间。"""

    timezone: str | None = Field(
        default=None,
        description="IANA 时区名（可选），如 Asia/Shanghai、UTC；不传则返回服务器本地时间。",
    )


def build_get_current_time_tool() -> Callable[..., dict]:
    """返回 LangChain 工具 `get_current_time`（UTC + 可选时区/服务器本地）。"""

    @tool("get_current_time", args_schema=CurrentTimeArgs)
    def get_current_time(timezone: str | None = None) -> dict:
        """
        查询当前服务器时间（调用瞬间）。用于回答「现在几点」「今天日期」等需要实时时钟的问题。
        返回 UTC ISO 字符串、本地/指定时区 ISO 字符串与 Unix 毫秒时间戳。
        """
        now_utc = datetime.now(dt_timezone.utc)
        tz_name = (timezone or "").strip() or None
        if tz_name:
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                return {
                    "error": f"未知或无效的 IANA 时区: {tz_name}",
                    "hint": "示例: Asia/Shanghai、UTC、Europe/London",
                }
            now_wall = now_utc.astimezone(tz)
            used = tz_name
        else:
            now_wall = datetime.now().astimezone()
            used = now_wall.tzname() or str(now_wall.tzinfo or "local")

        return {
            "utc_iso": now_utc.isoformat(),
            "local_iso": now_wall.isoformat(),
            "timezone": used,
            "unix_ms": int(now_utc.timestamp() * 1000),
        }

    return get_current_time
