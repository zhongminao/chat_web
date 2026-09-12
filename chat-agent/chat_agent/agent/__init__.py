"""agent 能力：四个基础工具（read/write/edit/bash）+ 多轮工具调用循环。

对外暴露：
    run_agent_turn  多轮循环：模型想用工具 → 执行 → 回喂 → 直到直接说话
    TOOL_SCHEMAS    工具清单（发给模型的说明书）
    execute_tool    工具分发（工具名 + 参数 JSON 字符串 → 结果文本）
    write_trace     轨迹落盘（一次请求一个 JSONL 文件，供回看与 eval）
    session_store   会话日志（一个会话一个 append-only JSONL，历史用重放得到）
"""
from chat_agent.agent import session_store
from chat_agent.agent.loop import run_agent_turn
from chat_agent.agent.tools import TOOL_SCHEMAS, execute_tool
from chat_agent.agent.trace_log import write_trace

__all__ = ["run_agent_turn", "TOOL_SCHEMAS", "execute_tool", "write_trace", "session_store"]
