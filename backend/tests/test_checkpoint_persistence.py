from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from graph.agent_factory import AgentConfig, create_agent_from_config
from graph.agent import AgentManager
from middleware import AgentRunContext, ContextManagementMiddleware
from service.context_manager import ContextManager, ContextPolicy


def test_l4_checkpoint_survives_later_turns_and_database_reopen(tmp_path: Path) -> None:
    asyncio.run(_exercise_checkpoint_recovery(tmp_path))


async def _exercise_checkpoint_recovery(tmp_path: Path) -> None:
    database_path = tmp_path / "checkpoints.sqlite"
    thread_id = "session-checkpoint-test"
    config = {"configurable": {"thread_id": thread_id}}
    policy = ContextPolicy(
        max_context_tokens=200,
        context_token_reserve=0,
        max_messages=50,
        summary_keep_recent_messages=2,
    )
    summary_text = "Current goal: verify checkpoint persistence."

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        await saver.setup()
        summary_model = FakeListChatModel(responses=[summary_text])
        main_model = FakeMessagesListChatModel(
            responses=[AIMessage(content="first answer"), AIMessage(content="second answer")]
        )
        graph = create_agent_from_config(
            AgentConfig(
                llm=main_model,
                tools=[],
                system_prompt="test",
                middleware=[
                    ContextManagementMiddleware(
                        ContextManager(tmp_path, policy),
                        summary_model,
                    )
                ],
                checkpointer=saver,
            )
        )

        first = await graph.ainvoke(
            {"messages": [HumanMessage(content="very long history " * 300)]},
            config=config,
            context=AgentRunContext(session_id=thread_id),
        )
        assert str(first["messages"][0].content).startswith("[Compacted context]")
        assert summary_text in str(first["messages"][0].content)

        second = await graph.ainvoke(
            {"messages": [HumanMessage(content="follow-up question")]},
            config=config,
            context=AgentRunContext(session_id=thread_id),
        )
        second_contents = [str(message.content) for message in second["messages"]]
        assert any(summary_text in content for content in second_contents)
        assert "follow-up question" in second_contents
        assert not any("very long history" in content for content in second_contents)

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as reopened_saver:
        await reopened_saver.setup()
        reopened_graph = create_agent_from_config(
            AgentConfig(
                llm=FakeMessagesListChatModel(
                    responses=[AIMessage(content="answer after restart")]
                ),
                tools=[],
                system_prompt="test",
                middleware=[
                    ContextManagementMiddleware(
                        ContextManager(tmp_path, policy),
                        FakeListChatModel(responses=["unused"]),
                    )
                ],
                checkpointer=reopened_saver,
            )
        )
        restored = await reopened_graph.ainvoke(
            {"messages": [HumanMessage(content="question after restart")]},
            config=config,
            context=AgentRunContext(session_id=thread_id),
        )
        restored_contents = [str(message.content) for message in restored["messages"]]
        assert any(summary_text in content for content in restored_contents)
        assert "follow-up question" in restored_contents
        assert "question after restart" in restored_contents
        assert "answer after restart" in restored_contents
        assert not any("very long history" in content for content in restored_contents)

        manager = AgentManager()
        manager.initialize(tmp_path, checkpointer=reopened_saver)
        assert manager.session_manager is not None
        manager.session_manager.load_session_record(thread_id)
        await manager.delete_session(thread_id)
        assert not (tmp_path / "sessions" / f"{thread_id}.json").exists()
        assert await reopened_saver.aget_tuple(config) is None
