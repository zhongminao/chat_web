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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from pydantic import BaseModel, Field

from chat import create_client, get_model_temperature
from chat.agent import make_executor, run_agent_turn, session_store, workspace_store
from chat.agent.cancel import REGISTRY, TurnCancelled
from chat.agent import observed as observed_store
from chat.agent.sandbox import DEFAULT_SANDBOX_MODE, normalize_mode

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

# 提示词模板不在这里，在 prompts.yaml（随包分发，跟 providers.yaml 同一套做法）。
# 模板是**数据**不是代码：改措辞不该要求改 Python、也不该让前端再抄一遍拼接规则。
PROMPTS_YAML = Path(__file__).resolve().parent / "prompts.yaml"

# 拼接规则只有这一处。工具说明在前、基础提示词在后，中间空一行。
PROMPT_SEPARATOR = "\n\n"


def load_prompt_templates() -> dict[str, str]:
    """读 prompts.yaml → {"base": ..., "tools": ...}。

    **每次调用都重读**（跟 load_provider_catalog 一样），所以改完提示词文件不用重启
    服务 —— 调提示词时这一点很关键。文件小，读一次的代价远小于一次模型调用。
    """
    with PROMPTS_YAML.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    templates = {
        "base": str(data.get("base") or "").strip(),
        "tools": str(data.get("tools") or "").strip(),
    }
    if not templates["base"]:
        raise ValueError("prompts.yaml: 缺少 'base'（基础提示词）")
    return templates


def default_system_prompt(tools_enabled: bool = False) -> str:
    """不开工具时的默认提示词；开了就把工具说明拼在前面。"""
    templates = load_prompt_templates()
    if tools_enabled and templates["tools"]:
        return f"{templates['tools']}{PROMPT_SEPARATOR}{templates['base']}"
    return templates["base"]


def resolve_system_prompt(system_prompt: str | None, tools_enabled: bool) -> str | None:
    """把请求里的 system_prompt 解析成**实际会发出去的那条消息**。

        None      → 用模板（开了工具就拼上工具说明）        → 返回字符串
        "" / 空白 → 调用方明确表示不要 system 消息           → 返回 None
        非空      → 原样（所见即所得：面板写什么，模型就看到什么）

    这是**纯函数**，而且只有这一处实现。它被两个地方调：
      - normalize_messages：真正拼请求的时候；
      - app.py / run.py：在 writer.begin() 之前解析一次，把结果记进 turn 记录。

    为什么要提前解析并落盘：那是"这一轮用的提示词"这个事实本身。以前只在 turn-end
    里记"实际生效的"，于是取消的轮次记不上（request_real_reply 抛异常了，调用方拿不到
    结果），而且刷新页面之后前端没法把面板恢复成这个会话在用的那份。
    """
    if system_prompt is None:
        return default_system_prompt(tools_enabled)
    if system_prompt.strip():
        return system_prompt.strip()
    return None



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
# 进程**启动那一刻**就存在的环境变量 = 服务管理器给的（systemd 的 Environment= /
# EnvironmentFile），那是显式配置，优先级高于 ~/.bashrc，永不覆盖。
#
# 快照必须在 import 时取：这个模块自己会往 os.environ 里写 key，等到调用时再看，
# 就分不清"systemd 给的"和"上一轮自己从 bashrc 读进来的"了。
_STARTUP_ENV = dict(os.environ)


