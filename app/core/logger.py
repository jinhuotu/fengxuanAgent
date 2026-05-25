"""全局日志配置。"""

import logging
import sys
from logging.config import dictConfig


def setup_logging(debug: bool = False) -> None:
    # Windows 控制台默认 GBK，DEBUG 日志里若含对话中的 emoji 会触发 UnicodeEncodeError。
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except OSError:
            pass

    level = "DEBUG" if debug else "INFO"
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                }
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "default"},
                "file": {
                    "class": "logging.FileHandler",
                    "filename": "app.log",
                    "encoding": "utf-8",
                    "formatter": "default",
                },
            },
            "root": {"level": level, "handlers": ["console", "file"]},
        }
    )


logger = logging.getLogger("agent-backend")
