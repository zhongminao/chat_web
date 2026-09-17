"""会话日志：一个会话一个 append-only JSONL，历史用重放得到。

**日志即权威**：历史是折叠出来的，不是另存一份。所以不存在"日志和历史对不上"这种
可能 —— 这里曾经并存的 trace_log（每请求一个文件的观测轨迹）已于 2026-09-12 删除，
因为它记的东西这个日志已经记全了，两套并存只会漂移。

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
# 不给 base_dir 时的兜底位置。服务端**总是**显式传 SESSION_DIR，这只是给"单独用这个
# 模块"的场景留的默认值。路径里的 chat-agent 是合并前的旧包名，一并改掉：那个目录
# 从没被创建过（本机 ~/.local/state/ 下就没有它），所以改默认值不会丢东西。
DEFAULT_DIR = Path.home() / ".local" / "state" / "chat" / "sessions"

# 会话 id 来自客户端，会被当文件名用。**必须**限制成单个安全路径段 ——
# 否则 "../../etc/foo" 就是一次路径穿越（服务在局域网可达，不是只有本机能连）。
# 允许字母数字与连字符/下划线，长度设上限。
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_HISTORY_TYPES = frozenset({"user", "assistant", "tool"})

# load_items 会产出的条目种类 —— **这里是唯一的声明处**。
#
# 为什么要显式声明而不是"看 load_items 里写了什么"：前端有一张 kind → 渲染 的映射表，
# 两边的取值域必须一样。后端加了新 kind 而前端不认识时，正确的结果是**立刻响**，
# 而不是前端悄悄把它当成一条 assistant 消息画出来（那正是以前的行为：映射的兜底分支
# 什么都不检查）。
#
# 这个集合会进 backend-contract.json 的 enums，前端 smoke 拿它逐一渲染、断言没有
# 落到"未知条目"上 —— 于是"后端能发什么"和"前端能画什么"被同一份声明审着。
ITEM_KINDS = frozenset({"user", "step", "assistant", "running", "bash-request", "plan"})


def _append_records(path: Path, records: list[dict[str, Any]]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False

def repair_orphans(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给"声明了 tool_calls 但没有对应结果"的调用补一条合成结果。
    """
    from chat.agent.tools import TOOL_ERROR_PREFIX

    answered = {m.get("tool_call_id") for m in history if m.get("role") == "tool"}
    repaired: list[dict[str, Any]] = []
    for message in history:
        repaired.append(message)
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            call_id = str(call.get("id") or "")
            if not call_id or call_id in answered:
                continue
            repaired.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"{TOOL_ERROR_PREFIX}这一轮没有留下结果（进程中断或被取消）",
            })
            answered.add(call_id)
    return repaired


def load_history(base_dir: Path | str, session_id: str) -> list[dict[str, Any]]:
    """把日志折叠成模型要的消息列表（不含 system）。

    user 是**扣着**放的：一轮要产生了产出（assistant/tool 记录）之后才进历史。
    user 现在必须在跑之前就写盘（否则崩了不知道用户问过什么），所以"失败的那次
    提问不留痕"这条规矩搬到了重放这一侧。最后跑一遍 repair_orphans。
    """
    from chat.agent.tools import BASH_REQUEST_PREFIX

    # 先收集已批准的 bash 执行结果：恢复 loop 时要把 pending 的 bash request 换成
    # 真实执行结果，模型才能接着往下说（否则它看到的还是那张"请求卡片"）。
    bash_results: dict[str, str] = {}
    for record in read_records(base_dir, session_id):
        if record.get("type") == "bash-result":
            bash_results[str(record.get("requestId") or "")] = record.get("content") or ""

    history: list[dict[str, Any]] = []
    pending_users: list[dict[str, Any]] = []
    turn_produced = False

    def _message(record: dict[str, Any]) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": record["type"],
            "content": record.get("content") or "",
        }
        if record.get("tool_calls"):
            message["tool_calls"] = record["tool_calls"]
        if record.get("tool_call_id"):
            message["tool_call_id"] = record["tool_call_id"]
        return message

    for record in read_records(base_dir, session_id):
        record_type = record.get("type")
        if record_type == "turn":
            pending_users, turn_produced = [], False
        elif record_type == "turn-end":
            pending_users = []          # 这一轮没有任何产出 → 丢掉它的 user
        elif record_type == "user":
            pending_users.append(_message(record))
        elif record_type == "tool":
            content = record.get("content") or ""
            if content.startswith(BASH_REQUEST_PREFIX):
                try:
                    payload = json.loads(content[len(BASH_REQUEST_PREFIX):])
                    request_id = str(payload.get("id") or "")
                    if request_id in bash_results:
                        record = {**record, "content": bash_results[request_id]}
                except (json.JSONDecodeError, KeyError):
                    pass
            if not turn_produced:
                history.extend(pending_users)
                pending_users, turn_produced = [], True
            history.append(_message(record))
        elif record_type == "assistant":
            if not turn_produced:
                history.extend(pending_users)
                pending_users, turn_produced = [], True
            history.append(_message(record))

    return repair_orphans(history)      # 残留的 pending_users = 最后一轮被杀，同样丢掉



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


