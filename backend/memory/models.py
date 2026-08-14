from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MemoryType = Literal["user", "project", "feedback", "reference"]
MEMORY_TYPES: frozenset[str] = frozenset(
    {"user", "project", "feedback", "reference"}
)


@dataclass(frozen=True)
class MemoryDraft:
    name: str
    type: MemoryType
    description: str
    body: str


@dataclass(frozen=True)
class MemoryMetadata:
    id: str
    name: str
    type: MemoryType
    description: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MemoryRecord(MemoryMetadata):
    body: str
