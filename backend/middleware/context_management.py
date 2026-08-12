from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from service.context_manager import ContextManager
from service.model_recovery import classify_model_error
from .error_recovery import RecoveryState


@dataclass
class AgentRunContext:
    session_id: str = "default"
    recovery_state: RecoveryState = field(default_factory=RecoveryState)


class ContextManagementMiddleware(AgentMiddleware):
    """Run the L3 -> L1 -> L2 -> L4 pipeline before every agent model call."""

    def __init__(self, manager: ContextManager, summary_model: BaseChatModel) -> None:
        super().__init__()
        self.manager = manager
        self.summary_model = summary_model

    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self,
        state: dict[str, Any],
        runtime: Runtime[AgentRunContext],
    ) -> dict[str, Any] | None:
        context = runtime.context
        run_id = context.session_id if context is not None else "default"
        try:
            result = await self.manager.manage(
                state["messages"],
                summary_model=self.summary_model,
                run_id=run_id,
                on_recovery=getattr(runtime, "stream_writer", None),
            )
        except Exception as exc:
            classified = classify_model_error(exc)
            message = AIMessage(
                content="上下文总结失败，本轮无法继续，请稍后再试。",
                additional_kwargs={
                    "recovery": {
                        "status": "error",
                        "reason": f"summary_{classified.reason}",
                        "attempts": 0,
                        "continuation_count": 0,
                        "generated_by": "system",
                    }
                },
            )
            return {"messages": [message], "jump_to": "end"}
        if result.summarized and context is not None:
            context.recovery_state.l4_attempted_this_run = True
        if not result.changed:
            return None
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *result.messages,
            ]
        }
