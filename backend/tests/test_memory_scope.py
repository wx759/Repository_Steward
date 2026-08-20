from pathlib import Path

from memory import MemoryDraft, MemoryStore


def test_memory_catalog_is_scoped_to_user_and_current_workspace(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite")
    user = store.save_memory(
        MemoryDraft("language", "user", "Preferred language", "Use Chinese", workspace_id="ignored")
    )
    project_a = store.save_memory(
        MemoryDraft("stack-a", "project", "A stack", "FastAPI", workspace_id="a")
    )
    project_b = store.save_memory(
        MemoryDraft("stack-b", "project", "B stack", "Django", workspace_id="b")
    )

    scoped_a = {item.id for item in store.list_scoped_metadata("a")}
    scoped_b = {item.id for item in store.list_scoped_metadata("b")}
    assert scoped_a == {user.id, project_a.id}
    assert scoped_b == {user.id, project_b.id}
    assert user.workspace_id is None
