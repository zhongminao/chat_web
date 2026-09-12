"""自包含 LLM 客户端库（零项目依赖）。

单次对话执行者：provider 解析 / api_key / 调用 OpenAI 兼容接口 / 解析。

**会话历史不在这里。** 这里曾经有一个 ConversationSession（服务端历史容器），
2026-09-12 删除 —— 改为把历史交给会话日志（chat_agent.agent.session_store），
日志即权威、历史由重放得到，不再需要另存一份内存里的会话对象。

合并自 IM_Opt 的 LLM/util/openai_client.py + graph/util/dialogue/conversation_session.py：
- 删除从未被调用的流式解析分支 _assistant_message_from_stream（stream 恒为 False）
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from openai import OpenAI


PROVIDERS_YAML = Path(__file__).resolve().parent / "providers.yaml"


# ============================================================
# 供应商目录
# ============================================================

def load_provider_catalog() -> dict[str, dict[str, Any]]:
    """读取 providers.yaml，返回 {provider: 配置项} 映射（字段说明见该文件）。"""
    with PROVIDERS_YAML.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        raise ValueError("providers.yaml: 缺少 'providers' 映射")
    return providers


def list_providers() -> list[dict[str, Any]]:
    """供前端模型选择器使用的扁平供应商列表，不含任何密钥信息。"""
    result: list[dict[str, Any]] = []
    for provider, entry in load_provider_catalog().items():
        models = [
            {
                "id": model["id"],
                "name": model.get("name", model["id"]),
                "temperature": float(model.get("temperature", 0.2)),
            }
            for model in entry.get("models", [])
        ]
        result.append(
            {
                "provider": provider,
                "display_name": entry.get("display_name", provider),
                "models": models,
            }
        )
    return result


def _provider_entry(provider: str) -> dict[str, Any]:
    normalized_provider = str(provider).strip().lower()
    entry = load_provider_catalog().get(normalized_provider)
    if entry is None:
        raise ValueError(f"Unsupported provider: {provider}")
    return entry


def resolve_provider_config(
    provider: str,
    model_name: str | None = None,
    ) -> dict[str, str | None]:
    """按 providers.yaml 解析单个请求的 api_key / base_url / model_name。

    - api_key: 优先环境变量 api_key_env，其次 api_key_default，都没有则为空
    - base_url: 设置 base_url_env 时优先读环境变量，否则用 yaml 的 base_url
    - model_name: 请求参数 > model_name_env 环境变量 > default_model > 列表第一个
    """
    entry = _provider_entry(provider)

    api_key = os.getenv(entry["api_key_env"]) if entry.get("api_key_env") else None
    if not api_key:
        api_key = entry.get("api_key_default")

    base_url = entry.get("base_url")
    if entry.get("base_url_env"):
        base_url = os.getenv(entry["base_url_env"]) or base_url

    if not model_name and entry.get("model_name_env"):
        model_name = os.getenv(entry["model_name_env"])

    if not model_name:
        models = entry.get("models") or []
        model_name = entry.get("default_model") or (models[0]["id"] if models else None)

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model_name": model_name,
    }


def get_model_temperature(
    provider: str,
    model_name: str | None = None,
    default: float = 0.2,
    ) -> float:
    """取某供应商某模型在 providers.yaml 里声明的默认温度；找不到则返回 default。"""
    entry = _provider_entry(provider)
    resolved_name = model_name or resolve_provider_config(provider, model_name)["model_name"]
    for model in entry.get("models", []):
        if model.get("id") == resolved_name:
            return float(model.get("temperature", default))
    return default


def create_client(
    provider: str = "gpt",
    model_name: str | None = None,
    temperature: float = 0.2,
    ) -> "Client":
    return Client(
        provider=provider,
        model_name=model_name,
        temperature=temperature,
    )


def _copy_message(
    message: dict[str, Any],
    ) -> dict[str, Any]:
    """深拷贝一条消息（嵌套 dict/list 逐层复制，防调用方篡改会话历史）。"""
    copied: dict[str, Any] = {}
    for key, value in message.items():
        if isinstance(value, list):
            copied[key] = [item if not isinstance(item, dict) else dict(item) for item in value]
            continue

        if isinstance(value, dict):
            copied[key] = dict(value)
            continue

        copied[key] = value
    return copied


# ============================================================
# 无状态 LLM 客户端
# ============================================================

class Client:
    """无状态 LLM 通道：一次请求 ↔ 一次回复，不记历史。

    配置（provider / api_key / base_url / model / temperature）来自 providers.yaml；
    """

    def __init__(
        self,
        provider: str = "gpt",
        model_name: str | None = None,
        temperature: float = 0.2,
        ) -> None:
        provider_config = resolve_provider_config(
            provider=provider,
            model_name=model_name,
        )
        api_key = provider_config["api_key"]
        base_url = provider_config["base_url"]
        self.model = str(provider_config["model_name"])
        self.provider = provider
        self.temperature = temperature

        if not api_key:
            raise ValueError("Missing API key for the selected provider.")

        if base_url:
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
            )
        else:
            self.client = OpenAI(api_key=api_key)

    def __call__(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
        return self.request_assistant_message(
            messages=messages,
            tools=tools,
        )

    def request_assistant_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": self.temperature,
        }

        if tools is not None:
            request_kwargs["tools"] = tools

        response = self.client.chat.completions.create(**request_kwargs)

        choice = response.choices[0]
        assistant_message = self._assistant_message_from_message(choice.message)
        finish_reason = choice.finish_reason

        metadata = self._build_response_metadata(
            raw_response_text=str(assistant_message["content"]),
            finish_reason=finish_reason,
        )
        metadata["assistant_message"] = _copy_message(assistant_message)
        return assistant_message, metadata

    @staticmethod
    def _message_content_to_text(
        content: Any,
        ) -> str:
        if content is None:
            return ""

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                    continue

                if not isinstance(item, dict):
                    continue

                if "type" not in item:
                    continue

                item_type = item["type"]
                if item_type in {"text", "output_text"} and "text" in item and item["text"]:
                    chunks.append(str(item["text"]))
                    continue

                if item_type == "output_text" and "content" in item and item["content"]:
                    chunks.append(str(item["content"]))
            return "\n".join(chunks)

        return str(content)

    @classmethod
    def _assistant_message_from_message(
        cls,
        message: Any,
        ) -> dict[str, Any]:
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": cls._message_content_to_text(message.content),
        }
        tool_calls = cls._tool_calls_to_list(message)
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        return assistant_message

    @staticmethod
    def _tool_calls_to_list(
        message: Any,
        ) -> list[dict[str, Any]]:
        if not hasattr(message, "tool_calls") or message.tool_calls is None:
            return []

        normalized_tool_calls: list[dict[str, Any]] = []
        for tool_call in message.tool_calls:
            function_name = ""
            function_arguments = ""

            if hasattr(tool_call, "function") and tool_call.function is not None:
                if hasattr(tool_call.function, "name") and tool_call.function.name is not None:
                    function_name = str(tool_call.function.name)

                if hasattr(tool_call.function, "arguments") and tool_call.function.arguments is not None:
                    function_arguments = str(tool_call.function.arguments)

            normalized_tool_calls.append(
                {
                    "id": str(tool_call.id),
                    "type": str(tool_call.type),
                    "function": {
                        "name": function_name,
                        "arguments": function_arguments,
                    },
                }
            )

        return normalized_tool_calls

    def _build_response_metadata(
        self,
        raw_response_text: str,
        finish_reason: str | None,
        ) -> dict[str, Any]:
        return {
            "raw_response_text": raw_response_text,
            "model_name": self.model,
            "provider": self.provider,
            "temperature": self.temperature,
            "finish_reason": finish_reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
