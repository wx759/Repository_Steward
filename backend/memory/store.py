from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, cast
from uuid import uuid4

from .models import MEMORY_TYPES, MemoryDraft, MemoryMetadata, MemoryRecord, MemoryType


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """SQLite-backed persistence boundary for long-term memories."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    type        TEXT NOT NULL CHECK (
                                    type IN ('user', 'project', 'feedback', 'reference')
                                ),
                    description TEXT NOT NULL,
                    body        TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_memories_name_nocase
                ON memories(name COLLATE NOCASE);

                CREATE INDEX IF NOT EXISTS ix_memories_updated_at
                ON memories(updated_at DESC);
                """
            )

    @staticmethod
    def _clean_draft(draft: MemoryDraft) -> MemoryDraft:
        name = draft.name.strip()
        memory_type = str(draft.type).strip().lower()
        description = draft.description.strip()
        body = draft.body.strip()
        if not name or not description or not body:
            raise ValueError("Memory name, description, and body must not be empty")
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"Unsupported memory type: {memory_type}")
        return MemoryDraft(
            name=name,
            type=cast(MemoryType, memory_type),
            description=description,
            body=body,
        )

    @staticmethod
    def _metadata_from_row(row: sqlite3.Row) -> MemoryMetadata:
        return MemoryMetadata(
            id=str(row["id"]),
            name=str(row["name"]),
            type=cast(MemoryType, str(row["type"])),
            description=str(row["description"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @classmethod
    def _record_from_row(cls, row: sqlite3.Row) -> MemoryRecord:
        metadata = cls._metadata_from_row(row)
        return MemoryRecord(
            id=metadata.id,
            name=metadata.name,
            type=metadata.type,
            description=metadata.description,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
            body=str(row["body"]),
        )

    def list_metadata(self) -> list[MemoryMetadata]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name, type, description, created_at, updated_at
                FROM memories
                ORDER BY updated_at DESC, name COLLATE NOCASE ASC
                """
            ).fetchall()
        return [self._metadata_from_row(row) for row in rows]

    def get_memories(self, ids: Sequence[str]) -> list[MemoryRecord]:
        ordered_ids = list(dict.fromkeys(str(memory_id) for memory_id in ids if memory_id))
        if not ordered_ids:
            return []
        placeholders = ",".join("?" for _ in ordered_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders})",
                ordered_ids,
            ).fetchall()
        records = {str(row["id"]): self._record_from_row(row) for row in rows}
        return [records[memory_id] for memory_id in ordered_ids if memory_id in records]

    def save_memory(self, draft: MemoryDraft) -> MemoryRecord:
        cleaned = self._clean_draft(draft)
        memory_id = uuid4().hex
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, name, type, description, body, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        cleaned.name,
                        cleaned.type,
                        cleaned.description,
                        cleaned.body,
                        now,
                        now,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Memory name already exists: {cleaned.name}") from exc
        return MemoryRecord(
            id=memory_id,
            name=cleaned.name,
            type=cleaned.type,
            description=cleaned.description,
            body=cleaned.body,
            created_at=now,
            updated_at=now,
        )

    def update_memory(self, memory_id: str, draft: MemoryDraft) -> MemoryRecord:
        cleaned = self._clean_draft(draft)
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT created_at FROM memories WHERE id = ?",
                    (memory_id,),
                ).fetchone()
                if existing is None:
                    raise KeyError(f"Memory not found: {memory_id}")
                connection.execute(
                    """
                    UPDATE memories
                    SET name = ?, type = ?, description = ?, body = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        cleaned.name,
                        cleaned.type,
                        cleaned.description,
                        cleaned.body,
                        now,
                        memory_id,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Memory name already exists: {cleaned.name}") from exc
        return MemoryRecord(
            id=memory_id,
            name=cleaned.name,
            type=cleaned.type,
            description=cleaned.description,
            body=cleaned.body,
            created_at=str(existing["created_at"]),
            updated_at=now,
        )

    def delete_memory(self, memory_id: str) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM memories WHERE id = ?",
                (memory_id,),
            )
            connection.commit()
        return cursor.rowcount > 0