def load_env_value_from_bashrc(key_name: str) -> None:
    """把 ~/.bashrc 里 `export KEY="..."` 的值**同步**进 os.environ。

    为什么要读**文件**而不是靠环境变量继承：服务由 systemd 启动，继承不到你终端的
    shell 环境（环境变量只在自己那棵进程树里往下传）。CLI 从终端启动时其实继承得到，
    但走同一个函数才能保证两个入口行为一致。

    **每次调用都重读，而不是"os.environ 里已经有值就跳过"**：bashrc 是这些 key 的
    唯一来源，你在 bashrc 里换了 key，服务下一轮就该用新的。以前这里是早退的，于是
    换 key 之后必须**手动重启服务**才生效 —— 重启前表现出来的是一个
    `401 Invalid token`，跟"key 本身写错了"长得一模一样，很难查到真正的原因在
    "进程内存里那份是旧的"。`_STARTUP_ENV` 里有的（systemd / EnvironmentFile 给的）
    不在此列：那是显式配置，仍然优先。
    """
    if _STARTUP_ENV.get(key_name, "").strip():
        return

    bashrc_path = Path.home() / ".bashrc"
    if not bashrc_path.exists():
        # 读不到**不等于**"没有这个 key"：此时把 os.environ 里那份清掉，
        # 只会把一个本来能用的服务弄坏。所以只做"读到就更新"，不做"读不到就删"。
        return

    lines = bashrc_path.read_text(encoding="utf-8").splitlines()
    prefix = f'export {key_name}="'

    for line in lines:
        stripped_line = line.strip()
        if not stripped_line.startswith(prefix):
            continue

        value = stripped_line[len(prefix):]
        # 行尾的注释（`... " #12`）在引号外，切到引号就自然丢掉；
        # 别再自己按 "#" 切 —— key 里出现 # 是合法的。
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
    # thinking 模式的思维链。**必须原样回传**：DeepSeek 在下一次请求里要求带上它，
    # 少了就整个请求 400（见 openai_client._reasoning_content_of）。
    reasoning_content: str | None = None


def normalize_messages(
    messages: list[ChatMessage],
    system_prompt: str | None = None,
    tools_enabled: bool = False,
    ) -> list[dict]:
    # None 和 "" 在业务上是两回事：
    #   None（请求里根本没有 system_prompt 字段）→ 调用方没表态 → 用模板
    #   ""（传了空字符串）                        → 调用方明确表示不要 → 一条 system 都不发
    # 传了非空内容 → 原样发送（所见即所得）：面板写什么，模型就看到什么。
    #
    # 解析规则只有一处实现：resolve_system_prompt()。这里不再自己拼 —— 前端也不拼。
    normalized_messages: list[dict] = []

    system_content = resolve_system_prompt(system_prompt, tools_enabled)
    if system_content is not None:
        normalized_messages.append({"role": "system", "content": system_content})

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
        # 思维链跟着 assistant 一起回传（别的供应商不会给这个字段，所以只有拿到过才有）。
        if message.reasoning_content:
            entry["reasoning_content"] = message.reasoning_content
        normalized_messages.append(entry)
    return normalized_messages


def with_system_note(messages: list[dict], note: str) -> list[dict]:
    """把一条系统级事件说明并进**开头那条 system 消息**（没有就在最前面补一条）。

    为什么不能在末尾再 append 一条 system —— 那是原来的写法，也正是这个 bug 的根源：
    消息列表里出现第二条 system，严格的 chat 模板直接拒收。vLLM 上的 Qwen 回的是

        400 System message must be at the beginning.

    云端 API（DeepSeek）宽容，所以这个 bug **只在本地模型上暴露**，而且表现极具
    误导性：命令其实执行了、结果也落盘了，但恢复请求被拒 → 审批接口 500 →
    模型永远接不上话；用户再点一次，又生成一条新的待审批 —— 看起来就是"明明命令
    是对的，却一直要审批"。

    normalize_messages 那边本来就是"只有一条 system、且在最前面"（它会把历史里
    所有 system 丢掉，只留自己拼的那条）。这里跟着同一条不变式走，而不是另立一套。

    **动这段代码前请注意**：任何"在 normalize_messages 之后往消息列表里再塞一条
    非 user/assistant/tool 角色的消息"的写法都会重蹈覆辙。想复核就在恢复路径上
    让模型真调一次，断言实际发出的 messages 里位置 > 0 没有 system —— 一条 assert
    就够。（这条不变式原来有一个专门的校验脚本，后来按"少放文件"的要求删了；
    复核方法留在这里，免得下次再踩。）
    """
    if messages and messages[0].get("role") == "system":
        head = dict(messages[0])
        head["content"] = f"{head['content']}\n\n{note}" if head["content"] else note
        return [head, *messages[1:]]
    # 没有 system（调用方明确传了 ""：提示词一条都不发）时补一条，仍然在位置 0。
    # 事件说明不是提示词 —— 它得让模型看见，所以那种配置下也会多出这一条。
    return [{"role": "system", "content": note}, *messages]


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


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
    sandbox_mode: str = DEFAULT_SANDBOX_MODE
    temperature: float | None = None


