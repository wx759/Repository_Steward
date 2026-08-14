from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel

from .models import MemoryMetadata
from .prompts import SELECTOR_SYSTEM_PROMPT, format_catalog


@dataclass(frozen=True)
class SelectionResult:
    ids: list[str]
    used_fallback: bool = False
    failure_reason: str | None = None


def _response_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    return str(content or "").strip()


def _terms(text: str) -> set[str]:
    normalized = text.lower()
    terms = set(re.findall(r"[a-z0-9_./\\-]{2,}", normalized))
    for group in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(group) <= 4:
            terms.add(group)
        terms.update(group[index : index + 2] for index in range(len(group) - 1))
    return terms


def keyword_select(
    user_request: str,
    catalog: list[MemoryMetadata],
    *,
    limit: int,
) -> list[str]:
    request_terms = _terms(user_request)
    if not request_terms:
        return []
    ranked: list[tuple[int, str, str]] = []
    for memory in catalog:
        candidate_terms = _terms(f"{memory.name} {memory.description}")
        score = len(request_terms & candidate_terms)
        if score:
            ranked.append((score, memory.updated_at, memory.id))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [memory_id for _, _, memory_id in ranked[:limit]]


class MemorySelector:
    def __init__(
        self,
        model: BaseChatModel,
        *,
        max_items: int = 5,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.model = model
        self.max_items = max(1, min(max_items, 5))
        self.timeout_seconds = timeout_seconds

    async def select(
        self,
        user_request: str,
        catalog: list[MemoryMetadata],
    ) -> SelectionResult:
        if not catalog:
            return SelectionResult([])
        try:
            response = await asyncio.wait_for(
                self.model.ainvoke(
                    [
                        {
                            "role": "system",
                            "content": SELECTOR_SYSTEM_PROMPT.format(limit=self.max_items),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Current request:\n{user_request}\n\n"
                                f"Memory catalog:\n{format_catalog(catalog)}"
                            ),
                        },
                    ]
                ),
                timeout=self.timeout_seconds,
            )
            ids = self._parse_ids(_response_text(getattr(response, "content", "")), catalog)
            return SelectionResult(ids)
        except Exception as exc:
            return SelectionResult(
                keyword_select(user_request, catalog, limit=self.max_items),
                used_fallback=True,
                failure_reason=type(exc).__name__,
            )

    def _parse_ids(self, raw: str, catalog: list[MemoryMetadata]) -> list[str]:
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) > self.max_items:
            raise ValueError("Selector response must be a bounded JSON array")
        allowed = {memory.id for memory in catalog}
        if any(not isinstance(item, str) or item not in allowed for item in payload):
            raise ValueError("Selector returned an unknown memory id")
        return list(dict.fromkeys(payload))
