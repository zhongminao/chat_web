"""web 与 CLI 共用的运行时：路径、环境、消息归一、一轮对话。

**这里刻意不 import fastapi。** 分成两层：
    runtime.py   这一层：数据放哪、key 怎么读、消息怎么拼、一轮怎么跑
    app.py       只负责 HTTP：路由、请求/响应模型、静态文件

两个入口都用同一套核心：
    python -m chat        服务（web UI 走 /api/chat）
    python -m chat.run    CLI（不经过网页）

所以"系统提示词怎么拼""工具开关影响什么""会话日志记什么"只有一份实现 ——
CLI 要是自己抄一遍，两边迟早不一致。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

from chat import create_client, get_model_temperature
from chat.agent import make_executor, run_agent_turn, session_store, workspace_store

# ---------------------------------------------------------------------------
# 运行时数据（会话日志 + 工作区登记表）在哪
# --------------------------------------------------------------------------
# 顺位：CHAT_STORAGE 环境变量 > 仓库根下的 storage/ > 包目录旁边（老行为兜底）。
#
# 为什么需要"往上找仓库根"这一档：默认值以前是"跟着代码走"的（BASE_DIR.parent/
# storage），代码一挪数据目录就跟着挪，表现成**历史对话凭空消失**（文件还在老地方），
# 而且不报错。服务端在 systemd unit 里用 CHAT_STORAGE 钉住了，但**CLI 在普通 shell
# 里跑，没有那个变量** —— 实测它会解析成 <包目录>/../storage，一个空目录：
# CLI 看不到网页里的对话，还在那儿另建一份 storage。
# 所以默认值改成从包目录往上找 .git（仓库根），找到就用 <仓库根>/storage。
def find_repo_root(start: Path) -> Path | None:
    """往上找含 .git 的那一层；找不到返回 None。"""
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _default_storage_dir() -> Path:
    package_dir = Path(__file__).resolve().parent
    repo_root = find_repo_root(package_dir)
    if repo_root is not None:
        return repo_root / "storage"
    return package_dir.parent / "storage"


_env_storage = os.environ.get("CHAT_STORAGE")
STORAGE_DIR = (
    Path(_env_storage).expanduser().resolve() if _env_storage else _default_storage_dir()
)
SESSION_DIR = STORAGE_DIR / "sessions"   # 会话日志就是状态本身
WORKSPACE_DIR = STORAGE_DIR              # 工作区登记表 workspaces.json 放这

# CLI 记住"上次那场对话"的小文件（网页端对应 localStorage 里的 id）。不进 git。
CLI_SESSION_FILE = STORAGE_DIR / "cli-session"

# 默认工作区的根 —— **也是将来沙箱的默认边界**。
#
# 工作区是**实体**（id / 名字 / 根路径），会话 header 里记的是它的 id 引用而不是路径
# 快照。这个常量只在**登记表还空着**时用来兜底登记一条，不再每次启动都往回加 ——
# 否则用户删掉的工作区一重启就复活。想钉死默认根就用 CHAT_WORKSPACE。
#
# 现在仍然**没有任何东西读它来限制访问** —— 四个工具只是在它的根里操作（相对路径
# 按它解析），绝对路径照样能走到任何地方。登记表的意义是让"允许 agent 活动的根"
# 这件事有落点：沙箱将来要判断的正是"目标路径在不在某个工作区的根下面"。
DEFAULT_WORKSPACE_ROOT = Path(os.environ.get("CHAT_WORKSPACE") or Path.cwd()).resolve()

# 默认供应商/模型。改这里就够 —— /api/providers 会把它们发给前端，CLI 也用它当默认值。
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL_NAME = "deepseek-flash"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. "
    "Keep context across turns and answer in the same language as the user when possible."
)

# 工具模式的系统提示词：告诉模型它可以调用工具、何时用哪个、有哪些行为规则。
# 注意：不贴 JSON schema——工具定义走 API 的 tools 参数，这里只给可读的规则，
# 避免与 chat/agent/tools.py 里的实现重复维护而漂移。
TOOL_SYSTEM_PROMPT = (
    "You are an agent that can take real actions through tools. "
    "Tools available: "
    "read_file — read any UTF-8 text file (page large files with offset/limit); "
    "write_file — create a new file or fully overwrite one, parent directories "
    "are created automatically (use ONLY for new files or complete rewrites); "
    "edit_file — replace exactly one text block in an existing file "
    "(old_text must be copied verbatim from read_file output, never invented); "
    "run_bash — execute a shell command (ls, grep, git, run programs). "
    "Rules: always read a file before editing or quoting it; never invent file "
    "contents; prefer run_bash for listing/searching/git; for multi-step or "
    "environment-sensitive work (conda activate, long scripts), do not chain "
    "fragile one-liners: write a run_task.sh with write_file, review it with "
    "read_file, then run it with 'bash run_task.sh'. When the task is done, "
    "reply concisely in the user's language and summarize what you read, wrote, "
    "edited, or ran."
)


def ensure_default_workspace() -> None:
    """登记表还空着时兜底登记一条默认工作区（第一次跑、或文件被清掉）。

    **不能无条件调**：那样"你删掉的工作区一重启就回来了" —— 登记表是用户的意图，
    服务不该替他往回加。
    """
    if not workspace_store.load(WORKSPACE_DIR):
        workspace_store.ensure(WORKSPACE_DIR, DEFAULT_WORKSPACE_ROOT)


def resolve_workspace(workspace_id: str | None) -> dict[str, Any]:
    """按 id 找登记过的工作区；给空或找不到就回落到默认那个。

    刻意**不报错**：一个指向已删工作区的 id，回落到默认比让请求失败更合理。
    """
    entry = workspace_store.by_id(WORKSPACE_DIR, workspace_id) if workspace_id else None
    if entry is not None:
        return entry
    fallback = workspace_store.default(WORKSPACE_DIR)
    if fallback is None:
        fallback = workspace_store.ensure(WORKSPACE_DIR, DEFAULT_WORKSPACE_ROOT)
    return fallback


def migrate_legacy_workspace_field() -> int:
    """把老会话 header 里的工作区**路径**换成工作区 id 引用。返回迁移条数。

    历史原因：会话 header 里曾经记的是工作区根路径的快照，后来改成记 id。不改的话
    那些老会话在"按工作区分组"的列表里会掉进"未分组"。幂等。
    """
    migrated = 0
    if not SESSION_DIR.is_dir():
        return 0
    for path in sorted(SESSION_DIR.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if not lines:
            continue
        try:
            header = json.loads(lines[0])
        except json.JSONDecodeError:
            continue
        if header.get("type") != "session" or header.get("workspaceId"):
            continue
        entry = workspace_store.by_root(WORKSPACE_DIR, header.get("workspace") or "")
        if entry is None:
            continue
        header["workspaceId"] = entry["id"]
        lines[0] = json.dumps(header, ensure_ascii=False)
        try:
            # 原子替换：写临时文件再改名，中途崩了不会留下半个日志
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            continue
        migrated += 1
    return migrated


# ---------------------------------------------------------------------------
# 环境变量：API key 从 ~/.bashrc 读进来
# --------------------------------------------------------------------------
def load_env_value_from_bashrc(key_name: str) -> None:
    """把 ~/.bashrc 里 `export KEY="..."` 的值填进 os.environ（已有值则不覆盖）。

    为什么要读**文件**而不是靠环境变量继承：服务由 systemd 启动，继承不到你终端的
    shell 环境（环境变量只在自己那棵进程树里往下传）。CLI 从终端启动时其实继承得到，
    但走同一个函数才能保证两个入口行为一致。
    """
    if key_name in os.environ and os.environ[key_name].strip():
        return

    bashrc_path = Path.home() / ".bashrc"
    if not bashrc_path.exists():
        return

    lines = bashrc_path.read_text(encoding="utf-8").splitlines()
    prefix = f'export {key_name}="'

    for line in lines:
        stripped_line = line.strip()
        if not stripped_line.startswith(prefix):
            continue

        value = stripped_line[len(prefix):]
        quote_index = value.find('"')
        if quote_index >= 0:
            value = value[:quote_index]

        value = value.strip()
        if value:
            os.environ[key_name] = value
        return


def ensure_runtime_env(provider: str) -> None:
    """把这个供应商的 API key 从 ~/.bashrc 读进 os.environ。

    **"哪个 provider 用哪个环境变量"的唯一来源是 `providers.yaml` 的 `api_key_env`。**

    这里以前是一串硬编码的 if（gpt → GPT_API_KEY，deepseek → …），等于同一件事在
    yaml 和代码里各写一份。两份必须保持一致，但不一致时**不会报错** —— 只会静默读不到
    key，最后以一个 401 收场。现在只剩 yaml 一份：加供应商、改变量名都只动那个文件。

    provider 不认识时**不在这里报错**，直接返回：这个函数的职责只是填环境变量，
    "不支持的供应商"该由 create_client 去说（错误信息与时机都保持原样）。
    """
    from chat import load_provider_catalog

    entry = load_provider_catalog().get(str(provider).strip().lower()) or {}
    key_name = entry.get("api_key_env")
    if key_name:
        load_env_value_from_bashrc(key_name)


# ---------------------------------------------------------------------------
# 消息
# --------------------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str = Field(min_length=1)
    content: str = ""                  # assistant(tool_calls) 消息可能无正文，允许空
    tool_calls: list | None = None     # 协议回放：assistant 声明的工具调用列表
    tool_call_id: str | None = None    # 协议回放：tool 结果消息配对用


def normalize_messages(
    messages: list[ChatMessage],
    system_prompt: str | None = None,
    tools_enabled: bool = False,
    ) -> list[dict]:
    # None 和 "" 在业务上是两回事：
    #   None（请求里根本没有 system_prompt 字段）→ 调用方没表态 → 后端代拼默认
    #   ""（传了空字符串）                        → 调用方明确表示不要 → 一条 system 都不发
    # 传了非空内容 → 原样发送（所见即所得）：面板写什么，模型就看到什么。
    # 工具模式同样不自动拼接，避免"UI 看不到却实际发送"的歧义；
    # 想要工具说明，把 TOOL_SYSTEM_PROMPT（/api/providers 返回 tool_system_prompt）粘进面板即可。
    normalized_messages: list[dict] = []

    if system_prompt is None:
        # 字段缺失（调用方没表态）→ 后端代拼默认：
        #   工具模式 = 工具说明 + 默认基础提示词；普通模式 = 默认基础提示词
        if tools_enabled:
            system_content = f"{TOOL_SYSTEM_PROMPT}\n\n{DEFAULT_SYSTEM_PROMPT}"
        else:
            system_content = DEFAULT_SYSTEM_PROMPT
        normalized_messages.append(
            {
                "role": "system",
                "content": system_content,
            }
        )
    elif system_prompt.strip():
        normalized_messages.append(
            {
                "role": "system",
                "content": system_prompt.strip(),
            }
        )
    # 剩下的情况（空字符串或纯空白）什么都不加：这一轮请求没有 system 消息

    for message in messages:
        if message.role == "system":
            continue
        # 未启用工具时，历史里可能残留上一轮的工具协议消息
        # （assistant 带 tool_calls / role=tool），此时请求不带 tools 参数，
        # 发给模型会被严格服务拒收 → 直接丢弃，只留纯文本对话。
        if not tools_enabled and (message.role == "tool" or message.tool_calls):
            continue
        entry: dict = {
            "role": message.role,
            "content": message.content,
        }
        if message.tool_calls:
            entry["tool_calls"] = message.tool_calls
        if message.tool_call_id:
            entry["tool_call_id"] = message.tool_call_id
        normalized_messages.append(entry)
    return normalized_messages


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def effective_system_prompt(normalized_messages: list[dict]) -> str | None:
    """归一化后真正发给模型的那条 system 消息；没有则 None。

    记这个而不是请求里的 system_prompt 原值：那个可能是 None（用默认提示词）或 ""
    （一条 system 都不发），两个都不等于模型实际看到的东西。
    """
    for message in normalized_messages:
        if message.get("role") == "system":
            return message.get("content")
    return None


# ---------------------------------------------------------------------------
# 一轮对话
# --------------------------------------------------------------------------
class TurnResult(NamedTuple):
    reply: str
    steps: list
    # 本轮新增的协议消息（assistant tool_calls + tool 结果 + 最终文本），
    # 落盘时存成日志里的 "protocol" 字段 —— 两个名字指同一件东西，
    # 存储键叫 protocol 是因为它描述的是"重发用的协议消息"，别改名（已有历史按它读）。
    protocol_messages: list
    messages: list       # 实际发给模型的消息（含拼好的 system）
    temperature: float   # 生效值：请求没给时来自 providers.yaml


@dataclass(frozen=True)
class TurnRequest:
    """一轮请求里与传输方式无关的部分。

    web 从 HTTP body 建它，CLI 从命令行参数建它 —— request_real_reply 只认这个，
    于是两个入口跑的是同一段逻辑。
    """

    messages: list[ChatMessage] = field(default_factory=list)
    provider: str = DEFAULT_PROVIDER
    model_name: str = DEFAULT_MODEL_NAME
    system_prompt: str | None = None
    tools_enabled: bool = False
    temperature: float | None = None


def request_real_reply(
    request: TurnRequest,
    prior_messages: list[ChatMessage],
    workspace_root: str | None = None,
    ) -> TurnResult:
    ensure_runtime_env(request.provider)
    temperature = request.temperature
    if temperature is None:
        temperature = get_model_temperature(
            provider=request.provider,
            model_name=request.model_name,
        )
    client = create_client(
        provider=request.provider,
        model_name=request.model_name,
        temperature=temperature,
    )
    # 历史（prior_messages）由会话日志重放，拼上本轮新增的消息，再走 normalize_messages。
    # 归一化只有这一条路径，不另写一份。
    combined = [*prior_messages, *request.messages]
    normalized_messages = normalize_messages(
        combined,
        request.system_prompt,
        tools_enabled=request.tools_enabled,
    )
    if request.tools_enabled:
        # agent 模式：多轮工具调用，直到模型直接回答。
        # 工具在**这场会话所属工作区的根**里干活 —— 相对路径按它解析、bash 在它里面跑。
        # 这就是"切工作区"的实际含义：不传 root 的话四个工具都按进程 cwd 走，
        # 界面上选哪个工作区都一样（那正是以前的 bug）。
        reply, steps, protocol_messages = run_agent_turn(
            client,
            normalized_messages,
            execute_tool=make_executor(workspace_root),
        )
    else:
        assistant_message, _ = client.request_assistant_message(
            messages=normalized_messages,
        )
        reply = str(assistant_message["content"])
        steps = []
        # protocol_messages 的不变式：本轮产生的协议消息，**总以最终 assistant 消息结尾**。
        # 非工具模式过去返回 []，后果是回放的历史里没有 assistant 轮 ——
        # 前端 protocol 只收 user 消息，模型记不住自己说过什么（会话日志同样缺）。
        protocol_messages = [{"role": "assistant", "content": reply}]

    return TurnResult(
        reply=reply,
        steps=steps,
        protocol_messages=protocol_messages,
        messages=normalized_messages,
        temperature=temperature,
    )