# ---------------------------------------------------------------------------
# 沙箱策略：此刻是什么，以及怎么让模型知道
# ---------------------------------------------------------------------------
#
# 这一段的形状全部对着 DSH 抄（packages/sandbox/sandbox-policy 与
# packages/core/agent-loop/src/runtime-context.ts），因为那边已经把这件事想清楚了：
#
#   1. **真相在日志里**：开关是一次事件（session_store 的 `sandbox` 记录），当前值 =
#      按位置折出来的最后一条。`turn` 记录里那份降级为历史，任何决策都不许只读它。
#   2. **执行侧在每次操作边界折一遍**：模式随时可改（包括一轮中途），冻结在闭包里的
#      字符串必然过期。
#   3. **模型只在"权限变了"的时候被告知一句**（事件式的通知），**不是**常驻一句"现在是
#      什么"。为什么这样更干净：模型不需要靠提示词知道自己的权限 —— 它靠工具返回学
#      （bash 的审批请求自带"没执行、别重试"；文件围栏拒绝时自带原因和路径）。而一句
#      常驻的状态必须**每个请求重新注入**（历史是重建的，上次那条不在里面），于是每轮
#      白付一次 token，且它随时可能过期 —— 正是早先那个 bug 的形状。
#
# DSH 在这一点上不同：它**每次请求都渲染**当前策略（renderPolicyContext / ASK_SENTENCE），
# 但 `project()` 只在值变化时才产生那条消息，而且把消息**存进会话历史**（surface event），
# 所以下次请求靠重放就带着它、不必重发。它不重复付 token，靠的是"存了"。
#
# chat 这里的选择是：不存，也不常驻；只在变化的那一刻说一句。那一句是
# `(上一轮记录的模式, 折出来的此刻值)` 的纯函数 —— 两个输入都已经在日志里，所以
# **文本不必落盘**（这也正是"派生内容不存第二遍"那条规矩要我们做的）。
#
# 想从一份请求的 messages 里认出它，用 is_runtime_notice()。
RUNTIME_NOTICE_PREFIX = "The sandbox policy changed "


def session_sandbox_mode(session_id: str, fallback: str | None = None) -> str:
    """这场会话**此刻**的沙箱模式。

    日志里折出来的值优先 —— 因为模式随时可改，人可以改在两轮之间，也可以改在一轮
    **中途**（审批面板正横在那儿的时候）。turn 记录里那个值只是"这一轮开始时是什么"，
    拿它当"现在"正是以前那个死循环的形状：workspace-write 下每次 run_bash 都要一次审批
    → 人改成 full-access → 恢复的循环读旧快照 → 又要一次审批，改多少次都没用。

    折不出值时才用 fallback（新会话 / 更早的日志里没记过 / 没说过话）：
    web 是这一轮 payload 带上的草稿，CLI 是 --sandbox 参数。
    """
    return normalize_mode(
        session_store.sandbox_mode_of(SESSION_DIR, session_id)
        or fallback
        or DEFAULT_SANDBOX_MODE)


def policy_change_note(previous: str, current: str) -> str:
    """把"权限刚被改了"写成一句事件说明（照 DSH 的措辞：changed from X to Y）。

    只讲**发生了什么**：不说"现在是什么"，更不说"接下来一定会怎样"。前者需要每个请求
    重新注入（历史重建后上次那条不在里面 → 每轮白付 token），后者在模式再变时变成假话。
    一句"从 X 改成 Y（人改的）"是**恒真的历史事件**，说过一次就够，重放时也不会过期。

    不教它怎么绕过策略：被拦下时工具自己会给原因和路径，照工具返回的走就行。
    """
    return (f'{RUNTIME_NOTICE_PREFIX}from "{normalize_mode(previous)}" '
            f'to "{normalize_mode(current)}" (changed by the user).')


