from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


AUDIT_FILE = "bash-audit.jsonl"


def append(storage_dir: str | Path | None, event: dict[str, Any]) -> None:
    if storage_dir is None:
        return
    path = Path(storage_dir) / AUDIT_FILE
    record = {"time": time.time(), **event}
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        # Auditing must not make the user-facing tool fail. The command result is
        # still recorded in the session log through the normal tool message.
        return
