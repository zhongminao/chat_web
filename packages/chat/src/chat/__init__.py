"""chat —— LLM 客户端 + agent 运行时 + FastAPI 应用（一个包）。

分层（自上而下，依赖单向，别反过来）：
    app.py             FastAPI：会话、工作区、工具开关；把 agent 的产出落进日志
    agent/             工具调用循环、四个工具、观测守卫、spill、会话日志
    openai_client.py   provider 目录与无状态 Client（单次对话）
    providers.yaml     供应商/模型目录（随包分发，__file__ 相对加载）

**会话历史不在内存里。** 由 agent.session_store 的日志承载：日志即权威，历史靠重放
得到 —— 不另存一份会话对象，就不会出现"内存里的历史"和"落盘的记录"对不上。

源码用 src/ 布局（packages/chat/src/chat/）：包目录不挨着仓库里的其他目录，
"同名的普通目录被当成命名空间包、把真包遮蔽掉"这类事故就不可能发生
（这个坑踩过两次：llm_client 那次服务起不来，chat-agent 那次靠连字符躲）。
"""

from chat.openai_client import (
    Client,
    create_client,
    get_model_temperature,
    list_providers,
    load_provider_catalog,
    resolve_provider_config,
)

__version__ = "0.1.0"

__all__ = [
    "Client",
    "create_client",
    "get_model_temperature",
    "list_providers",
    "load_provider_catalog",
    "resolve_provider_config",
]