def load_items(base_dir: Path | str, session_id: str) -> list[dict[str, Any]]:
    """按**渲染顺序**折出这场对话：user / step / assistant / running 四类。

    为什么不分别返回 messages 和 steps 两个平铺列表：那样前端恢复历史时对不上
    位置 —— 工具步骤本来夹在"用户提问"和"最终回复"之间，两张平铺表拼不回原顺序。
    这里直接给渲染序，前端照着 map 一遍就行。

    **一条带 tool_calls 的 assistant 记录会产生两类条目**：正文（如果有）先作为一条
    assistant 条目，随后每个工具结果各一条 step —— 也就是说 assistant 条目不再只出现
    在收尾处，它还会夹在工具步骤**中间**。这正是"模型先说一句再动手"要的渲染序。

    kind="running"：**声明了 tool_calls 但还没有结果的调用** —— 也就是此刻正在跑的
    那个/那些。它是"日志就是进度"这条路的最后一环：记录是边跑边落盘的，所以轮询
    这个接口就能看到进度；但一条工具记录只在**跑完**之后才存在，光看已完成的结果
    会卡在"上一步"上，看不到正在跑的那一个。

    正常跑完的轮次里不会有 running 条目（每个声明都会配上结果）；被中断的轮次也不会
    （loop 的 finally 会给没跑完的补合成结果）。所以它只在**真的在途**时出现 ——
    恰好就是我们要显示"正在执行…"的那一刻。
    """
    from chat.agent.tools import BASH_REQUEST_PREFIX, TOOL_ERROR_PREFIX

    items: list[dict[str, Any]] = []
    pending: dict[str, dict[str, str]] = {}
    bash_request_indexes: dict[str, int] = {}

    for record in read_records(base_dir, session_id):
        record_type = record.get("type")
        if record_type == "user":
            items.append({"kind": "user", "content": record.get("content") or ""})
        elif record_type == "assistant":
            calls = record.get("tool_calls")
            content = record.get("content") or ""
            if calls:
                # 带 tool_calls 的消息**也可以有正文**（模型一边说一边动手），而正文和
                # "调用意图"是两回事：意图由随后的工具结果代表，正文是模型给人看的
                # 叙述 —— 所以正文独立成一条 assistant 条目，排在这一批工具结果之前。
                #
                # 这里原来是整个丢掉的，于是界面上"全是工具调用"。但叙述一直在盘上：
                # loop.py 把带 tool_calls 的整条消息原样落盘（content 和 tool_calls
                # 一起），从来没裁过字段；丢的是折 items 这一步没去读 content。
                #
                # 空正文不产出条目：runtime 允许 assistant(tool_calls) 无正文
                # （见 messages.Message.content 的说明），那种情况不该多画一个空气泡。
                # 门槛用 strip()，落盘用**原样** content —— 前后空白在 markdown 里有
                # 意义（缩进代码块），只拿它判"要不要画"。
                if content.strip():
                    items.append({"kind": "assistant", "content": content})
                # 工具调用本身仍然只登记、不产出条目 —— 给人看的是随后那条工具结果，
                # 不是模型的调用意图。剩下没被 pop 掉的会在最后变成 running 条目。
                for call in calls:
                    function = call.get("function") or {}
                    pending[str(call.get("id") or "")] = {
                        "tool": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or ""),
                    }
            else:
                items.append({"kind": "assistant", "content": content})
        elif record_type == "plan":
            todos = record.get("todos")
            items.append({"kind": "plan", "todos": todos if isinstance(todos, list) else []})
        elif record_type == "tool":
            call = pending.pop(str(record.get("tool_call_id") or ""), {})
            result = record.get("content") or ""
            if call.get("tool") == "run_bash" and result.startswith(BASH_REQUEST_PREFIX):
                try:
                    request = json.loads(result[len(BASH_REQUEST_PREFIX):])
                except json.JSONDecodeError:
                    request = {}
                request_id = str(request.get("id") or "")
                item = {
                    "kind": "bash-request",
                    "id": request_id,
                    "command": str(request.get("command") or ""),
                    "cwd": str(request.get("cwd") or ""),
                    "timeout": request.get("timeout"),
                    "status": "pending",
                    "result": "",
                }
                bash_request_indexes[request_id] = len(items)
                items.append(item)
            else:
                items.append(
                    {
                        "kind": "step",
                        "tool": call.get("tool", ""),
                        "arguments": call.get("arguments", ""),
                        "result": result,
                        "ok": not result.startswith(TOOL_ERROR_PREFIX),
                    }
                )
        elif record_type == "bash-result":
            request_id = str(record.get("requestId") or "")
            index = bash_request_indexes.get(request_id)
            if index is not None:
                items[index]["status"] = str(record.get("status") or "")
                items[index]["result"] = record.get("content") or ""

    for call in pending.values():
        items.append(
            {"kind": "running", "tool": call.get("tool", ""), "arguments": call.get("arguments", "")}
        )
    return items

