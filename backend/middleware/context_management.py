from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from service.context_manager import ContextManager


@dataclass(frozen=True)
class AgentRunContext:
    session_id: str = "default"


class ContextManagementMiddleware(AgentMiddleware):
    """Run the L3 -> L1 -> L2 -> L4 pipeline before every agent model call."""

    def __init__(self, manager: ContextManager, summary_model: BaseChatModel) -> None:
        super().__init__()
        self.manager = manager
        self.summary_model = summary_model

    async def abefore_model(
        self,
        state: dict[str, Any],
        runtime: Runtime[AgentRunContext],
    ) -> dict[str, Any] | None:
        context = runtime.context
        run_id = context.session_id if context is not None else "default"
        result = await self.manager.manage(
            state["messages"],
            summary_model=self.summary_model,
            run_id=run_id,
        )
        if not result.changed:
            return None
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *result.messages,
            ]
        }
