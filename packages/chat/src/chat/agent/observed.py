"""观测状态：按**执行上下文**隔离地记录 agent 读过哪些文件、读到的是哪一版。

支撑两条守卫：
- 先读后改：没读过的文件不许直接改（这条是硬拦）；
- 变更提醒：读过之后被外部改过，**先拦一次并说明变了多少**，之后模型要重读
  还是照样改，由它自己判断。

第二条只提醒一次是刻意的：守卫能说出"文件变了"，但说不出"哪里变了"，所以它
没法命令模型"去读该读的地方"。只有模型知道自己依赖了什么 —— 告知 + 交还判断，
比拦死更准确。

形制取自 DSH 的 fs-observation-policy（packages/fs/fs-observation-policy）：DSH 把
观测状态放在一张以 `agent.session` **对象身份**为弱键的 WeakMap 里，于是"按会话
隔离"是天生的，会话被回收时状态跟着消失（它把"不持久化"写在自己的 Known
Limitations 里）。Python 没有 WeakMap，这里就把那层键**显式化**成 context_id：
一个 context 一张表，长期不用的 context 由 TTL 扫掉。

状态是**进程内存**，不持久化。进程重启后 agent 必须重新读文件，这是可接受的代价
—— 守卫丢掉只会让它"多拦一次、让模型重读"，永远不会变成"放行一次错误写入"。

**为什么每个 context 必须由宿主显式给 id（没有默认 context）**：以前这两张表是
模块级全局的、并且只按路径记，于是"先读后改"跨会话失效 —— A 场读过的文件，B 场
能直接改（实测过）。所以这里不提供"拿不到 id 就退回到一张共享表"的兜底：要么宿主
明确说这是哪个 context，要么这个工具调用就不成立。

**TTL 不做陈旧判断**：是不是同一版永远由 `guard()` 里的 version 比较回答（那是
事实）。TTL 只回答另一件事："这个 context 多久没人用了，内存该不该还回来"。
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock


# 超过这个体量就不数行数了（体量判断是白给的：guard 里本来就刚 stat 过）。
# 数行要读全文，成本随体积线性增长；字节数在 stat 里是常数时间，任何体量都能给。
LINE_COUNT_MAX_BYTES = 1024 * 1024

# 一个 context 多久没被碰过就从内存里扫掉。
# 这不是"文件多久没变"的猜测 —— 那件事由 version 比较回答。TTL 只负责内存回收，
# 所以它过期导致的后果和最坏情况一样：模型下一次改之前被要求重读一遍。
DEFAULT_TTL_SECONDS = 6 * 60 * 60

# 扫全表不必每次调用都做：这个间隔决定"过期 context 最多还多活多久"。
SWEEP_INTERVAL_SECONDS = 5 * 60


def _key(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def version(path: str | Path) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _count_lines(path: str | Path, size: int) -> int | None:
    """只在检测到变化、且文件不够大时才调用。

    size 由调用方传入（它刚 stat 过），这里不再重复 stat。
    """
    if size > LINE_COUNT_MAX_BYTES:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return len(handle.read().splitlines())
    except OSError:
        return None


def _changed_detail(
    before: tuple[int, int],
    before_lines: int | None,
    after: tuple[int, int],
    after_lines: int | None,
    ) -> str:
    parts: list[str] = []
    if before[1] != after[1]:
        parts.append(f"{before[1]} → {after[1]} bytes")
    if (
        before_lines is not None
        and after_lines is not None
        and before_lines != after_lines
    ):
        parts.append(f"{before_lines} → {after_lines} lines")
    if parts:
        return ", ".join(parts)
    if before_lines is None or after_lines is None:
        return "same size, content changed"
    return "same size and line count, content changed"


@dataclass
class ObservationContext:
    """一个执行上下文的观测状态。

    context_id 的命名由宿主决定，约定形如：
        session:<会话id>                            网页 / CLI 的一场对话
        session:<会话id>:agent:<子agent id>          同一场对话里的一个子 agent
        eval:<case>-<时间戳>-<第几次>                 评估里的一次 case run
    """

    context_id: str
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    last_used: float = field(default_factory=time.monotonic)
    # 规范路径 -> ((mtime_ns, size), 行数或 None)
    #
    # 行数在"记录时"是白给的 —— 三个工具手里本来就有内容（read_file 刚读过、
    # write_file 刚写了、edit_file 刚改完），数一下不用额外 I/O。
    # 行数刻意用 splitlines() 数，与 read_file 报的"N lines total"同一定义，
    # 否则两个数字对不上，反而误导模型。
    _observed: dict[str, tuple[tuple[int, int], int | None]] = field(default_factory=dict)
    # 规范路径 -> 已经提醒过的那一版。每个"新版本"只拦一次：
    # 提醒完之后模型要重读还是硬改，是它自己的判断 —— 守卫的职责是"告知"，
    # 不是"禁止"。守卫本身说不出文件哪里变了，只有模型知道自己依赖了什么。
    #
    # 这张表随 context 走，所以"A 场花掉额度、B 场裸奔"那种串味不会发生：额度是
    # 每个上下文各自的一份。
    _warned: dict[str, tuple[int, int]] = field(default_factory=dict)

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def expired(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return now - self.last_used > self.ttl_seconds

    def remember(self, path: str | Path, lines: int | None = None) -> None:
        """记下"本 context 已观测到该文件在此版本"。

        lines: 调用方已知的行数（它手里有内容，白给）。不给才自己去数。
        """
        self.touch()
        key = _key(path)
        current = version(path)
        if current is None:
            self._observed.pop(key, None)
        else:
            if lines is None:
                lines = _count_lines(path, current[1])
            self._observed[key] = (current, lines)
        self._warned.pop(key, None)

    def guard(self, path: str | Path) -> str | None:
        """返回 None = 允许改动；否则返回提醒文本（给模型看）。"""
        self.touch()
        current = version(path)
        if current is None:
            # 文件不存在：等同于"新建"，放行 —— 对应 DSH 的 createIfAbsent 语义
            return None

        key = _key(path)
        entry = self._observed.get(key)
        if entry is None:
            return "not read yet — read it first, then retry"
        seen, seen_lines = entry
        if seen == current:
            return None
        if self._warned.get(key) == current:
            # 这一版已经提醒过了。之后怎么做是模型自己的事。
            return None

        self._warned[key] = current
        detail = _changed_detail(seen, seen_lines, current, _count_lines(path, current[1]))
        return (
            f"changed since you read it ({detail}) — "
            f"re-read the part you are about to change, then retry"
        )


class ObservationRegistry:
    """进程内的 context 表。

    **这是进程内状态，不跨进程共享、不持久化。** 独立 CLI 子进程天然有自己的
    一份；真要跨进程共享，得换掉这个后端（SQLite / 主进程 API），而不是在这里
    加缓存。
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        sweep_interval_seconds: int = SWEEP_INTERVAL_SECONDS,
        ) -> None:
        self._ttl_seconds = ttl_seconds
        self._sweep_interval_seconds = sweep_interval_seconds
        # 只保护 _contexts 这张表本身。单个 ObservationContext 内部的 dict 不加锁：
        # 不同会话本来就碰不到同一个 context，而同一会话并发跑两轮是另一件事
        # （README「要做什么」第 7 条在跟）。
        self._lock = RLock()
        self._contexts: dict[str, ObservationContext] = {}
        # 0.0 让第一次调用就顺带扫一遍，省一个"启动时初始化"的钩子。
        self._last_sweep = 0.0

    def context(self, context_id: str) -> ObservationContext:
        """取（必要时新建）某个 context。context_id 必填 —— 没有默认 context。"""
        if not context_id or not str(context_id).strip():
            raise ValueError("observed context_id 不能为空：没有默认 context 可退回")
        context_id = str(context_id).strip()
        now = time.monotonic()
        with self._lock:
            self._sweep_if_due(now)
            existing = self._contexts.get(context_id)
            if existing is None:
                existing = ObservationContext(
                    context_id=context_id,
                    ttl_seconds=self._ttl_seconds,
                )
                self._contexts[context_id] = existing
            existing.touch()
            return existing

    def new_context(self, prefix: str = "adhoc") -> ObservationContext:
        """开一个全新的、不与任何东西共享的 context。

        给"没有会话身份"的调用方用（loop 的兜底执行器、演示脚本）。用一次性 id
        而不是一个固定的默认 id，是为了让"兜底"也天然隔离 —— 否则它自己就变成了
        一张共享表。
        """
        return self.context(f"{prefix}:{uuid.uuid4().hex[:12]}")

    def forget(self, context_id: str) -> bool:
        """丢掉一个 context（删会话时顺手调）。返回它是否真的存在过。"""
        with self._lock:
            return self._contexts.pop(context_id, None) is not None

    def forget_all(self) -> None:
        with self._lock:
            self._contexts.clear()

    def sweep(self) -> int:
        """立刻清理过期 context，返回清掉几个。"""
        now = time.monotonic()
        with self._lock:
            return self._sweep_locked(now)

    def live_context_ids(self) -> list[str]:
        """当前还活着的 context id（测试/排错用，别拿它做业务判断）。"""
        with self._lock:
            return sorted(self._contexts)

    def _sweep_if_due(self, now: float) -> None:
        if now - self._last_sweep < self._sweep_interval_seconds:
            return
        self._sweep_locked(now)

    def _sweep_locked(self, now: float) -> int:
        expired = [
            context_id
            for context_id, context in self._contexts.items()
            if context.expired(now)
        ]
        for context_id in expired:
            del self._contexts[context_id]
        self._last_sweep = now
        return len(expired)


# 全进程唯一的那张表。调用方一律显式走它并给出 context_id，没有模块级
# remember/guard 那种"用默认表"的入口 —— 那正是以前跨会话串味的原因。
REGISTRY = ObservationRegistry()