class TurnWriter:
    """一场会话里**一轮**的落盘器。三步，顺序不能换：

        writer = TurnWriter(base_dir, session_id, workspace_id=...)
        writer.begin(meta, user_messages)   # 跑 agent 之前
        writer.record(message)              # 跑的过程中，每产生一条调一次
        writer.finish(...)                  # 跑完 / 失败 / 被中断之后
    """

    def __init__(self, base_dir: Path | str, session_id: str, *,
                 workspace_id: str | None = None):
        self._path = session_file(base_dir, session_id)
        self._session_id = session_id
        self._workspace_id = workspace_id
        self.failed = False      # 任何一次写失败都置 True —— 调用方必须看它

    def _append(self, *records: dict[str, Any]) -> None:
        if not _append_records(self._path, list(records)):
            self.failed = True

    def begin(self, meta: dict[str, Any], user_messages: list[dict[str, Any]]) -> None:
        """跑之前写：会话头（首次才写）+ turn + 用户消息。

        meta 只能放**跑之前就知道**的东西 —— 温度、生效的 system 提示、耗时都要
        跑完才知道，那些进 finish()。字段记在开头还是结尾，取决于它什么时候才可知。
        """
        records: list[dict[str, Any]] = []
        if not self._path.exists():
            records.append({
                "type": "session", "version": VERSION, "id": self._session_id,
                "createdAt": int(time.time() * 1000), "workspaceId": self._workspace_id,
            })
        records.append({"type": "turn", "time": int(time.time() * 1000), **meta})
        for message in user_messages:
            records.append({"type": "user", "content": message.get("content") or ""})
        self._append(*records)

    def record(self, message: dict[str, Any]) -> None:
        """**立刻**写这一条 —— 就在调用它的那一行。不是攒一批、更不是跑完统一交，
        所以一轮中途崩溃/被杀/被取消，已经发生的事都在盘上。"""
        role = message.get("role")
        if role not in _HISTORY_TYPES:
            return
        record: dict[str, Any] = {"type": role, "content": message.get("content") or ""}
        if message.get("tool_calls"):
            record["tool_calls"] = message["tool_calls"]
        if message.get("tool_call_id"):
            record["tool_call_id"] = message["tool_call_id"]
        self._append(record)

    def record_plan(self, todos: list[dict[str, Any]]) -> None:
        """写当前计划快照。它是 UI 状态，不进入模型历史回放。"""
        self._append({"type": "plan", "time": int(time.time() * 1000), "todos": todos})

    def finish(self, *, durationMs: int, temperature: float | None = None,
               error: str | None = None) -> None:
        """收尾。**是新类型 turn-end，不是第二条 turn** —— turn_count() 按
        type=="turn" 数条数，再写一条会把轮数算成两倍。

        systemPrompt 不在这里：它是**跑之前**就解析好、写进 turn 记录的（见
        runtime.resolve_system_prompt）。放这儿的话取消的轮次就记不上 —— 而"这一轮
        用的哪份提示词"恰恰是取消之后最想知道的事。
        """
        self._append({
            "type": "turn-end", "time": int(time.time() * 1000),
            "durationMs": durationMs, "temperature": temperature,
            "error": error,
        })


