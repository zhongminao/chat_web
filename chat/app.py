import os
import time
from pathlib import Path
from typing import NamedTuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
# 运行时数据放在仓库内的 storage/ 下（不进 git，见 .gitignore）—— 放在手边才找得到。
# 位置由这里决定而不是 chat_agent：那个包是独立可安装的，不该知道仓库布局。
STORAGE_DIR = BASE_DIR.parent / "storage"
SESSION_DIR = STORAGE_DIR / "sessions"   # 会话日志就是状态本身

# 会话绑定的工作目录 —— **也是将来沙箱的边界**。
#
# 现在还没有任何东西读它来限制访问（run_bash 仍是 shell=True、能走到任何地方），
# 它只被记进会话日志。留着它是为了让"限制"将来有个明确的落点：沙箱需要的配置
# 就是这一个值，允许 agent 活动的根。
#
# 单值、可用环境变量覆盖。默认取进程的工作目录 —— systemd unit 的
# WorkingDirectory 正是仓库根，所以现在等价于"在这份代码里干活"。
WORKSPACE_ROOT = Path(os.environ.get("CHAT_WORKSPACE") or Path.cwd()).resolve()
DEFAULT_PROVIDER = "gpt"
DEFAULT_MODEL_NAME = "gpt-5.5"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. "
    "Keep context across turns and answer in the same language as the user when possible."
)

# 工具模式的系统提示词：告诉模型它可以调用工具、何时用哪个、有哪些行为规则。
# 注意：不贴 JSON schema——工具定义走 API 的 tools 参数，这里只给可读的规则，
# 避免与 chat_agent/agent/tools.py 里的实现重复维护而漂移。
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

from chat_agent import create_client, get_model_temperature, list_providers
from chat_agent.agent import run_agent_turn, session_store


class TurnResult(NamedTuple):
    reply: str
    steps: list
    trace: list
    messages: list       # 实际发给模型的消息（含拼好的 system），轨迹要记它
    temperature: float   # 生效值：payload 没给时来自 providers.yaml


class ChatMessage(BaseModel):
    role: str = Field(min_length=1)
    content: str = ""                  # assistant(tool_calls) 消息可能无正文，允许空
    tool_calls: list | None = None     # 协议回放：assistant 声明的工具调用列表
    tool_call_id: str | None = None    # 协议回放：tool 结果消息配对用


class ChatRequest(BaseModel):
    # sessionId 必填：历史由服务端按会话日志重放，前端只发**本轮新增**的消息。
    # 没有"无状态模式"这一说 —— 前端不再持有历史，也就没有全量重发这回事。
    sessionId: str
    messages: list[ChatMessage] = Field(default_factory=list)
    provider: str = DEFAULT_PROVIDER
    model_name: str = DEFAULT_MODEL_NAME
    temperature: float | None = None  # None → 使用 providers.yaml 里该模型的默认温度
    system_prompt: str | None = None   # None → 用 DEFAULT_SYSTEM_PROMPT；"" → 一条 system 都不发
    tools_enabled: bool = False        # True → 附带工具并允许模型调用

class ChatResponse(BaseModel):
    reply: str
    steps: list[dict] = Field(default_factory=list)  # 工具执行流水账（agent 模式才有）


