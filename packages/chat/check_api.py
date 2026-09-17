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
