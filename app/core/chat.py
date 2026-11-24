"""对话的固定类型；可调整的业务参数仍放在 core/config.py。"""
from datetime import timedelta, timezone
from enum import IntEnum


class MessageRole(IntEnum):
    USER = 1
    ASSISTANT = 2


class MessageStatus(IntEnum):
    GENERATING = 1
    COMPLETED = 2
    FAILED = 3
    INTERRUPTED = 4


ROLE_NAMES = {MessageRole.USER: "user", MessageRole.ASSISTANT: "assistant"}
STATUS_NAMES = {
    MessageStatus.GENERATING: "generating", MessageStatus.COMPLETED: "completed",
    MessageStatus.FAILED: "failed", MessageStatus.INTERRUPTED: "interrupted",
}
CHAT_TIMEZONE = timezone(timedelta(hours=8))
CHAT_TITLE_LENGTH = 60
CHAT_GENERATION_GATE_ID = 1
CHAT_INPUT_LENGTH = 4000
CHAT_DEFAULT_PAGE_SIZE = 20
CHAT_MAX_PAGE_SIZE = 100
CHAT_STREAM_HEARTBEAT_SECONDS = 1
CHAT_STREAM_DEADLINE_MARGIN_SECONDS = 15
CHAT_CACHE_PREFIX = "fengxuan:chat:context"
CHAT_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