app = FastAPI(title="chat-app")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 鉴权**不在这个进程里**做。
#
# 现状：chat 只在局域网可达（8200 直连）。**公网入口已撤掉** —— cpolar 的启动列表里
# 不再有 chat8200，且 cpolar 本身是 disabled。要对外演示（给 hr 看）时，把 chat8200
# 加回 cpolar.service 的 ExecStart，密码由那一层的 auth 负责。
#
# 为什么密码不放在应用层：应用层分不清「公网来的」和「局域网来的」。cpolar 客户端
# 跑在本机、连的是 localhost:8200，局域网设备也连同一个端口，两者在应用眼里长得
# 一样；靠来源 IP 判断会被 X-Forwarded-For 缺失（退化成 127.0.0.1，看起来正好像
# 局域网）绕过，靠 Host 头判断则可以直接伪造。只有放在 cpolar 边缘才是结构性的：
# 公网请求必过它，局域网请求根本不经过它。
#
# 2026-09-11 这里曾挂过一个 BasicAuthMiddleware（chat/basic_auth.py，66 行），
# 现已删除 —— 它的活由 cpolar 接管了，留着就是挂着不执行的死代码。
#
# 因此当前**没有密码**：安全性完全建立在"只有局域网连得上"之上。换通道（如 ssh
# 端口转发 + 密钥）时，鉴权责任随之转移到那条通道；若直接把 8200 暴露出去，
# 就必须把应用层鉴权加回来。


def load_env_value_from_bashrc(
    key_name: str,
    ) -> None:
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


def ensure_runtime_env(
    provider: str,
    ) -> None:
    normalized_provider = provider.strip().lower()

    if normalized_provider == "gpt":
        load_env_value_from_bashrc("GPT_API_KEY")
        return

    if normalized_provider == "deepseek":
        load_env_value_from_bashrc("DEEPSEEK_API_KEY")
        return

    if normalized_provider == "local_qwen":
        load_env_value_from_bashrc("LOCAL_QWEN_API_KEY")


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


def elapsed_ms(
    started: float,
    ) -> int:
    return int((time.monotonic() - started) * 1000)


def effective_system_prompt(
    normalized_messages: list[dict],
    ) -> str | None:
    """归一化后真正发给模型的那条 system 消息；没有则 None。

    记这个而不是请求里的 system_prompt 原值：那个可能是 None（用默认提示词）或 ""
    （一条 system 都不发），两个都不等于模型实际看到的东西。
    """
    for message in normalized_messages:
        if message.get("role") == "system":
            return message.get("content")
    return None


def request_real_reply(
    payload: ChatRequest,
    prior_messages: list[ChatMessage],
    ) -> "TurnResult":
    ensure_runtime_env(payload.provider)
    temperature = payload.temperature
    if temperature is None:
        temperature = get_model_temperature(
            provider=payload.provider,
            model_name=payload.model_name,
        )
    client = create_client(
        provider=payload.provider,
        model_name=payload.model_name,
        temperature=temperature,
    )
    # 历史（prior_messages）由会话日志重放，拼上本轮新增的消息，再走 normalize_messages。
    # 归一化只有这一条路径，不另写一份。
    combined = [*prior_messages, *payload.messages]
    normalized_messages = normalize_messages(
        combined,
        payload.system_prompt,
        tools_enabled=payload.tools_enabled,
    )
    if payload.tools_enabled:
        # agent 模式：多轮工具调用，直到模型直接回答
        reply, steps, trace = run_agent_turn(client, normalized_messages)
    else:
        assistant_message, _ = client.request_assistant_message(
            messages=normalized_messages,
        )
        reply = str(assistant_message["content"])
        steps = []
        # trace 的不变式：本轮产生的协议消息，**总以最终 assistant 消息结尾**。
        # 非工具模式过去返回 []，后果是回放的历史里没有 assistant 轮 ——
        # 前端 protocol 只收 user 消息，模型记不住自己说过什么（会话日志同样缺）。
        trace = [{"role": "assistant", "content": reply}]

    return TurnResult(
        reply=reply,
        steps=steps,
        trace=trace,
        messages=normalized_messages,
        temperature=temperature,
    )


@app.get("/")
def index(
    ) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health(
    ) -> dict[str, str]:
    return {
        "status": "ok",
        "mode": "real-gpt",
        "provider": DEFAULT_PROVIDER,
        "model_name": DEFAULT_MODEL_NAME,
    }


