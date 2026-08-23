import asyncio
from pathlib import Path

from api.memories import ArchiveMemoryRequest, archive_memory, list_memories
from graph.agent import agent_manager
from memory import MemoryDraft, MemoryStore
from service.workspace_manager import WorkspaceManager


def test_memory_api_lists_scoped_records_and_archives_active_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "repositories"
    repository = workspace_root / "demo"
    repository.mkdir(parents=True)
    manager = WorkspaceManager(tmp_path / "state", workspace_root)
    workspace = manager.create_workspace(repository)
    store = MemoryStore(tmp_path / "memory.sqlite")
    record = store.save_memory(
        MemoryDraft(
            "language",
            "user",
            "Preferred language",
            "Use Chinese",
            evidence_quote="以后都用中文回答",
        )
    )
    monkeypatch.setattr(agent_manager, "workspace_manager", manager)
    monkeypatch.setattr(agent_manager, "memory_store", store)

    listed = asyncio.run(list_memories(workspace.workspace_id, None, None))
    assert listed[0]["id"] == record.id
    assert listed[0]["evidence_quote"] == "以后都用中文回答"

    response = asyncio.run(
        archive_memory(record.id, ArchiveMemoryRequest(workspace_id=workspace.workspace_id))
    )
    assert response == {"ok": True}
    assert store.list_metadata(status=None)[0].status == "archived"
