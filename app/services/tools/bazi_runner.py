"""在本地 china-testing/bazi 仓库目录下通过子进程调用排盘脚本。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from app.core.config import get_settings

_ANSI_RE = re.compile(r"\x1B\[[0-9;]*m")
_DEFAULT_MAX_OUTPUT_CHARS = 16_000


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _decode_subprocess_output(data: bytes) -> str:
    """Windows 下 bazi 脚本常输出 GBK，依次尝试常见编码。"""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def resolve_bazi_home() -> Path | None:
    raw = (get_settings().bazi_home or "").strip()
    if not raw:
        return None
    home = Path(raw).expanduser().resolve()
    if not home.is_dir():
        return None
    return home


def resolve_bazi_python(home: Path) -> Path | None:
    configured = (get_settings().bazi_python or "").strip()
    if configured:
        exe = Path(configured).expanduser()
        return exe if exe.is_file() else None

    candidates = [
        home / ".venv" / "Scripts" / "python.exe",
        home / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for exe in candidates:
        if exe.is_file():
            return exe
    return None


def run_bazi_script(
    script_name: str,
    args: list[str],
    *,
    timeout_seconds: int | None = None,
    max_output_chars: int | None = None,
) -> dict[str, str | bool]:
    """
    在 bazi 仓库根目录执行 ``python <script_name> <args>``，返回统一结构。

    成功时 ``ok=True`` 且 ``output`` 为 stdout 文本；失败时 ``ok=False`` 且 ``error`` 说明原因。
    """
    home = resolve_bazi_home()
    if home is None:
        return {
            "ok": False,
            "error": "未配置或找不到 BAZI_HOME 目录。请在 .env 中设置 BAZI_HOME（例如 D:/code/tools/bazi）。",
        }

    script = home / script_name
    if not script.is_file():
        return {"ok": False, "error": f"脚本不存在: {script}"}

    python_exe = resolve_bazi_python(home)
    if python_exe is None:
        return {
            "ok": False,
            "error": "找不到可用的 Python 解释器。请设置 BAZI_PYTHON 或在 BAZI_HOME 下创建 .venv。",
        }

    timeout = timeout_seconds if timeout_seconds is not None else get_settings().bazi_subprocess_timeout_seconds
    max_chars = max_output_chars if max_output_chars is not None else get_settings().bazi_max_output_chars

    cmd = [str(python_exe), str(script), *args]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(home),
            capture_output=True,
            timeout=timeout,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"排盘脚本超时（>{timeout}s）: {script_name}"}
    except OSError as e:
        return {"ok": False, "error": f"无法启动子进程: {e!r}"}

    stdout = _decode_subprocess_output(completed.stdout)
    stderr = _decode_subprocess_output(completed.stderr)
    stdout = _strip_ansi(stdout).strip()
    stderr = _strip_ansi(stderr).strip()

    if completed.returncode != 0:
        detail = stderr or stdout or f"exit code {completed.returncode}"
        return {"ok": False, "error": detail, "output": stdout}

    truncated = False
    if len(stdout) > max_chars:
        stdout = stdout[:max_chars] + f"\n\n…（输出已截断，共约 {len(stdout)} 字符，仅保留前 {max_chars} 字符）"
        truncated = True

    return {"ok": True, "output": stdout, "truncated": truncated}
