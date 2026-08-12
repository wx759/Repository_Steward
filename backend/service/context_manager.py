from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import count_tokens_approximately

from service.model_recovery import classify_model_error, retry_delay


TOOL_RESULT_PLACEHOLDER = "[Earlier tool result compacted. Re-run the tool if needed.]"
SUMMARY_PROMPT = """You summarize a coding-agent conversation so work can continue.
Return text only and do not call tools.

Preserve these sections, using concrete paths and facts:
1. Current goal
2. Key findings and decisions, including rejected options and reasons
3. Files read or modified
4. Remaining tasks
5. User constraints

Be compact but do not omit information needed to continue the work."""


@dataclass(frozen=True)
class ContextPolicy:
    max_context_tokens: int = 50_000
    context_token_reserve: int = 8_000
    max_messages: int = 50
    keep_head_messages: int = 3
    keep_recent_tool_results: int = 3
    tool_results_budget_bytes: int = 200_000
    preview_chars: int = 2_000
    summary_max_tokens: int = 2_000
    summary_input_chars: int = 80_000
    summary_keep_recent_messages: int = 6
    summary_max_retries: int = 3
    summary_retry_initial_seconds: float = 0.5
    summary_retry_max_seconds: float = 8.0


@dataclass(frozen=True)
class ContextManagementResult:
    messages: list[AnyMessage]
    changed: bool
    summarized: bool = False
    summary: str | None = None


def _content_text(content: Any) -> str:
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


def estimate_context_tokens(
    messages: Sequence[BaseMessage],
    *,
    reserve_tokens: int = 0,
) -> int:
    return count_tokens_approximately(messages) + max(0, reserve_tokens)


