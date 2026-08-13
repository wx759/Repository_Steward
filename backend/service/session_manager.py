from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from service.message_codec import (
    message_to_record,
    normalize_persisted_records,
    records_to_display_messages,
    records_to_messages,
)


class SessionManager:
    """Small JSON-backed session store used by both the API and the agent."""

    def __init__(self, base_dir: Path) -> None:
        self.sessions_dir = base_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        if not session_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in session_id):
            raise ValueError("Invalid session id")
        return self.sessions_dir / f"{session_id}.json"

    def _default_record(self, session_id: str, title: str = "新会话") -> dict[str, Any]:
        now = time.time()
        return {
            "id": session_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "schema_version": 2,
            "messages": [],
        }

    def _read_session_file(self, session_id: str) -> dict[str, Any]:
        path = self._session_path(session_id)
        if not path.exists():
            record = self._default_record(session_id)
            self._write_session(record)
            return record

        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            record = self._default_record(session_id)
            record["messages"], _ = normalize_persisted_records(raw)
            self._write_session(record)
            return record

        raw.setdefault("id", session_id)
        raw.setdefault("title", "新会话")
        raw.setdefault("created_at", time.time())
        raw.setdefault("updated_at", raw["created_at"])
        raw.setdefault("messages", [])
        raw["messages"], migrated = normalize_persisted_records(raw["messages"])
        removed_agent_context = raw.pop("agent_context", None) is not None
        if migrated or removed_agent_context or raw.get("schema_version") != 2:
            raw["schema_version"] = 2
            self._write_session(raw)
        return raw

    def _write_session(self, record: dict[str, Any]) -> None:
        record["updated_at"] = time.time()
        self._session_path(str(record["id"])).write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _visible_message_count(records: list[dict[str, Any]]) -> int:
        """Count chat bubbles, not internal AI/tool protocol records."""
        count = 0
        previous_role: str | None = None
        for message in records_to_display_messages(records):
            role = str(message.get("role") or "")
            if role == "assistant" and previous_role == "assistant":
                continue
            count += 1
            previous_role = role
        return count

    def create_session(self, title: str = "新会话") -> dict[str, Any]:
        record = self._default_record(uuid.uuid4().hex, title=title)
        self._write_session(record)
        return record

    def list_sessions(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self.sessions_dir.glob("*.json"):
            try:
                record = self._read_session_file(path.stem)
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            records.append(
                {
                    "id": record.get("id", path.stem),
                    "title": record.get("title", "新会话"),
                    "created_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                    "message_count": self._visible_message_count(
                        record.get("messages", [])
                    ),
                }
            )
        return sorted(records, key=lambda item: item.get("updated_at") or 0, reverse=True)

    def load_session_record(self, session_id: str) -> dict[str, Any]:
        return self._read_session_file(session_id)

    def load_session(self, session_id: str) -> list[dict[str, Any]]:
        return self._read_session_file(session_id)["messages"]

    def load_session_for_agent(self, session_id: str) -> list[AnyMessage]:
        record = self._read_session_file(session_id)
        return records_to_messages(record["messages"])

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if role == "user":
            records = [message_to_record(HumanMessage(content=content))]
        elif role == "assistant":
            calls = []
            tool_messages = []
            for index, call in enumerate(tool_calls or []):
                call_id = str(call.get("id") or f"saved-{time.time_ns()}-{index}")
                args = call.get("args", call.get("input", {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"input": args} if args else {}
                calls.append(
                    {
                        "id": call_id,
                        "name": str(call.get("tool") or call.get("name") or "tool"),
                        "args": args,
                        "type": "tool_call",
                    }
                )
                tool_messages.append(
                    ToolMessage(
                        content=str(call.get("output", "") or ""),
                        tool_call_id=call_id,
                        name=str(call.get("tool") or call.get("name") or "tool"),
                    )
                )
            records = [message_to_record(AIMessage(content=content, tool_calls=calls))]
            records.extend(message_to_record(message) for message in tool_messages)
        else:
            raise ValueError(f"Unsupported saved role: {role}")

        record = self._read_session_file(session_id)
        record["messages"].extend(records)
        self._write_session(record)
        return records[0]

    def append_agent_messages(
        self,
        session_id: str,
        messages: list[AnyMessage],
    ) -> list[dict[str, Any]]:
        records = [message_to_record(message) for message in messages]
        record = self._read_session_file(session_id)
        record["messages"].extend(records)
        self._write_session(record)
        return records

    def get_history(self, session_id: str) -> dict[str, Any]:
        record = self._read_session_file(session_id)
        return {
            **record,
            "messages": records_to_display_messages(record["messages"]),
        }

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        record = self._read_session_file(session_id)
        record["title"] = title.strip() or "新会话"
        self._write_session(record)
        return record

    def set_title(self, session_id: str, title: str) -> dict[str, Any]:
        return self.rename_session(session_id, title)

    def delete_session(self, session_id: str) -> None:
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
