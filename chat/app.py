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
DEFAULT_PROVIDER = "gpt"
DEFAULT_MODEL_NAME = "gpt-5.5"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. "
    "Keep context across turns and answer in the same language as the user when possible."
)

# 工具模式的系统提示词：告诉模型它可以调用工具、何时用哪个、有哪些行为规则。
# 注意：不贴 JSON schema——工具定义走 API 的 tools 参数，这里只给可读的规则，
# 避免与 llm_client/agent/tools.py 里的实现重复维护而漂移。
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

from llm_client import create_client, get_model_temperature, list_providers
from llm_client.agent import run_agent_turn, write_trace


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
    messages: list[ChatMessage]
    provider: str = DEFAULT_PROVIDER
    model_name: str = DEFAULT_MODEL_NAME
    temperature: float | None = None  # None → 使用 providers.yaml 里该模型的默认温度
    system_prompt: str | None = None   # 新增：None/空 → 用 DEFAULT_SYSTEM_PROMPT
    tools_enabled: bool = False        # 新增：True → 系统提示词介绍工具（配合后端工具调用）

class ChatResponse(BaseModel):
    reply: str
    steps: list[dict] = Field(default_factory=list)  # 工具执行流水账（agent 模式才有）
    trace: list[dict] = Field(default_factory=list)  # 本次产生的协议消息，前端存下来跨轮回放


app = FastAPI(title="chat-app")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 鉴权**不在这个进程里**做。
#
# 公网入口的密码由 cpolar 边缘负责（/usr/local/etc/cpolar/cpolar.yml 里的 auth 项），
# 局域网直连 8200 则完全不需要密码 —— 这正是想要的效果：公网要密码，局域网不要。
#
# 为什么不能在这里做：应用层分不清「公网来的」和「局域网来的」。cpolar 客户端
# 跑在本机、连的是 localhost:8200，局域网设备也连同一个端口，两者在应用眼里长得
# 一样；靠来源 IP 判断会被 X-Forwarded-For 缺失（退化成 127.0.0.1，看起来正好像
# 局域网）绕过，靠 Host 头判断则可以直接伪造。只有放在 cpolar 边缘才是结构性的：
# 公网请求必过它，局域网请求根本不经过它。
#
# 2026-09-11 这里曾挂过一个 BasicAuthMiddleware（chat/basic_auth.py，66 行），
# 现已删除 —— 它的活由 cpolar 接管了，留着就是挂着不执行的死代码。
# 真需要应用层密码时（比如换隧道方式、或要直接暴露端口），见 git 历史取回。


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


def record_trace(
    payload: ChatRequest,
    duration_ms: int,
    result: "TurnResult | None" = None,
    error: str | None = None,
    ) -> None:
    """把一次请求写进轨迹文件。

    刻意不 try/except：write_trace 自己就是 fail-soft 的（异常吞掉、只写 stderr），
    所以这里再包一层只会掩盖问题。轨迹写不进去也绝不该弄死一次对话。
    """
    write_trace(
        provider=payload.provider,
        model_name=payload.model_name,
        temperature=result.temperature if result else payload.temperature,
        tools_enabled=payload.tools_enabled,
        system_prompt=payload.system_prompt,
        messages=result.messages if result else [],
        steps=result.steps if result else [],
        reply=result.reply if result else "",
        duration_ms=duration_ms,
        error=error,
    )


def request_real_reply(
    payload: ChatRequest,
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
    normalized_messages = normalize_messages(
        payload.messages,
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
        reply, steps, trace = str(assistant_message["content"]), [], []

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


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    ) -> ChatResponse:
    started = time.monotonic()
    try:
        result = request_real_reply(payload)
    except Exception as exc:
        record_trace(payload, elapsed_ms(started), error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    record_trace(payload, elapsed_ms(started), result=result)
    return ChatResponse(reply=result.reply, steps=result.steps, trace=result.trace)