def is_runtime_notice(message: dict) -> bool:
    """这条消息是不是我们**注入**的运行时说明（不是用户打的字）。

    存在的理由很实际：这种通知进的是发给模型的消息列表，而它**没有**任何额外字段 ——
    加字段（比如 source / meta）会被严格的服务端拒收，所以只能在文本层面认它。于是把
    "怎么认"收成一处：任何要统计/过滤/压缩消息的地方都调这个，别各自 startsWith 一遍。

    （DSH 那边不需要这个函数：它的注入消息带 `source: {kind:'plugin', plugin, form}`，
    别的包按 plugin 名过滤，见 packages/core/agent-loop/src/runtime-context.ts。
    chat 的消息模型里没有 source 这个位置，索引不进 API 请求，所以只能这样。）
    """
    return (str(message.get("role") or "") == "user"
            and str(message.get("content") or "").startswith(RUNTIME_NOTICE_PREFIX))


def project_policy_change(history: list[dict], current: str, baseline: str,
                          applied: list) -> list[dict]:
    """模式变了才往消息末尾追加一句通知；没变就什么都不做（一个 token 都不花）。

    比较基准是**上一次告诉过模型的值**（applied[0]，还没有过就用 baseline）：
      - `baseline` = 这次请求开始时"模型以为的"模式。web 的新一轮取**上一轮记录的模式**
        （所以"两轮之间改了开关"能在下一轮第一步被说出来），恢复取本轮记录的模式；
      - 说过一次之后 `applied[0]` 就是新值，所以同一句话不会每步重复塞（前缀缓存也保住了）。

    为什么追加在末尾、而不是改写开头那条 system：系统提示词是稳定前缀，每步重写它会把
    模型侧的前缀缓存整段打掉；而且 system_prompt="" 的会话也照样能被通知到。

    **不落盘**：它是 `(baseline, 折出来的值)` 的纯函数，两个输入都已经在日志里
    （`turn.sandboxMode` + `sandbox` 事件），存下来就是同一份信息存两遍。
    它也**不进模型历史**（`load_history` 只认 user/assistant/tool）—— 这条通知只属于
    "发出它的那一次请求"，重放不该把它当成历史里的一句话。
    """
    announced = applied[0]
    if current == (announced if announced is not None else baseline):
        return history
    applied[0] = current
    return [*history, {"role": "user", "content": policy_change_note(announced or baseline, current)}]