def _tool_groups(messages: Sequence[BaseMessage]) -> list[tuple[int, int]]:
    """Return [start, end) ranges for AI tool calls and their adjacent results."""
    groups: list[tuple[int, int]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue
        call_ids = {str(call.get("id")) for call in message.tool_calls if call.get("id")}
        end = index + 1
        while end < len(messages) and isinstance(messages[end], ToolMessage):
            tool_message = messages[end]
            if call_ids and str(tool_message.tool_call_id) not in call_ids:
                break
            end += 1
        if end > index + 1:
            groups.append((index, end))
    return groups


def _safe_boundary(
    messages: Sequence[BaseMessage],
    boundary: int,
    *,
    prefer: str,
) -> int:
    for start, end in _tool_groups(messages):
        if start < boundary < end:
            return end if prefer == "right" else start
    return boundary


def _recent_tail_start_within_budget(
    messages: Sequence[BaseMessage],
    *,
    max_tokens: int,
) -> int:
    """Find a pair-safe recent suffix that fits the post-summary budget."""
    start = len(messages)
    while start > 0:
        candidate = _safe_boundary(messages, start - 1, prefer="left")
        if estimate_context_tokens(messages[candidate:]) > max_tokens:
            break
        start = candidate
    return start


def snip_middle_messages(
    messages: Sequence[AnyMessage],
    *,
    max_messages: int,
    keep_head_messages: int,
) -> tuple[list[AnyMessage], bool]:
    if len(messages) <= max_messages:
        return list(messages), False
    if max_messages < 3:
        raise ValueError("max_messages must be at least 3")

    head_end = min(keep_head_messages, max_messages - 2)
    tail_count = max_messages - head_end - 1
    tail_start = len(messages) - tail_count

    head_end = _safe_boundary(messages, head_end, prefer="right")
    tail_start = _safe_boundary(messages, tail_start, prefer="left")
    if head_end >= tail_start:
        return list(messages), False

    removed = tail_start - head_end
    placeholder = HumanMessage(
        content=f"[snipped {removed} messages from conversation middle]",
        additional_kwargs={"context_compaction": {"layer": "L1", "removed": removed}},
    )
    return [*messages[:head_end], placeholder, *messages[tail_start:]], True


def compact_old_tool_results(
    messages: Sequence[AnyMessage],
    *,
    keep_recent: int,
) -> tuple[list[AnyMessage], bool]:
    result = list(messages)
    tool_indexes = [
        index for index, message in enumerate(result) if isinstance(message, ToolMessage)
    ]
    indexes_to_clear = tool_indexes[:-keep_recent] if keep_recent else tool_indexes
    changed = False

    for index in indexes_to_clear:
        message = result[index]
        assert isinstance(message, ToolMessage)
        metadata = dict(message.response_metadata)
        context_metadata = dict(metadata.get("context_compaction", {}))
        if context_metadata.get("layer") == "L2":
            continue
        if _content_text(message.content) == TOOL_RESULT_PLACEHOLDER:
            continue
        context_metadata.update({"layer": "L2", "compacted": True})
        metadata["context_compaction"] = context_metadata
        result[index] = message.model_copy(
            update={
                "artifact": None,
                "content": TOOL_RESULT_PLACEHOLDER,
                "response_metadata": metadata,
            }
        )
        changed = True

    return result, changed


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned[:100] or "unknown"


def apply_tool_result_budget(
    messages: Sequence[AnyMessage],
    *,
    output_dir: Path,
    run_id: str,
    max_bytes: int,
    preview_chars: int,
) -> tuple[list[AnyMessage], bool]:
    """Persist oversized results from the most recent tool-result batch."""
    result = list(messages)
    if not result or not isinstance(result[-1], ToolMessage):
        return result, False

    batch_indexes: list[int] = []
    index = len(result) - 1
    while index >= 0 and isinstance(result[index], ToolMessage):
        batch_indexes.append(index)
        index -= 1
    batch_indexes.reverse()

    def size_at(message_index: int) -> int:
        message = result[message_index]
        assert isinstance(message, ToolMessage)
        return len(_content_text(message.content).encode("utf-8"))

    total = sum(size_at(message_index) for message_index in batch_indexes)
    if total <= max_bytes:
        return result, False

    ranked = sorted(batch_indexes, key=size_at, reverse=True)
    changed = False
    target_dir = output_dir / _safe_name(run_id)

    for message_index in ranked:
        if total <= max_bytes:
            break
        message = result[message_index]
        assert isinstance(message, ToolMessage)
        metadata = dict(message.response_metadata)
        if metadata.get("context_compaction", {}).get("layer") == "L3":
            continue

        full_output = _content_text(message.content)
        full_size = len(full_output.encode("utf-8"))
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{_safe_name(str(message.tool_call_id))}.txt"
        path.write_text(full_output, encoding="utf-8")
        relative_path = path.relative_to(output_dir.parent.parent).as_posix()
        replacement = (
            "<persisted-output>\n"
            f"Full output: {relative_path}\n"
            f"Preview:\n{full_output[:preview_chars]}\n"
            "</persisted-output>"
        )
        context_metadata = dict(metadata.get("context_compaction", {}))
        context_metadata.update(
            {"layer": "L3", "persisted": True, "path": relative_path}
        )
        metadata["context_compaction"] = context_metadata
        result[message_index] = message.model_copy(
            update={"artifact": None, "content": replacement, "response_metadata": metadata}
        )
        total -= full_size
        total += len(replacement.encode("utf-8"))
        changed = True

    return result, changed


def _serialize_messages(messages: Sequence[BaseMessage], max_chars: int) -> str:
    records = [
        {
            "role": message.type,
            "content": _content_text(message.content),
            "tool_calls": getattr(message, "tool_calls", None),
            "tool_call_id": getattr(message, "tool_call_id", None),
        }
        for message in messages
    ]
    text = json.dumps(records, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    head_chars = max_chars // 4
    tail_chars = max_chars - head_chars
    return f"{text[:head_chars]}\n...[summary input clipped]...\n{text[-tail_chars:]}"


class ContextManager:
    def __init__(self, base_dir: Path, policy: ContextPolicy | None = None) -> None:
        self.base_dir = base_dir
        self.policy = policy or ContextPolicy()
        self.tool_results_dir = base_dir / ".task_outputs" / "tool-results"

    async def manage(
        self,
        messages: Sequence[AnyMessage],
        *,
        summary_model: BaseChatModel,
        run_id: str = "default",
        keep_recent_after_summary: bool = True,
        force_summary: bool = False,
        on_recovery: Callable[[dict[str, Any]], None] | None = None,
    ) -> ContextManagementResult:
        managed, l3_changed = apply_tool_result_budget(
            messages,
            output_dir=self.tool_results_dir,
            run_id=run_id,
            max_bytes=self.policy.tool_results_budget_bytes,
            preview_chars=self.policy.preview_chars,
        )
        managed, l1_changed = snip_middle_messages(
            managed,
            max_messages=self.policy.max_messages,
            keep_head_messages=self.policy.keep_head_messages,
        )
        managed, l2_changed = compact_old_tool_results(
            managed,
            keep_recent=self.policy.keep_recent_tool_results,
        )
        changed = l3_changed or l1_changed or l2_changed

        token_count = estimate_context_tokens(
            managed,
            reserve_tokens=self.policy.context_token_reserve,
        )
        if token_count <= self.policy.max_context_tokens and not force_summary:
            return ContextManagementResult(messages=managed, changed=changed)

        preserved: list[AnyMessage] = []
        messages_to_summarize = managed
        if keep_recent_after_summary:
            message_floor = max(
                0, len(managed) - self.policy.summary_keep_recent_messages
            )
            tail_budget = max(
                1,
                (self.policy.max_context_tokens - self.policy.context_token_reserve) // 2,
            )
            token_tail_start = _recent_tail_start_within_budget(
                managed,
                max_tokens=tail_budget,
            )
            tail_start = max(message_floor, token_tail_start)
            tail_start = _safe_boundary(managed, tail_start, prefer="left")
            if 0 < tail_start < len(managed):
                messages_to_summarize = managed[:tail_start]
                preserved = managed[tail_start:]

        if not messages_to_summarize:
            messages_to_summarize = managed
            preserved = []

        summary = await self._summarize(
            messages_to_summarize,
            summary_model,
            on_recovery=on_recovery,
        )
        summary_message = HumanMessage(
            content=f"[Compacted context]\n\n{summary}",
            additional_kwargs={
                "context_compaction": {"layer": "L4"}
            },
        )
        return ContextManagementResult(
            messages=[summary_message, *preserved],
            changed=True,
            summarized=True,
            summary=summary,
        )

    async def _summarize(
        self,
        messages: Sequence[BaseMessage],
        model: BaseChatModel,
        *,
        on_recovery: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        conversation = _serialize_messages(messages, self.policy.summary_input_chars)
        response = None
        for attempt in range(self.policy.summary_max_retries + 1):
            try:
                response = await model.ainvoke(
                    [
                        SystemMessage(content=SUMMARY_PROMPT),
                        HumanMessage(content=conversation),
                    ],
                    max_tokens=self.policy.summary_max_tokens,
                )
                break
            except Exception as exc:
                classified = classify_model_error(exc)
                if not classified.retryable or attempt >= self.policy.summary_max_retries:
                    raise
                delay = retry_delay(
                    attempt,
                    initial_delay=self.policy.summary_retry_initial_seconds,
                    max_delay=self.policy.summary_retry_max_seconds,
                    retry_after=classified.retry_after,
                )
                if on_recovery is not None:
                    on_recovery(
                        {
                            "type": "recovery",
                            "reason": f"summary_{classified.reason}",
                            "attempt": attempt + 1,
                            "max_attempts": self.policy.summary_max_retries,
                            "delay_seconds": round(delay, 2),
                            "message": "上下文总结模型暂时不可用，正在重试。",
                        }
                    )
                await asyncio.sleep(delay)
        if response is None:
            raise RuntimeError("Context summary model did not return a response")
        summary = _content_text(response.content).strip()
        if not summary:
            raise RuntimeError("Context summary model returned empty content")
        return summary
