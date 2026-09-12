"""假 client 驱动的闭环演示：让 agent 循环真实地"写一个文件再读回来"。

不调用任何真实模型/网络：FakeClient 按脚本扮演模型，
但 execute_tool 走 tools.py 的真实实现——文件是真实落盘的。

脚本剧情（路径一律用**相对**写法，演示 root 的作用）：
    第 1 轮 模型要 write_file（在演示目录里创建 hello.txt）
    第 2 轮 模型要 read_file （读回验证）
    第 3 轮 模型直接说话（任务结束）

最后几条断言专门守 root：工具必须在调用方给的目录里干活，且模型**不能**自己指定 root。
"""
import json
import shutil
from pathlib import Path

from chat_agent.agent import TOOL_SCHEMAS, make_executor, run_agent_turn

DEMO_DIR = Path("/tmp/agent_demo")      # 演示用的"工作区根"
DEMO_FILE = DEMO_DIR / "hello.txt"


class FakeClient:
    """模拟 chat_agent.Client：按脚本依次返回 assistant_message。

    真实 client 的 request_assistant_message 长一样：
    传入 (messages, tools)，返回 (assistant_message, metadata)。
    """

    def __init__(self, script):
        self._script = list(script)
        self.history_per_call = []  # 每次收到的 messages，供检查 tool_call_id 配对

    def request_assistant_message(self, messages, tools=None):
        self.history_per_call.append([dict(m) for m in messages])
        if not self._script:
            raise AssertionError("脚本用完了模型还想继续 → 循环没正确终止")
        return self._script.pop(0), {"finish_reason": "stop"}


def tool_call(call_id: str, name: str, args: dict) -> dict:
    """构造一条"模型想调工具"的 assistant 消息（跟真实返回结构一致）。"""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def main() -> None:
    # 清掉上次演示的痕迹，保证从零开始（write_file 会自动建父目录）
    shutil.rmtree(DEMO_FILE.parent, ignore_errors=True)

    script = [
        tool_call("call_1", "write_file", {"path": "hello.txt", "content": "Hello from agent loop\nline 2"}),
        tool_call("call_2", "read_file", {"path": "hello.txt"}),
        {"role": "assistant", "content": "已完成：创建了 hello.txt 并读取验证。"},
    ]
    fake = FakeClient(script)

    user_messages = [{"role": "user", "content": "请帮我写一个 hello.txt"}]
    reply, steps, protocol_messages = run_agent_turn(
        client=fake,
        messages=user_messages,
        tool_schemas=TOOL_SCHEMAS,
        # 相对路径都得落在这个目录里 —— 服务端传的是工作区的根，这里传演示目录
        execute_tool=make_executor(DEMO_DIR),
    )

    # ---- 结果展示 ----
    print("=== steps（agent 干了什么）===")
    for i, step in enumerate(steps, 1):
        print(f"  第{i}步 [{step['tool']}] ok={step['ok']}")
        print(f"      参数: {step['arguments']}")
        print(f"      结果: {step['result'][:100]}")

    print("\n=== 最终回复 ===")
    print(" ", reply)

    print("\n=== 文件真实落盘验证 ===")
    print(DEMO_FILE.read_text(encoding="utf-8"))

    # ---- 断言：闭环正确性 ----
    assert len(steps) == 2, "应该恰好执行 2 次工具"
    assert steps[0]["tool"] == "write_file" and steps[0]["ok"]
    assert steps[1]["tool"] == "read_file" and steps[1]["ok"]
    assert DEMO_FILE.exists()

    # 检查配对：第 2 次调用时，历史里应有一条 tool 消息，其 tool_call_id 与 call_1 一致
    second_call_history = fake.history_per_call[1]
    tool_msgs = [m for m in second_call_history if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[-1]["tool_call_id"] == "call_1", "tool_call_id 配对失败"

    # 返回值的第三个是 protocol_messages，落盘时原样写进会话日志 —— 历史能回放全靠它。
    # 断言它的**形状**：本轮新增的协议消息按顺序排，且以最终 assistant 文本结尾。
    # （这层以前没人测：demo 只是把它接出来就丢了，改成别的名字改坏了也不会响。）
    roles = [m["role"] for m in protocol_messages]
    assert roles == ["assistant", "tool", "assistant", "tool", "assistant"], f"协议消息序列不对: {roles}"
    assert protocol_messages[0].get("tool_calls"), "第一轮的调用声明丢了"
    assert protocol_messages[-1]["content"] == reply, "最后一条必须是最终回复，否则回放会缺结尾"

    # ---- 断言：root 真的生效（这条守的是一个曾经存在的静默 bug）----
    # 以前四个工具都按**进程 cwd** 解析，于是"工作区"在界面上能选、能分组，
    # 但 agent 始终在服务进程的目录里干活 —— 选了等于没选，而且不报错。
    executor = make_executor(DEMO_DIR)
    assert str(DEMO_DIR) in executor("run_bash", '{"command": "pwd"}'), "bash 没在给定的 root 里跑"
    assert "hello.txt" in executor("run_bash", '{"command": "ls"}'), "root 里看不到刚写的文件"
    # 模型自己塞 root 必须无效：基准目录只能由宿主决定
    sneak = executor("run_bash", json.dumps({"command": "pwd", "root": "/"}))
    assert str(DEMO_DIR) in sneak, "模型把自己的 root 塞进来了 —— 基准目录被模型劫持"
    print("\n✅ 闭环通过：写文件 → 读回验证 → 模型总结，tool_call_id 配对正确")


if __name__ == "__main__":
    main()
