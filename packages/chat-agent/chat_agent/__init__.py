"""chat_agent —— LLM 客户端 + agent 运行时（零项目依赖，可独立安装使用）。

两部分：
    openai_client.py   provider 目录 / 无状态 Client（单次对话）
    agent/             工具调用循环、观测守卫、spill、会话日志

**会话历史不在这个包里。** 这里曾经有一个 ConversationSession（内存里的历史容器），
2026-09-12 删除 —— 改为由 agent.session_store 的日志承载：日志即权威，历史靠重放
得到，不再需要另存一份会话对象，也就不会出现"内存里的历史"和"落盘的记录"对不上。

由 IM_Opt 的 LLM/util/openai_client.py 与 graph/util/dialogue/conversation_session.py
合并精简而来（后者已随之删除）。
"""

from chat_agent.openai_client import (
    Client,
    create_client,
    get_model_temperature,
    list_providers,
    load_provider_catalog,
    resolve_provider_config,
)

__all__ = [
    "Client",
    "create_client",
    "get_model_temperature",
    "list_providers",
    "load_provider_catalog",
    "resolve_provider_config",
]
