from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage


class MemoryPromptMiddleware(AgentMiddleware):
    """Append selected memories to this model request without changing graph state."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        context = request.runtime.context
        memory_context = getattr(context, "memory_context", "") if context else ""
        if not memory_context:
            return await handler(request)

        current = request.system_message
        current_text = str(current.content) if current is not None else ""
        system_message = SystemMessage(
            content=f"{current_text}\n\n{memory_context}".strip()
        )
        return await handler(request.override(system_message=system_message))
