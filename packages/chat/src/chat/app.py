import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


# 路径 / 环境 / 消息归一 / 一轮对话 —— 全在 runtime.py，CLI 用的是同一份实现。
# 这个文件只留 HTTP 那一层：请求响应模型 + 路由 + 静态文件。
# 路由直接用这两个 store（runtime 里也用，但它不 re-export —— 直接用比再包一层清楚）
from chat import list_providers
from chat.agent import session_store, workspace_store
from chat.runtime import (
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_WORKSPACE_ROOT,
    SESSION_DIR,
    TOOL_SYSTEM_PROMPT,
    WORKSPACE_DIR,
    ChatMessage,
    TurnRequest,
    effective_system_prompt,
    elapsed_ms,
    ensure_default_workspace,
    migrate_legacy_workspace_field,
    request_real_reply,
    resolve_workspace,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

ensure_default_workspace()
MIGRATED_SESSIONS = migrate_legacy_workspace_field()





class ChatRequest(BaseModel):
    # sessionId 必填：历史由服务端按会话日志重放，前端只发**本轮新增**的消息。
    # 没有"无状态模式"这一说 —— 前端不再持有历史，也就没有全量重发这回事。
    sessionId: str
    messages: list[ChatMessage] = Field(default_factory=list)
    workspaceId: str | None = None     # 新会话绑到哪个工作区；老会话以 header 里记的为准
    provider: str = DEFAULT_PROVIDER
    model_name: str = DEFAULT_MODEL_NAME
    temperature: float | None = None  # None → 使用 providers.yaml 里该模型的默认温度
    system_prompt: str | None = None   # None → 用 DEFAULT_SYSTEM_PROMPT；"" → 一条 system 都不发
    tools_enabled: bool = False        # True → 附带工具并允许模型调用

    def to_turn_request(self) -> TurnRequest:
        """只取"与传输方式无关"的部分，交给 runtime 跑（CLI 同样构造 TurnRequest）。"""
        return TurnRequest(
            messages=self.messages,
            provider=self.provider,
            model_name=self.model_name,
            system_prompt=self.system_prompt,
            tools_enabled=self.tools_enabled,
            temperature=self.temperature,
        )


class WorkspaceRequest(BaseModel):
    # 两种用法：给 root 登记一个已存在的目录；或给 parent + name 新建一个目录再登记。
    root: str | None = None
    parent: str | None = None
    name: str | None = None


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


@app.get("/api/workspaces")
def list_workspaces(
    ) -> dict:
    """工作区登记表。default 是没指定时用的那个。"""
    return {
        "workspaces": workspace_store.load(WORKSPACE_DIR),
        "default": (workspace_store.default(WORKSPACE_DIR) or {}).get("id"),
    }


@app.post("/api/workspaces")
def create_workspace(
    payload: WorkspaceRequest,
    ) -> dict:
    """登记一个工作区。同一个目录重复登记会返回原来那条（id 由路径哈希得来）。

    两种用法：
      root            登记一个**已存在**的目录
      parent + name   在 parent 下**新建**一个目录再登记 —— 不该逼用户先自己去 mkdir

    只要求"是个目录"。等做沙箱时这里才是要收紧的地方（比如只允许 HOME 下面）——
    注意这条和 /api/browse 一样，是会写盘、且暴露在局域网上的入口。
    """
    if payload.name is not None:
        parent = Path(payload.parent or Path.home()).expanduser()
        if not parent.is_dir():
            raise HTTPException(status_code=400, detail="parent is not a directory")

        # 名字只能是单个目录名：挡掉 "../x" 这类想跳出 parent 的写法。不做"尽力清洗"，直接拒。
        name = payload.name.strip()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise HTTPException(status_code=400, detail="invalid workspace name")

        target = parent / name
        if target.exists():
            raise HTTPException(status_code=409, detail="已经存在同名目录")
        try:
            target.mkdir()      # 只建一级：parent 必须已存在，免得凭空造出一串
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"建目录失败：{exc}") from exc
        return {"workspace": workspace_store.ensure(WORKSPACE_DIR, target)}

    if not payload.root:
        raise HTTPException(status_code=400, detail="root or name is required")
    root = Path(payload.root).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="not an existing directory")
    return {"workspace": workspace_store.ensure(WORKSPACE_DIR, root)}