TITLE_LENGTH = 24


def derive_title(records: list[dict[str, Any]]) -> str:
    """标题 = 第一条用户消息的前几个字符。

    从日志现算，不落盘：它只是渲染用的投影，存下来就会和原文漂移（DSH 那边的
    标题投影还带 seq 水位线，就是为了处理这件事 —— 这里便宜得多，直接每次算）。
    """
    for record in records:
        if record.get("type") != "user":
            continue
        text = " ".join(str(record.get("content") or "").split())
        if not text:
            continue
        return text[:TITLE_LENGTH] + ("…" if len(text) > TITLE_LENGTH else "")
    return "(空对话)"


def list_sessions(base_dir: Path | str, workspace_id: str | None = None) -> list[dict[str, Any]]:
    """列会话元数据：id / 标题 / 所属工作区 / 创建时间 / 轮数 / 最后活动。

    按最后活动倒序 —— 侧栏就是这么排的。给了 workspace_id 就只列那个工作区下的：
    切到别的工作区时，不该还看见另一个工作区的对话。
    """
    result: list[dict[str, Any]] = []
    try:
        for path in sorted(Path(base_dir).glob("*.jsonl")):
            records = read_records(base_dir, path.stem)
            if not records:
                continue
            header = records[0] if records[0].get("type") == "session" else {}
            owner = header.get("workspaceId")
            if workspace_id is not None and owner != workspace_id:
                continue
            turns = [r for r in records if r.get("type") == "turn"]
            result.append(
                {
                    "id": path.stem,
                    "title": derive_title(records),
                    "workspaceId": owner,
                    "createdAt": header.get("createdAt"),
                    "turns": len(turns),
                    "lastActivity": turns[-1].get("time") if turns else header.get("createdAt"),
                }
            )
    except OSError:
        return []
    result.sort(key=lambda item: item.get("lastActivity") or 0, reverse=True)
    return result


def load_workspace_id(base_dir: Path | str, session_id: str) -> str | None:
    """这场会话绑定的工作区（首行 header 里的引用）。"""
    for record in read_records(base_dir, session_id)[:1]:
        if record.get("type") == "session":
            return record.get("workspaceId")
    return None


def append_bash_result(base_dir: Path | str, session_id: str, request_id: str, *,
                       status: str, content: str = "") -> bool:
    safe_id = sanitize_id(session_id)
    if safe_id is None:
        return False
    return _append_records(session_file(base_dir, safe_id), [{
        "type": "bash-result",
        "time": int(time.time() * 1000),
        "requestId": request_id,
        "status": status,
        "content": content,
    }])


def find_bash_request(base_dir: Path | str, session_id: str, request_id: str) -> dict[str, Any] | None:
    from chat.agent.tools import BASH_REQUEST_PREFIX

    records = read_records(base_dir, session_id)
    call_by_id: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()
    found: dict[str, Any] | None = None
    for record in records:
        record_type = record.get("type")
        if record_type == "assistant":
            for call in record.get("tool_calls") or []:
                function = call.get("function") or {}
                if function.get("name") == "run_bash":
                    call_by_id[str(call.get("id") or "")] = function
        elif record_type == "tool":
            content = record.get("content") or ""
            if not content.startswith(BASH_REQUEST_PREFIX):
                continue
            try:
                payload = json.loads(content[len(BASH_REQUEST_PREFIX):])
            except json.JSONDecodeError:
                continue
            if payload.get("id") != request_id:
                continue
            function = call_by_id.get(str(record.get("tool_call_id") or "")) or {}
            try:
                arguments = json.loads(str(function.get("arguments") or "{}"))
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            found = {
                "id": request_id,
                "command": str(arguments.get("command") or payload.get("command") or ""),
                "timeout": payload.get("timeout"),
                "cwd": str(payload.get("cwd") or ""),
            }
        elif record_type == "bash-result" and record.get("requestId") == request_id:
            completed.add(request_id)
    if request_id in completed:
        return None
    return found


