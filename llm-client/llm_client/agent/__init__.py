"""agent 能力：四个基础工具（read/write/edit/bash）+ 多轮工具调用循环。

对外只暴露三个名字：
    run_agent_turn  多轮循环：模型想用工具 → 执行 → 回喂 → 直到直接说话
    TOOL_SCHEMAS    工具清单（发给模型的说明书）
    execute_tool    工具分发（工具名 + 参数 JSON 字符串 → 结果文本）
"""
from llm_client.agent.loop import run_agent_turn
from llm_client.agent.tools import TOOL_SCHEMAS, execute_tool

__all__ = ["run_agent_turn", "TOOL_SCHEMAS", "execute_tool"]
