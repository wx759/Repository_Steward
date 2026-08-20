from __future__ import annotations

import fnmatch
import shlex
from pathlib import Path

from tools import ReadFileTool, TerminalTool, WriteFileTool
from pydantic import PrivateAttr


ROLE_DEFAULT_PATHS: dict[str, list[str]] = {
    "backend": ["backend/**"],
    "frontend": ["frontend/**"],
    "test": ["tests/**", "backend/tests/**", "frontend/**/*.test.*"],
    "general": ["**"],
}

REVIEW_COMMANDS = {
    "pytest",
    "python -m pytest",
    "npm test",
    "npm run test",
    "npm run lint",
    "npm run build",
    "git diff",
    "git status",
    "git log",
    "ruff check",
    "mypy",
}


def path_allowed(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return any(
        pattern == "**"
        or fnmatch.fnmatch(normalized, pattern)
        or (pattern.endswith("/**") and normalized == pattern[:-3])
        for pattern in patterns
    )


class ScopedWriteFileTool(WriteFileTool):
    _allowed_paths: list[str] = PrivateAttr()

    def __init__(self, root_dir: Path, allowed_paths: list[str], **kwargs) -> None:
        super().__init__(root_dir, **kwargs)
        self._allowed_paths = allowed_paths

    def _run(self, path: str, content: str, run_manager=None) -> str:
        if not path_allowed(path, self._allowed_paths):
            return f"Write denied: {path} is outside allowed_paths"
        return super()._run(path, content, run_manager)


class ReviewerTerminalTool(TerminalTool):
    def _run(self, command: str, run_manager=None) -> str:
        normalized = " ".join(command.strip().split())
        if not any(normalized == item or normalized.startswith(item + " ") for item in REVIEW_COMMANDS):
            return "Command denied: reviewer terminal is limited to checks and tests."
        if any(token in command for token in (">", "|", ";", "&&", "||")):
            return "Command denied: shell composition is not allowed for reviewer."
        try:
            shlex.split(command)
        except ValueError:
            return "Command denied: invalid shell syntax."
        return super()._run(command, run_manager)


def worker_tools(root_dir: Path, role: str, allowed_paths: list[str]):
    scope = allowed_paths or ROLE_DEFAULT_PATHS[role]
    return [
        ReadFileTool(root_dir),
        ScopedWriteFileTool(root_dir, scope),
        TerminalTool(root_dir),
    ]


def reviewer_tools(root_dir: Path):
    return [ReadFileTool(root_dir), ReviewerTerminalTool(root_dir)]
