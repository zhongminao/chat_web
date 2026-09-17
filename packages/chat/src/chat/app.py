import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


# 路径 / 环境 / 消息归一 / 一轮对话 —— 全在 runtime.py，CLI 用的是同一份实现。
# 这个文件只留 HTTP 那一层：请求响应模型 + 路由 + 静态文件。
# 路由直接用这两个 store（runtime 里也用，但它不 re-export —— 直接用比再包一层清楚）
from chat import list_providers
from chat.agent import session_store, workspace_store
from chat.agent.cancel import REGISTRY, TurnCancelled
from chat.agent.sandbox import DEFAULT_SANDBOX_MODE, normalize_mode
from chat.agent.tools import run_bash
from chat.runtime import (
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER,
    DEFAULT_WORKSPACE_ROOT,
    SESSION_DIR,
    WORKSPACE_DIR,
    ChatMessage,
    TurnRequest,
    default_system_prompt,
    elapsed_ms,
    ensure_default_workspace,
    migrate_legacy_workspace_field,
    request_real_reply,
    resolve_system_prompt,
    resolve_workspace,
    resume_after_approval,
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
    system_prompt: str | None = None   # None → 用 prompts.yaml 的模板；"" → 一条 system 都不发
    tools_enabled: bool = False        # True → 附带工具并允许模型调用
    sandbox_mode: str = DEFAULT_SANDBOX_MODE

    def to_turn_request(self) -> TurnRequest:
        """只取"与传输方式无关"的部分，交给 runtime 跑（CLI 同样构造 TurnRequest）。"""
        return TurnRequest(
            messages=self.messages,
            provider=self.provider,
            model_name=self.model_name,
            system_prompt=self.system_prompt,
            tools_enabled=self.tools_enabled,
            sandbox_mode=normalize_mode(self.sandbox_mode),
            temperature=self.temperature,
        )


class WorkspaceRequest(BaseModel):
    # 两种用法：给 root 登记一个已存在的目录；或给 parent + name 新建一个目录再登记。
    root: str | None = None
    parent: str | None = None
    name: str | None = None


class ChatResponse(BaseModel):
    """一轮结束后的**状态回执** —— 不是数据的副本。

    这个接口只回答一件事："服务端做了什么决定"。发生过什么由
    `GET /api/sessions/{id}` 回答（那是日志的投影，是唯一权威）。

    以前这里返回 `reply` + `steps`，而前端拿到之后**根本不用** —— 它转头重取会话、
    以日志为准渲染，只在"取日志失败"的兜底分支里才读一眼。同一份数据两个表示，
    迟早不一致。现在删掉：要结果就去读日志；读不到就明说读不到，别拿一份可能过期的
    副本顶上（那样最坏的情况是屏幕上显示的内容和磁盘上的记录不一样）。

    state:
      "ok"          —— 这一轮正常结束
      "interrupted" —— 被 /interrupt 中止（**不是错误**）
    toolsLocked:
      这一轮写完之后这场会话就"说过话了" → 工具开关锁死。由服务端告知，
      **前端别自己推断**（以前它只在加载会话时读一次，于是发完第一条消息后开关
      还能点，再发一条服务端直接 409）。
    """

    state: Literal["ok", "interrupted"]
    toolsLocked: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """关服务时先把在跑的轮次都请求停下。

    不这么做的话 `systemctl --user restart chat` 会一直等到那个长轮次自己跑完
    （uvicorn 优雅关机会等在途请求），而 loop.py 已经没有轮数上限了 —— 可能很久。
    取消之后循环在下一个步边界收尾：写 turn-end、给没跑完的调用补上合成结果。
    """
    yield
    stopped = REGISTRY.cancel_all("服务正在关闭")
    if stopped:
        print(f"关闭中：已请求 {stopped} 个在跑的轮次停止")


app = FastAPI(title="chat-app", lifespan=lifespan)
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
    """供应商与模型目录（来自 providers.yaml），供前端模型选择器使用。

    系统提示词这里给的是**服务端拼好的两份成品**，不是模板碎片。前端以前自己拼
    （`工具说明 + "\\n\\n" + 基础`，抄了三处），改一次分隔符三处都不会跟着动。
    现在拼接只有 resolve_system_prompt 一处，前端只负责"按工具开关选哪一份"。
    """
    return {
        "providers": list_providers(),
        "default_provider": DEFAULT_PROVIDER,
        "default_model": DEFAULT_MODEL_NAME,
        # 不开工具时用哪份 / 开了工具用哪份 —— 都是可以直接发给模型的成品文本
        "system_prompt_plain": default_system_prompt(tools_enabled=False),
        "system_prompt_with_tools": default_system_prompt(tools_enabled=True),
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
    workspaceId: str | None = None,
    ) -> dict:
    """工作区登记表。

    resolved = **这个客户端现在该用哪个工作区**：给它一个 id，有效就原样返回，无效
    （已被删、或它自己就是空的）就回落到默认那个。这条判断以前前端自己算了一遍
    （"本地存的不在列表里 → 用 default"），等于同一条规则两处实现；现在服务端把
    答案直接给它，前端只负责采纳 —— 客户端不该知道"失效了怎么办"。
    """
    return {
        "workspaces": workspace_store.load(WORKSPACE_DIR),
        "default": (workspace_store.default(WORKSPACE_DIR) or {}).get("id"),
        "resolved": resolve_workspace(workspaceId)["id"],
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

    **正在跑的一轮不许删**（409）。实测过后果：文件删掉之后，那一轮还在跑，而
    TurnWriter.record() 用的是 open(path,"a") —— 它会**把文件重新创建出来**，但
    header 只在 begin() 写过一次，于是留下一个首行是 tool 的日志：服务端把它显示成
    工作区为空的「(空对话)」，既进不了任何工作区分组，也不是一场能读的会话。
    正确的顺序是先停止、再删。
    """
    if not session_store.exists(SESSION_DIR, session_id):
        raise HTTPException(status_code=404, detail="session not found")
    if REGISTRY.is_running(session_id):
        raise HTTPException(
            status_code=409, detail="这场对话正在跑一轮 —— 先点停止，再删")
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
        # 正在跑吗？刷新页面之后前端靠这个决定要不要显示"停止"按钮 —— 这一轮跑在
        # 服务端，跟浏览器在不在没关系（日志是权威，浏览器只是订阅者）。
        "running": REGISTRY.is_running(safe_id),
        "items": session_store.load_items(SESSION_DIR, safe_id),
    }


@app.post("/api/sessions/{session_id}/bash-requests/{request_id}/approve")
def approve_bash_request(
    session_id: str,
    request_id: str,
    ) -> dict:
    safe_id = session_store.sanitize_id(session_id)
    if safe_id is None or not session_store.exists(SESSION_DIR, safe_id):
        raise HTTPException(status_code=404, detail="session not found")
    request = session_store.find_bash_request(SESSION_DIR, safe_id, request_id)
    if request is None:
        raise HTTPException(status_code=409, detail="bash request is not pending")
    command = str(request.get("command") or "")
    if not command:
        raise HTTPException(status_code=400, detail="bash request has no command")
    result = run_bash(
        command,
        timeout=request.get("timeout"),
        root=request.get("cwd") or None,
        sandbox_mode="full-access",
        audit_root=WORKSPACE_DIR,
        audit_session_id=safe_id,
        approved_request_id=request_id,
    )
    if not session_store.append_bash_result(
        SESSION_DIR, safe_id, request_id, status="executed", content=result):
        raise HTTPException(status_code=500, detail="failed to record bash result")
    reply = resume_after_approval(
        safe_id,
        approval={"requestId": request_id, "command": command, "status": "executed"},
    )
    return {"status": "executed", "content": result, "reply": reply}


@app.post("/api/sessions/{session_id}/bash-requests/{request_id}/reject")
def reject_bash_request(
    session_id: str,
    request_id: str,
    ) -> dict:
    safe_id = session_store.sanitize_id(session_id)
    if safe_id is None or not session_store.exists(SESSION_DIR, safe_id):
        raise HTTPException(status_code=404, detail="session not found")
    request = session_store.find_bash_request(SESSION_DIR, safe_id, request_id)
    if request is None:
        raise HTTPException(status_code=409, detail="bash request is not pending")
    content = "[bash rejected] user rejected this one-time bash request"
    if not session_store.append_bash_result(
        SESSION_DIR, safe_id, request_id, status="rejected", content=content):
        raise HTTPException(status_code=500, detail="failed to record bash rejection")
    # 拒绝也要恢复 loop：模型得知道这条没跑，才好继续（换个做法或把情况说清楚）。
    reply = resume_after_approval(
        safe_id,
        approval={
            "requestId": request_id,
            "command": str(request.get("command") or ""),
            "status": "rejected",
        },
    )
    return {"status": "rejected", "content": content, "reply": reply}


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
        settings = session_store.load_settings(SESSION_DIR, session_id)
        recorded = settings.get("toolsEnabled")
        if recorded is not None and bool(payload.tools_enabled) != bool(recorded):
            raise HTTPException(
                status_code=409,
                detail="这个对话已经说过了，工具开关不再可改 —— 请开新对话",
            )
    # 工具干活的地方 = 这个工作区的根。工作区被删过（或 id 失效）时 resolve_workspace
    # 会回落到默认那个，不报错 —— 一场指向已删工作区的会话，还能继续用，落在默认根里。
    workspace_root = resolve_workspace(bound_workspace_id).get("root")

    # **在跑之前**把提示词解析成最终那条 system 消息。
    # 它是"这一轮用的提示词"这个事实本身，所以跟着 turn 一起落盘 —— 取消的轮次也
    # 记得上（以前只在 turn-end 里记"实际生效的"，而取消时拿不到），前端也能靠它
    # 把面板恢复成这个会话在用的那份。
    effective_prompt = resolve_system_prompt(payload.system_prompt, payload.tools_enabled)

    prior_messages = [
        ChatMessage(**record) for record in session_store.load_history(SESSION_DIR, session_id)
    ]

    # 每会话互斥：同一场会话不许两轮并发跑。并发时 TurnWriter.begin() 里那句
    # `if not path.exists()` 会各写一行 header，而且两轮交错追加之后重放出来的
    # 历史顺序是错的 —— 宁可让用户等，也不写坏日志。
    # 这个令牌同时就是"外部请求中止这一轮"的抓手（见 /interrupt 路由）。
    token = REGISTRY.begin(session_id)
    if token is None:
        raise HTTPException(
            status_code=409, detail="这场对话已经有一轮在跑 —— 等它结束，或者先点停止")

    # 跑 agent 之前先写 header + turn + user：这一轮即使崩了/被取消/Ctrl-C，
    # 磁盘上也已经知道用户问了什么、用的是哪份提示词。
    writer = session_store.TurnWriter(
        SESSION_DIR, session_id, workspace_id=bound_workspace_id)
    try:
        # meta 里放"跑之前就知道"的东西；**提示词只在变了的时候才放**（见
        # system_prompt_meta）—— 它一千多字，每轮写一遍等于同一份信息存 N 遍。
        meta = {"provider": payload.provider, "model": payload.model_name,
                "toolsEnabled": payload.tools_enabled,
                "sandboxMode": normalize_mode(payload.sandbox_mode)}
        meta.update(session_store.system_prompt_meta(
            SESSION_DIR, session_id, effective_prompt))
        writer.begin(meta, [message.model_dump() for message in payload.messages])
        result = request_real_reply(
            payload.to_turn_request(), prior_messages, workspace_root,
            writer=writer, should_stop=token.should_stop,
            observed_context_id=f"session:{session_id}")
    except TurnCancelled as exc:
        # 中止**不是错误**：已经跑过的步骤都逐条落盘了。写个收尾就正常返回，
        # 由 interrupted 标记告诉前端"去重拉一次会话，把过程画出来"。
        writer.finish(durationMs=elapsed_ms(started), error=f"cancelled: {exc}")
        return ChatResponse(state="interrupted", toolsLocked=True)
    except Exception as exc:
        writer.finish(durationMs=elapsed_ms(started), error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        REGISTRY.end(session_id, token)

    writer.finish(
        durationMs=elapsed_ms(started),
        temperature=result.temperature,
    )
    if writer.failed:
        raise HTTPException(status_code=500, detail="会话日志写入失败")

    # 这一轮写完之后，这场会话**一定**已经有轮次了 → 工具开关从此锁死。
    # 回执里直接告诉前端，别让它自己推断 —— 以前它只在加载会话时读 toolsLocked，
    # 于是"发完第一条消息之后开关还能点，再发就 409"（用户看得见）。
    return ChatResponse(state="ok", toolsLocked=True)


@app.post("/api/sessions/{session_id}/interrupt")
def interrupt_session(
    session_id: str,
    ) -> dict:
    """请求中止这场会话正在跑的那一轮。

    **协作式**：循环只在步边界检查（每次模型调用之前、每条工具调用之前），所以
    "正在等模型回复"或"正在跑一个慢命令"的那一下拦不住 —— 本接口立刻返回，但真正
    停下来要等当前这一步结束。前端应当显示"正在停止…"，别当成已停止。

    没在跑 → interrupted: false。**这不是错误**：用户点停止时那一轮可能刚好自己
    结束了，两者对用户是一样的结果。
    """
    safe_id = session_store.sanitize_id(session_id)
    if safe_id is None:
        raise HTTPException(status_code=400, detail="invalid sessionId")
    return {"interrupted": REGISTRY.cancel(safe_id, "用户点了停止")}

