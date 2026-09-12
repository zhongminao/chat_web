"""命令行入口：不经过网页使用 agent。

    python -m chat.run "把 workplace 里的 README 的标题改成 XXX"
    python -m chat.run -i              # 连着聊
    python -m chat.run -s <id> "接着上次说"   # 续某场会话（id 见 --list）

跟服务端**共用同一套核心**（chat/runtime.py）：同一份消息归一、同一份一轮逻辑、
同一份会话日志格式。所以 CLU 里聊的东西在网页侧栏里能看到，网页里聊的也能用
`-s <id>` 接着聊 —— 两边不是两套东西。

工作区：默认用登记表里那个默认工作区（跟网页的"新对话落在哪"一致），
`-w <id>` 可以换。工具就在那个工作区的根里干活（相对路径的基准、bash 的 cwd）。

工具开关：**一场会话内固定**。第一轮用 `--tools` 定下来，之后沿用日志里记的值 ——
因为关掉工具时 normalize_messages 会丢掉历史里的工具协议消息（静默截断历史），
所以服务端干脆拒绝中途改，CLI 照同一条规矩来。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from chat.agent import session_store
from chat.runtime import (
    CLI_SESSION_FILE,
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER,
    SESSION_DIR,
    ChatMessage,
    TurnRequest,
    effective_system_prompt,
    elapsed_ms,
    ensure_default_workspace,
    ensure_runtime_env,
    request_real_reply,
    resolve_workspace,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m chat.run",
        description="不经过网页使用 agent（工具在工作区的根里干活）",
    )
    parser.add_argument("message", nargs="*", help="这一轮要说什么；给了 -i 时可省略")
    parser.add_argument("-i", "--interactive", action="store_true", help="连续对话，直到输入 exit")
    parser.add_argument("-s", "--session", help="续聊指定会话（默认沿用上次 CLI 用的那场）")
    parser.add_argument("--new", action="store_true", help="开一场新会话")
    parser.add_argument("--tools", action="store_true", help="启用工具（只在首轮生效）")
    parser.add_argument("-w", "--workspace", help="工作区 id（默认：登记表里的默认工作区）")
    parser.add_argument("-p", "--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--system", help="系统提示词（默认用内置的那份）")
    parser.add_argument("--list", action="store_true", help="列出最近的会话然后退出")
    return parser.parse_args(argv)


def pick_session_id(args: argparse.Namespace) -> str:
    """会话 id 的顺位：--new > --session > 上次 CLI 用的那场 > 新生成一个。"""
    if args.new:
        return session_store.new_id()
    if args.session:
        return args.session
    if CLI_SESSION_FILE.exists():
        remembered = CLI_SESSION_FILE.read_text(encoding="utf-8").strip()
        if remembered:
            return remembered
    return session_store.new_id()


def remember_session_id(session_id: str) -> None:
    """记住这次用的会话，下次不带 -s 就接着它。失败不影响主流程。"""
    try:
        CLI_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        CLI_SESSION_FILE.write_text(session_id + "\n", encoding="utf-8")
    except OSError:
        pass


def print_sessions(limit: int = 15) -> None:
    sessions = session_store.list_sessions(SESSION_DIR)[:limit]
    if not sessions:
        print("还没有任何会话")
        return
    print(f"{'id':32s} {'轮数':>4s}  标题")
    for item in sessions:
        print(f"{item['id']:32s} {item['turns']:>4d}  {item['title']}")
    print("\n用 -s <id> 接着聊；网页里的会话也在上面。")


def print_steps(steps: list[dict]) -> None:
    for index, step in enumerate(steps, 1):
        mark = "✅" if step.get("ok") else "❌"
        print(f"  {mark} [{index}] {step['tool']} {step['arguments']}")
        result = str(step.get("result") or "").rstrip()
        # 只给个摘要：完整结果太长，真要看得去翻会话日志
        lines = result.splitlines()
        preview = "\n      ".join(lines[:6])
        if len(lines) > 6:
            preview += f"\n      …（还有 {len(lines) - 6} 行）"
        print(f"      {preview}")


def run_turn(
    session_id: str,
    message: str,
    *,
    workspace: dict,
    provider: str,
    model_name: str,
    system_prompt: str | None,
    tools_requested: bool,
) -> str:
    """跑一轮，写进会话日志，返回模型的回复。"""
    # 工具开关：说过的会话沿用日志里记的值（跟服务端拒绝中途改是同一条规矩）
    tools_enabled = tools_requested
    if session_store.turn_count(SESSION_DIR, session_id) > 0:
        stored = bool(session_store.load_settings(SESSION_DIR, session_id).get("toolsEnabled"))
        if stored != tools_requested:
            print(f"  （这场会话的工具开关已在首轮定为 {'开' if stored else '关'}，"
                  f"沿用日志里的值；要改请开新会话）")
        tools_enabled = stored

    prior_messages = [
        ChatMessage(**record) for record in session_store.load_history(SESSION_DIR, session_id)
    ]
    request = TurnRequest(
        messages=[ChatMessage(role="user", content=message)],
        provider=provider,
        model_name=model_name,
        system_prompt=system_prompt,
        tools_enabled=tools_enabled,
    )

    started = time.monotonic()
    try:
        result = request_real_reply(request, prior_messages, workspace.get("root"))
    except Exception as exc:
        # 跟服务端一样：失败**不往历史里写 user 消息**（否则重试会把同一句话追加两遍），
        # 只在 turn 记录里留一条 error + attempted 备查。
        session_store.append_turn(
            SESSION_DIR, session_id,
            user_messages=[], protocol=[],
            workspace_id=workspace.get("id"),
            meta={"error": str(exc), "attempted": [message]},
        )
        raise

    session_store.append_turn(
        SESSION_DIR, session_id,
        user_messages=[{"role": "user", "content": message}],
        protocol=result.protocol_messages,
        workspace_id=workspace.get("id"),
        meta={
            "provider": provider,
            "model": model_name,
            "temperature": result.temperature,
            "toolsEnabled": tools_enabled,
            "systemPrompt": effective_system_prompt(result.messages),
            "durationMs": elapsed_ms(started),
        },
    )
    return result.reply, result.steps


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    ensure_default_workspace()

    if args.list:
        print_sessions()
        return 0

    workspace = resolve_workspace(args.workspace)
    session_id = pick_session_id(args)
    remember_session_id(session_id)

    # 放在跑之前：环境变量（API key）要从 ~/.bashrc 读进来，跟服务端同一条路
    ensure_runtime_env(args.provider)

    print(f"会话 {session_id}")
    print(f"工作区 {workspace.get('name')} → {workspace.get('root')}")
    print(f"模型 {args.provider}/{args.model}｜工具 {'开' if args.tools else '关'}"
          f"（首轮定下，之后沿用）\n")

    def one_round(text: str) -> None:
        try:
            reply, steps = run_turn(
                session_id, text,
                workspace=workspace,
                provider=args.provider,
                model_name=args.model,
                system_prompt=args.system,
                tools_requested=args.tools,
            )
        except Exception as exc:
            print(f"❌ 这一轮失败：{exc}\n")
            return
        if steps:
            print(f"工具调用（{len(steps)} 步）：")
            print_steps(steps)
            print()
        print(f"{reply}\n")

    if args.interactive:
        print("连续对话模式：输入内容回车发送，输入 exit 或按 Ctrl-D 结束。\n")
        while True:
            try:
                text = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if text in {"exit", "quit"}:
                break
            if not text:
                continue
            one_round(text)
        print(f"会话已保存在 {SESSION_DIR / (session_id + '.jsonl')}")
        return 0

    message = " ".join(args.message).strip()
    if not message:
        print("没给要说的内容。用 -i 进交互模式，或直接：python -m chat.run \"你的问题\"")
        return 2
    one_round(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
