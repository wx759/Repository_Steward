from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.runtime import Runtime

from middleware import AgentRunContext
from middleware.context_management import ContextManagementMiddleware
from middleware.error_recovery import (
    CONTINUATION_PROMPT,
    ErrorRecoveryMiddleware,
    RecoveryPolicy,
    classify_model_error,
)
from service.context_manager import ContextManagementResult


class HttpFailure(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class RecordingManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def manage(self, messages, **kwargs):
        self.calls.append(kwargs)
        return ContextManagementResult(
            messages=[HumanMessage(content="[Compacted context]\n\nsummary")],
            changed=True,
            summarized=True,
            summary="summary",
        )


def _request(events: list[dict] | None = None) -> ModelRequest:
    writer = events.append if events is not None else lambda _event: None
    return ModelRequest(
        model=FakeListChatModel(responses=["unused"]),
        messages=[HumanMessage(content="question")],
        runtime=Runtime(
            context=AgentRunContext(session_id="recovery-test"),
            stream_writer=writer,
        ),
    )


def _middleware(manager=None, **policy_overrides) -> ErrorRecoveryMiddleware:
    async def no_sleep(_delay: float) -> None:
        return None

    return ErrorRecoveryMiddleware(
        manager or RecordingManager(),
        FakeListChatModel(responses=["summary"]),
        RecoveryPolicy(**policy_overrides),
        sleeper=no_sleep,
        random_uniform=lambda _start, _end: 0.0,
    )


def test_classifies_rate_limit_overload_and_context_errors() -> None:
    assert classify_model_error(HttpFailure(429, "limited")).reason == "rate_limit"
    assert classify_model_error(HttpFailure(529, "overloaded")).reason == "overloaded"
    context = classify_model_error(HttpFailure(400, "context_length_exceeded"))
    assert context.reason == "prompt_too_long"
    assert context.retryable is False


def test_429_retries_and_emits_visible_recovery_events() -> None:
    events: list[dict] = []
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise HttpFailure(429, "rate limited")
        return ModelResponse(result=[AIMessage(content="ok")])

    response = asyncio.run(
        _middleware(max_retries=3).awrap_model_call(_request(events), handler)
    )

    assert calls == 3
    assert response.result[-1].content == "ok"
    assert [event["reason"] for event in events] == ["rate_limit", "rate_limit"]
    assert events[0]["message"] == "请求频率受限，正在等待后重试。"


def test_529_exhaustion_returns_system_generated_error_ai_message() -> None:
    async def handler(_request):
        raise HttpFailure(529, "overloaded")

    response = asyncio.run(
        _middleware(max_retries=2).awrap_model_call(_request(), handler)
    )
    message = response.result[-1]

    assert isinstance(message, AIMessage)
    assert "持续过载" in str(message.content)
    assert message.additional_kwargs["recovery"] == {
        "status": "error",
        "reason": "overloaded",
        "attempts": 2,
        "continuation_count": 0,
        "generated_by": "system",
    }


def test_prompt_too_long_forces_existing_l4_once_and_rebuilds_state() -> None:
    manager = RecordingManager()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HttpFailure(400, "context_length_exceeded")
        assert str(request.messages[0].content).startswith("[Compacted context]")
        return ModelResponse(result=[AIMessage(content="recovered")])

    request = _request()
    response = asyncio.run(
        _middleware(manager, max_retries=0).awrap_model_call(request, handler)
    )

    assert manager.calls[0]["force_summary"] is True
    assert request.runtime.context.recovery_state.l4_attempted_this_run is True
    assert isinstance(response.result[0], RemoveMessage)
    assert str(response.result[1].content).startswith("[Compacted context]")
    assert response.result[-1].content == "recovered"


def test_prompt_too_long_does_not_repeat_l4_in_same_run() -> None:
    manager = RecordingManager()
    request = _request()
    request.runtime.context.recovery_state.l4_attempted_this_run = True

    async def handler(_request):
        raise HttpFailure(400, "context_length_exceeded")

    response = asyncio.run(
        _middleware(manager, max_retries=0).awrap_model_call(request, handler)
    )

    assert manager.calls == []
    assert "仍超过模型限制" in str(response.result[-1].content)


def test_forced_l4_output_continuation_uses_compacted_messages() -> None:
    manager = RecordingManager()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HttpFailure(400, "context_length_exceeded")
        assert str(request.messages[0].content).startswith("[Compacted context]")
        if calls == 2:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="part-1",
                        response_metadata={"finish_reason": "length"},
                    )
                ]
            )
        assert request.messages[-1].content == CONTINUATION_PROMPT
        return ModelResponse(
            result=[AIMessage(content="part-2", response_metadata={"finish_reason": "stop"})]
        )

    response = asyncio.run(
        _middleware(manager, max_retries=0).awrap_model_call(_request(), handler)
    )

    assert isinstance(response.result[0], RemoveMessage)
    assert response.result[-1].content == "part-1part-2"


def test_output_truncation_uses_internal_prompts_but_returns_one_merged_ai() -> None:
    responses = [
        AIMessage(content="part-1", response_metadata={"finish_reason": "length"}),
        AIMessage(content="part-2", response_metadata={"finish_reason": "length"}),
        AIMessage(content="part-3", response_metadata={"finish_reason": "stop"}),
    ]
    requests: list[ModelRequest] = []

    async def handler(request):
        requests.append(request)
        return ModelResponse(result=[responses[len(requests) - 1]])

    response = asyncio.run(
        _middleware(max_continuations=3).awrap_model_call(_request(), handler)
    )

    assert response.result == [responses[-1].model_copy(update={"content": "part-1part-2part-3"})]
    assert requests[0].model_settings["max_tokens"] == 8_192
    assert requests[1].model_settings["max_tokens"] == 65_536
    assert requests[2].model_settings["max_tokens"] == 65_536
    assert requests[1].messages[-1].content == CONTINUATION_PROMPT
    assert not any(
        isinstance(message, HumanMessage) and message.content == CONTINUATION_PROMPT
        for message in response.result
    )


def test_three_truncated_continuations_are_merged_and_marked_incomplete() -> None:
    call_count = 0

    async def handler(_request):
        nonlocal call_count
        call_count += 1
        return ModelResponse(
            result=[
                AIMessage(
                    content=str(call_count),
                    response_metadata={"finish_reason": "length"},
                )
            ]
        )

    response = asyncio.run(
        _middleware(max_continuations=3).awrap_model_call(_request(), handler)
    )
    message = response.result[-1]

    assert call_count == 4
    assert message.content == "1234"
    assert message.additional_kwargs["recovery"]["status"] == "incomplete"
    assert message.additional_kwargs["recovery"]["continuation_count"] == 3


def test_context_summary_failure_ends_with_sanitized_ai_message() -> None:
    class FailingManager:
        async def manage(self, *_args, **_kwargs):
            raise HttpFailure(503, "provider internal details")

    middleware = ContextManagementMiddleware(
        FailingManager(),  # type: ignore[arg-type]
        FakeListChatModel(responses=["unused"]),
    )
    runtime = Runtime(context=AgentRunContext(session_id="summary-failure"))
    result = asyncio.run(
        middleware.abefore_model(
            {"messages": [HumanMessage(content="question")]}, runtime
        )
    )

    assert result is not None
    assert result["jump_to"] == "end"
    message = result["messages"][0]
    assert isinstance(message, AIMessage)
    assert "provider internal details" not in str(message.content)
    assert message.additional_kwargs["recovery"]["status"] == "error"
