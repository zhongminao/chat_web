"""观测状态：记录 agent 读过哪些文件、读到的版本是什么。

支撑两条守卫：
- 先读后改：没读过的文件不许直接改（这条是硬拦）；
- 变更提醒：读过之后被外部改过，**先拦一次并说明变了多少**，之后模型要重读
  还是照样改，由它自己判断。

第二条只提醒一次是刻意的：守卫能说出"文件变了"，但说不出"哪里变了"，所以它
没法命令模型"去读该读的地方"。只有模型知道自己依赖了什么 —— 告知 + 交还判断，
比拦死更准确。

形制取自 DSH 的 fs-observation-policy（packages/fs/fs-observation-policy）：它维护
observed state，在写入/编辑意图上转成版本守卫，不符则报错并附恢复指令。区别在于
DSH 是硬拦（FS_STALE_VERSION），这里多给一次自主权。

状态是**进程内存**，不持久化 —— DSH 同样如此，并把这条列在 Known Limitations 里。
进程重启后 agent 必须重新读文件，这是可接受的代价。
"""
from __future__ import annotations

import os
from pathlib import Path


# 超过这个体量就不数行数了（体量判断是白给的：guard 里本来就刚 stat 过）。
# 数行要读全文，成本随体积线性增长；字节数在 stat 里是常数时间，任何体量都能给。
LINE_COUNT_MAX_BYTES = 1024 * 1024

# 规范路径 -> ((mtime_ns, size), 行数或 None)
#
# 行数在"记录时"是白给的 —— 三个工具手里本来就有内容（read_file 刚读过、
# write_file 刚写了、edit_file 刚改完），数一下不用额外 I/O。
# 行数刻意用 splitlines() 数，与 read_file 报的"N lines total"同一定义，
# 否则两个数字对不上，反而误导模型。
_observed: dict[str, tuple[tuple[int, int], int | None]] = {}

# 规范路径 -> 已经提醒过的那一版。每个"新版本"只拦一次：
# 提醒完之后模型要重读还是硬改，是它自己的判断 —— 守卫的职责是"告知"，
# 不是"禁止"。守卫本身说不出文件哪里变了，只有模型知道自己依赖了什么。
_warned: dict[str, tuple[int, int]] = {}


def _key(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def version(path: str) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _count_lines(path: str, size: int) -> int | None:
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


def remember(path: str, lines: int | None = None) -> None:
    """记下"已观测到该文件在此版本"。

    lines: 调用方已知的行数（它手里有内容，白给）。不给才自己去数。
    """
    key = _key(path)
    current = version(path)
    if current is None:
        _observed.pop(key, None)
    else:
        if lines is None:
            lines = _count_lines(path, current[1])
        _observed[key] = (current, lines)
    _warned.pop(key, None)


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


def guard(path: str) -> str | None:
    """返回 None = 允许改动；否则返回提醒文本（给模型看）。"""
    current = version(path)
    if current is None:
        # 文件不存在：等同于"新建"，放行 —— 对应 DSH 的 createIfAbsent 语义
        return None

    key = _key(path)
    entry = _observed.get(key)
    if entry is None:
        return "not read yet — read it first, then retry"
    seen, seen_lines = entry
    if seen == current:
        return None
    if _warned.get(key) == current:
        # 这一版已经提醒过了。之后怎么做是模型自己的事。
        return None

    _warned[key] = current
    detail = _changed_detail(seen, seen_lines, current, _count_lines(path, current[1]))
    return (
        f"changed since you read it ({detail}) — "
        f"re-read the part you are about to change, then retry"
    )


def forget_all() -> None:
    """清空观测状态（测试用）。"""
    _observed.clear()
    _warned.clear()