def request_real_reply(
    request: TurnRequest,
    prior_messages: list[ChatMessage],
    workspace_root: str | None = None,
    *,
    writer: Any = None,
    should_stop: Any = None,
    sandbox_mode: "str | Callable[[], str] | None" = None,
    sandbox_baseline: str | None = None,
    observed_context_id: str,
    ) -> TurnResult:
    """observed_context_id: 这一轮的观测状态属于哪个上下文（**必填**）。

    sandbox_mode: 执行时用的模式，**字符串或零参回调**；None → 用 request 里那个值。
    web 传回调（每次工具调用现折一遍会话日志：人拨了开关之后，**下一条工具调用**就按
    新的走 —— 不必等下一轮、也不必等某次审批被点）；CLI 不传（它的模式是一次运行的
    参数，本来就不变）。
    sandbox_baseline: "模型以为现在是哪个模式" —— 变了才据此给一句通知（见
    project_policy_change）。web 的新一轮取**上一轮记录的模式**；不传 = 与当前相同 =
    一句都不说（CLI 就是这种：没有界面可以拨开关）。

    没有默认值，也不退回任何共享表 —— 那正是以前"先读后改"跨会话失效的原因：
    A 场读过的文件，B 场能直接改（实测过）。调用方必须表态：
        web / CLI   f"session:{会话id}"
        评估        f"eval:{case}-{时间戳}-{第几次}"
        子 agent    f"session:{会话id}:agent:{子agent id}"
    """
    ensure_runtime_env(request.provider)
    # 每轮都取一次（取的动作会 touch），所以 TTL 的实际语义是"这场会话闲置了多久"，
    # 而不是"多久没用过工具"。这样"长期关掉的会话"会被扫掉，正在聊的不会 ——
    # 注意 TTL 只回收内存，它不做陈旧判断（那件事由 guard 里的 version 比较回答）。
    observed_context = observed_store.REGISTRY.context(observed_context_id)
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
    # 模式**每次操作边界现算**（见上面的三段说明）。字符串进来就包成常量回调 —— 后面
    # 只认一种形状，不必在两处各写一遍分支。
    mode_source: Any = sandbox_mode if sandbox_mode is not None else request.sandbox_mode
    current_mode: Callable[[], str] = (
        mode_source if callable(mode_source) else (lambda: normalize_mode(mode_source)))
    if request.tools_enabled:
        if writer is None:
            raise ValueError("工具模式必须给 writer：循环要边跑边落盘")
        # 已告知的状态：[上一次告诉过模型的模式]。None = 这次请求还没说过。
        announced: list = [None]
        baseline = normalize_mode(sandbox_baseline if sandbox_baseline is not None
                                 else current_mode())
        reply, steps, protocol_messages = run_agent_turn(
            client,
            normalized_messages,
            writer=writer,
            # root、observed、should_stop 都是**宿主注入**的：基准目录不能让模型改，
            # 观测状态属于哪个上下文也不能让模型挑（挑一个"已读过"的就能绕过守卫），
            # 取消权同样。should_stop 一路传到 run_bash —— 没有它，用户点了停止之后
            # 一条正在跑的 sleep 只能等它自己结束。
            # sandbox_mode 同理（模型塞进参数里会被 _INJECTED_ARGS 过滤掉）：而且是回调，
            # 每一步、每条命令都重折一次日志。
            execute_tool=make_executor(
                workspace_root, observed_context, should_stop=should_stop,
                sandbox_mode=current_mode, audit_root=STORAGE_DIR,
                audit_session_id=observed_context_id.removeprefix("session:")),
            # 取消令牌的轮询函数一路传到循环里。web 从 TurnRegistry 拿，CLI 不传
            # （它在同一进程里前台跑，SIGINT 直接打断阻塞调用，不需要协作式检查）。
            should_stop=should_stop,
            # 每步检查"权限被改了没"：两步之间被拨走，下一步就说一句（值没变则一句不说）。
            refresh_messages=lambda history: project_policy_change(
                history, current_mode(), baseline, announced),
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
        # **落盘由这里负责**（有 writer 的话）。工具模式是 loop.py 边跑边记，
        # 这条路没有循环，只有这一条消息。
        #
        # 漏了这一行的后果实测过：纯聊天会话的日志里只有 user + turn-end，没有
        # assistant —— 重开这场对话时模型记不住自己说过什么，而且前端"以日志为准
        # 重新渲染"会把刚拿到的回复弄丢（界面上只看到自己的提问）。
        if writer is not None:
            writer.record(protocol_messages[0])

    return TurnResult(
        reply=reply,
        steps=steps,
        protocol_messages=protocol_messages,
        messages=normalized_messages,
        temperature=temperature,
    )


def _turn_elapsed_ms(session_id: str) -> int:
    """从这一轮**开始**算到现在的毫秒数（暂停把一轮劈成几段，不能只算最后一段）。"""
    meta, _ = session_store.last_turn_meta(SESSION_DIR, session_id)
    started = meta.get("time")
    if not isinstance(started, int):
        return 0
    return max(0, int(time.time() * 1000) - started)


def close_turn(session_id: str, *, temperature: float | None = None,
               error: str | None = None) -> None:
    """这一轮**真的**结束了：写 turn-end、注销取消令牌。

    为什么单独有这么一件事：以前"暂停等审批"那一刻就把 turn-end 写了，于是日志说这一轮
    结束了、它后面却还在长；`/interrupt` 和界面上的 `running` 也一起失效（令牌在暂停时被
    注销了）。现在暂停**不收尾**，收尾只发生在两个时刻：模型真的说完（正常运行/恢复跑到
    出口），或者人明确放弃（/interrupt）。
    """
    workspace_id = session_store.load_workspace_id(SESSION_DIR, session_id)
    writer = session_store.TurnWriter(SESSION_DIR, session_id, workspace_id=workspace_id)
    writer.finish(durationMs=_turn_elapsed_ms(session_id), temperature=temperature, error=error)
    token = REGISTRY.adopt(session_id)
    if token is not None:
        REGISTRY.end(session_id, token)


def abandon_paused_turn(session_id: str, reason: str) -> bool:
    """放弃一个**停在审批上**的轮次：把没答复的请求记成 cancelled，然后收尾。

    这就是"停止"在暂停期间的含义（以前那一刻 `/interrupt` 恒返回 false，因为令牌已经被
    注销、没有任何循环在跑）。记 cancelled 是为了不变式②：每个声明过的 tool_call 都得有
    一条配对的 tool 结果，否则重放时那条请求会永远悬着。
    """
    if not session_store.turn_is_paused(SESSION_DIR, session_id):
        return False
    for request_id in session_store.pending_bash_requests(SESSION_DIR, session_id):
        session_store.append_bash_result(
            SESSION_DIR, session_id, request_id, status="cancelled",
            content="[bash cancelled] 用户停止了这一轮：这条命令没有执行")
    close_turn(session_id, error=f"cancelled: {reason}")
    return True


def resume_after_approval(session_id: str, *, approval: dict[str, Any] | None = None) -> str:
    """审批 bash 后恢复 loop：用执行结果替换 pending request，继续跑到最终文本。

    approve/reject 接口先把结果写进 session（type=bash-result），然后调这里。
    load_history 会把那条 pending 的 bash request 换成真实结果，于是模型看到完整
    工具流程后继续往下说，而不是停在"等待批准"。

    approval 是**事件通知**：把"用户批准/拒绝了这条命令"作为一条 system 消息注入本次
    请求（不落盘）。系统提示词里因此不需要讲权限模式 —— 模型读事件，不必推断。

    不写新 turn：这是**同一轮**的延续，writer 只 record、不 begin，轮数不变。
    """
    meta, temperature = session_store.last_turn_meta(SESSION_DIR, session_id)
    provider = str(meta.get("provider") or DEFAULT_PROVIDER)
    model_name = str(meta.get("model") or DEFAULT_MODEL_NAME)
    tools_enabled = bool(meta.get("toolsEnabled"))
    # 模式**现折日志**，不从这一轮的 turn 记录推 —— 暂停期间人可能已经把开关拨走了，
    # 而 turn 里那个值是"这一轮开始时"的快照。以前就卡在这里：批准一次 → 恢复 → 读到旧
    # 快照 → 下一条 run_bash 又要一次审批，改多少次模式都没用。
    recorded_mode = normalize_mode(str(meta.get("sandboxMode") or DEFAULT_SANDBOX_MODE))
    current_mode: Callable[[], str] = lambda: session_sandbox_mode(session_id, recorded_mode)
    system_prompt = meta.get("systemPrompt")

    if not tools_enabled:
        return ""

    # **这一轮还停着吗**：模型已经给过最终答复的话，就不要再去"恢复"它 —— 那样发出的
    # 请求会以 assistant 结尾（没有新的 user 消息），DeepSeek 的 thinking 模式直接 400，
    # 结果是"命令跑了、结果也记了，但接口 500、界面卡在一条批不动的待审批上"。
    # 一批里两条 bash 请求时就会走到这里：批准第一条之后模型已经答完了。
    if not session_store.turn_is_paused(SESSION_DIR, session_id):
        return ""

    # **批里还有没答复的请求时也不能恢复**：模型一旦被叫起来，就可能在这一批还没批完的
    # 时候收尾抽身（实测：它写完"回合在此暂停"就走了，审批窗口还开着、那一轮却结束了）。
    # 等这一批都答完，再把所有结果一次性交给模型。命令本身不受影响 —— 批准哪条哪条就跑。
    if session_store.pending_bash_requests(SESSION_DIR, session_id):
        return ""

    if temperature is None:
        temperature = get_model_temperature(provider, model_name)

    workspace_id = session_store.load_workspace_id(SESSION_DIR, session_id)
    workspace_root = resolve_workspace(workspace_id).get("root")

    ensure_runtime_env(provider)
    observed_context = observed_store.REGISTRY.context(f"session:{session_id}")
    client = create_client(provider=provider, model_name=model_name, temperature=temperature)

    prior = session_store.load_history(SESSION_DIR, session_id)
    prior_messages = [ChatMessage(**record) for record in prior]
    normalized_messages = normalize_messages(prior_messages, system_prompt, tools_enabled=True)

    # 审批事件通知：并进开头那条 system（**不是**在末尾另加一条 —— 那会破坏
    # "只有一条 system、且在位置 0"，严格的模板会 400，见 with_system_note）。
    #
    # 措辞只讲**发生了什么**：批准了 → 它跑了、输出就是上面那条工具结果；拒绝了 → 没跑。
    # 不许出现"会话没有任何变化"这类断言 —— 那是一句**冻结的承诺**，人只要在这期间拨了
    # 沙箱开关它就成了假话，而模型会照着假话继续推理（以前就是这么写的）。
    notes: list[str] = []
    if approval is not None:
        status = str(approval.get("status") or "")
        command = str(approval.get("command") or "")
        if status == "executed":
            notes.append(
                f"The user approved the pending bash command, and it has now run:\n{command}\n"
                "Its output is the most recent tool result above. The approval covered that "
                "single command only."
            )
        else:
            notes.append(
                f"The user rejected the pending bash command:\n{command}\n"
                "It did not run. Do not retry it unless the user asks."
            )
    # 权限变了**不当成系统消息说**：它由 project_policy_change 在步边界追加一句（与新一轮
    # 完全同一条路），判据是"这一轮记录的模式 vs 折出来的此刻值"。系统里只留"发生了什么"
    # 那类事件（批准/拒绝），因为那个必须待在开头那条 system 里（见 with_system_note）。
    for note in notes:
        normalized_messages = with_system_note(normalized_messages, note)

    # **接着用暂停时那枚令牌**：它一直留在 REGISTRY 里（暂停不收尾），所以
    #   - 恢复期间 /interrupt 能真的把它停下来；
    #   - 界面上的 running 在整段（暂停+恢复）里都是 true；
    #   - 每会话互斥覆盖整段，不会有两个循环同时写这份日志。
    token = REGISTRY.adopt(session_id)
    if token is not None and token.should_stop():
        # 人在暂停期间已经点了停止：别跑了，把这一轮按"放弃"收掉。
        abandon_paused_turn(session_id, str(token.should_stop()))
        return ""

    # 继续写同一轮：不 begin（否则会多算一轮 turn）。
    writer = session_store.TurnWriter(SESSION_DIR, session_id, workspace_id=workspace_id)
    announced: list = [None]   # [上一次告诉过模型的模式]，见 project_policy_change
    try:
        if token is not None:
            token.attach()
        reply, _steps, _protocol = run_agent_turn(
            client,
            normalized_messages,
            writer=writer,
            execute_tool=make_executor(
                workspace_root, observed_context,
                should_stop=token.should_stop if token is not None else None,
                sandbox_mode=current_mode, audit_root=STORAGE_DIR,
                audit_session_id=session_id),
            should_stop=token.should_stop if token is not None else None,
            # 恢复期间人还可能再拨开关（面板正开着）—— 同样每步检查、变了就说一句。
            refresh_messages=lambda history: project_policy_change(
                history, current_mode(), recorded_mode, announced),
        )
    except TurnCancelled as exc:
        # 恢复途中被停止：loop 的 finally 已经给没结果的调用补了合成结果，这里收尾。
        close_turn(session_id, temperature=temperature, error=f"cancelled: {exc}")
        return ""
    finally:
        if token is not None:
            token.detach()

    # 跑完了：如果**又**停在新的审批请求上，这一轮仍不收尾（等下一批审批）。
    if session_store.turn_is_paused(SESSION_DIR, session_id):
        return reply
    close_turn(session_id, temperature=temperature)
    return reply
