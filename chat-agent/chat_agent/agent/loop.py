"""agent 循环：把"模型想用工具"变成多轮对话直到模型直接说话。

职责边界（刻意保持窄）：
- 只做循环控制：调模型 → 有 tool_calls 就逐个执行并回喂 → 直到纯文本回复；
- 不关心工具是什么：工具由 execute_tool 回调提供；
- 不负责提示词：system 消息由调用方（chat）拼好放进 messages；
- 无状态：history 是本次调用内部的局部列表，请求结束即丢；
  但会把本次新产生的协议消息放进 protocol_messages 返回，调用方可存下来跨请求回放，
  让模型下一轮仍能看到完整工具过程（assistant tool_calls + tool 结果对）。

execute_tool 契约（由 chat_agent/agent/tools.py 提供）：
    execute_tool(name: str, arguments_raw: str) -> str
    arguments_raw 是模型返回的 JSON 字符串；结果/错误都以文本返回，不抛异常。
    因为是"以文本返回"，成败只能靠 tools.TOOL_ERROR_PREFIX 前缀区分 ——
    steps 里的 ok 就是据此算的，不是"没有异常"。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

MAX_ROUNDS_DEFAULT = 12

def run_agent_turn(
    client: Any,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]] | None = None,
    execute_tool: Callable[[str, str], str] | None = None,
    max_rounds: int = MAX_ROUNDS_DEFAULT,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """跑完一轮 agent 任务。

    client: chat_agent 的 Client 实例（提供 request_assistant_message）。
    messages: 完整的对话消息（第一条通常是 system，由调用方拼好）。
    tool_schemas: 工具说明书列表；None → 用 tools.TOOL_SCHEMAS。
    execute_tool: 执行回调；None → 用 tools.execute_tool。
    max_rounds: 工具调用轮数上限，防模型无限循环。

    返回 (最终文本, steps, protocol_messages)：
    - steps: 工具调用流水账，每项 {"tool", "arguments", "result", "ok"}，供展示/审核；
    - protocol_messages: 本次运行**新产生**的协议消息（assistant 带 tool_calls 的消息 + 每条
      tool 结果消息 + 最终 assistant 文本消息），可直接追加进调用方的历史缓存，
      下次原样重发即可让模型"看到"本次完整工具过程。
    """
    if max_rounds < 1:
        raise ValueError("max_rounds 必须 >= 1")

    # 延迟 import：避免循环依赖，也允许 tools.py 还在迭代时 loop.py 先存在
    from chat_agent.agent import tools as _tools

    if tool_schemas is None:
        tool_schemas = _tools.TOOL_SCHEMAS
    if execute_tool is None:
        execute_tool = _tools.execute_tool

    history = list(messages)      # 局部历史：无状态，请求结束即丢
    steps: list[dict[str, Any]] = []
    protocol_messages: list[dict[str, Any]] = []   # 本次新增的协议消息，供跨请求回放

    for round_no in range(max_rounds):
        assistant_message, _ = client.request_assistant_message(
            messages=history,
            tools=tool_schemas,   # 每轮都带工具清单，模型才能继续调用
        )

        tool_calls = assistant_message.get("tool_calls")
        if not tool_calls:
            # 模型直接说话了 → 任务结束
            content = str(assistant_message.get("content") or "")
            protocol_messages.append({"role": "assistant", "content": content})
            return content, steps, protocol_messages

        # 不变式①：声明要调工具的消息必须原样进历史，
        # 否则服务端无法把后续 tool 结果与它配对。
        history.append(assistant_message)
        protocol_messages.append(assistant_message)

        for tc in tool_calls:
            # 用 .get 取值而不是 []：个别网关返回的字段可能缺失
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "")
            arguments_raw = str(fn.get("arguments") or "")

            try:
                result = execute_tool(name, arguments_raw)
                # execute_tool 从不抛异常，失败是以 TOOL_ERROR_PREFIX 开头的文本返回的，
                # 所以这里必须看文本。只写 ok = True 会让 ok 恒真 ——
                # 曾经如此，结果是按 ok 统计失败率永远得 0。
                ok = not result.startswith(_tools.TOOL_ERROR_PREFIX)
            except Exception as exc:
                # 防弹：单个工具失败不能弄死整个循环。
                # 错误文本回喂给模型，让它能根据原因自救/重试。
                result = f"{_tools.TOOL_ERROR_PREFIX}{exc}"
                ok = False

            steps.append(
                {
                    "tool": name,
                    "arguments": arguments_raw,
                    "result": result,
                    "ok": ok,
                }
            )

            # 不变式②：每个工具结果一条 tool 消息，tool_call_id 与上面配对
            tool_message = {
                "role": "tool",
                "tool_call_id": str(tc.get("id") or f"call_r{round_no}"),
                "content": result,
            }
            history.append(tool_message)
            protocol_messages.append(tool_message)

    # 达到轮数上限仍没结束（模型陷入工具循环）：
    # 不带工具再问一次，逼它给出最终答复。
    assistant_message, _ = client.request_assistant_message(messages=history)
    content = str(assistant_message.get("content") or "")
    protocol_messages.append({"role": "assistant", "content": content})
    return content, steps, protocol_messages
