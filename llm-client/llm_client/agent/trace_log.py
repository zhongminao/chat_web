"""轨迹落盘：把一次 /api/chat 完整记成一个 JSONL 文件，供事后回看与 eval。

形制参照 DSH 的 session 日志（~/.dsh/sessions/*/session.jsonl.zstd），只取第一层：
一请求一文件、首行 header 带 version、每行带 seq/time、不压缩不加密（靠 600/700 权限）。
不记流式 chunk —— chat 不流式，一轮的记录天然完整。

落盘失败绝不影响对话：所有异常都吞掉，只在 stderr 留一行。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


VERSION = 1
DIR_ENV = "CHAT_TRACE_DIR"
DISABLE_ENV = "CHAT_TRACE"
# 只有在调用方没给目录、也没设环境变量时才用它。真正的落点由应用决定 ——
# 这个库是独立可安装的包，不该知道"自己在一个叫 chat 的仓库里"。
DEFAULT_DIR = Path.home() / ".local" / "state" / "llm-client" / "traces"

# agent 能读到 ~/.bashrc / ~/.dsh/.credentials.yaml / fortrix.env，轨迹里会带上密钥。
# DSH 靠权限门拦在前面，chat 没有门，所以在这里擦。
#
# 第二个模式必须匹配**整个变量名**（如 FORTRIX_SECRET_KEY），不能写成 \bsecret\b ——
# 下划线算词字符，SECRET_KEY 里的 "SECRET" 后面没有词边界，那样会漏。
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "sk-<redacted>"),
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_]*(?:key|secret|token|password|passwd))\b"
            r"(\s*[:=]\s*)([\"']?)([^\s\"',}]{6,})"
        ),
        r"\1\2\3<redacted>",
    ),
    (re.compile(r"(?i)(auth\s*:\s*)([\"'])([^\"']+)([\"'])"), r"\1\2<redacted>\4"),
)


def trace_dir() -> Path:
    override = os.environ.get(DIR_ENV, "").strip()
    return Path(override).expanduser() if override else DEFAULT_DIR


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redact_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    return value


def _new_path(directory: Path | None = None) -> Path:
    now = datetime.now()
    name = f"{now.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}.jsonl"
    base = Path(directory).expanduser() if directory is not None else trace_dir()
    return base / now.strftime("%Y-%m-%d") / name


def write_trace(
    *,
    provider: str,
    model_name: str,
    temperature: float | None,
    tools_enabled: bool,
    system_prompt: str | None,
    messages: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    reply: str,
    duration_ms: int,
    error: str | None = None,
    directory: Path | None = None,
    ) -> Path | None:
    """记一次请求。返回落盘路径；被禁用或失败时返回 None。"""
    if os.environ.get(DISABLE_ENV, "").strip().lower() in {"0", "false", "no", "off"}:
        return None

    try:
        path = _new_path(directory)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        seq = 0
        records: list[dict[str, Any]] = []

        def add(record_type: str, **fields: Any) -> None:
            nonlocal seq
            records.append(
                {
                    "type": record_type,
                    "seq": seq,
                    "time": int(time.time() * 1000),
                    **fields,
                }
            )
            seq += 1

        records.append(
            {
                "type": "request",
                "version": VERSION,
                "id": path.stem,
                "time": int(time.time() * 1000),
                "provider": provider,
                "model": model_name,
                "temperature": temperature,
                "toolsEnabled": tools_enabled,
                "systemPrompt": system_prompt,
                "cwd": str(Path.cwd()),
            }
        )
        seq = 1

        for message in messages:
            fields = {"role": message.get("role"), "content": message.get("content")}
            if message.get("tool_calls"):
                fields["tool_calls"] = message["tool_calls"]
            if message.get("tool_call_id"):
                fields["tool_call_id"] = message["tool_call_id"]
            add("message", **fields)

        for step in steps:
            add(
                "step",
                tool=step.get("tool"),
                arguments=step.get("arguments"),
                ok=step.get("ok"),
                result=step.get("result"),
            )

        if error is not None:
            add("error", error=error, durationMs=duration_ms)
        else:
            add("reply", content=reply, durationMs=duration_ms)

        body = "\n".join(
            json.dumps(_redact_obj(record), ensure_ascii=False) for record in records
        )
        path.write_text(body + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path
    except Exception as exc:
        print(f"[trace_log] 落盘失败（不影响对话）: {exc}", file=sys.stderr)
        return None
