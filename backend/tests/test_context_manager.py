from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from middleware import AgentRunContext, ContextManagementMiddleware
from service.context_manager import (
    TOOL_RESULT_PLACEHOLDER,
    ContextManagementResult,
    ContextManager,
    ContextPolicy,
    apply_tool_result_budget,
    compact_old_tool_results,
    snip_middle_messages,
)


def _tool_call(call_id: str, name: str = "terminal") -> dict:
    return {"id": call_id, "name": name, "args": {"command": "test"}}


def test_l3_persists_largest_results_before_replacing_content(tmp_path: Path) -> None:
    messages = [
        AIMessage(content="", tool_calls=[_tool_call("call-a"), _tool_call("call-b")]),
        ToolMessage(content="a" * 3_000, tool_call_id="call-a", name="terminal"),
        ToolMessage(content="b" * 3_000, tool_call_id="call-b", name="terminal"),
    ]

    managed, changed = apply_tool_result_budget(
        messages,
        output_dir=tmp_path / ".task_outputs" / "tool-results",
        run_id="session-1",
        max_bytes=3_500,
        preview_chars=20,
    )

    assert changed is True
    persisted = [message for message in managed if "<persisted-output>" in str(message.content)]
    assert len(persisted) == 1
    path = tmp_path / persisted[0].response_metadata["context_compaction"]["path"]
    assert path.read_text(encoding="utf-8") in {"a" * 3_000, "b" * 3_000}
    assert messages[1].content == "a" * 3_000
    assert messages[2].content == "b" * 3_000


def test_l1_does_not_split_ai_tool_result_group() -> None:
    messages = [
        HumanMessage(content="goal"),
        AIMessage(content="old answer"),
        AIMessage(content="", tool_calls=[_tool_call("call-a")]),
        ToolMessage(content="result", tool_call_id="call-a", name="terminal"),
        HumanMessage(content="follow up"),
        AIMessage(content="recent answer"),
    ]

    managed, changed = snip_middle_messages(
        messages,
        max_messages=5,
        keep_head_messages=1,
    )

    assert changed is True
    tool_index = next(index for index, message in enumerate(managed) if isinstance(message, ToolMessage))
    assert isinstance(managed[tool_index - 1], AIMessage)
    assert managed[tool_index - 1].tool_calls[0]["id"] == managed[tool_index].tool_call_id


def test_l2_keeps_only_recent_tool_results() -> None:
    messages = [
        ToolMessage(content=f"result-{index}", tool_call_id=f"call-{index}")
        for index in range(5)
    ]

    managed, changed = compact_old_tool_results(messages, keep_recent=2)

    assert changed is True
    assert [message.content for message in managed[:3]] == [TOOL_RESULT_PLACEHOLDER] * 3
    assert [message.content for message in managed[-2:]] == ["result-3", "result-4"]
    assert [message.tool_call_id for message in managed] == [f"call-{index}" for index in range(5)]


def test_l4_summarizes_after_cheap_layers_and_keeps_recent_messages(tmp_path: Path) -> None:
    manager = ContextManager(
        tmp_path,
        ContextPolicy(
            max_context_tokens=100,
            context_token_reserve=0,
            max_messages=50,
            summary_keep_recent_messages=2,
        ),
    )
    model = FakeListChatModel(responses=["Goal: finish context compaction. Remaining: tests."])
    messages = [
        HumanMessage(content="goal " * 80),
        AIMessage(content="decision " * 80),
        HumanMessage(content="recent question"),
        AIMessage(content="recent answer"),
    ]

    result = asyncio.run(
        manager.manage(messages, summary_model=model, run_id="session-2")
    )

    assert result.summarized is True
    assert result.summary == "Goal: finish context compaction. Remaining: tests."
    assert str(result.messages[0].content).startswith("[Compacted context]")
    assert result.messages[-2:] == messages[-2:]


def test_pipeline_persists_large_old_results_before_l2_clears_them(
    tmp_path: Path,
) -> None:
    policy = ContextPolicy(
        max_context_tokens=100_000,
        context_token_reserve=0,
        max_messages=50,
        keep_recent_tool_results=1,
        tool_results_budget_bytes=3_500,
        preview_chars=20,
    )
    manager = ContextManager(tmp_path, policy)
    messages = [
        AIMessage(
            content="",
            tool_calls=[_tool_call(f"call-{index}") for index in range(3)],
        ),
        *[
            ToolMessage(
                content=str(index) * 2_000,
                tool_call_id=f"call-{index}",
                name="terminal",
            )
            for index in range(3)
        ],
    ]

    result = asyncio.run(
        manager.manage(
            messages,
            summary_model=FakeListChatModel(responses=["unused"]),
            run_id="ordered",
        )
    )

    persisted_files = list(
        (tmp_path / ".task_outputs" / "tool-results" / "ordered").glob("*.txt")
    )
    assert result.summarized is False
    assert persisted_files
    assert all(len(path.read_text(encoding="utf-8")) == 2_000 for path in persisted_files)
    assert [message.content for message in result.messages[-3:-1]] == [
        TOOL_RESULT_PLACEHOLDER,
        TOOL_RESULT_PLACEHOLDER,
    ]


def test_middleware_runs_manager_each_time_before_model() -> None:
    class RecordingManager:
        calls = 0

        async def manage(self, messages, **_kwargs):
            self.calls += 1
            return ContextManagementResult(messages=list(messages), changed=False)

    manager = RecordingManager()
    middleware = ContextManagementMiddleware(
        manager,  # type: ignore[arg-type]
        FakeListChatModel(responses=["unused"]),
    )
    runtime = SimpleNamespace(context=AgentRunContext(session_id="session-3"))

    async def run_twice() -> None:
        state = {"messages": [HumanMessage(content="hello")]}
        assert await middleware.abefore_model(state, runtime) is None  # type: ignore[arg-type]
        assert await middleware.abefore_model(state, runtime) is None  # type: ignore[arg-type]

    asyncio.run(run_twice())
    assert manager.calls == 2
