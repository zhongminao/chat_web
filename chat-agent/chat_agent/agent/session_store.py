"""会话日志：一个会话一个 append-only JSONL，历史用重放得到。

与 trace_log.py 的分工：那个记"一次请求发生了什么"（观测用），这个记"一场对话
是什么"（状态用）。会话模式下，日志本身就是权威 —— 历史是折叠出来的，不是另存
一份。所以不存在"日志和历史对不上"这种可能。

行格式（首行 header，其余按轮追加）：

    {"type":"session","version":1,"id":...,"createdAt":...}
    {"type":"turn","seq":0,"time":...,"provider":...,"model":...,
     "temperature":...,"toolsEnabled":...,"systemPrompt":...,"durationMs":...}
    {"type":"user","content":...}
    {"type":"assistant","content":...,"tool_calls":[...]}
    {"type":"tool","tool_call_id":...,"content":...}

刻意**不记 steps**：它是上面协议消息的投影 —— 工具名与参数在 assistant 的
tool_calls 里，结果在 tool 的 content 里，ok 可由 TOOL_ERROR_PREFIX 前缀算出。
记了就是同一份信息存两遍（DSH 的 session 日志也是这个思路：日志是权威，
其余都是折叠）。

历史重放 = 把 user/assistant/tool 三类记录按序取出，就是模型要的消息列表
（不含 system —— 它是每请求现给的，对应前端那个系统提示词面板）。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


VERSION = 1
DEFAULT_DIR = Path.home() / ".local" / "state" / "chat-agent" / "sessions"

# 会话 id 来自客户端，会被当文件名用。**必须**限制成单个安全路径段 ——
# 否则 "../../etc/foo" 就是一次路径穿越（服务在局域网可达，不是只有本机能连）。
# 允许字母数字与连字符/下划线，长度设上限。
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_HISTORY_TYPES = frozenset({"user", "assistant", "tool"})


def sanitize_id(raw: str) -> str | None:
    """合法则原样返回，否则 None。调用方应当拒绝而不是"尽力清洗"。"""
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    return candidate if _ID_RE.match(candidate) else None


def new_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def session_file(base_dir: Path | str, session_id: str) -> Path:
    return Path(base_dir) / f"{session_id}.jsonl"


def exists(base_dir: Path | str, session_id: str) -> bool:
    return session_file(base_dir, session_id).is_file()


def read_records(base_dir: Path | str, session_id: str) -> list[dict[str, Any]]:
    path = session_file(base_dir, session_id)
    records: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records


def load_history(base_dir: Path | str, session_id: str) -> list[dict[str, Any]]:
    """把日志折叠成模型要的消息列表（不含 system）。"""
    history: list[dict[str, Any]] = []
    for record in read_records(base_dir, session_id):
        if record.get("type") not in _HISTORY_TYPES:
            continue
        message: dict[str, Any] = {
            "role": record["type"],
            "content": record.get("content") or "",
        }
        if record.get("tool_calls"):
            message["tool_calls"] = record["tool_calls"]
        if record.get("tool_call_id"):
            message["tool_call_id"] = record["tool_call_id"]
        history.append(message)
    return history


def load_items(base_dir: Path | str, session_id: str) -> list[dict[str, Any]]:
    """按**渲染顺序**折出这场对话：user / step / assistant 三类。

    为什么不分别返回 messages 和 steps 两个平铺列表：那样前端恢复历史时对不上
    位置 —— 工具步骤本来夹在"用户提问"和"最终回复"之间，两张平铺表拼不回原顺序。
    这里直接给渲染序，前端照着 map 一遍就行。
    """
    from chat_agent.agent.tools import TOOL_ERROR_PREFIX

    items: list[dict[str, Any]] = []
    pending: dict[str, dict[str, str]] = {}

    for record in read_records(base_dir, session_id):
        record_type = record.get("type")
        if record_type == "user":
            items.append({"kind": "user", "content": record.get("content") or ""})
        elif record_type == "assistant":
            calls = record.get("tool_calls")
            if calls:
                # 只登记，不产出条目 —— 给人看的是随后那条工具结果，不是模型的调用意图
                for call in calls:
                    function = call.get("function") or {}
                    pending[str(call.get("id") or "")] = {
                        "tool": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or ""),
                    }
            else:
                items.append({"kind": "assistant", "content": record.get("content") or ""})
        elif record_type == "tool":
            call = pending.pop(str(record.get("tool_call_id") or ""), {})
            result = record.get("content") or ""
            items.append(
                {
                    "kind": "step",
                    "tool": call.get("tool", ""),
                    "arguments": call.get("arguments", ""),
                    "result": result,
                    "ok": not result.startswith(TOOL_ERROR_PREFIX),
                }
            )
    return items


def append_turn(
    base_dir: Path | str,
    session_id: str,
    *,
    user_messages: list[dict[str, Any]],
    protocol: list[dict[str, Any]],
    meta: dict[str, Any],
    ) -> Path | None:
    """追加一轮。失败返回 None（调用方不该因此失败 —— 但会话模式下这是状态，
    所以失败要显式处理，不能像 trace 那样静默）。"""
    try:
        path = session_file(base_dir, session_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        first = not path.exists()

        lines: list[str] = []
        if first:
            lines.append(
                json.dumps(
                    {
                        "type": "session",
                        "version": VERSION,
                        "id": session_id,
                        "createdAt": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
            )

        lines.append(
            json.dumps({"type": "turn", "time": int(time.time() * 1000), **meta}, ensure_ascii=False)
        )
        for message in user_messages:
            lines.append(
                json.dumps(
                    {"type": "user", "content": message.get("content") or ""},
                    ensure_ascii=False,
                )
            )
        for message in protocol:
            role = message.get("role")
            if role not in _HISTORY_TYPES:
                continue
            record: dict[str, Any] = {"type": role, "content": message.get("content") or ""}
            if message.get("tool_calls"):
                record["tool_calls"] = message["tool_calls"]
            if message.get("tool_call_id"):
                record["tool_call_id"] = message["tool_call_id"]
            lines.append(json.dumps(record, ensure_ascii=False))

        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return path
    except Exception:
        return None


def list_sessions(base_dir: Path | str) -> list[dict[str, Any]]:
    """列会话元数据（id / 创建时间 / 轮数 / 最后活动）。"""
    result: list[dict[str, Any]] = []
    try:
        for path in sorted(Path(base_dir).glob("*.jsonl")):
            records = read_records(base_dir, path.stem)
            if not records:
                continue
            header = records[0] if records[0].get("type") == "session" else {}
            turns = [r for r in records if r.get("type") == "turn"]
            result.append(
                {
                    "id": path.stem,
                    "createdAt": header.get("createdAt"),
                    "turns": len(turns),
                    "lastActivity": turns[-1].get("time") if turns else header.get("createdAt"),
                }
            )
    except OSError:
        return []
    result.sort(key=lambda item: item.get("lastActivity") or 0, reverse=True)
    return result
