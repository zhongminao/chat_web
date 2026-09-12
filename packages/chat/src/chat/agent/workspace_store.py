"""工作区：agent 被允许活动的目录。

它是一个**实体**，不是路径的抄本：
- 有 id（由根路径哈希得来，同一个目录永远是同一个 id，不会重复登记）
- 有显示名（末端目录名）
- 有根路径

会话的 header 里记 `workspaceId` —— 那是**引用**，不是快照。区别在于：工作区改名
或搬家时，引用还能指对；抄一份路径就只能等它自己过期。

**这也是将来沙箱的边界。** 现在还没有任何东西读它来限制访问（run_bash 仍是
shell=True、能走到任何地方），登记表只是让"允许 agent 活动的根"这件事有个明确的
落点 —— 沙箱要判断的正是"目标路径在不在某个工作区的根下面"。

（曾经 `WORKSPACE_ROOT` 只是个常量，会话 header 里抄路径。2026-09-12 改成实体。）
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


REGISTRY_NAME = "workspaces.json"
DEFAULT_ID_PREFIX = "ws-"


def workspace_id(root: str | Path) -> str:
    """由根路径推出稳定的 id。同一目录永远得到同一个 id —— 所以重复登记会被
    by_root 找到而不是新增一条。"""
    resolved = str(Path(root).expanduser().resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]
    return f"{DEFAULT_ID_PREFIX}{digest}"


def display_name(root: str | Path) -> str:
    parts = [part for part in str(Path(root)).split("/") if part]
    return parts[-1] if parts else str(root)


def _registry_path(base_dir: Path | str) -> Path:
    return Path(base_dir) / REGISTRY_NAME


def load(base_dir: Path | str) -> list[dict[str, Any]]:
    try:
        with open(_registry_path(base_dir), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("workspaces") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def save(base_dir: Path | str, entries: list[dict[str, Any]]) -> None:
    path = _registry_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"workspaces": entries}, handle, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def build_entry(root: str | Path, name: str | None = None) -> dict[str, Any]:
    resolved = str(Path(root).expanduser().resolve())
    return {
        "id": workspace_id(resolved),
        "name": name or display_name(resolved),
        "root": resolved,
    }


def ensure(base_dir: Path | str, root: str | Path) -> dict[str, Any]:
    """登记一个工作区（已存在则原样返回）。用于启动时把默认根塞进登记表。"""
    entries = load(base_dir)
    resolved = str(Path(root).expanduser().resolve())
    for entry in entries:
        if entry.get("root") == resolved:
            return entry
    entry = build_entry(resolved)
    entries.append(entry)
    save(base_dir, entries)
    return entry


def by_id(base_dir: Path | str, target_id: str | None) -> dict[str, Any] | None:
    if not target_id:
        return None
    for entry in load(base_dir):
        if entry.get("id") == target_id:
            return entry
    return None


def by_root(base_dir: Path | str, root: str | Path) -> dict[str, Any] | None:
    resolved = str(Path(root).expanduser().resolve())
    for entry in load(base_dir):
        if entry.get("root") == resolved:
            return entry
    return None


def default(base_dir: Path | str) -> dict[str, Any] | None:
    """默认工作区 = 登记表里的第一条。列表为空时返回 None，由调用方 ensure。"""
    entries = load(base_dir)
    return entries[0] if entries else None
