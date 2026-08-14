from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from graph.agent import AgentManager
from graph.agent_factory import AgentConfig, create_agent_from_config
from memory import (
    ExtractionResult,
    MemoryDraft,
    MemoryExtractor,
    MemoryPromptMiddleware,
    MemorySelector,
    MemoryStore,
    SelectionResult,
    format_turn_snapshot,
)
from middleware import AgentRunContext


def test_memory_store_crud_and_ordered_loading(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite")
    first = store.save_memory(
        MemoryDraft(
            name="PowerShell preference",
            type="user",
            description="User prefers PowerShell commands.",
            body="Use PowerShell-oriented commands on Windows.",
        )
    )
    second = store.save_memory(
        MemoryDraft(
            name="Agent entrypoint",
            type="project",
            description="AgentManager owns the request loop.",
            body="The main request orchestration lives in backend/graph/agent.py.",
        )
    )

    assert {item.id for item in store.list_metadata()} == {first.id, second.id}
    assert [item.id for item in store.get_memories([second.id, "missing", first.id])] == [
        second.id,
        first.id,
    ]

    updated = store.update_memory(
        first.id,
        MemoryDraft(
            name="PowerShell preference",
            type="user",
            description="User consistently prefers PowerShell commands.",
            body="Use PowerShell and npm.cmd on Windows.",
        ),
    )
    assert updated.created_at == first.created_at
    assert updated.body == "Use PowerShell and npm.cmd on Windows."

    try:
        store.save_memory(
            MemoryDraft(
                name="powershell preference",
                type="user",
                description="duplicate",
                body="duplicate",
            )
        )
    except ValueError:
        pass
    else:
        raise AssertionError("case-insensitive duplicate name should fail")

    assert store.delete_memory(second.id) is True
    assert store.delete_memory(second.id) is False


def test_selector_returns_only_catalog_ids_and_falls_back_to_keywords(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite")
    ui = store.save_memory(
        MemoryDraft(
            name="light-ui",
            type="user",
            description="用户偏好浅色前端界面",
            body="Use a light palette.",
        )
    )
    backend = store.save_memory(
        MemoryDraft(
            name="backend-entry",
            type="project",
            description="后端入口位于 app.py",
            body="Backend starts from app.py.",
        )
    )
    catalog = store.list_metadata()

    selected = asyncio.run(
        MemorySelector(FakeListChatModel(responses=[f'["{backend.id}"]'])).select(
            "后端从哪里启动？",
            catalog,
        )
    )
    assert selected == SelectionResult([backend.id], used_fallback=False)

    fallback = asyncio.run(
        MemorySelector(FakeListChatModel(responses=["not-json"])).select(
            "请继续优化浅色前端界面",
            catalog,
        )
    )
    assert fallback.used_fallback is True
    assert fallback.ids == [ui.id]


def test_extractor_saves_new_memory_but_skips_duplicates_and_secrets(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.save_memory(
        MemoryDraft(
            name="existing-project-fact",
            type="project",
            description="The backend entrypoint is app.py.",
            body="Start the backend through app.py.",
        )
    )
    response = """[
      {
        "name": "prefer-light-ui",
        "type": "user",
        "description": "User prefers a light UI theme.",
        "body": "Use a restrained light color palette for future UI work."
      },
      {
        "name": "api-secret",
        "type": "reference",
        "description": "API key",
        "body": "api_key=sk-abcdefghijklmnop"
      }
    ]"""
    extractor = MemoryExtractor(
        store,
        FakeListChatModel(responses=[response]),
        max_items=3,
    )
    result = asyncio.run(
        extractor.extract_and_save(
            [
                HumanMessage(content="以后前端都使用浅色主题"),
                AIMessage(content="好的，后续会保持浅色。"),
            ]
        )
    )

    assert result.candidates == 2
    assert [item.name for item in result.saved] == ["prefer-light-ui"]
    assert {item.name for item in store.list_metadata()} == {
        "existing-project-fact",
        "prefer-light-ui",
    }


def test_snapshot_keeps_user_request_and_final_answer_with_small_budget() -> None:
    snapshot = format_turn_snapshot(
        [
            HumanMessage(content="U" * 2_000),
            ToolMessage(content="T" * 2_000, tool_call_id="call-1", name="terminal"),
            AIMessage(content="A" * 2_000),
        ],
        max_chars=1_000,
    )

    assert "USER:" in snapshot
    assert "FINAL ASSISTANT:" in snapshot
    assert len(snapshot) <= 1_002


def test_memory_middleware_changes_only_the_model_request() -> None:
    middleware = MemoryPromptMiddleware()
    context = AgentRunContext(
        session_id="memory-test",
        memory_context="<long-term-memory>light UI</long-term-memory>",
    )
    request = ModelRequest(
        model=FakeListChatModel(responses=["unused"]),
        messages=[HumanMessage(content="hello")],
        system_message=SystemMessage(content="base prompt"),
        tools=[],
        state={"messages": [HumanMessage(content="hello")]},
        runtime=SimpleNamespace(context=context),
    )
    captured: list[ModelRequest] = []

    async def handler(updated: ModelRequest) -> ModelResponse:
        captured.append(updated)
        return ModelResponse(result=[AIMessage(content="done")])

    asyncio.run(middleware.awrap_model_call(request, handler))

    assert len(captured) == 1
    assert "base prompt" in str(captured[0].system_message.content)
    assert "light UI" in str(captured[0].system_message.content)
    assert request.state["messages"] == [HumanMessage(content="hello")]
    assert request.messages == [HumanMessage(content="hello")]


def test_memory_context_is_not_written_to_langgraph_checkpoint(tmp_path: Path) -> None:
    asyncio.run(_exercise_transient_memory_checkpoint(tmp_path))


async def _exercise_transient_memory_checkpoint(tmp_path: Path) -> None:
    database_path = tmp_path / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "memory-checkpoint-test"}}
    marker = "TRANSIENT-MEMORY-MARKER"
    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        await saver.setup()
        graph = create_agent_from_config(
            AgentConfig(
                llm=FakeMessagesListChatModel(
                    responses=[AIMessage(content="answer from memory")]
                ),
                tools=[],
                system_prompt="base prompt",
                middleware=[MemoryPromptMiddleware()],
                checkpointer=saver,
            )
        )
        await graph.ainvoke(
            {"messages": [HumanMessage(content="question")]},
            config=config,
            context=AgentRunContext(memory_context=marker),
        )
        checkpoint = await saver.aget_tuple(config)
        assert checkpoint is not None
        contents = [
            str(message.content)
            for message in checkpoint.checkpoint["channel_values"]["messages"]
        ]
        assert contents == ["question", "answer from memory"]
        assert all(marker not in content for content in contents)


def test_agent_manager_runs_select_load_main_extract_without_persisting_memory(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite")
    record = store.save_memory(
        MemoryDraft(
            name="light-ui",
            type="user",
            description="User prefers light UI.",
            body="Use pale purple input fields.",
        )
    )

    class FakeSelector:
        async def select(self, _request, _catalog):
            return SelectionResult([record.id])

    class FakeExtractor:
        messages = None

        async def extract_and_save(self, messages):
            self.messages = list(messages)
            return ExtractionResult(0, [])

    class FakeGraph:
        context = None

        async def astream(self, _payload, **kwargs):
            self.context = kwargs["context"]
            yield "updates", {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="tool output",
                            tool_call_id="call-1",
                            name="read_file",
                        )
                    ]
                }
            }
            yield "updates", {"model": {"messages": [AIMessage(content="done")]}}

    manager = AgentManager()
    manager.base_dir = tmp_path
    manager.tools = []
    manager.memory_store = store
    manager.memory_selector = FakeSelector()  # type: ignore[assignment]
    extractor = FakeExtractor()
    manager.memory_extractor = extractor  # type: ignore[assignment]
    graph = FakeGraph()
    manager._agent_graph = graph
    manager._agent_graph_tools_id = id(manager.tools)

    events = asyncio.run(_collect(manager))
    done = events[-1]

    assert "pale purple" in graph.context.memory_context
    assert [type(message) for message in done["_session_messages"]] == [
        HumanMessage,
        ToolMessage,
        AIMessage,
    ]
    assert all(
        "pale purple" not in str(message.content)
        for message in done["_session_messages"]
    )
    assert extractor.messages == done["_session_messages"]


async def _collect(manager: AgentManager) -> list[dict]:
    return [event async for event in manager.astream("update the input", [])]
