from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SandboxMode = Literal["full-access", "workspace-write"]
DEFAULT_SANDBOX_MODE: SandboxMode = "workspace-write"
VALID_SANDBOX_MODES: tuple[SandboxMode, ...] = ("workspace-write", "full-access")


@dataclass(frozen=True)
class SandboxPolicy:
    mode: SandboxMode = DEFAULT_SANDBOX_MODE
    workspace_root: Path | None = None


def normalize_mode(mode: str | None) -> SandboxMode:
    if mode == "full-access":
        return "full-access"
    return "workspace-write"


def canonical_root(root: str | Path | None) -> Path:
    if root is None:
        return Path.cwd().resolve()
    return Path(root).expanduser().resolve()


def _resolve_existing_or_parent(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except FileNotFoundError:
        missing: list[str] = []
        current = path
        while not current.exists():
            missing.append(current.name)
            parent = current.parent
            if parent == current:
                return path.resolve(strict=False)
            current = parent
        return current.resolve(strict=True).joinpath(*reversed(missing))


def resolve_target(path: str | Path, root: str | Path | None) -> Path:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = canonical_root(root) / target
    return _resolve_existing_or_parent(target)


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def check_path(path: str | Path, policy: SandboxPolicy) -> Path:
    target = resolve_target(path, policy.workspace_root)
    if policy.mode == "full-access":
        return target
    root = canonical_root(policy.workspace_root)
    if not is_under(target, root):
        raise PermissionError(f"file access denied under workspace-write mode: {target}")
    return target


def policy_for(mode: str | None, root: str | Path | None) -> SandboxPolicy:
    return SandboxPolicy(mode=normalize_mode(mode), workspace_root=canonical_root(root))
