from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from service.context_manager import ContextManager
from service.model_recovery import (
    ClassifiedError,
    classify_model_error,
    retry_delay,
)


CONTINUATION_PROMPT = (
    "Output token limit hit. Resume directly from the exact stopping point. "
    "Do not apologize or recap previous content. Do not repeat text already written. "
    "Break the remaining work into smaller pieces."
)


@dataclass(frozen=True)
class RecoveryPolicy:
    max_retries: int = 3
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    backoff_factor: float = 2.0
    jitter_ratio: float = 0.25
    max_continuations: int = 3
    default_max_output_tokens: int = 8_192
    escalated_max_output_tokens: int = 65_536
    fallback_after_overloads: int = 3


@dataclass
class RecoveryState:
    l4_attempted_this_run: bool = False
    transient_retry_count: int = 0
    consecutive_overloads: int = 0
    continuation_count: int = 0
    current_stream_text: str = ""


class ModelCallFailure(Exception):
    def __init__(
        self,
        cause: Exception,
        classified: ClassifiedError,
        retries: int,
        partial_content: str = "",
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.classified = classified
        self.retries = retries
        self.partial_content = partial_content


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


def _response_ai_message(response: ModelResponse) -> AIMessage | None:
    return next(
        (message for message in reversed(response.result) if isinstance(message, AIMessage)),
        None,
    )


def _finish_reason(message: AIMessage) -> str:
    values = (
        message.response_metadata.get("finish_reason"),
        message.response_metadata.get("stop_reason"),
        message.additional_kwargs.get("finish_reason"),
        message.additional_kwargs.get("stop_reason"),
    )
    return str(next((value for value in values if value), "")).lower()


def _is_output_truncated(message: AIMessage) -> bool:
    return _finish_reason(message) in {"length", "max_tokens", "max_output_tokens"}


def _with_recovery_metadata(
    message: AIMessage,
    *,
    content: str,
    status: str,
    reason: str,
    attempts: int = 0,
    continuation_count: int = 0,
) -> AIMessage:
    additional = dict(message.additional_kwargs)
    additional["recovery"] = {
        "status": status,
        "reason": reason,
        "attempts": attempts,
        "continuation_count": continuation_count,
        "generated_by": "system" if status == "error" else "model",
    }
    return message.model_copy(update={"content": content, "additional_kwargs": additional})


def _failure_message(failure: ModelCallFailure) -> AIMessage:
    reason = failure.classified.reason
    texts = {
        "rate_limit": "请求频率限制仍未解除，自动重试已经结束，请稍后再试。",
        "overloaded": "模型服务持续过载，自动重试已经结束，请稍后再试。",
        "server_error": "模型服务暂时不可用，自动重试已经结束，请稍后再试。",
        "timeout": "模型服务响应超时，自动重试已经结束，请稍后再试。",
        "connection": "暂时无法连接模型服务，自动重试已经结束，请稍后再试。",
        "prompt_too_long": "上下文经过压缩后仍超过模型限制，本轮无法继续。",
        "authentication": "模型服务认证失败，请检查后端模型配置。",
        "bad_request": "模型服务拒绝了本次请求，请检查模型与参数配置。",
        "unknown": "模型调用失败，本轮未获得模型回答。",
    }
    return _with_recovery_metadata(
        AIMessage(content=texts.get(reason, texts["unknown"])),
        content=texts.get(reason, texts["unknown"]),
        status="error",
        reason=reason,
        attempts=failure.retries,
    )


class ErrorRecoveryMiddleware(AgentMiddleware):
    """Recover model calls without replacing LangChain's agent loop."""

    def __init__(
        self,
        manager: ContextManager,
        summary_model: BaseChatModel,
        policy: RecoveryPolicy | None = None,
        *,
        fallback_model: BaseChatModel | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_uniform: Callable[[float, float], float] | None = None,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.summary_model = summary_model
        self.policy = policy or RecoveryPolicy()
        self.fallback_model = fallback_model
        self._sleep = sleeper
        self._random_uniform = random_uniform

    @staticmethod
    def _state(request: ModelRequest) -> RecoveryState:
        context = request.runtime.context if request.runtime is not None else None
        state = getattr(context, "recovery_state", None)
        if isinstance(state, RecoveryState):
            return state
        return RecoveryState()

    @staticmethod
    def _emit(request: ModelRequest, event: dict[str, Any]) -> None:
        runtime = request.runtime
        if runtime is not None:
            runtime.stream_writer({"type": "recovery", **event})

    def _delay(self, retry_index: int, retry_after: float | None) -> float:
        kwargs: dict[str, Any] = {}
        if self._random_uniform is not None:
            kwargs["random_uniform"] = self._random_uniform
        return retry_delay(
            retry_index,
            initial_delay=self.policy.initial_delay_seconds,
            max_delay=self.policy.max_delay_seconds,
            backoff_factor=self.policy.backoff_factor,
            jitter_ratio=self.policy.jitter_ratio,
            retry_after=retry_after,
            **kwargs,
        )

    async def _invoke_with_retry(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        state: RecoveryState,
    ) -> ModelResponse:
        active_request = request
        retries = 0
        for attempt in range(self.policy.max_retries + 1):
            state.current_stream_text = ""
            try:
                response = await handler(active_request)
                state.transient_retry_count = 0
                state.consecutive_overloads = 0
                return response
            except Exception as exc:
                classified = classify_model_error(exc)
                partial = state.current_stream_text
                if partial or not classified.retryable or attempt >= self.policy.max_retries:
                    raise ModelCallFailure(exc, classified, retries, partial) from exc

                retries += 1
                state.transient_retry_count = retries
                if classified.reason == "overloaded":
                    state.consecutive_overloads += 1
                    if (
                        self.fallback_model is not None
                        and state.consecutive_overloads
                        >= self.policy.fallback_after_overloads
                    ):
                        active_request = active_request.override(model=self.fallback_model)
                        self._emit(
                            request,
                            {
                                "reason": "fallback_model",
                                "attempt": retries,
                                "max_attempts": self.policy.max_retries,
                                "message": "主模型持续过载，正在切换备用模型。",
                            },
                        )
                else:
                    state.consecutive_overloads = 0

                delay = self._delay(attempt, classified.retry_after)
                messages = {
                    "rate_limit": "请求频率受限，正在等待后重试。",
                    "overloaded": "模型服务当前过载，正在重试。",
                    "server_error": "模型服务暂时不可用，正在重试。",
                    "timeout": "模型服务响应超时，正在重试。",
                    "connection": "模型服务连接失败，正在重试。",
                }
                self._emit(
                    request,
                    {
                        "reason": classified.reason,
                        "attempt": retries,
                        "max_attempts": self.policy.max_retries,
                        "delay_seconds": round(delay, 2),
                        "message": messages.get(classified.reason, "模型调用失败，正在重试。"),
                    },
                )
                await self._sleep(delay)

        raise RuntimeError("unreachable model retry state")

    @staticmethod
    def _request_with_max_tokens(request: ModelRequest, max_tokens: int) -> ModelRequest:
        settings = dict(request.model_settings or {})
        settings["max_tokens"] = max_tokens
        return request.override(model_settings=settings)

    async def _continue_output(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        state: RecoveryState,
        initial_content: str,
    ) -> ModelResponse:
        merged = initial_content
        last_message = AIMessage(content=initial_content)
        structured_response = None

        while state.continuation_count < self.policy.max_continuations:
            state.continuation_count += 1
            self._emit(
                request,
                {
                    "reason": "max_output_tokens",
                    "attempt": state.continuation_count,
                    "max_attempts": self.policy.max_continuations,
                    "message": (
                        "输出达到长度限制，正在自动续写"
                        f"（{state.continuation_count}/{self.policy.max_continuations}）。"
                    ),
                },
            )
            continuation_request = request.override(
                messages=[
                    *request.messages,
                    AIMessage(content=merged),
                    HumanMessage(content=CONTINUATION_PROMPT),
                ]
            )
            continuation_request = self._request_with_max_tokens(
                continuation_request,
                self.policy.escalated_max_output_tokens,
            )
            try:
                response = await self._invoke_with_retry(
                    continuation_request, handler, state
                )
            except ModelCallFailure as failure:
                if failure.partial_content:
                    merged += failure.partial_content
                return ModelResponse(
                    result=[
                        _with_recovery_metadata(
                            last_message,
                            content=merged,
                            status="incomplete",
                            reason=failure.classified.reason,
                            attempts=failure.retries,
                            continuation_count=state.continuation_count,
                        )
                    ],
                    structured_response=None,
                )

            candidate = _response_ai_message(response)
            if candidate is None:
                return response
            last_message = candidate
            structured_response = response.structured_response
            merged += _message_text(candidate)
            if not _is_output_truncated(candidate):
                return ModelResponse(
                    result=[candidate.model_copy(update={"content": merged})],
                    structured_response=structured_response,
                )

        return ModelResponse(
            result=[
                _with_recovery_metadata(
                    last_message,
                    content=merged,
                    status="incomplete",
                    reason="max_output_tokens",
                    continuation_count=state.continuation_count,
                )
            ],
            structured_response=structured_response,
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        state = self._state(request)
        initial_request = self._request_with_max_tokens(
            request, self.policy.default_max_output_tokens
        )
        effective_request = request
        replacement: list[AnyMessage] | None = None

        try:
            response = await self._invoke_with_retry(initial_request, handler, state)
        except ModelCallFailure as failure:
            if failure.partial_content:
                response = await self._continue_output(
                    request, handler, state, failure.partial_content
                )
            elif failure.classified.reason == "prompt_too_long":
                if state.l4_attempted_this_run:
                    return ModelResponse(result=[_failure_message(failure)])
                self._emit(
                    request,
                    {
                        "reason": "prompt_too_long",
                        "attempt": 1,
                        "max_attempts": 1,
                        "message": "上下文被模型拒绝，正在强制执行现有L4后重试。",
                    },
                )
                try:
                    compacted = await self.manager.manage(
                        request.messages,
                        summary_model=self.summary_model,
                        run_id=getattr(request.runtime.context, "session_id", "default"),
                        force_summary=True,
                        on_recovery=request.runtime.stream_writer,
                    )
                except Exception as summary_exc:
                    summary_error = classify_model_error(summary_exc)
                    return ModelResponse(
                        result=[
                            _failure_message(
                                ModelCallFailure(
                                    summary_exc,
                                    summary_error,
                                    (
                                        self.policy.max_retries
                                        if summary_error.retryable
                                        else 0
                                    ),
                                )
                            )
                        ]
                    )
                state.l4_attempted_this_run = True
                replacement = compacted.messages
                compacted_request = initial_request.override(messages=replacement)
                effective_request = compacted_request
                try:
                    response = await self._invoke_with_retry(
                        compacted_request, handler, state
                    )
                except ModelCallFailure as retry_failure:
                    if retry_failure.partial_content:
                        response = await self._continue_output(
                            compacted_request,
                            handler,
                            state,
                            retry_failure.partial_content,
                        )
                    else:
                        return ModelResponse(
                            result=[
                                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                                *replacement,
                                _failure_message(retry_failure),
                            ]
                        )
            else:
                return ModelResponse(result=[_failure_message(failure)])

        message = _response_ai_message(response)
        if message is not None and _is_output_truncated(message):
            response = await self._continue_output(
                effective_request,
                handler,
                state,
                _message_text(message),
            )

        if replacement is not None:
            return ModelResponse(
                result=[
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    *replacement,
                    *response.result,
                ],
                structured_response=response.structured_response,
            )
        return response
