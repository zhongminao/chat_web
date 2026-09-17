"""进程的"轮次表"+ 取消令牌：让另一个请求能打断正在跑的那一轮。

**为什么这里用模块级全局状态是正当的**：它记的是"这个进程正在跑哪些轮次"，本质上
就是 /proc —— 进程表全局是对的。（对比 observed.py：那份"读过哪些文件"是**每个
agent 自己的**观察状态，放全局就成了跨会话串扰。两者不是一回事。）

用法：
    token = REGISTRY.begin(session_id)      # None = 这场会话已有一轮在跑
    ...
    request_real_reply(..., should_stop=token.should_stop)
    ...
    REGISTRY.end(session_id, token)         # finally 里

取消是**协作式**的：置位只写一个 Event，循环在步边界（模型调用前、每条工具调用前）
自己看见并抛出 TurnCancelled。原因见 CancelToken 的注释。
"""
from __future__ import annotations

import threading


class TurnCancelled(Exception):
    """调用方要求中断这一轮。

    **已经落盘的记录不回滚** —— 这个异常只负责把控制权交回调用方，让它收尾
    （写 turn-end）、把"没跑完的调用"补上合成结果，并把结果告诉用户。
    """


class CancelToken:
    """一轮的取消令牌。

    线程安全：置位的是**另一个线程**（HTTP 的 /interrupt 请求、关服务时的
    cancel_all），而轮询的是跑着循环的那个 AnyIO worker 线程 —— 所以用 Event
    而不是普通标志位。

    为什么不做得更"即时"：模型调用和工具执行都是**同步阻塞**的，没有可从外部中断的
    句柄（openai 的同步 client 没有 abort API）。所以粒度只能是步边界。用户点停止
    之后，实际停下来要等当前那一步结束 —— 前端该显示"正在停止…"，别当成已停止。
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = ""
        # 此刻有没有循环**真的**在跑这一轮（attach/detach 由跑循环的那段代码管）。
        #
        # 为什么需要它：暂停等审批时令牌是**留着**的（这样 /interrupt、running、停止按钮
        # 在暂停期间才有意义），但那一刻并没有循环在跑 —— 取消信号没人接。靠这一格就能
        # 区分"正在跑"和"停在审批上"，于是 /interrupt 能对后者做对的事（放弃这一轮），
        # 而不是把信号丢进一个空循环。
        self._attached = False

    def attach(self) -> None:
        """标记"循环现在跑起来了"。放在 run_agent_turn 外面，与 detach 成对（finally）。"""
        self._attached = True

    def detach(self) -> None:
        self._attached = False

    @property
    def attached(self) -> bool:
        return self._attached

    def cancel(self, reason: str) -> None:
        self._reason = reason or "cancelled"
        self._event.set()

    def should_stop(self) -> str | None:
        """步边界轮询用：None = 继续；返回字符串就是停止原因。"""
        return self._reason if self._event.is_set() else None


class TurnRegistry:
    """session_id → 正在跑的那一轮的令牌。

    它顺带兼任**每会话互斥**，这不是附赠功能而是必需的：

    - TurnWriter.begin() 里那句 `if not path.exists()` 是 check-then-act，两个请求
      同时进来会各自看到"文件不存在"，于是**两行 header 写进同一个 JSONL**；
    - 就算文件已存在，两轮交错追加之后重放出来的历史顺序也是错的（后开始的那轮
      构造历史时看不到先开始那轮的记录）。

    于是同一场会话的第二轮直接 409 拒绝 —— 宁可让用户等，也不写坏日志。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, CancelToken] = {}

    def begin(self, session_id: str) -> CancelToken | None:
        """登记一轮。已有在跑 → None（调用方应回 409）。"""
        with self._lock:
            if session_id in self._running:
                return None
            token = CancelToken()
            self._running[session_id] = token
            return token

    def end(self, session_id: str, token: CancelToken | None) -> None:
        """注销一轮。幂等，且只删自己那个 —— stale 的 token 不能删掉后来的同名会话。

        token 为 None（这场会话压根没登记过，比如服务重启后接着恢复一轮、或测试里直接调
        恢复路径）必须直接返回：`self._running.get()` 对不存在的键也给 None，于是
        `None is None` 成立、接着 del 一个不存在的键 → KeyError。这个洞以前没人踩，是因为
        调用方总是先 begin() 过；暂停要"收尾时才注销"，才有了没登记就得收尾的路径。
        """
        if token is None:
            return
        with self._lock:
            if self._running.get(session_id) is token:
                del self._running[session_id]

    def adopt(self, session_id: str) -> CancelToken | None:
        """拿回这场会话**已经登记着**的那枚令牌（暂停后恢复时用）；没有就 None。

        暂停等审批时令牌不注销（不然 /interrupt 和 running 在暂停期间就失效了），所以恢复
        的第一件事是把它认回来：接着接收取消、并在这一轮**真的**结束时注销它。
        """
        with self._lock:
            return self._running.get(session_id)

    def cancel(self, session_id: str, reason: str) -> bool:
        """请求中止。没在跑 → False（不是错误：那一轮可能刚好自己结束了）。"""
        with self._lock:
            token = self._running.get(session_id)
        if token is None:
            return False
        token.cancel(reason)
        return True

    def cancel_all(self, reason: str) -> int:
        """关服务时用：把所有在跑的轮次都请求停下。返回请求了几个。"""
        with self._lock:
            tokens = list(self._running.values())
        for token in tokens:
            token.cancel(reason)
        return len(tokens)

    def is_running(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._running


REGISTRY = TurnRegistry()
