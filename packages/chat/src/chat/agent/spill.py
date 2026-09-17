"""工具输出溢出（spill）：全文写盘，内联只留预览 + 定位符。

形制取自 DSH 的 spill capability（packages/spill/）——它一句话说清了这件事：

    persists oversized tool output and replaces the inline result with a
    bounded preview and retrieval locator

落在**工作区里**（`<workspace>/.chat-spill/<天>/`），不是系统临时目录。这条是本文
件最要紧的决定，理由不是整洁而是**可达性**：

    定位符告诉模型"用 read_file 分页读这个路径"。而 read_file 受沙箱围栏管，
    workspace-write 下**只能读工作区里的东西** —— 以前它指向 /tmp，模型照着做必然
    吃到 "file access denied"，只能退回 run_bash 去 cat/grep，而**每条 run_bash 都要
    用户批一次**。结果就是：每次想看被截断的长输出，都得再多批一条命令。

放进工作区之后 read_file 直接能读（不需要审批），那条提示语才真的成立。

目录名带点（`.chat-spill`）：不出现在 `ls` 里，也不出现在"添加工作区"的目录选择器
里（那个接口会过滤点开头的条目）。

失败一律 fail-soft：拿不到可写位置就返回 None，调用方退回纯截断，绝不能把一次
成功的命令执行变成失败（同 DSH 的 spill-policy）。
"""
from __future__ import annotations

import os
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path


DIR_NAME = ".chat-spill"
KEEP_DAYS = 7

SUFFIX_BY_SOURCE = {
    "run_bash": ".log",
}


def _temp_base() -> Path:
    return Path(tempfile.gettempdir()) / DIR_NAME


def spill_dir(root: str | Path | None = None) -> Path:
    """spill 的根目录：给了工作区就放工作区里，否则退回系统临时目录。

    退回那条是给"没有工作区"的调用留的（CLI 随手调、评估）—— 那种场合没有围栏，
    也就不存在"读不回来"的问题。
    """
    if root is None:
        return _temp_base()
    try:
        workspace = Path(root).expanduser().resolve()
        base = workspace / DIR_NAME
        # 防软链接逃逸：`.chat-spill` 若是预先埋好的软链接（agent 在 full-access 下
        # 有埋它的能力），真身就落到工作区外了 —— 那等于绕过围栏。和 sandbox.py 一样
        # 按 canonical 路径判断，不合法就退回临时目录（fail-soft，不是报错）。
        base.resolve().relative_to(workspace)
        return base
    except (OSError, ValueError):
        return _temp_base()


def save(text: str, source: str, root: str | Path | None = None) -> Path | None:
    """把全文原样落盘，返回路径；任何失败都返回 None。"""
    try:
        base = spill_dir(root)
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
