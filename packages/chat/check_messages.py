"""校验：发给模型的消息形状 —— 尤其是「system 只允许出现在最前面」。

为什么单独一个脚本：这条不变式**已经坏过一次，而且坏得毫无征兆**。审批批准之后
恢复 loop 时，原来是在消息列表**末尾**再 append 一条 system（把"用户批准了这条
命令"当事件通知）。云端 API 宽容，照收；本地模型（vLLM 上的 Qwen）直接拒收：

    400 System message must be at the beginning.

后果的误导性在于**命令其实执行了、结果也落盘了**，只是模型接不上话，审批接口回
500。用户再点一次 → 又一条待审批 → 看起来就是"命令明明是对的，却一直要审批"。
所以这不是"请求失败"这种一眼能看见的错，而是"一半成功"的错。

三组断言：
  1. normalize_messages 自己：有提示词时恰好一条 system，且在位置 0；传 "" 时
     一条都没有；历史里混进来的 system 会被丢掉。
  2. with_system_note：折进开头那条之后**仍然**只有一条 system、仍在位置 0，
     而且内容真的被追加了（不是被覆盖掉）。
  3. 整条恢复路径的形状：拿一份"user → assistant(tool_calls) → tool"的历史，
     走一遍 normalize + note，断言任何位置 > 0 都没有 system。

不需要模型、不需要网络：直接调真实实现。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from chat.runtime import ChatMessage, normalize_messages, with_system_note

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'✅' if condition else '❌'} {label}" + (f"  {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def roles(messages: list[dict]) -> list[str]:
    return [str(message.get("role")) for message in messages]


def system_positions(messages: list[dict]) -> list[int]:
    return [index for index, role in enumerate(roles(messages)) if role == "system"]


def history() -> list[ChatMessage]:
    """一份典型历史：用户提问 → 助手声明调 run_bash → 工具结果（就是审批暂停点）。"""
    return [
        ChatMessage(role="user", content="看看工作区"),
        ChatMessage(role="assistant", content="", tool_calls=[
            {"id": "call_1", "type": "function",
             "function": {"name": "run_bash", "arguments": '{"command":"pwd"}'}},
        ]),
        ChatMessage(role="tool", content="$ pwd\n/home/zhong", tool_call_id="call_1"),
    ]


print("\n[1. normalize_messages 自己]")
plain = normalize_messages(history(), "你是助手。", tools_enabled=True)
check("有提示词时恰好一条 system", len(system_positions(plain)) == 1,
      f"实得 {system_positions(plain)}")
check("那条 system 在位置 0", system_positions(plain) == [0])
check("system 在位置 0 且是提示词内容", plain[0]["content"] == "你是助手。")

no_prompt = normalize_messages(history(), "", tools_enabled=True)
check("提示词传 \"\" 时一条 system 都不发", system_positions(no_prompt) == [],
      f"实得 {system_positions(no_prompt)}")

# 历史里混进来的 system（比如更早的日志里被别的路径写进去过）必须被丢掉
polluted = normalize_messages(
    [ChatMessage(role="system", content="旧的 system"), *history()],
    "你是助手。",
    tools_enabled=True,
)
check("历史里混进来的 system 会被丢掉（只剩开头那条）",
      system_positions(polluted) == [0] and all("旧的 system" not in str(m.get("content")) for m in polluted),
      f"实得 {system_positions(polluted)}")

print("\n[2. with_system_note：折进开头那条]")
note = "The user approved the pending bash command, and it has now run:\npwd"
noted = with_system_note(plain, note)
check("折进去之后仍然只有一条 system", len(system_positions(noted)) == 1,
      f"实得 {system_positions(noted)}")
check("而且仍在位置 0", system_positions(noted) == [0])
check("原提示词还在（是追加不是覆盖）", noted[0]["content"].startswith("你是助手。"))
check("事件说明也在", note in noted[0]["content"])
check("历史长度不变（只是改了第一条，没有多出一条消息）", len(noted) == len(plain),
      f"{len(plain)} → {len(noted)}")

print("\n[3. 整条恢复路径（真跑 resume_after_approval，用假 client 截获实际发出的消息）]")
#
# 这一节必须走**真实调用点**，不能拿上面的 helper 自己拼一遍形状 —— 第一版就是
# 那样写的，结果把修复还原成旧的 append 之后它照样全绿（helper 是好的，坏的是
# 调用点）。所以这里造一份真日志、把 create_client 换成会记录消息的假 client，
# 断言模型**实际收到**的东西。
import json
import tempfile

from chat import runtime
from chat.agent import session_store
from chat.agent.tools import BASH_REQUEST_PREFIX

storage = Path(tempfile.mkdtemp(prefix="chat-check-msgs-"))
runtime.STORAGE_DIR = storage
runtime.SESSION_DIR = storage / "sessions"
runtime.WORKSPACE_DIR = storage

SESSION_ID = "web-check-msgs"
REQUEST_ID = "bashreq-check1234"

writer = session_store.TurnWriter(runtime.SESSION_DIR, SESSION_ID, workspace_id="ws-check")
writer.begin(
    {
        "provider": "local_qwen", "model": "local_qwen",
        "toolsEnabled": True, "sandboxMode": "workspace-write",
        "systemPrompt": "你是助手。",
    },
    [{"content": "看看工作区"}],
)
writer.record({
    "role": "assistant", "content": "",
    "tool_calls": [{"id": "call_1", "type": "function",
                    "function": {"name": "run_bash", "arguments": '{"command":"pwd"}'}}],
})
writer.record({
    "role": "tool", "tool_call_id": "call_1",
    "content": BASH_REQUEST_PREFIX + json.dumps(
        {"id": REQUEST_ID, "command": "pwd", "cwd": str(storage), "timeout": 60,
         "note": "Not executed yet — this command is waiting for the user to approve it."}),
})
writer.finish(durationMs=1)
session_store.append_bash_result(
    runtime.SESSION_DIR, SESSION_ID, REQUEST_ID, status="executed",
    content="$ pwd\n/home/zhong/mydisk/tools/chat/workplace\n[exit code: 0]")

captured: list[dict] = []


class FakeClient:
    """假 client：把"实际要发给模型的消息"截下来，然后直接说一句话结束这一轮。"""

    def request_assistant_message(self, messages, tools=None):
        captured.append([dict(m) for m in messages])
        return {"role": "assistant", "content": "好的。"}, {}


runtime.ensure_runtime_env = lambda provider: None
runtime.create_client = lambda **kwargs: FakeClient()
runtime.resolve_workspace = lambda workspace_id: {"root": str(storage)}

reply = runtime.resume_after_approval(
    SESSION_ID,
    approval={"requestId": REQUEST_ID, "command": "pwd", "status": "executed"},
)

check("恢复路径真的调了模型（假 client 收到了请求）", len(captured) == 1,
      f"实得 {len(captured)} 次")
sent = captured[0] if captured else []
check("位置 > 0 没有 system —— 这正是本地模型 400 的那条",
      [i for i in system_positions(sent) if i != 0] == [],
      f"实得 system 位置 {system_positions(sent)}")
check("恰好一条 system 且在位置 0", system_positions(sent) == [0],
      f"实得 {system_positions(sent)}")
check("审批说明并进了那条 system", "The user approved the pending bash command" in str(sent[0].get("content")) if sent else False)
check("原提示词没被覆盖", str(sent[0].get("content", "")).startswith("你是助手。") if sent else False)
check("顺序仍是 system → user → assistant → tool",
      roles(sent) == ["system", "user", "assistant", "tool"], f"实得 {roles(sent)}")
# 顺带钉住"pending 被换成真实结果"这件事：模型看到的必须是执行输出，不是那张请求卡片
tool_content = str(sent[3].get("content")) if len(sent) > 3 else ""
check("模型看到的是执行结果，不是 pending 的请求卡片",
      "$ pwd" in tool_content and BASH_REQUEST_PREFIX.strip() not in tool_content,
      f"实得 {tool_content[:60]!r}")
check("恢复跑了并拿到回复", reply == "好的。", f"实得 {reply!r}")

print()
if failures:
    print(f"失败 {len(failures)} 项：" + "；".join(failures))
    sys.exit(1)
print("消息形状校验通过：system 只出现在最前面。")
