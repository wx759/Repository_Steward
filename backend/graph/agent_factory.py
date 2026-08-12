from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver

from config import get_settings
from graph.llm import build_llm_config_from_settings, get_llm
from service.prompt_builder import build_system_prompt
from middleware import AgentRunContext

AgentGraph = Any


@dataclass
class AgentConfig:
    llm: BaseChatModel
    tools: list[BaseTool]
    system_prompt: str
    middleware: list[AgentMiddleware]
    checkpointer: BaseCheckpointSaver | None = None


def build_agent_config(
    base_dir: Path,
    tools: list[BaseTool],
    *,
    middleware: list[AgentMiddleware] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> AgentConfig:
    settings = get_settings()
    return AgentConfig(
        llm=get_llm(
            build_llm_config_from_settings(
                settings,
                temperature=0.0,
                streaming=True,
            )
        ),
        tools=tools,
        system_prompt=build_system_prompt(base_dir),
        middleware=list(middleware or []),
        checkpointer=checkpointer,
    )


def create_agent_from_config(config: AgentConfig) -> AgentGraph:
    return create_agent(
        model=config.llm,
        tools=config.tools,
        system_prompt=config.system_prompt,
        middleware=config.middleware,
        context_schema=AgentRunContext,
        checkpointer=config.checkpointer,
    )
