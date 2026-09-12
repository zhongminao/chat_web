"""chat_agent —— 自包含 LLM 客户端库（零项目依赖，可独立安装使用）。

由 IM_Opt 的 LLM/util/openai_client.py 与 graph/util/dialogue/conversation_session.py
合并精简而来，两个文件合并为一，消除重复的 _copy_message 与未使用的流式解析代码。
"""

from chat_agent.openai_client import (
    Client,
    ConversationSession,
    create_client,
    get_model_temperature,
    list_providers,
    load_provider_catalog,
    resolve_provider_config,
)

__all__ = [
    "Client",
    "ConversationSession",
    "create_client",
    "get_model_temperature",
    "list_providers",
    "load_provider_catalog",
    "resolve_provider_config",
]