@app.get("/api/providers")
def providers(
    ) -> dict:
    """供应商与模型目录（来自 LLM/util/providers.yaml），供前端模型选择器使用。"""
    return {
        "providers": list_providers(),
        "default_provider": DEFAULT_PROVIDER,
        "default_model": DEFAULT_MODEL_NAME,
        "default_system_prompt": DEFAULT_SYSTEM_PROMPT,
        "tool_system_prompt": TOOL_SYSTEM_PROMPT,  # 工具说明文本：想启用工具时粘进系统提示词面板
    }


def resolve_session_id(
    payload: ChatRequest,
    ) -> str:
    """校验 sessionId，不合法 → 400，不做"尽力清洗"。

    它会被当文件名用，而服务在局域网可达 —— 放行 "../../x" 就是一次路径穿越。
    """
    session_id = session_store.sanitize_id(payload.sessionId)
    if session_id is None:
        raise HTTPException(status_code=400, detail="invalid sessionId")
    return session_id


@app.post("/api/sessions")
def create_session(
    ) -> dict:
    return {"id": session_store.new_id()}


@app.get("/api/sessions")
def list_sessions(
    ) -> dict:
    """侧栏用的会话列表。

    workspace 放顶层而不是每行重复一遍：当前服务只有一个工作区（进程的
    WorkingDirectory）。行级那个 workspace 字段是给将来"多工作区、按区分组"
    留的，现在每行都一样。
    """
    return {
        "workspace": str(WORKSPACE_ROOT),
        "sessions": session_store.list_sessions(SESSION_DIR),
    }


@app.get("/api/sessions/{session_id}")
def get_session(
    session_id: str,
    ) -> dict:
    """取一场会话，供前端渲染。

    返回的 items 已是**渲染顺序**（user / step / assistant 交替），前端照着 map
    一遍即可 —— 分别给 messages 和 steps 两张平铺表会让工具步骤错位。
    """
    safe_id = session_store.sanitize_id(session_id)
    if safe_id is None or not session_store.exists(SESSION_DIR, safe_id):
        raise HTTPException(status_code=404, detail="session not found")

    return {"id": safe_id, "workspace": session_store.load_workspace(SESSION_DIR, safe_id),
            "items": session_store.load_items(SESSION_DIR, safe_id)}


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    ) -> ChatResponse:
    started = time.monotonic()
    session_id = resolve_session_id(payload)
    # 历史从会话日志重放 —— 客户端只发本轮新增，服务端不信任它带的历史。
    prior_messages = [
        ChatMessage(**record) for record in session_store.load_history(SESSION_DIR, session_id)
    ]

    try:
        result = request_real_reply(payload, prior_messages)
    except Exception as exc:
        # 失败时**不往历史里写 user 消息**：那条提问没有对应的回答，留在历史里
        # 会让重试把同一句话追加第二遍。失败只记在 turn 记录里（turn 不是历史类型，
        # 重放会跳过），内容放 attempted 字段备查。
        session_store.append_turn(
            SESSION_DIR, session_id,
            user_messages=[], protocol=[],
            workspace=str(WORKSPACE_ROOT),
            meta={
                "error": str(exc),
                "attempted": [message.content for message in payload.messages],
            },
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # 只写会话日志，不另写轨迹 —— 日志本身就是记录，写两份等于同一份信息存两遍
    # （steps 也能从协议消息折出来）。
    session_store.append_turn(
        SESSION_DIR, session_id,
        user_messages=[message.model_dump() for message in payload.messages],
        protocol=result.trace,
        workspace=str(WORKSPACE_ROOT),
        meta={
            "provider": payload.provider,
            "model": payload.model_name,
            "temperature": result.temperature,
            "toolsEnabled": payload.tools_enabled,
            # 记**实际生效**的 system 消息（归一化后拼进去的那条），不是请求里那个
            # 可能为 None 的原值 —— 否则事后没法还原模型到底看到了什么。
            "systemPrompt": effective_system_prompt(result.messages),
            "durationMs": elapsed_ms(started),
        },
    )

    return ChatResponse(reply=result.reply, steps=result.steps)

