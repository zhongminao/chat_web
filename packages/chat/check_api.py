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

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# 必须在 import chat.* 之前设好：storage 位置是模块级读的
_TMP_STORAGE = tempfile.mkdtemp(prefix="check-api-")
os.environ["CHAT_STORAGE"] = _TMP_STORAGE

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# 后端响应的**形状契约**：这一份进 git，两侧都对着它断言。
#   后端这边：本脚本真调接口，把响应的形状和它比
#   前端那边：packages/frontend/smoke.mjs 拿自己的 stub 和它比
# 后端改了字段 → 这里红（契约过期）→ 你跑 --update 更新 → 前端 smoke 拿到新形状 →
# 前端扛不住就红。**改契约因此是一个必须显式做的动作**，而不是悄悄发生的漂移。
CONTRACT_PATH = Path(__file__).resolve().parent / "backend-contract.json"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------------------
# 形状提取：值会变（id、时间戳、标题），形状不会
# ---------------------------------------------------------------------------
def _tag(shape) -> str:
    if isinstance(shape, dict):
        return "list" if "list" in shape else ("list" if "list_by_kind" in shape else "object")
    return str(shape)


def _merge(a, b):
    """合并两个形状（同一个位置出现的多种取值）。"""
    if a == b:
        return a
    # 空列表不携带元素形状，所以它和任何元素形状都合得来 —— 否则"这次 steps 是空数组"
    # 会变成一条假差异。
    if isinstance(a, dict) and "list" in a and a["list"] == "empty":
        return b
    if isinstance(b, dict) and "list" in b and b["list"] == "empty":
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        if "list" in a and "list" in b:
            return {"list": _merge(a["list"], b["list"])}
        if "list_by_kind" in a and "list_by_kind" in b:
            out = dict(a["list_by_kind"])
            for kind, shape in b["list_by_kind"].items():
                out[kind] = _merge(out[kind], shape) if kind in out else shape
            return {"list_by_kind": dict(sorted(out.items()))}
        merged = dict(a)
        for key, value in b.items():
            merged[key] = _merge(merged[key], value) if key in merged else value
        return dict(sorted(merged.items()))
    if isinstance(a, str) and isinstance(b, str):
        return "|".join(sorted(set(a.split("|")) | set(b.split("|"))))
    return "|".join(sorted({_tag(a), _tag(b)}))


