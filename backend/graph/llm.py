from __future__ import annotations

from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from config import Settings


@dataclass(frozen=True)
class ResolvedLLMConfig:
    provider: str
    model: str
    api_key: str | None
    base_url: str
    temperature: float = 0.0
    streaming: bool = False


def build_llm_config_from_settings(
    settings: Settings,
    *,
    temperature: float = 0.0,
    streaming: bool = False,
) -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=temperature,
        streaming=streaming,
    )


def get_llm(config: ResolvedLLMConfig) -> BaseChatModel:
    if not config.api_key:
        raise RuntimeError(f"Missing API key for provider {config.provider}")

    kwargs: dict = {}
    if config.streaming:
        kwargs["stream_options"] = {"include_usage": True}

    return ChatOpenAI(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        streaming=config.streaming,
        **kwargs,
    )
