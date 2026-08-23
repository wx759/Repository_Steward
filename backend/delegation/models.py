from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator


WorkerRole = Literal["backend", "frontend", "test", "general"]
CommandName = Literal[
    "pytest",
    "ruff_check",
    "mypy",
    "npm_test",
    "npm_lint",
    "npm_build",
]


class TaskSpec(BaseModel):
    role: WorkerRole
    description: str = Field(..., min_length=1, max_length=4_000)
    context: str = Field(default="", max_length=12_000)
    allowed_paths: list[str] = Field(default_factory=list, max_length=50)
    allowed_commands: list[CommandName] = Field(default_factory=list, max_length=10)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("allowed_paths")
    @classmethod
    def validate_allowed_paths(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            normalized = pattern.replace("\\", "/")
            path = PurePosixPath(normalized)
            if (
                not normalized
                or path.is_absolute()
                or PureWindowsPath(normalized).is_absolute()
                or ".." in path.parts
            ):
                raise ValueError("allowed_paths must be repository-relative glob patterns")
        return patterns


class WorkerResult(BaseModel):
    status: Literal["success", "failed"]
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    tests_run: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    feedback: str = ""
