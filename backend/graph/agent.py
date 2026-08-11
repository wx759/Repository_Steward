from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

from config import get_settings
from graph.agent_factory import build_agent_config, create_agent_from_config
from graph.llm import build_llm_config_from_settings, get_llm
from service.session_manager import SessionManager
from tools import get_all_tools


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

    def initialize(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.session_manager = SessionManager(base_dir)
        self.tools = get_all_tools(base_dir)
        self._agent_graph = None

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
            self._agent_graph = create_agent_from_config(
                build_agent_config(self.base_dir, self.tools)
            )
            self._agent_graph_tools_id = tools_id
        return self._agent_graph

    @staticmethod
    def _build_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for item in history:
            role = item.get("role")
            content = str(item.get("content", "") or "")
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        return messages

    async def astream(
        self,
        message: str,
        history: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        turn_messages = self._build_messages(history)
        turn_messages.append({"role": "user", "content": message})

        final_content_parts: list[str] = []
        last_ai_message = ""
        pending_tools: dict[str, dict[str, str]] = {}

        async for mode, payload in self._build_agent().astream(
            {"messages": turn_messages},
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                chunk, metadata = payload
                node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
                if node is not None and node != "agent":
                    continue
                text = _stringify_content(getattr(chunk, "content", ""))
                if text:
                    final_content_parts.append(text)
                    yield {"type": "token", "content": text}
                continue

            if mode != "updates":
                continue

            for update in payload.values():
                if not update:
                    continue
                for agent_message in update.get("messages", []):
                    message_type = getattr(agent_message, "type", "")
                    tool_calls = getattr(agent_message, "tool_calls", []) or []

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
        yield {"type": "done", "content": final_content}

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