@app.delete("/api/workspaces/{workspace_id}")
def delete_workspace(
    workspace_id: str,
    withSessions: bool = False,
    ) -> dict:
    """删掉一个工作区登记。

    里面有会话时，默认**不删**并返回 409：那些会话的 header 里存的是它的 id，
    登记一删它们就成了孤儿 —— 既不在任何工作区的列表里，也没法从界面找回来。

    要连会话一起删，客户端必须显式带 withSessions=true。这个开关是刻意的：
    会话日志是不可撤销的，不能让一次手滑或一个手写的 DELETE 就把历史抹掉 ——
    界面那边是先把"会删掉几场对话"说给用户听、拿到确认后才带这个参数。

    注意删的是登记和会话日志，**不碰磁盘上的工作目录**：那是用户的项目目录，
    删登记只等于"不再在侧栏里列出来"，不等于 rm -rf。
    """
    entries = workspace_store.load(WORKSPACE_DIR)
    if not any(entry.get("id") == workspace_id for entry in entries):
        raise HTTPException(status_code=404, detail="workspace not found")

    used_by = session_store.list_sessions(SESSION_DIR, workspace_id)
    if used_by and not withSessions:
        raise HTTPException(
            status_code=409,
            detail=f"还有 {len(used_by)} 场对话在这个工作区里",
        )

    deleted_sessions = 0
    for session in used_by:
        if session_store.delete_session(SESSION_DIR, session["id"]):
            deleted_sessions += 1

    workspace_store.save(
        WORKSPACE_DIR,
        [entry for entry in entries if entry.get("id") != workspace_id],
    )
    remaining = workspace_store.load(WORKSPACE_DIR)
    # 一个不剩就把它自己复活 —— 否则下一次解析工作区没有回落对象。
    if not remaining:
        workspace_store.ensure(WORKSPACE_DIR, DEFAULT_WORKSPACE_ROOT)
    return {
        "workspaces": workspace_store.load(WORKSPACE_DIR),
        "default": (workspace_store.default(WORKSPACE_DIR) or {}).get("id"),
        "deletedSessions": deleted_sessions,
    }


@app.delete("/api/sessions/{session_id}")
def delete_session(
    session_id: str,
    ) -> dict:
    """删掉一场对话（它的日志文件就是它的全部）。

    删了就没了：没有数据库、没有回收站，历史是折叠回放这份日志得来的。
    所以界面上必须先确认再调这里。
    """
    if not session_store.exists(SESSION_DIR, session_id):
        raise HTTPException(status_code=404, detail="session not found")
    if not session_store.delete_session(SESSION_DIR, session_id):
        raise HTTPException(status_code=500, detail="删不掉这场对话的日志文件")
    return {"deleted": session_id}


@app.get("/api/sessions")
def list_sessions(
    workspaceId: str | None = None,
    ) -> dict:
    """会话列表。

    默认返回**全部**工作区的会话，由前端按工作区分组渲染（对齐上游 sidebar 的
    groupBy=workspace）。给了 workspaceId 仍然只返回那个工作区的 —— 接口保留
    这个能力，只是界面不再用它。

    workspace 是"没指定时用哪个"，前端拿它当新对话的落点。
    """
    return {
        "workspace": resolve_workspace(workspaceId),
        "sessions": session_store.list_sessions(SESSION_DIR, workspaceId),
    }


@app.get("/api/browse")
def browse_directories(
    path: str | None = None,
    ) -> dict:
    """列一个目录下的**子目录**，供"添加工作区"挑选。

    为什么需要它：让用户手打绝对路径不叫交互。上游用的是 ui-directory-picker
    那两个包（原生 + 浏览两种），这里做最小可用版：列出子目录、能往上走、选中即登记。

    **只列目录名，不读文件内容。** 但要知道这仍是把文件系统的目录结构暴露给了
    局域网 —— 本服务只在局域网可达，而 agent 本来就能用 run_bash 走到任何地方，
    所以这不是新增的能力面，只是换了条路径。等做沙箱时这里要一起收紧。
    """
    base = Path(path).expanduser() if path else Path.home()
    try:
        base = base.resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="bad path")
    if not base.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")

    entries: list[dict] = []
    try:
        for child in sorted(base.iterdir(), key=lambda item: item.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="permission denied")

    parent = base.parent
    return {
        "path": str(base),
        "parent": str(parent) if parent != base else None,
        "entries": entries,
    }


