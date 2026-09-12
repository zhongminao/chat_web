"""工具输出溢出（spill）：全文写盘，内联只留预览 + 定位符。

形制取自 DSH 的 spill capability（packages/spill/）——它一句话说清了这件事：

    persists oversized tool output and replaces the inline result with a
    bounded preview and retrieval locator

落在系统临时目录（TMPDIR / /tmp）下的 chat-spill/，按天分目录。定位符就是那个
文件路径，取回手段是 read_file 的 offset/limit 分页或 run_bash grep —— 无需新工具。

失败一律 fail-soft：拿不到路径就返回 None，调用方退回纯截断，绝不能把一次
成功的命令执行变成失败（同 DSH 的 spill-policy）。
"""
from __future__ import annotations

import os
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path


DIR_NAME = "chat-spill"
KEEP_DAYS = 7

SUFFIX_BY_SOURCE = {
    "run_bash": ".log",
}


def spill_dir() -> Path:
    return Path(tempfile.gettempdir()) / DIR_NAME


def save(text: str, source: str) -> Path | None:
    """把全文原样落盘，返回路径；任何失败都返回 None。"""
    try:
        base = spill_dir()
        day = datetime.now().strftime("%Y-%m-%d")
        target_dir = base / day
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        suffix = SUFFIX_BY_SOURCE.get(source, ".txt")
        name = f"{datetime.now().strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}{suffix}"
        path = target_dir / name

        # O_EXCL：独占创建，防预先埋好的软链接把写入引到别处（同 DSH spill-local）
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)

        _expire(base)
        return path
    except Exception:
        return None


def _expire(base: Path) -> None:
    """删掉过期的按天目录。惰性执行：每次落盘顺手扫一遍（spill 不频繁）。"""
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    for stale in entry.iterdir():
                        stale.unlink(missing_ok=True)
                    entry.rmdir()
            except OSError:
                continue
    except OSError:
        return
