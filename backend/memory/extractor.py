from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from .models import MEMORY_TYPES, MemoryDraft, MemoryMetadata, MemoryRecord, MemoryType
from .prompts import EXTRACTOR_SYSTEM_PROMPT, format_catalog
from .store import MemoryStore


@dataclass(frozen=True)
class ExtractionResult:
    candidates: int
    saved: list[MemoryRecord]
    failure_reason: str | None = None


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


def _normalize(text: str) -> str:
    return "".join(character.lower() for character in text if character.isalnum())


def _description_terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./\\-]+|[\u4e00-\u9fff]", text.lower()))


def _is_duplicate(draft: MemoryDraft, catalog: list[MemoryMetadata]) -> bool:
    draft_name = _normalize(draft.name)
    draft_description = _normalize(draft.description)
    draft_terms = _description_terms(draft.description)
    for existing in catalog:
        if draft_name == _normalize(existing.name):
            return True
        if draft_description == _normalize(existing.description):
            return True
        existing_terms = _description_terms(existing.description)
        union = draft_terms | existing_terms
        if union and len(draft_terms & existing_terms) / len(union) >= 0.8:
            return True
    return False


_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]{8,}",
        re.IGNORECASE,
    ),
)


def _contains_secret(draft: MemoryDraft) -> bool:
    text = f"{draft.name}\n{draft.description}\n{draft.body}"
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def format_turn_snapshot(messages: list[AnyMessage], *, max_chars: int) -> str:
    user_parts: list[str] = []
    final_parts: list[str] = []
    tool_parts: list[str] = []
    other_parts: list[str] = []
    last_plain_ai = next(
        (
            item
            for item in reversed(messages)
            if isinstance(item, AIMessage) and not item.tool_calls
        ),
        None,
    )
    for message in messages:
        content = _message_text(message.content).strip()
        if isinstance(message, HumanMessage) and content:
            user_parts.append(f"USER:\n{content}")
        elif message is last_plain_ai and content:
            final_parts.append(f"FINAL ASSISTANT:\n{content}")
        elif isinstance(message, ToolMessage) and content:
            name = message.name or "tool"
            tool_parts.append(f"TOOL {name}:\n{content[:1500]}")
        elif isinstance(message, AIMessage) and message.tool_calls:
            names = ", ".join(str(call.get("name", "tool")) for call in message.tool_calls)
            other_parts.append(f"ASSISTANT TOOL CALLS: {names}")

    output: list[str] = []
    core_parts = user_parts + final_parts
    if core_parts:
        per_core_limit = max(1, max_chars // len(core_parts))
        output.extend(part[:per_core_limit] for part in core_parts)
    remaining = max_chars - sum(len(part) + 2 for part in output)
    for part in list(reversed(tool_parts)) + other_parts:
        if remaining <= 0:
            break
        clipped = part[:remaining]
        output.append(clipped)
        remaining -= len(clipped) + 2
    return "\n\n".join(output)


class MemoryExtractor:
    def __init__(
        self,
        store: MemoryStore,
        model: BaseChatModel,
        *,
        max_items: int = 3,
        timeout_seconds: float = 20.0,
        input_chars: int = 12_000,
    ) -> None:
        self.store = store
        self.model = model
        self.max_items = max(1, max_items)
        self.timeout_seconds = timeout_seconds
        self.input_chars = max(1_000, input_chars)

    async def extract_and_save(self, messages: list[AnyMessage]) -> ExtractionResult:
        catalog = await asyncio.to_thread(self.store.list_metadata)
        snapshot = format_turn_snapshot(messages, max_chars=self.input_chars)
        if not snapshot:
            return ExtractionResult(0, [])
        try:
            response = await asyncio.wait_for(
                self.model.ainvoke(
                    [
                        {
                            "role": "system",
                            "content": EXTRACTOR_SYSTEM_PROMPT.format(limit=self.max_items),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Existing memory catalog:\n"
                                f"{format_catalog(catalog, include_type=True)}\n\n"
                                f"Completed turn:\n{snapshot}"
                            ),
                        },
                    ]
                ),
                timeout=self.timeout_seconds,
            )
            drafts = self._parse_drafts(_message_text(getattr(response, "content", "")))
        except Exception as exc:
            return ExtractionResult(0, [], failure_reason=type(exc).__name__)

        saved: list[MemoryRecord] = []
        current_catalog = list(catalog)
        for draft in drafts:
            if _contains_secret(draft) or _is_duplicate(draft, current_catalog):
                continue
            try:
                record = await asyncio.to_thread(self.store.save_memory, draft)
            except ValueError:
                continue
            saved.append(record)
            current_catalog.append(record)
        return ExtractionResult(len(drafts), saved)

    def _parse_drafts(self, raw: str) -> list[MemoryDraft]:
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) > self.max_items:
            raise ValueError("Extractor response must be a bounded JSON array")
        drafts: list[MemoryDraft] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("Extractor item must be an object")
            name = str(item.get("name", "")).strip()
            memory_type = str(item.get("type", "")).strip().lower()
            description = str(item.get("description", "")).strip()
            body = str(item.get("body", "")).strip()
            if memory_type not in MEMORY_TYPES:
                raise ValueError("Extractor returned an unsupported type")
            if not name or not description or not body:
                raise ValueError("Extractor returned an empty field")
            if len(name) > 120 or len(description) > 500 or len(body) > 4_000:
                raise ValueError("Extractor returned an oversized field")
            drafts.append(
                MemoryDraft(
                    name=name,
                    type=cast(MemoryType, memory_type),
                    description=description,
                    body=body,
                )
            )
        return drafts