def last_turn_meta(base_dir: Path | str, session_id: str) -> tuple[dict[str, Any], float | None]:
    """读最后一轮 turn 的元信息 + 最后一个 turn-end 的温度。

    审批 bash 后要恢复 loop，需要重建 provider/model/temperature/sandboxMode 等
    上下文；这些散落在 turn（跑之前）和 turn-end（跑完才知道的温度）里。
    """
    meta: dict[str, Any] = {}
    temperature: float | None = None
    for record in read_records(base_dir, session_id):
        record_type = record.get("type")
        if record_type == "turn":
            meta = {
                "provider": record.get("provider"),
                "model": record.get("model"),
                "toolsEnabled": record.get("toolsEnabled"),
                "sandboxMode": record.get("sandboxMode"),
                "systemPrompt": record.get("systemPrompt"),
            }
        elif record_type == "turn-end":
            temperature = record.get("temperature")
    return meta, temperature


def delete_session(base_dir: Path | str, session_id: str) -> bool:
    """删掉一场会话的日志文件。删掉了返回 True，本来就不在返回 False。

    日志是这场会话的**唯一**载体（没有数据库、没有索引），所以删文件就是删会话，
    删完没有回收站。调用方必须先让用户确认过。

    id 先过 sanitize_id：它是要拼进路径的，没校验就能被 "../" 带出去删到别的文件。
    """
    safe_id = sanitize_id(session_id)
    if safe_id is None:
        return False
    try:
        session_file(base_dir, safe_id).unlink()
    except OSError:
        return False
    return True


def turn_count(base_dir: Path | str, session_id: str) -> int:
    return sum(1 for record in read_records(base_dir, session_id) if record.get("type") == "turn")


# ---------------------------------------------------------------------------
# 会话级设置
# ---------------------------------------------------------------------------
#
# 会话级设置：两个值 —— "是否使用工具"和"这一轮用的系统提示词"。
#
# 它们决定的是**这个对话现在是什么**，所以属于对话、不属于界面 —— 跟着这一场对话走，
# 切到别的对话时跟着变回来。都记在**每一轮的 turn 元信息**里（那一轮实际用的值），
# 不再有单独的 settings 记录：那种记录只可能在"还没说过话"的会话里出现，内容又跟
# 轮次重复。**没说过话就不该有会话文件**，所以那条路整个删掉了。
#
# 两者的区别在**能不能中途改**：
#   toolsEnabled —— 第一轮定死，之后服务端 409 拒绝。关掉工具会让 normalize_messages
#     丢掉历史里的工具协议消息，等于静默截断历史。
#   systemPrompt —— 允许中途改。改它不破坏协议，只改变行为；而且每一轮各记各的，
#     事后能查到"第 3 轮用的是哪份"。
def load_settings(base_dir: Path | str, session_id: str) -> dict[str, Any]:
    """这场会话的设置：**每个键各自向后找最近一次记下的值**；一个都没有就返回空。

    为什么要分别回溯、而不是"取最后一轮"：
      toolsEnabled —— 每轮都记（它锁死之后不变，重复写也无所谓，而且它很短）；
      systemPrompt —— **只在变化时才记**（一千多字，每轮写一遍等于同一份信息存 N 遍，
        违反"记了就是同一份信息存两遍"那条规矩）。所以它的最新值可能在好几轮之前。

    于是语义是：turn 记录里**带 systemPrompt = 这一轮换了**，不带 = 沿用上一轮的。
    """
    settings: dict[str, Any] = {}
    for record in reversed(read_records(base_dir, session_id)):
        if record.get("type") != "turn":
            continue
        for key in ("toolsEnabled", "sandboxMode", "systemPrompt"):
            if key not in settings and key in record:
                settings[key] = record[key]
        if "toolsEnabled" in settings and "sandboxMode" in settings and "systemPrompt" in settings:
            break

    # systemPrompt 的"没记过"和"记过、值是 None（这一轮不发送系统消息）"是两回事，
    # 后者必须能被区分出来 —— 上面靠 `key in record` 区分，所以这里不能把它抹成
    # 一个默认值。没记过就真的没有这个键。
    return settings


def system_prompt_meta(base_dir: Path | str, session_id: str,
                       effective_prompt: str | None) -> dict[str, Any]:
    """这一轮的提示词要不要写进 turn 记录 —— **变了才写**，没变返回空 dict。

    每轮都写一遍同一份一千多字的提示词，就是同一份信息存 N 遍。变了的轮次写下去，
    没变的沿用 —— 读回来靠 load_settings 回溯，语义完全一样，日志小得多。

    第一轮总会写（此前没有可比的值），所以任何说过话的会话都查得到"现在用的是哪份"。
    """
    settings = load_settings(base_dir, session_id)
    if "systemPrompt" in settings and settings["systemPrompt"] == effective_prompt:
        return {}
    return {"systemPrompt": effective_prompt}