def shape_of(value):
    """把一个 JSON 值压成形状。

    叶子 → 类型名（"str" / "int" / "bool" / "null"，同一位有多种时 "null|str"）
    对象 → {"键": 子形状}
    列表 → {"list": 元素形状}；元素是带 kind 的 dict 时按 kind 分组（items 正是这种，
          分组之后读起来是"user 有哪些键、step 有哪些键"，比一个并集清楚得多）
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        if not value:
            return {"list": "empty"}
        if all(isinstance(item, dict) and "kind" in item for item in value):
            by_kind: dict = {}
            for item in value:
                kind = item["kind"]
                by_kind[kind] = _merge(by_kind[kind], shape_of(item)) if kind in by_kind else shape_of(item)
            return {"list_by_kind": dict(sorted(by_kind.items()))}
        merged = shape_of(value[0])
        for item in value[1:]:
            merged = _merge(merged, shape_of(item))
        return {"list": merged}
    if isinstance(value, dict):
        return {key: shape_of(value[key]) for key in sorted(value)}
    return type(value).__name__


def _diff(actual, expected, path="$") -> list[str]:
    """比两个形状，返回人话形式的差异列表（空 = 一致）。"""
    if actual == expected:
        return []
    # 空列表不携带形状信息，两个方向都算一致（见 _merge 里同一句的理由）
    for side in (actual, expected):
        if isinstance(side, dict) and side.get("list") == "empty":
            return []
    if isinstance(actual, dict) and isinstance(expected, dict):
        if ("list" in expected) != ("list" in actual) or ("list_by_kind" in expected) != ("list_by_kind" in actual):
            return [f"{path}: 列表形态变了（{_tag(expected)} → {_tag(actual)}）"]
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                differences.append(f"{path}.{key}: 字段不见了")
            elif key not in expected:
                differences.append(f"{path}.{key}: 多出新字段（前端还不知道它）")
            else:
                differences.extend(_diff(actual[key], expected[key], f"{path}.{key}"))
        return differences
    return [f"{path}: {expected!r} → {actual!r}"]


def _seed_rich_session(base_dir: Path, session_id: str) -> None:
    """造一份**四种 kind 都出现**的会话日志，好把 items 的形状采全。

    直接写日志而不是走 HTTP：step / running 这两种条目要一轮真实的工具调用才会出现
    （要花钱调模型，而且内容不确定）。手写日志仍然经过 load_items 的真实代码路径，
    所以采到的形状是真的 —— 只是数据是造的。
    """
    records = [
        {"type": "session", "version": 1, "id": session_id, "createdAt": 0, "workspaceId": "ws-shape"},
        {"type": "turn", "time": 0, "provider": "p", "model": "m",
         "toolsEnabled": True, "systemPrompt": "x"},
        {"type": "user", "content": "问题"},
        {"type": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "run_bash", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "run_bash", "arguments": '{"command":"true"}'}},
            {"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"type": "tool", "tool_call_id": "c1", "content": "结果"},
        {"type": "tool", "tool_call_id": "c2", "content": "[bash request] {\"id\":\"bashreq-shape\",\"command\":\"true\",\"cwd\":\"/tmp\",\"timeout\":60}"},
        {"type": "plan", "time": 0, "todos": [
            {"content": "检查现状", "status": "completed"},
            {"content": "继续执行", "status": "in_progress"},
        ]},
        {"type": "assistant", "content": "回答"},
        {"type": "turn-end", "time": 0, "durationMs": 1, "temperature": 0.2, "error": None},
    ]
    path = Path(base_dir) / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


def _seed_pending_bash_request(base_dir: Path, session_id: str, request_id: str) -> None:
    records = [
        {"type": "session", "version": 1, "id": session_id, "createdAt": 0, "workspaceId": "ws-shape"},
        {"type": "turn", "time": 0, "provider": "p", "model": "m", "toolsEnabled": True,
         "sandboxMode": "workspace-write", "systemPrompt": "x"},
        {"type": "user", "content": "跑 true"},
        {"type": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "run_bash", "arguments": '{"command":"true"}'}}]},
        {"type": "tool", "tool_call_id": "c1",
         "content": f"[bash request] {{\"id\":\"{request_id}\",\"command\":\"true\",\"cwd\":\"/tmp\",\"timeout\":60}}"},
    ]
    path = Path(base_dir) / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


def _shape_of_chat_response(client, session_id: str):
    """采 /api/chat 响应的形状 —— 用**假 client 跑一轮真实的工具调用**。

    为什么不从响应模型 model_dump() 采：`ChatResponse.steps` 的类型是 `list[dict]`，
    它对元素**什么都没说**。拿一个手写的样本去填，采到的就是"我以为的形状"——
    第一版就漏了 `arguments`（loop.py 真实产出的是 tool/arguments/result/ok 四个键），
    而这一点恰恰是这个检查器最该防的毛病。

    换成跑一轮真调用：换成假 client 所以不花钱、结果确定，但**走的是真实代码路径**
    （app.py → runtime → loop.py → loop 里那个 steps.append），采到的形状是真的。
    """
    import chat.runtime as runtime

    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def request_assistant_message(self, messages, tools=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return ({"role": "assistant", "content": "", "tool_calls": [
                    {"id": "shape_probe", "type": "function",
                     "function": {"name": "run_bash", "arguments": '{"command":"true"}'}}]}, {})
            return {"role": "assistant", "content": "ok"}, {}

    original = runtime.create_client
    runtime.create_client = lambda **kwargs: _FakeClient(**kwargs)
    try:
        response = client.post("/api/chat", json={
            "sessionId": session_id,
            "messages": [{"role": "user", "content": "shape probe"}],
            "tools_enabled": True,
        })
    finally:
        runtime.create_client = original
    return shape_of(response.json())


def _capture_shapes(client, session_id: str) -> dict:
    """把前端依赖的接口的**形状**采下来。"""
    import chat.app as app_module
    from chat.runtime import SESSION_DIR

    original_resume = app_module.resume_after_approval
    app_module.resume_after_approval = lambda *args, **kwargs: "ok"
    try:
        _seed_pending_bash_request(SESSION_DIR, "shape-approve", "bashreq-approve")
        approve_shape = shape_of(
            client.post("/api/sessions/shape-approve/bash-requests/bashreq-approve/approve").json())
        _seed_pending_bash_request(SESSION_DIR, "shape-reject", "bashreq-reject")
        reject_shape = shape_of(
            client.post("/api/sessions/shape-reject/bash-requests/bashreq-reject/reject").json())
    finally:
        app_module.resume_after_approval = original_resume

    shapes = {
        "GET /api/providers": shape_of(client.get("/api/providers").json()),
        "GET /api/workspaces": shape_of(client.get("/api/workspaces").json()),
        "GET /api/sessions": shape_of(client.get("/api/sessions").json()),
        "GET /api/sessions/{id}": shape_of(client.get(f"/api/sessions/{session_id}").json()),
        "GET /api/browse": shape_of(client.get("/api/browse").json()),
        "POST /api/sessions/{id}/interrupt": shape_of(
            client.post(f"/api/sessions/{session_id}/interrupt").json()),
        "POST /api/chat": _shape_of_chat_response(client, session_id),
        "POST /api/sessions/{id}/sandbox": shape_of(
            client.post(f"/api/sessions/{session_id}/sandbox",
                        json={"sandboxMode": "workspace-write"}).json()),
        "POST /api/sessions/{id}/bash-requests/{requestId}/approve": approve_shape,
        "POST /api/sessions/{id}/bash-requests/{requestId}/reject": reject_shape,
    }
    return shapes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python packages/chat/check_api.py")
    parser.add_argument("--update", action="store_true",
                        help="把当前后端的响应形状写回 backend-contract.json（改契约时的显式动作）")
    args = parser.parse_args(argv)

    try:
        from fastapi.testclient import TestClient
    except ImportError:
        print("需要 httpx（TestClient 依赖它）：pip install httpx")
        return 2

    import chat.app as app_module
    # 形状断言要用这两个：直接读落盘的记录（断言 turn 里真有 systemPrompt）
    from chat.agent import session_store
    from chat.runtime import SESSION_DIR

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

    print("\n=== 中断接口 ===")
    # 没在跑的会话 → interrupted: false。**这不是错误**：点停止时那一轮可能刚好
    # 自己结束了，对用户来说结果一样。
    response = client.post(f"/api/sessions/{session_id}/interrupt")
    check("POST /api/sessions/{id}/interrupt", response.status_code < 500,
          f"HTTP {response.status_code}")
    check("没在跑的会话返回 interrupted: false",
          response.json().get("interrupted") is False, str(response.json()))
    # 前端刷新页面后靠这个字段决定要不要显示"停止"按钮
    check("GET /api/sessions/{id} 带 running 字段",
          "running" in client.get(f"/api/sessions/{session_id}").json(),
          str(client.get(f"/api/sessions/{session_id}").json().get("running")))

    print("\n=== 契约比对：后端响应的形状 vs backend-contract.json ===")
    #
    # 为什么需要这一节：前端那套 smoke 把 fetch **全 stub** 掉，它验的是"给定数据下
    # 渲染对不对" —— stub 的响应体是**手写的**，信的是前端的想象，不是后端的事实。
    # 所以后端把 items 里的 kind 改个名、把 settings 的键挪个位，两边不会有任何测试
    # 变红，只有用户点开网页才会发现。
    #
    # 现在这份形状契约进 git，两侧都对着它断言：后端改了字段这里就红，你跑 --update
    # 更新它，前端 smoke 随即拿到新形状、扛不住也红。**改契约成了必须显式做的动作。**
    _seed_rich_session(SESSION_DIR, "shape-probe")
    live_shapes = _capture_shapes(client, "shape-probe")

    if args.update:
        CONTRACT_PATH.write_text(
            json.dumps({
                "_comment": "后端响应的形状契约。由 check_api.py --update 生成；"
                            "两侧（check_api.py 与 frontend/smoke.mjs）都对着它断言。",
                "endpoints": live_shapes,
                # 取值域单独列出来：形状能说"这个位置是字符串"，说不出"只能是这几个之一"。
                # item_kind 是 load_items 能产出的种类 —— 前端那张 kind → 渲染 的映射表
                # 必须覆盖它，smoke 拿这个列表逐一渲染来验。
                "enums": {"item_kind": sorted(session_store.ITEM_KINDS)},
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"  ✍️  已写入 {CONTRACT_PATH.relative_to(CONTRACT_PATH.parent.parent.parent)}")
        print(f"      采到 {len(live_shapes)} 个接口的形状 + "
              f"{len(session_store.ITEM_KINDS)} 个 item kind")
        return 1 if failures else 0

    if not CONTRACT_PATH.exists():
        check("契约文件存在", False, f"缺 {CONTRACT_PATH.name} —— 跑 --update 生成")
    else:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        expected_shapes = contract["endpoints"]
        for endpoint, actual in live_shapes.items():
            want = expected_shapes.get(endpoint)
            if want is None:
                check(f"{endpoint} 在契约里", False, "契约里没有这个接口 —— 跑 --update")
                continue
            differences = _diff(actual, want, endpoint)
            check(f"{endpoint} 形状没变", not differences,
                  "；".join(differences[:3]) if differences else "")
        # 取值域：契约里的 item_kind 必须**等于**后端声明的集合（多一个少一个都算变）
        declared = sorted(session_store.ITEM_KINDS)
        recorded = sorted(contract.get("enums", {}).get("item_kind", []))
        check("契约里的 item_kind 取值域与后端声明一致（前端按它渲染）",
              declared == recorded,
              f"后端 {declared} vs 契约 {recorded} —— 跑 --update")

    print("\n=== 语义断言：契约表达不了的那些 ===")
    session = client.get("/api/sessions/shape-probe").json()
    check("running 是布尔（前端轮询靠它决定停不停）", isinstance(session.get("running"), bool))
    check("toolsLocked 是布尔（前端靠它置灰开关）", isinstance(session.get("toolsLocked"), bool))
    kinds = {item.get("kind") for item in session["items"]}
    check("items 的 kind 都在约定内",
          kinds <= session_store.ITEM_KINDS, str(sorted(kinds)))
    # /api/providers 给的是**拼好的两份成品**，不是模板碎片 —— 前端不许自己拼
    prov = client.get("/api/providers").json()
    check("providers 给了 tools-off / tools-on 两份成品提示词",
          bool(prov.get("system_prompt_plain")) and bool(prov.get("system_prompt_with_tools")),
          f"plain={len(prov.get('system_prompt_plain') or '')} 字, "
          f"with_tools={len(prov.get('system_prompt_with_tools') or '')} 字")
    check("开工具的那份确实更长（拼接真的生效了）",
          len(prov.get("system_prompt_with_tools") or "")
          > len(prov.get("system_prompt_plain") or ""))
    check("providers 不再漏出裸模板碎片（前端不该自己拼）",
          "tool_system_prompt" not in prov and "default_system_prompt" not in prov,
          str([k for k in prov if "prompt" in k]))
    # 落盘的 turn 记录必须带解析后的提示词 —— 但**只在它变化的那一轮**
    # （一千多字，每轮写一遍等于同一份信息存 N 遍；见 session_store.system_prompt_meta）。
    records = session_store.read_records(SESSION_DIR, session_id)
    turns = [r for r in records if r.get("type") == "turn"]
    recorded_prompts = [r["systemPrompt"] for r in turns if "systemPrompt" in r]
    check("第一轮一定记下 systemPrompt（此前没有可比的值）",
          bool(turns) and "systemPrompt" in turns[0],
          str([sorted(t) for t in turns]))
    check("没有变化的那一轮不再重复记（同一份信息不存 N 遍）",
          len(recorded_prompts) == 1,
          f"{len(turns)} 轮里记了 {len(recorded_prompts)} 次")
    # 更强的一条：**落盘的必须就是服务端该拼出的那一份**。
    # 下面这次 /api/chat 没开工具，所以它应当等于 tools-off 的那份成品。
    # 这条能抓住"记的是请求里的原值（None）而不是解析后的成品"这类错。
    from chat.runtime import default_system_prompt
    check("落盘的 systemPrompt == 服务端该拼出的那份（tools-off）",
          bool(recorded_prompts)
          and recorded_prompts[0] == default_system_prompt(tools_enabled=False),
          f"实得 {str(recorded_prompts[0])[:40]!r}" if recorded_prompts else "没有记过")

    # ── 换一份提示词 → 必须新记一次；再发同一份 → 不再记 ────────────────────────
    client.post("/api/chat", json={
        "sessionId": session_id, "messages": [{"role": "user", "content": "换个提示词"}],
        "tools_enabled": False, "system_prompt": "换了一份提示词"})
    turns = [r for r in session_store.read_records(SESSION_DIR, session_id) if r.get("type") == "turn"]
    check("换提示词的那一轮会新记一次",
          len(turns) >= 2 and turns[1].get("systemPrompt") == "换了一份提示词",
          str(turns[1].get("systemPrompt")) if len(turns) >= 2 else "没有第二轮")
    client.post("/api/chat", json={
        "sessionId": session_id, "messages": [{"role": "user", "content": "还是同一份"}],
        "tools_enabled": False, "system_prompt": "换了一份提示词"})
    turns = [r for r in session_store.read_records(SESSION_DIR, session_id) if r.get("type") == "turn"]
    check("同一份提示词再发一轮，不再记（沿用）",
          len(turns) >= 3 and "systemPrompt" not in turns[2],
          str(sorted(turns[2])) if len(turns) >= 3 else "没有第三轮")
    check("回溯能取到当前在用的那份（load_settings）",
          session_store.load_settings(SESSION_DIR, session_id).get("systemPrompt") == "换了一份提示词",
          str(session_store.load_settings(SESSION_DIR, session_id).get("systemPrompt"))[:40])
    check("turn-end 里不再重复记 systemPrompt（一份就够）",
          all("systemPrompt" not in r
              for r in records if r.get("type") == "turn-end"))
    # **纯聊天（不开工具）也必须把回复落盘。**
    # 上面那次 /api/chat 是 tools_enabled=False，所以它走的是"不走循环"那条路 ——
    # 那条路曾经漏了落盘（writer.record 只在 loop.py 里被调用），后果是日志里只有
    # user + turn-end：模型记不住自己说过什么，前端"以日志为准重渲染"还会把回复弄丢。
    check("纯聊天也落了 assistant（不开工具时回复不能丢）",
          any(r.get("type") == "assistant" for r in records),
          str([r.get("type") for r in records]))
    check("纯聊天的历史能把 assistant 轮重放出来",
          any(m["role"] == "assistant" for m in session_store.load_history(SESSION_DIR, session_id)),
          str([m["role"] for m in session_store.load_history(SESSION_DIR, session_id)]))

    # ── 沙箱模式：拨开关 = 写一条事件；执行侧每条命令现折一遍 ────────────────────
    #
    # 这一节钉的是一个**真实踩过的 bug**：模式原先只能靠下一轮 /api/chat 的 payload
    # 带上来，而恢复的循环（resume_after_approval）读的是"这一轮开始时"的 turn 快照。
    # 于是 workspace-write 下每调一次 run_bash 就生成一条待审批 → 停住 → 人改成
    # full-access → 恢复读到的还是旧值 → 又生成一条待审批。**改多少次模式都没用**，
    # 因为那一刻服务端根本没有"你想改用什么"这条信息。
    #
    # 修法照 DSH 的 `sandbox/mode`：开关**就是**一条事件，当前值 = 按位置折出的最后一条，
    # 执行侧每次操作边界折一遍。下面四层各钉一环：接口写了事件 / 折出来的值变了 /
    # 同一个 executor 不重建就按新值走 / 每步重投影给模型的是"此刻"。
    import tempfile as _tempfile

    import chat.runtime as runtime
    from chat.agent import observed as _observed
    from chat.agent.loop import run_agent_turn as _run_agent_turn
    from chat.agent.tools import (
        BASH_REQUEST_PREFIX as _BASH_REQ,
        TOOL_ERROR_PREFIX as _TOOL_ERR,
        make_executor as _make_executor,
    )
    from chat.runtime import session_sandbox_mode as _mode_of

    print("\n=== 沙箱模式：拨开关即事件，执行侧每次折日志 ===")
    mode_session = "web-1789000000000-mode01"
    client.post("/api/chat", json={
        "sessionId": mode_session, "messages": [{"role": "user", "content": "建一场会话"}],
        "tools_enabled": False, "sandbox_mode": "workspace-write"})
    check("没有开关事件时，折出来的是这一轮 payload 带的模式",
          _mode_of(mode_session) == "workspace-write", str(_mode_of(mode_session)))

    response = client.post(f"/api/sessions/{mode_session}/sandbox",
                           json={"sandboxMode": "full-access"})
    check("拨开关：POST /sandbox 记下一条事件",
          response.status_code == 200 and response.json().get("recorded") is True,
          f"HTTP {response.status_code} {str(response.json())[:60]}")
    events = [r for r in session_store.read_records(SESSION_DIR, mode_session)
              if r.get("type") == "sandbox"]
    check("这条事件真的在日志里（人在中途拨的开关有落点）",
          len(events) == 1 and events[0].get("mode") == "full-access", str(events))
    check("折出来的值跟着变 —— turn 快照不再说了算",
          _mode_of(mode_session) == "full-access", str(_mode_of(mode_session)))
    check("load_settings 也认这条事件（前端刷新后不会倒回旧值）",
          session_store.load_settings(SESSION_DIR, mode_session).get("sandboxMode") == "full-access",
          str(session_store.load_settings(SESSION_DIR, mode_session).get("sandboxMode")))
    check("它不产 item（前端不认识它，也不需要认识）",
          all(item.get("kind") in session_store.ITEM_KINDS
              for item in session_store.load_items(SESSION_DIR, mode_session)))
    check("它不进模型历史（log-only：状态由每步投影说，不由日志里那条事实说）",
          all(message.get("role") != "sandbox"
              for message in session_store.load_history(SESSION_DIR, mode_session)))
    check("模式非法时明说，不静默归一",
          client.post(f"/api/sessions/{mode_session}/sandbox",
                      json={"sandboxMode": "host-root"}).status_code == 400)
    unknown = client.post("/api/sessions/web-1789000000000-mode02/sandbox",
                          json={"sandboxMode": "full-access"})
    check("没说过话的会话：不落盘、也不造出会话文件",
          unknown.status_code == 200 and unknown.json().get("recorded") is False
          and not session_store.exists(SESSION_DIR, "web-1789000000000-mode02"),
          f"HTTP {unknown.status_code} {str(unknown.json())[:60]}")

    # 执行侧：**同一个 executor**、不重建，折到的就是此刻的值。
    probe_root = _tempfile.mkdtemp(prefix="check-sandbox-root-")
    executor = _make_executor(
        probe_root, _observed.REGISTRY.new_context("check-sandbox"),
        sandbox_mode=lambda: _mode_of(mode_session), audit_root=None)
    executed = executor("run_bash", '{"command":"true"}')
    check("此刻是 full-access → 同一条命令直接跑（不再生成审批请求）",
          not executed.startswith(_BASH_REQ), executed[:80])
    client.post(f"/api/sessions/{mode_session}/sandbox", json={"sandboxMode": "workspace-write"})
    paused = executor("run_bash", '{"command":"true"}')
    check("拨回 workspace-write → 同一个 executor 立刻按新值走（不必重建）",
          paused.startswith(_BASH_REQ), paused[:80])

    # 通知是**事件式**的：只在"权限相对模型以为的值变了"时说一句。
    # 为什么不常驻一句"现在是什么"：那必须每个请求重新注入（历史重建后上次那条不在里面），
    # 每轮白付一次 token，而且随时可能过期 —— 正是早先那个 bug 的形状。
    # 第 1 步里顺手把开关拨到 full-access —— 模拟"模型跑着的时候人改了权限"。
    seen: list[list[dict]] = []
    announced: list = [None]   # 已告知过模型的模式（None = 这次请求还没说过）

    class _ModeProbeClient:
        def __init__(self, **kwargs):
            self.calls = 0

        def request_assistant_message(self, messages, tools=None):
            self.calls += 1
            seen.append([dict(m) for m in messages])
            if self.calls == 1:
                session_store.append_sandbox_mode(SESSION_DIR, mode_session, "full-access")
            if self.calls < 3:
                return ({"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"m{self.calls}", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path":"nope.txt"}'}}]}, {})
            return {"role": "assistant", "content": "done"}, {}

    # 用 runtime.is_runtime_notice 认它 —— 这条通知**没有**额外字段（加了会被严格的服务端
    # 拒收），只能在文本层面认，所以"怎么认"必须只有一处实现，这里也顺便把它跑起来。
    def _notices(messages: list[dict]) -> list[str]:
        return [str(m.get("content") or "") for m in messages if runtime.is_runtime_notice(m)]

    probe_writer = session_store.TurnWriter(SESSION_DIR, mode_session, workspace_id="ws-shape")
    _run_agent_turn(
        _ModeProbeClient(),
        [{"role": "system", "content": "s"}, {"role": "user", "content": "干活"}],
        writer=probe_writer,
        execute_tool=_make_executor(probe_root, _observed.REGISTRY.new_context("check-sandbox-2"),
                                    sandbox_mode=lambda: _mode_of(mode_session), audit_root=None),
        refresh_messages=lambda history: runtime.project_policy_change(
            history, _mode_of(mode_session), "workspace-write", announced),
    )
    check("第 1 步**一句都不说**（值没变 → 一个 token 都不花）",
          len(seen) >= 1 and _notices(seen[0]) == [], str(_notices(seen[0]))[:120])
    check("一轮中途拨开关 → **下一步**说一句「从 X 改成 Y」，不说「现在是什么」",
          len(seen) >= 2 and _notices(seen[1]) == [
              'The sandbox policy changed from "workspace-write" to "full-access" '
              "(changed by the user)."],
          str(_notices(seen[1]))[:160] if len(seen) >= 2 else "只跑了一步")
    check("通知追加在末尾（不改系统提示词 → 不动模型侧的前缀缓存）",
          len(seen) >= 2 and _notices(seen[1]) and seen[1][-1].get("content") == _notices(seen[1])[0],
          str(seen[1][-1].get("content", ""))[:40] if len(seen) >= 2 else "只跑了一步")
    check("说过一次就不再重复塞（每一步都塞同样会打掉前缀缓存）",
          len(seen) >= 3 and _notices(seen[2]) == _notices(seen[1]) == _notices(seen[1])
          and len(_notices(seen[2])) == 1,
          f"第 3 步 {len(_notices(seen[2]))} 条" if len(seen) >= 3 else "只跑了两步")
    check("注入的通知认得出、且不会误伤用户真打的话",
          runtime.is_runtime_notice({"role": "user", "content": runtime.policy_change_note(
              "workspace-write", "full-access")})
          and not runtime.is_runtime_notice({"role": "user", "content": "The sandbox policy changed"})
          and not runtime.is_runtime_notice({"role": "user", "content": "你好"})
          and not runtime.is_runtime_notice({"role": "assistant", "content":
              runtime.policy_change_note("workspace-write", "full-access")}),
          "认不出来 → 以后统计/压缩消息的地方会把它当成用户消息")
    check("这条通知**不落盘**（它是「上一轮模式 vs 此刻折出来的值」的纯函数，两个输入都已在日志里）",
          not [r for r in session_store.read_records(SESSION_DIR, mode_session)
               if r.get("type") == "notice"],
          str([r.get("type") for r in session_store.read_records(SESSION_DIR, mode_session)]))
    check("它也不进模型历史、不产 item（重放不该把它当成历史里的一句话）",
          all(message.get("role") in ("user", "assistant", "tool")
              for message in session_store.load_history(SESSION_DIR, mode_session))
          and all(item.get("kind") in session_store.ITEM_KINDS
                  for item in session_store.load_items(SESSION_DIR, mode_session)))

    # 跨轮：**最常发生的情形**是"改在轮与轮之间"（拨了开关再发一句）—— 下一轮第一步必须
    # 说出来，而且第三轮不该再说一遍。这条钉的是 app.py 那个 baseline（上一轮记录的模式）。
    cross_seen: list[list[dict]] = []

    class _CrossClient:
        def __init__(self, **kwargs):
            pass

        def request_assistant_message(self, messages, tools=None):
            cross_seen.append([dict(m) for m in messages])
            return {"role": "assistant", "content": "好"}, {}

    cross_id = "web-1789000000000-cross"
    original_client = runtime.create_client
    runtime.create_client = lambda **kwargs: _CrossClient(**kwargs)
    try:
        # 顺序要像真人：**先拨开关、再发消息**（拨在那一轮之后就成了"下一轮的基准"，
        # 那一轮本身仍按旧模式跑 —— 这也是"日志折出来的值优先于 payload"那条规则）。
        for index, (turn_mode, text) in enumerate(
                (("workspace-write", "一轮"), ("full-access", "二轮"), ("full-access", "三轮"))):
            if index > 0:
                client.post(f"/api/sessions/{cross_id}/sandbox",
                            json={"sandboxMode": turn_mode})
            client.post("/api/chat", json={
                "sessionId": cross_id, "messages": [{"role": "user", "content": text}],
                "tools_enabled": True, "sandbox_mode": turn_mode})
    finally:
        runtime.create_client = original_client
    check("新会话第一轮：不说（本来就没有「变过」这回事）",
          len(cross_seen) >= 1 and _notices(cross_seen[0]) == [], str(_notices(cross_seen[0]))[:80])
    check("两轮之间拨开关 → 下一轮第一步说一句",
          len(cross_seen) >= 2 and _notices(cross_seen[1]) == [
              'The sandbox policy changed from "workspace-write" to "full-access" '
              "(changed by the user)."],
          str(_notices(cross_seen[1]))[:140] if len(cross_seen) >= 2 else "只跑了一轮")
    check("再下一轮没变 → 又是一句不说（不是每轮都塞）",
          len(cross_seen) >= 3 and _notices(cross_seen[2]) == [],
          str(_notices(cross_seen[2]))[:80] if len(cross_seen) >= 3 else "只跑了两轮")

    # 一批里两条 bash：**它一次申请两条（允许），人一条一条审，都审完才叫它一次**。
    #
    # 这是用户要的形状：一个一个审批（每条当场执行），最后才唤醒模型。反过来做（每批一条、
    # 叫它一次再申请下一条）也行得通，但那不是他要的 —— 见下面那组端到端断言。
    loop_seen: list[list[dict]] = []

    class _TwoBashClient:
        def __init__(self, **kwargs):
            self.calls = 0

        def request_assistant_message(self, messages, tools=None):
            self.calls += 1
            loop_seen.append([dict(m) for m in messages])
            return ({"role": "assistant", "content": "", "tool_calls": [
                {"id": "b1", "type": "function",
                 "function": {"name": "run_bash", "arguments": '{"command":"echo A"}'}},
                {"id": "b2", "type": "function",
                 "function": {"name": "run_bash", "arguments": '{"command":"echo B"}'}}]}, {})

    two_step = _run_agent_turn(
        _TwoBashClient(),
        [{"role": "system", "content": "s"}, {"role": "user", "content": "同一条命令跑两次"}],
        writer=session_store.TurnWriter(SESSION_DIR, mode_session, workspace_id="ws-shape"),
        execute_tool=_make_executor(probe_root, _observed.REGISTRY.new_context("check-two-bash"),
                                    sandbox_mode="workspace-write", audit_root=None),
    )
    two_results = [str(st.get("result") or "") for st in two_step[1]]
    check("一批两条 bash → **两条**都变成待批准请求（不人为限制它一次只能申请一条）",
          len(two_results) == 2 and all(r.startswith(_BASH_REQ) for r in two_results),
          str([r[:30] for r in two_results]))

    # 端到端：走**真实的 approve 路线**。上面几条钉的是机制，这一条钉的是
    # "用户报的那个现象真的没了"：暂停期间拨了开关，恢复的循环必须按新的跑；
    # 没拨的话必须**照旧**要一次审批（负控 —— 否则一个"永远 full-access"的假修法
    # 也能让上一条绿）。
    def _seed_pending(session_id: str, turn_time: int = 0) -> None:
        seeded = [
            {"type": "session", "version": 1, "id": session_id, "createdAt": 0,
             "workspaceId": "ws-shape"},
            # provider/model 用真名：审批后会走 get_model_temperature。
            {"type": "turn", "time": turn_time, "provider": "deepseek",
             "model": "deepseek-flash",
             "toolsEnabled": True, "sandboxMode": "workspace-write", "systemPrompt": "x"},
            {"type": "user", "content": "跑一下"},
            {"type": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "run_bash", "arguments": '{"command":"true"}'}}]},
            {"type": "tool", "tool_call_id": "c1",
             "content": '[bash request] {"id":"bashreq-resume","command":"true",'
                        '"cwd":"/tmp","timeout":60}'},
        ]
        (Path(SESSION_DIR) / f"{session_id}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in seeded) + "\n",
            encoding="utf-8")

    class _ResumeProbeClient:
        """恢复后的第一步：模型又要一条命令（这条命令就是"会不会再要审批"的探针）。"""

        def __init__(self, **kwargs):
            self.calls = 0

        def request_assistant_message(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return ({"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c2", "type": "function",
                     "function": {"name": "run_bash", "arguments": '{"command":"true"}'}}]}, {})
            return {"role": "assistant", "content": "好了"}, {}

    def _resume_and_last_tool(session_id: str) -> str:
        original = runtime.create_client
        runtime.create_client = lambda **kwargs: _ResumeProbeClient(**kwargs)
        try:
            response = client.post(
                f"/api/sessions/{session_id}/bash-requests/bashreq-resume/approve")
        finally:
            runtime.create_client = original
        check(f"approve 返回 200（{session_id}）", response.status_code == 200,
              f"HTTP {response.status_code} {response.text[:80]}")
        tools = [r for r in session_store.read_records(SESSION_DIR, session_id)
                 if r.get("type") == "tool"]
        return str(tools[-1].get("content") or "")

    _seed_pending("web-1789000000000-resume1")
    unchanged = _resume_and_last_tool("web-1789000000000-resume1")
    check("负控：没拨开关时，恢复后**照旧**要一次审批（不是永远放行）",
          unchanged.startswith(_BASH_REQ), unchanged[:80])

    _seed_pending("web-1789000000000-resume2")
    client.post("/api/sessions/web-1789000000000-resume2/sandbox",
                json={"sandboxMode": "full-access"})
    switched = _resume_and_last_tool("web-1789000000000-resume2")
    check("暂停期间拨到 full-access → 恢复后那条命令**真的执行了**（原 bug 的现场）",
          not switched.startswith(_BASH_REQ) and "[exit code: 0]" in switched,
          switched[:80].replace("\n", " ⏎ "))

    # ── thinking 模式的思维链必须原样回传 + 不恢复已经答完的轮次 ──────────────
    #
    # 这一节钉的是一个**真实事故**：一批里两条 bash 请求，批准第一条后模型已经给了最终
    # 答复；再批准第二条时服务端又去"恢复"这一轮 → 发出的请求以 assistant 结尾（没有新的
    # user 消息）→ DeepSeek 的 thinking 模式回
    #   400 The `reasoning_content` in the thinking mode must be passed back to the API.
    # 后果：命令其实跑了、结果也记了，但接口 500、界面上那条待审批永远批不动。
    #
    # 两处各自成环：① reasoning_content 抓/存/放全链路；② 恢复前先问"这一轮还停着吗"。
    from chat.openai_client import Client as _Client

    class _FakeSDKMessage:
        """假装是 OpenAI SDK 的 ChatCompletionMessage（非标准字段进 model_extra）。"""

        def __init__(self, content, tool_calls=None, extra=None):
            self.content = content
            self.tool_calls = tool_calls
            self.model_extra = extra or {}

    extracted = _Client._assistant_message_from_message(
        _FakeSDKMessage("答案", extra={"reasoning_content": "先想了一下"}))
    check("客户端把 reasoning_content 抓下来了（原来在这里就被丢掉）",
          extracted.get("reasoning_content") == "先想了一下", str(extracted))
    plain = _Client._assistant_message_from_message(_FakeSDKMessage("答案"))
    check("没有思维链时不硬塞这个字段（别的供应商不认识它）",
          "reasoning_content" not in plain, str(plain))

    think_id = "web-1789000000000-think"
    (Path(SESSION_DIR) / f"{think_id}.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
        {"type": "session", "version": 1, "id": think_id, "createdAt": 0, "workspaceId": "ws-shape"},
        {"type": "turn", "time": 0, "provider": "deepseek", "model": "deepseek-flash",
         "toolsEnabled": True, "sandboxMode": "workspace-write", "systemPrompt": "x"},
        {"type": "user", "content": "问题"},
        {"type": "assistant", "content": "答案", "reasoning_content": "先想了一下"},
    ]) + "\n", encoding="utf-8")
    replayed = session_store.load_history(SESSION_DIR, think_id)
    check("日志重放时带回来了（不然下一次请求就没有它可传）",
          replayed and replayed[-1].get("reasoning_content") == "先想了一下", str(replayed[-1]))
    from chat.runtime import ChatMessage, normalize_messages
    outgoing = normalize_messages([ChatMessage(**m) for m in replayed], "s", tools_enabled=True)
    check("实际发给模型的 messages 里也带着它（全链路通）",
          outgoing[-1].get("reasoning_content") == "先想了一下", str(outgoing[-1]))

    # 「这一轮还停着吗」：最后一条协议记录是 tool = 停着；是 assistant = 模型已经答完。
    paused_id = "web-1789000000000-paused"
    def _write_paused(last_type: str) -> None:
        records = [
            {"type": "session", "version": 1, "id": paused_id, "createdAt": 0, "workspaceId": "ws-shape"},
            {"type": "turn", "time": 0, "provider": "deepseek", "model": "deepseek-flash",
             "toolsEnabled": True, "sandboxMode": "workspace-write", "systemPrompt": "x"},
            {"type": "user", "content": "干活"},
            {"type": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "run_bash", "arguments": '{"command":"true"}'}}]},
            {"type": "tool", "tool_call_id": "c1",
             "content": '[bash request] {"id":"bashreq-p","command":"true","cwd":"/tmp","timeout":60}'},
        ]
        if last_type == "assistant":
            records.append({"type": "assistant", "content": "我答完了"})
        (Path(SESSION_DIR) / f"{paused_id}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")

    _write_paused("tool")
    check("停在工具结果上 → 认为「还停着」（可以继续跑）",
          session_store.turn_is_paused(SESSION_DIR, paused_id) is True)
    _write_paused("assistant")
    check("模型已经答完 → 认为「没停着」（不许再拉起这一轮）",
          session_store.turn_is_paused(SESSION_DIR, paused_id) is False)

    # 端到端：模型答完之后再批准那条**兄弟**请求 —— 结果照记，但不再恢复、也不再发请求。
    calls = {"n": 0}

    class _NeverCalledClient:
        def __init__(self, **kwargs):
            pass

        def request_assistant_message(self, messages, tools=None):
            calls["n"] += 1
            return {"role": "assistant", "content": "不该被调用"}, {}

    _write_paused("assistant")
    original_client2 = runtime.create_client
    runtime.create_client = lambda **kwargs: _NeverCalledClient(**kwargs)
    try:
        response = client.post(f"/api/sessions/{paused_id}/bash-requests/bashreq-p/approve")
    finally:
        runtime.create_client = original_client2
    body = response.json()
    check("答完之后再批准：200、结果记下、resumed=false",
          response.status_code == 200 and body.get("resumed") is False, str(body)[:120])
    check("而且**一次模型调用都没发**（这就是那个 400 的现场）", calls["n"] == 0, f"发了 {calls['n']} 次")
    check("结果仍然进了日志（命令跑了不能白跑）",
          any(r.get("type") == "bash-result" for r in session_store.read_records(SESSION_DIR, paused_id))
          and "".join(str(r.get("content") or "") for r in session_store.read_records(SESSION_DIR, paused_id)).count("[exit code: 0]") >= 1)

    # ── 一批里两条 bash 请求：**都答完才叫模型**（不许它在还有命令待批时收尾抽身）──
    #
    # 现场：用户让模型"同一条命令跑两次"，模型把两条塞进同一批。用户批准第一条 → 服务端
    # 立刻恢复 → 模型写完"回合在此暂停"就收尾走了，而第二条的审批窗口还开着、那一轮却
    # 已经结束（用户的原话："他就把对话完全抽出来了，审批窗口都还在"）。
    # 规则：批里还有没答复的请求 → 不恢复；命令本身不受影响，批准哪条哪条跑。
    batch_id = "web-1789000000000-batch"
    def _seed_batch() -> None:
        batch = [
            {"type": "session", "version": 1, "id": batch_id, "createdAt": 0,
             "workspaceId": "ws-shape"},
            {"type": "turn", "time": 0, "provider": "deepseek", "model": "deepseek-flash",
             "toolsEnabled": True, "sandboxMode": "workspace-write", "systemPrompt": "x"},
            {"type": "user", "content": "同一条命令跑两次"},
            {"type": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "run_bash", "arguments": '{"command":"echo A"}'}},
                {"id": "c2", "type": "function",
                 "function": {"name": "run_bash", "arguments": '{"command":"echo B"}'}}]},
            {"type": "tool", "tool_call_id": "c1",
             "content": '[bash request] {"id":"bashreq-batch-A","command":"echo A",'
                        '"cwd":"/tmp","timeout":60}'},
            {"type": "tool", "tool_call_id": "c2",
             "content": '[bash request] {"id":"bashreq-batch-B","command":"echo B",'
                        '"cwd":"/tmp","timeout":60}'},
        ]
        (Path(SESSION_DIR) / f"{batch_id}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in batch) + "\n", encoding="utf-8")

    batch_calls = {"n": 0}
    batch_messages: list[list[dict]] = []

    class _BatchClient:
        def __init__(self, **kwargs):
            pass

        def request_assistant_message(self, messages, tools=None):
            batch_calls["n"] += 1
            batch_messages.append([dict(m) for m in messages])
            return {"role": "assistant", "content": "两条都看到了"}, {}

    def _approve(request_id: str) -> dict:
        response = client.post(f"/api/sessions/{batch_id}/bash-requests/{request_id}/approve")
        return response.json() if response.status_code == 200 else {"http": response.status_code}

    _seed_batch()
    original_batch_client = runtime.create_client
    runtime.create_client = lambda **kwargs: _BatchClient(**kwargs)
    try:
        first = _approve("bashreq-batch-A")
        pending_after_first = session_store.pending_bash_requests(SESSION_DIR, batch_id)
        check("批里还有一条没批 → 第 1 条批准后**不叫模型**（不许它中途收尾抽身）",
              first.get("resumed") is False and batch_calls["n"] == 0,
              f"resumed={first.get('resumed')} 模型调用={batch_calls['n']}")
        check("但命令照样执行、结果照样记（执行不受影响）",
              "[exit code: 0]" in str(first.get("content")) and pending_after_first == ["bashreq-batch-B"],
              f"content={str(first.get('content'))[:40]!r} 还剩={pending_after_first}")
        check("界面这时仍会问第 2 条（它还是 pending）",
              any(i.get("kind") == "bash-request" and i.get("id") == "bashreq-batch-B"
                  and i.get("status") == "pending"
                  for i in session_store.load_items(SESSION_DIR, batch_id)))

        second = _approve("bashreq-batch-B")
        check("这一批都答完了 → 才叫模型，且只叫一次",
              second.get("resumed") is True and batch_calls["n"] == 1,
              f"resumed={second.get('resumed')} 模型调用={batch_calls['n']}")
        seen_contents = " ".join(str(m.get("content")) for m in (batch_messages[-1] if batch_messages else []))
        check("模型这一次能同时看到两份结果",
              "echo A" in seen_contents and "echo B" in seen_contents,
              seen_contents[-90:] if batch_messages else "没有请求记录")
    finally:
        runtime.create_client = original_batch_client

    # ── 「暂停」是一种真状态：这一轮没结束、能停、真结束才收尾 ──────────────
    #
    # 现场：暂停那一刻就写了 turn-end、还把取消令牌注销了 → 日志说"这一轮结束了"可它后面
    # 还在长；/interrupt 恒 false、界面 running 恒 false；审批面板占着输入端，于是
    # **审批期间根本没有可用的停止**（用户最早抱怨的就是这个）。
    # 现在：暂停不收尾、令牌留着；收尾只发生在"模型真的说完"或"人明确放弃"。
    pause_id = "web-1789000000000-pause1"
    pause_calls = {"n": 0}

    class _BashThenAnswer:
        """第一次叫它 → 请求一条 bash 命令（于是这一轮停在审批上）；之后 → 直接给答复。"""

        def __init__(self, **kwargs):
            pass

        def request_assistant_message(self, messages, tools=None):
            pause_calls["n"] += 1
            if pause_calls["n"] == 1:
                return ({"role": "assistant", "content": "", "tool_calls": [
                    {"id": "p1", "type": "function",
                     "function": {"name": "run_bash", "arguments": '{"command":"true"}'}}]}, {})
            return {"role": "assistant", "content": "好了"}, {}

    original_pause_client = runtime.create_client
    runtime.create_client = lambda **kwargs: _BashThenAnswer(**kwargs)
    try:
        paused_response = client.post("/api/chat", json={
            "sessionId": pause_id, "messages": [{"role": "user", "content": "跑一下"}],
            "tools_enabled": True, "sandbox_mode": "workspace-write"})
        pause_records = session_store.read_records(SESSION_DIR, pause_id)
        check("暂停时 /api/chat 正常回执（不是错误，也不是 interrupted）",
              paused_response.status_code == 200
              and paused_response.json().get("state") == "ok",
              f"HTTP {paused_response.status_code} {str(paused_response.json())[:60]}")
        check("暂停时**不写 turn-end**（这一轮没结束 —— 以前这里就写死了）",
              not any(r.get("type") == "turn-end" for r in pause_records),
              str([r.get("type") for r in pause_records]))
        check("暂停时仍有待批准请求", bool(session_store.pending_bash_requests(SESSION_DIR, pause_id)),
              str(session_store.pending_bash_requests(SESSION_DIR, pause_id)))
        check("暂停时 running=True（界面据此继续轮询、并显示「这一轮还在」）",
              client.get(f"/api/sessions/{pause_id}").json().get("running") is True,
              str(client.get(f"/api/sessions/{pause_id}").json().get("running")))

        # 暂停期间的"停止" = 放弃这一轮（以前那是死路：令牌注销了，没有循环在跑）
        interrupted = client.post(f"/api/sessions/{pause_id}/interrupt").json()
        check("暂停期间 /interrupt 真的管用（以前恒返回 false）",
              interrupted.get("interrupted") is True, str(interrupted))
        after = session_store.read_records(SESSION_DIR, pause_id)
        check("没答复的请求被记成 cancelled（每个声明的调用都有配对结果）",
              any(r.get("type") == "bash-result" and r.get("status") == "cancelled"
                  for r in after),
              str([(r.get("type"), r.get("status")) for r in after]))
        ends = [r for r in after if r.get("type") == "turn-end"]
        check("放弃时收尾一次，并写明原因",
              len(ends) == 1 and str(ends[0].get("error")).startswith("cancelled"),
              str([(r.get("error"), r.get("durationMs")) for r in ends]))
        check("令牌注销了 → 新的一轮不会被 409 挡住",
              client.get(f"/api/sessions/{pause_id}").json().get("running") is False
              and client.post("/api/chat", json={
                  "sessionId": pause_id, "messages": [{"role": "user", "content": "再来一句"}],
                  "tools_enabled": True,
                  "sandbox_mode": "workspace-write"}).status_code == 200,
              f"running={client.get(f'/api/sessions/{pause_id}').json().get('running')}")

        # 恢复跑完 → 才收尾，而且耗时是**整轮**的（暂停那几秒也算进去）
        resume_id = "web-1789000000000-pause2"
        _seed_pending(resume_id, turn_time=int(time.time() * 1000) - 30_000)
        pause_calls["n"] = 5      # 让假模型直接给答复（不再请求命令）
        approved = client.post(
            f"/api/sessions/{resume_id}/bash-requests/bashreq-resume/approve")
        check("批准后恢复跑完 → 200", approved.status_code == 200,
              f"HTTP {approved.status_code} {approved.text[:60]}")
        done = session_store.read_records(SESSION_DIR, resume_id)
        done_ends = [r for r in done if r.get("type") == "turn-end"]
        check("真结束时收尾一次，且耗时从**这一轮开始**算（≥30 秒）",
              len(done_ends) == 1 and int(done_ends[0].get("durationMs") or 0) >= 30_000,
              str([r.get("durationMs") for r in done_ends]))
    finally:
        runtime.create_client = original_pause_client

    # ── 会话 id：客户端生成的形状，服务端必须接受 ──────────────────────────────
    # 前端按一个**约定**生成 id（`web-<毫秒时间戳>-<6 位 base36>`，为了在
    # storage/sessions/ 里一眼看出是浏览器建的），而服务端有一条严格正则（它拿 id
    # 当文件名，是防路径穿越的）。两边本来靠注释维系。
    #
    # 尺子放在**能坏的那一侧**：服务端的正则一收紧，这里就红，而不是等用户发消息时
    # 收到一个 400。查的是"形状"（长度、字符集、结构），不是某一次的具体值。
    import re as _re
    frontend_id = f"web-{int(__import__('time').time() * 1000)}-abc123"
    check("服务端接受前端那套 id 形状（约定 vs 校验的耦合被盯住）",
          session_store.sanitize_id(frontend_id) == frontend_id,
          f"{frontend_id} 被拒了" if session_store.sanitize_id(frontend_id) is None else frontend_id)
    check("路径穿越仍然被拒（收紧正则时别把这条一起放开）",
          session_store.sanitize_id("../../etc/passwd") is None
          and session_store.sanitize_id("a/b") is None)

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
        code = main(sys.argv[1:])
    finally:
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)
    raise SystemExit(code)
