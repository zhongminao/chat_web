"""观测状态：记录 agent 读过哪些文件、读到的版本是什么。

支撑两条守卫：
- 先读后改：没读过的文件不许直接改；
- 版本守卫：读过之后被外部改过，就拒绝改动、要求重读。

形制取自 DSH 的 fs-observation-policy（packages/fs/fs-observation-policy）：它维护
observed state，在写入/编辑意图上转成 CAS 守卫，版本不符则报错并附恢复指令
（"re-read the file, then retry"）。

状态是**进程内存**，不持久化 —— DSH 同样如此，并把这条列在 Known Limitations 里。
进程重启后 agent 必须重新读文件，这是可接受的代价。
"""
from __future__ import annotations

import os
from pathlib import Path


# 规范路径 -> (mtime_ns, size)
_observed: dict[str, tuple[int, int]] = {}


def _key(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def version(path: str) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def remember(path: str) -> None:
    """记下"已观测到该文件在此版本"。文件不存在则清除记录。"""
    current = version(path)
    if current is None:
        _observed.pop(_key(path), None)
    else:
        _observed[_key(path)] = current


def guard(path: str) -> str | None:
    """返回 None = 允许改动；否则返回拒绝原因（给模型看的文本）。"""
    current = version(path)
    if current is None:
        # 文件不存在：等同于"新建"，放行 —— 对应 DSH 的 createIfAbsent 语义
        return None

    seen = _observed.get(_key(path))
    if seen is None:
        return "not read yet — read it first, then retry"
    if seen != current:
        return "changed since you read it — re-read it, then retry"
    return None


def forget_all() -> None:
    """清空观测状态（测试用）。"""
    _observed.clear()
