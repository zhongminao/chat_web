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
from chat.agent.sandbox import DEFAULT_SANDBOX_MODE, VALID_SANDBOX_MODES, normalize_mode
from chat.runtime import (
    CLI_SESSION_FILE,
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER,
    SESSION_DIR,
    STORAGE_DIR,
    ChatMessage,
    TurnRequest,
    elapsed_ms,
    ensure_default_workspace,
    ensure_runtime_env,
    request_real_reply,
    resolve_system_prompt,
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
    parser.add_argument("--sandbox", choices=VALID_SANDBOX_MODES, default=DEFAULT_SANDBOX_MODE,
                        help="工具沙箱模式（每轮可改）")
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


def resolve_tools_enabled(session_id: str, requested: bool) -> tuple[bool, bool]:
    """这场会话**实际**用哪个工具开关：说过话的以日志里记的为准。

    返回 (生效值, 是否和请求的不一致)。服务端对中途改动是 409 拒绝，CLI 不把用户
    拦在门外 —— 直接沿用记录值并说明一句。
    """
    if session_store.turn_count(SESSION_DIR, session_id) > 0:
        recorded = session_store.load_settings(SESSION_DIR, session_id).get("toolsEnabled")
        if recorded is not None:
            return bool(recorded), bool(recorded) != requested
    return requested, False


def run_turn(
    session_id: str,
    message: str,
    *,
    workspace: dict,
    provider: str,
    model_name: str,
    system_prompt: str | None,
    tools_enabled: bool,
    sandbox_mode: str,
) -> str:
    """跑一轮，写进会话日志，返回模型的回复。"""
    prior_messages = [
        ChatMessage(**record) for record in session_store.load_history(SESSION_DIR, session_id)
    ]
    request = TurnRequest(
        messages=[ChatMessage(role="user", content=message)],
        provider=provider,
        model_name=model_name,
        system_prompt=system_prompt,
        tools_enabled=tools_enabled,
        sandbox_mode=normalize_mode(sandbox_mode),
    )

    started = time.monotonic()
    writer = session_store.TurnWriter(
        SESSION_DIR, session_id, workspace_id=workspace.get("id"))
    try:
        # 提示词在**跑之前**解析并落盘（跟 web 一个做法）：这样取消/失败的那一轮也
        # 记得住用的是哪份。**只在变了的时候才写** —— 没变就沿用上一轮记下的。
        meta = {"provider": provider, "model": model_name, "toolsEnabled": tools_enabled,
                "sandboxMode": normalize_mode(sandbox_mode)}
        meta.update(session_store.system_prompt_meta(
            SESSION_DIR, session_id,
            resolve_system_prompt(system_prompt, tools_enabled)))
        writer.begin(meta, [{"role": "user", "content": message}])
        result = request_real_reply(
            request, prior_messages, workspace.get("root"), writer=writer,
            observed_context_id=f"session:{session_id}")
    except KeyboardInterrupt:
        # SIGINT 直接打断阻塞中的模型调用，所以 CLI 不需要协作式取消令牌。
        # loop 已经在自己的 finally 里给没跑完的调用补了带真实原因的合成结果。
        writer.finish(durationMs=elapsed_ms(started), error="interrupted by user (Ctrl-C)")
        raise
    except Exception as exc:
        # 失败不留痕这条规矩没变，但它现在由 load_history 扣住"没有产出的那一轮"
        # 来保证（user 已经在 begin() 写过了），不再靠"失败不写 user"。
        writer.finish(durationMs=elapsed_ms(started), error=str(exc))
        raise

    writer.finish(
        durationMs=elapsed_ms(started),
        temperature=result.temperature,
    )
    # 会话模式里日志就是状态，写失败不能静默 —— 这条契约以前写在 append_turn 的
    # docstring 里，但两个调用方都没检查返回值。现在检查了。
    if writer.failed:
        raise OSError("会话日志写入失败")
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

    tools_enabled, overridden = resolve_tools_enabled(session_id, args.tools)
    sandbox_mode = normalize_mode(args.sandbox)

    print(f"会话 {session_id}")
    print(f"工作区 {workspace.get('name')} → {workspace.get('root')}")
    # 把数据目录打出来：位置一旦解析错（比如没找到仓库根、落到包目录旁边），
    # 表现是"历史全是空的"，而这件事以前完全没有提示 —— 踩过。
    print(f"数据 {STORAGE_DIR}")
    print(f"模型 {args.provider}/{args.model}｜工具 {'开' if tools_enabled else '关'}"
          f"｜沙箱 {sandbox_mode}（每轮可改）")
    if overridden:
        print(f"  （你说的是 {'开' if args.tools else '关'}，但这场会话首轮定的是 "
              f"{'开' if tools_enabled else '关'}，沿用日志里的值；要改请开新会话）")
    print()

    def one_round(text: str) -> None:
        try:
            reply, steps = run_turn(
                session_id, text,
                workspace=workspace,
                provider=args.provider,
                model_name=args.model,
                system_prompt=args.system,
                tools_enabled=tools_enabled,
                sandbox_mode=sandbox_mode,
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