@app.get("/api/sessions/{session_id}")
def get_session(
    session_id: str,
    ) -> dict:
    """取一场会话，供前端渲染。

    items 已是**渲染顺序**（user / step / assistant 交替），前端照着 map 一遍即可
    —— 分别给 messages 和 steps 两张平铺表会让工具步骤错位。
    settings 是会话级设置（是否使用工具），前端切到这场对话时要照着恢复。
    """
    safe_id = session_store.sanitize_id(session_id)
    if safe_id is None or not session_store.exists(SESSION_DIR, safe_id):
        raise HTTPException(status_code=404, detail="session not found")

    return {
        "id": safe_id,
        "workspaceId": session_store.load_workspace_id(SESSION_DIR, safe_id),
        "settings": session_store.load_settings(SESSION_DIR, safe_id),
        # 说过的对话，工具开关就锁死了：它决定系统提示词里有没有工具说明，
        # 而历史一旦有工具协议消息，中途关掉会让 normalize_messages 把那些消息
        # 整段丢掉 —— 模型看到的历史会凭空少一块。所以只在新对话上可选。
        "toolsLocked": session_store.turn_count(SESSION_DIR, safe_id) > 0,
        "items": session_store.load_items(SESSION_DIR, safe_id),
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    ) -> ChatResponse:
    started = time.monotonic()
    session_id = resolve_session_id(payload)
    # 新会话绑到客户端指定的工作区；**老会话以 header 里记的为准** —— 绑定不该
    # 半路变，否则历史里那些相对路径的含义就乱了。
    bound_workspace_id = session_store.load_workspace_id(SESSION_DIR, session_id)
    if bound_workspace_id is None:
        bound_workspace_id = resolve_workspace(payload.workspaceId)["id"]
    # 工具开关：说过话的会话**不许中途改**，以最后一轮记下的值为准。
    #
    # 以前这条约束挂在 PATCH 上（那个接口已经删了 —— 它会在磁盘上造出"没说过话的
    # 会话文件"）。现在 payload 里带的值才要设防：关掉工具时 normalize_messages 会
    # 丢掉历史里的工具协议消息，等于静默截断历史，所以这里不信任客户端这一轮说的值。
    if session_store.turn_count(SESSION_DIR, session_id) > 0:
        recorded = session_store.load_settings(SESSION_DIR, session_id).get("toolsEnabled")
        if recorded is not None and bool(payload.tools_enabled) != bool(recorded):
            raise HTTPException(
                status_code=409,
                detail="这个对话已经说过了，工具开关不再可改 —— 请开新对话",
            )

    # 工具干活的地方 = 这个工作区的根。工作区被删过（或 id 失效）时 resolve_workspace
    # 会回落到默认那个，不报错 —— 一场指向已删工作区的会话，还能继续用，落在默认根里。
    workspace_root = resolve_workspace(bound_workspace_id).get("root")

    # 历史从会话日志重放 —— 客户端只发本轮新增，服务端不信任它带的历史。
    prior_messages = [
        ChatMessage(**record) for record in session_store.load_history(SESSION_DIR, session_id)
    ]

    try:
        result = request_real_reply(payload.to_turn_request(), prior_messages, workspace_root)
    except Exception as exc:
        # 失败时**不往历史里写 user 消息**：那条提问没有对应的回答，留在历史里
        # 会让重试把同一句话追加第二遍。失败只记在 turn 记录里（turn 不是历史类型，
        # 重放会跳过），内容放 attempted 字段备查。
        session_store.append_turn(
            SESSION_DIR, session_id,
            user_messages=[], protocol=[],
            workspace_id=bound_workspace_id,
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
        protocol=result.protocol_messages,
        workspace_id=bound_workspace_id,
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

