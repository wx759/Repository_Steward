from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator

from langchain.agents.middleware import ToolErrorMiddleware
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.checkpoint.base import BaseCheckpointSaver

from config import get_settings
from graph.agent_factory import build_agent_config, create_agent_from_config
from graph.llm import build_llm_config_from_settings, get_llm
from middleware import (
    AgentRunContext,
    ContextManagementMiddleware,
    ErrorRecoveryMiddleware,
    RecoveryPolicy,
)
from memory import (
    MemoryExtractor,
    MemoryPromptMiddleware,
    MemorySelector,
    MemoryStore,
    format_memory_context,
)
from service.context_manager import ContextManager, ContextPolicy
from service.session_manager import SessionManager
from tools import get_all_tools


logger = logging.getLogger(__name__)


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


class AgentManager:
    def __init__(self) -> None:
        self.base_dir: Path | None = None
        self.session_manager: SessionManager | None = None
        self.tools = []
        self._agent_graph = None
        self._agent_graph_tools_id: int | None = None
        self.context_manager: ContextManager | None = None
        self._summary_model = None
        self._side_model = None
        self.checkpointer: BaseCheckpointSaver | None = None
        self.memory_store: MemoryStore | None = None
        self.memory_selector: MemorySelector | None = None
        self.memory_extractor: MemoryExtractor | None = None

    def initialize(
        self,
        base_dir: Path,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.session_manager = SessionManager(base_dir)
        self.tools = get_all_tools(base_dir)
        settings = get_settings()
        self.context_manager = ContextManager(
            base_dir,
            ContextPolicy(
                max_context_tokens=settings.context_max_tokens,
                context_token_reserve=settings.context_token_reserve,
                max_messages=settings.context_max_messages,
                keep_head_messages=settings.context_keep_head_messages,
                keep_recent_tool_results=settings.context_keep_recent_tool_results,
                tool_results_budget_bytes=settings.context_tool_results_budget_bytes,
                preview_chars=settings.context_preview_chars,
                summary_max_tokens=settings.context_summary_max_tokens,
                summary_max_retries=settings.recovery_max_retries,
                summary_retry_initial_seconds=(
                    settings.recovery_initial_delay_ms / 1000
                ),
                summary_retry_max_seconds=(settings.recovery_max_delay_ms / 1000),
            ),
        )
        self._agent_graph = None
        self._summary_model = None
        self._side_model = None
        self.checkpointer = checkpointer
        self.memory_store = None
        self.memory_selector = None
        self.memory_extractor = None
        if settings.memory_enabled:
            self._side_model = self._build_chat_model()
            self.memory_store = MemoryStore(base_dir / "memory.sqlite")
            self.memory_selector = MemorySelector(
                self._side_model,
                max_items=settings.memory_selector_max_items,
                timeout_seconds=settings.memory_side_call_timeout_seconds,
            )
            self.memory_extractor = MemoryExtractor(
                self.memory_store,
                self._side_model,
                max_items=settings.memory_extract_max_items,
                timeout_seconds=settings.memory_side_call_timeout_seconds,
                input_chars=settings.memory_extract_input_chars,
            )

    def _build_chat_model(self):
        settings = get_settings()
        return get_llm(
            build_llm_config_from_settings(
                settings,
                temperature=0.0,
                streaming=False,
            )
        )

    def _build_agent(self):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")
        tools_id = id(self.tools)
        if self._agent_graph is None or self._agent_graph_tools_id != tools_id:
            if self.context_manager is None:
                raise RuntimeError("Context manager is not initialized")
            if self._summary_model is None:
                self._summary_model = self._build_chat_model()
            settings = get_settings()
            fallback_model = None
            if settings.llm_fallback_model:
                fallback_config = replace(
                    build_llm_config_from_settings(
                        settings,
                        temperature=0.0,
                        streaming=True,
                    ),
                    model=settings.llm_fallback_model,
                )
                fallback_model = get_llm(fallback_config)
            middleware = [
                ContextManagementMiddleware(
                    self.context_manager,
                    self._summary_model,
                )
            ]
            if self.memory_selector is not None:
                middleware.append(MemoryPromptMiddleware())
            middleware.extend(
                [
                    ErrorRecoveryMiddleware(
                        self.context_manager,
                        self._summary_model,
                        RecoveryPolicy(
                            max_retries=settings.recovery_max_retries,
                            initial_delay_seconds=(
                                settings.recovery_initial_delay_ms / 1000
                            ),
                            max_delay_seconds=(
                                settings.recovery_max_delay_ms / 1000
                            ),
                            max_continuations=settings.recovery_max_continuations,
                            default_max_output_tokens=(
                                settings.recovery_default_max_output_tokens
                            ),
                            escalated_max_output_tokens=(
                                settings.recovery_escalated_max_output_tokens
                            ),
                        ),
                        fallback_model=fallback_model,
                    ),
                    ToolErrorMiddleware(
                        lambda exc, request: (
                            f"{request.tool_call.get('name', 'tool')} failed with "
                            f"{type(exc).__name__}. Check the arguments or use another tool."
                        )
                    ),
                ]
            )
            self._agent_graph = create_agent_from_config(
                build_agent_config(
                    self.base_dir,
                    self.tools,
                    middleware=middleware,
                    checkpointer=self.checkpointer,
                )
            )
            self._agent_graph_tools_id = tools_id
        return self._agent_graph

    @staticmethod
    def _build_messages(history: list[Any]) -> list[AnyMessage]:
        messages: list[AnyMessage] = []
        for item in history:
            if isinstance(item, BaseMessage):
                messages.append(item)
                continue
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = str(item.get("content", "") or "")
            if role == "user" and content:
                messages.append(HumanMessage(content=content))
            elif role == "assistant" and content:
                messages.append(AIMessage(content=content))
        return messages

    @staticmethod
    def _thread_config(session_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": session_id}}

    async def load_checkpoint_seed(self, session_id: str) -> list[AnyMessage]:
        """Seed an empty checkpoint from the complete session archive once."""
        if self.session_manager is None:
            raise RuntimeError("AgentManager is not initialized")
        if self.checkpointer is None:
            return self.session_manager.load_session_for_agent(session_id)
        checkpoint = await self.checkpointer.aget_tuple(self._thread_config(session_id))
        if checkpoint is not None:
            return []
        return self.session_manager.load_session_for_agent(session_id)

    async def delete_session(self, session_id: str) -> None:
        if self.session_manager is None:
            raise RuntimeError("AgentManager is not initialized")
        self.session_manager.delete_session(session_id)
        if self.checkpointer is not None:
            await self.checkpointer.adelete_thread(session_id)

    async def _select_memory_context(self, user_request: str) -> str:
        if self.memory_store is None or self.memory_selector is None:
            return ""
        try:
            catalog = await asyncio.to_thread(self.memory_store.list_metadata)
            selection = await self.memory_selector.select(user_request, catalog)
            memories = await asyncio.to_thread(
                self.memory_store.get_memories,
                selection.ids,
            )
            logger.info(
                "memory selector selected=%s fallback=%s failure=%s",
                len(memories),
                selection.used_fallback,
                selection.failure_reason,
            )
            return format_memory_context(memories)
        except Exception as exc:
            logger.warning("memory selection skipped: %s", type(exc).__name__)
            return ""

    async def _extract_turn_memories(self, messages: list[AnyMessage]) -> None:
        if self.memory_extractor is None:
            return
        try:
            result = await self.memory_extractor.extract_and_save(messages)
            logger.info(
                "memory extractor candidates=%s saved=%s failure=%s",
                result.candidates,
                len(result.saved),
                result.failure_reason,
            )
        except Exception as exc:
            logger.warning("memory extraction skipped: %s", type(exc).__name__)

    async def astream(
        self,
        message: str,
        history: list[Any],
        *,
        session_id: str = "default",
    ) -> AsyncIterator[dict[str, Any]]:
        turn_messages = self._build_messages(history)
        current_user_message = HumanMessage(content=message)
        turn_messages.append(current_user_message)

        memory_context = await self._select_memory_context(message)

        final_content_parts: list[str] = []
        last_ai_message = ""
        pending_tools: dict[str, dict[str, str]] = {}
        raw_turn_snapshot: list[AnyMessage] = [current_user_message]
        persisted_turn_messages = raw_turn_snapshot
        run_context = AgentRunContext(
            session_id=session_id,
            memory_context=memory_context,
            raw_turn_snapshot=raw_turn_snapshot,
        )

        async for mode, payload in self._build_agent().astream(
            {"messages": turn_messages},
            config=self._thread_config(session_id),
            context=run_context,
            stream_mode=["messages", "updates", "custom"],
        ):
            if mode == "custom":
                if isinstance(payload, dict) and payload.get("type") == "recovery":
                    yield payload
                continue

            if mode == "messages":
                chunk, metadata = payload
                node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
                if node is not None and node not in {"agent", "model"}:
                    continue
                text = _stringify_content(getattr(chunk, "content", ""))
                if text:
                    run_context.recovery_state.current_stream_text += text
                    final_content_parts.append(text)
                    yield {"type": "token", "content": text}
                continue

            if mode != "updates":
                continue

            for node_name, update in payload.items():
                if not update:
                    continue
                update_messages = list(update.get("messages", []))
                if any(isinstance(item, RemoveMessage) for item in update_messages):
                    update_messages = [
                        next(
                            (
                                item
                                for item in reversed(update_messages)
                                if isinstance(item, AIMessage)
                            ),
                            update_messages[-1],
                        )
                    ]
                for agent_message in update_messages:
                    message_type = getattr(agent_message, "type", "")
                    tool_calls = getattr(agent_message, "tool_calls", []) or []

                    is_recovery_message = bool(
                        getattr(agent_message, "additional_kwargs", {}).get("recovery")
                    )
                    if (
                        node_name in {"agent", "model", "tools"}
                        or is_recovery_message
                    ) and isinstance(agent_message, (AIMessage, ToolMessage)):
                        persisted_turn_messages.append(agent_message)

                    if message_type == "ai" and not tool_calls:
                        candidate = _stringify_content(getattr(agent_message, "content", ""))
                        if candidate:
                            last_ai_message = candidate

                    for tool_call in tool_calls:
                        call_id = str(tool_call.get("id") or tool_call.get("name"))
                        tool_args = tool_call.get("args", "")
                        if not isinstance(tool_args, str):
                            tool_args = json.dumps(tool_args, ensure_ascii=False)
                        pending_tools[call_id] = {
                            "tool": str(tool_call.get("name", "tool")),
                            "input": tool_args,
                        }
                        yield {
                            "type": "tool_start",
                            "tool": pending_tools[call_id]["tool"],
                            "input": tool_args,
                        }

                    if message_type == "tool":
                        call_id = str(getattr(agent_message, "tool_call_id", ""))
                        pending = pending_tools.pop(
                            call_id,
                            {"tool": getattr(agent_message, "name", "tool"), "input": ""},
                        )
                        yield {
                            "type": "tool_end",
                            "tool": pending["tool"],
                            "output": _stringify_content(getattr(agent_message, "content", "")),
                        }
                        yield {"type": "new_response"}

        final_content = "".join(final_content_parts).strip() or last_ai_message.strip()
        if final_content and not any(
            isinstance(item, AIMessage)
            and not item.tool_calls
            and _stringify_content(item.content).strip() == final_content
            for item in persisted_turn_messages
        ):
            persisted_turn_messages.append(AIMessage(content=final_content))
        final_ai = next(
            (
                item
                for item in reversed(persisted_turn_messages)
                if isinstance(item, AIMessage) and not item.tool_calls
            ),
            None,
        )
        recovery = (
            final_ai.additional_kwargs.get("recovery", {})
            if final_ai is not None
            else {}
        )
        if final_content and recovery.get("status", "completed") != "error":
            await self._extract_turn_memories(list(run_context.raw_turn_snapshot))
        yield {
            "type": "done",
            "content": final_content,
            "status": recovery.get("status", "completed"),
            "reason": recovery.get("reason"),
            "continuation_count": recovery.get("continuation_count", 0),
            "_session_messages": persisted_turn_messages,
        }

    async def generate_title(self, first_user_message: str) -> str:
        prompt = (
            "根据用户第一条消息生成一个简短的中文会话标题。"
            "不超过十个汉字，不要引号和解释。"
        )
        try:
            response = await self._build_chat_model().ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": first_user_message},
                ]
            )
            return _stringify_content(getattr(response, "content", "")).strip()[:10] or "新会话"
        except Exception:
            return (first_user_message.strip() or "新会话")[:10]


agent_manager = AgentManager()
