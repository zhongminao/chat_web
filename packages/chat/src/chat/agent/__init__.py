"""agent 能力：工具调用循环 + 观测守卫 + 溢出存储 + 会话日志。

    run_agent_turn  多轮循环：模型想用工具 → 执行 → 回喂 → 直到直接说话
    TOOL_SCHEMAS    工具清单（发给模型的说明书）
    execute_tool    工具分发（工具名 + 参数 JSON 字符串 + root → 结果文本）
    make_executor   把 root 绑进一个执行回调，交给 run_agent_turn
    session_store   会话日志：一个会话一个 append-only JSONL，历史用重放得到
    workspace_store 工作区登记表：agent 被允许活动的目录（将来沙箱的边界）
    cancel          轮次表 + 取消令牌：让另一个请求能打断正在跑的那一轮
"""

from chat.agent import cancel, session_store, workspace_store
from chat.agent.cancel import REGISTRY, CancelToken, TurnCancelled
from chat.agent.loop import run_agent_turn
from chat.agent.tools import TOOL_SCHEMAS, execute_tool, make_executor

__all__ = [
    "run_agent_turn",
    "TOOL_SCHEMAS",
    "execute_tool",
    "make_executor",
    "session_store",
    "workspace_store",
    "cancel",
    "REGISTRY",
    "CancelToken",
    "TurnCancelled",
]
