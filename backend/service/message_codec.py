from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content or "")


def message_to_record(message: AnyMessage) -> dict[str, Any]:
    if isinstance(message, HumanMessage):
        return {"type": "human", "content": message.content}

    if isinstance(message, AIMessage):
        record: dict[str, Any] = {"type": "ai", "content": message.content}
        if message.tool_calls:
            record["tool_calls"] = [
                {
                    "id": str(call.get("id") or call.get("name") or "tool-call"),
                    "name": str(call.get("name") or "tool"),
                    "args": call.get("args", {}),
                }
                for call in message.tool_calls
            ]
        recovery = message.additional_kwargs.get("recovery", {})
        if recovery.get("status") in {"incomplete", "error"}:
            record["status"] = recovery["status"]
            record["finish_reason"] = recovery.get("reason")
            record["continuation_count"] = recovery.get("continuation_count", 0)
            record["recovery_attempts"] = recovery.get("attempts", 0)
        return record

    if isinstance(message, ToolMessage):
        record = {
            "type": "tool",
            "tool_call_id": str(message.tool_call_id),
            "name": message.name or "tool",
            "content": message.content,
        }
        if message.status != "success":
            record["status"] = message.status
        return record

    raise TypeError(f"Unsupported message type: {type(message).__name__}")


def record_to_message(record: dict[str, Any]) -> AnyMessage:
    message_type = record.get("type")
    content = record.get("content", "")

    if message_type == "human":
        return HumanMessage(content=content)
    if message_type == "ai":
        additional_kwargs: dict[str, Any] = {}
        if record.get("status") in {"incomplete", "error"}:
            additional_kwargs["recovery"] = {
                "status": record["status"],
                "reason": record.get("finish_reason"),
                "continuation_count": record.get("continuation_count", 0),
                "attempts": record.get("recovery_attempts", 0),
                "generated_by": (
                    "system" if record["status"] == "error" else "model"
                ),
            }
        return AIMessage(
            content=content,
            additional_kwargs=additional_kwargs,
            tool_calls=[
                {
                    "id": str(call.get("id") or call.get("name") or "tool-call"),
                    "name": str(call.get("name") or "tool"),
                    "args": call.get("args", {}),
                    "type": "tool_call",
                }
                for call in record.get("tool_calls", [])
                if isinstance(call, dict)
            ],
        )
    if message_type == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=str(record.get("tool_call_id") or "unknown"),
            name=str(record.get("name") or "tool"),
            status="error" if record.get("status") == "error" else "success",
        )
    raise ValueError(f"Unknown persisted message type: {message_type!r}")


def messages_to_records(messages: Iterable[AnyMessage]) -> list[dict[str, Any]]:
    return [message_to_record(message) for message in messages]


def records_to_messages(records: Iterable[dict[str, Any]]) -> list[AnyMessage]:
    return [record_to_message(record) for record in records]


def normalize_persisted_records(
    records: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Convert the old UI-oriented session shape to canonical message records."""
    normalized: list[dict[str, Any]] = []
    changed = False

    for message_index, record in enumerate(records):
        if record.get("type") in {"human", "ai", "tool"}:
            normalized.append(dict(record))
            continue

        changed = True
        role = record.get("role")
        content = record.get("content", "")
        if role == "user":
            normalized.append({"type": "human", "content": content})
            continue
        if role != "assistant":
            continue

        legacy_calls = [
            call for call in record.get("tool_calls", []) if isinstance(call, dict)
        ]
        ai_record: dict[str, Any] = {"type": "ai", "content": content}
        if legacy_calls:
            ai_record["tool_calls"] = [
                {
                    "id": f"legacy-{message_index}-{call_index}",
                    "name": str(call.get("tool") or "tool"),
                    "args": _parse_legacy_args(call.get("input", "")),
                }
                for call_index, call in enumerate(legacy_calls)
            ]
        normalized.append(ai_record)

        for call_index, call in enumerate(legacy_calls):
            normalized.append(
                {
                    "type": "tool",
                    "tool_call_id": f"legacy-{message_index}-{call_index}",
                    "name": str(call.get("tool") or "tool"),
                    "content": str(call.get("output", "") or ""),
                }
            )

    return normalized, changed


def records_to_display_messages(
    records: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project canonical records into the existing frontend message contract."""
    display: list[dict[str, Any]] = []
    tool_result_indexes: dict[str, tuple[int, int]] = {}

    for record in records:
        message_type = record.get("type")
        if message_type == "human":
            display.append(
                {"role": "user", "content": content_to_text(record.get("content", ""))}
            )
            continue

        if message_type == "ai":
            message: dict[str, Any] = {
                "role": "assistant",
                "content": content_to_text(record.get("content", "")),
            }
            if record.get("status") in {"incomplete", "error"}:
                message["status"] = record["status"]
                message["finish_reason"] = record.get("finish_reason")
                message["continuation_count"] = record.get(
                    "continuation_count", 0
                )
            calls = [call for call in record.get("tool_calls", []) if isinstance(call, dict)]
            if calls:
                message["tool_calls"] = []
                display_index = len(display)
                for call in calls:
                    call_id = str(call.get("id") or call.get("name") or "tool-call")
                    ui_call = {
                        "tool": str(call.get("name") or "tool"),
                        "input": _display_args(call.get("args", {})),
                        "output": "",
                    }
                    message["tool_calls"].append(ui_call)
                    tool_result_indexes[call_id] = (
                        display_index,
                        len(message["tool_calls"]) - 1,
                    )
            display.append(message)
            continue

        if message_type == "tool":
            call_id = str(record.get("tool_call_id") or "")
            target = tool_result_indexes.get(call_id)
            if target is None:
                display.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "tool": str(record.get("name") or "tool"),
                                "input": "",
                                "output": content_to_text(record.get("content", "")),
                            }
                        ],
                    }
                )
                continue
            display_index, call_index = target
            display[display_index]["tool_calls"][call_index]["output"] = content_to_text(
                record.get("content", "")
            )

    return display


def _parse_legacy_args(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"input": value} if value else {}


def _display_args(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
