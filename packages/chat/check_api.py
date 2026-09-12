"""把所有 HTTP 路由真调一遍，任何一个 5xx 就算失败。

**为什么需要这个**：前端那套 smoke 测试把 fetch 全 stub 掉了 —— 它验的是"界面在
给定数据下渲染对不对"，所以**后端某个接口 500 它照样全绿**。实际就被咬过两次：
删 import 时漏掉 session_store / workspace_store，以及漏掉 list_providers
（后者让 /api/providers 一直 500，表现是**网页上模型选择器整个用不了**，而且从
重构那次起坏了好几轮都没人发现，因为缺 import 只在路由真的被执行时才炸）。

    python packages/chat/check_api.py

用临时目录当 storage，**不碰真实数据**；不会调用真实模型的接口只有
/api/chat（它确实会调一次，用默认供应商）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 必须在 import chat.* 之前设好：storage 位置是模块级读的
_TMP_STORAGE = tempfile.mkdtemp(prefix="check-api-")
os.environ["CHAT_STORAGE"] = _TMP_STORAGE

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def main() -> int:
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        print("需要 httpx（TestClient 依赖它）：pip install httpx")
        return 2

    import chat.app as app_module

    # raise_server_exceptions=False 是关键：默认情况下路由里抛的异常会被 TestClient
    # **重新抛出来**，脚本直接崩掉、看不到是哪条路由坏了。浏览器看到的是 500，
    # 这里也要按 500 来判 —— 顺带把异常文本打出来，省得再去翻服务日志。
    client = TestClient(app_module.app, raise_server_exceptions=False)

    print("=== 每个路由都不该 5xx ===")
    simple = [
        ("GET", "/"),
        ("GET", "/api/health"),
        ("GET", "/api/providers"),
        ("GET", "/api/workspaces"),
        ("GET", "/api/sessions"),
        ("GET", "/api/browse"),
    ]
    for method, path in simple:
        response = client.request(method, path)
        detail = f"HTTP {response.status_code}"
        if response.status_code >= 500:
            detail += f"  {response.text[:120]}"
        check(f"{method} {path}", response.status_code < 500, detail)

    # 写操作与带 id 的读操作：用临时 storage，随便造
    response = client.post("/api/workspaces", json={"parent": _TMP_STORAGE, "name": "probe"})
    check("POST /api/workspaces", response.status_code < 500, f"HTTP {response.status_code}")

    session_id = "check-api-session"
    response = client.post(
        "/api/chat",
        json={"sessionId": session_id, "messages": [{"role": "user", "content": "hi"}],
              "tools_enabled": False},
    )
    # 会真调一次模型，所以不要求 200 —— 只要不是 5xx 崩掉就说明路由本身是通的
    check("POST /api/chat（会真调一次模型）", response.status_code < 500,
          f"HTTP {response.status_code}")

    for method, path in [("GET", f"/api/sessions/{session_id}"),
                         ("GET", "/api/sessions/does-not-exist"),
                         ("DELETE", "/api/sessions/does-not-exist"),
                         ("DELETE", "/api/workspaces/ws-nope")]:
        response = client.request(method, path)
        check(f"{method} {path}", response.status_code < 500, f"HTTP {response.status_code}")

    print("\n=== 模型选择器要拿到的东西 ===")
    payload = client.get("/api/providers").json()
    check("/api/providers 有供应商列表", bool(payload.get("providers")),
          f"{len(payload.get('providers') or [])} 个")
    check("/api/providers 有默认供应商/模型",
          bool(payload.get("default_provider")) and bool(payload.get("default_model")),
          f"{payload.get('default_provider')}/{payload.get('default_model')}")
    check("默认供应商真的在列表里",
          any(p["provider"] == payload.get("default_provider") for p in payload["providers"]))
    default_entry = next(
        (p for p in payload["providers"] if p["provider"] == payload.get("default_provider")), None
    )
    check("默认模型真的是它的一个模型",
          bool(default_entry) and any(
              m["id"] == payload.get("default_model") for m in default_entry["models"]
          ))
    check("每个供应商都有模型可列",
          all(p.get("models") for p in payload["providers"]))
    check("/api/providers 没漏出密钥字段",
          not any("api_key" in str(p) for p in payload["providers"]))

    print()
    if failures:
        print(f"❌ {len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("✅ 所有路由都能正常响应")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)
    raise SystemExit(code)
