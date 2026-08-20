from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Type

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class WriteFileInput(BaseModel):
    path: str = Field(..., description="Relative path inside the repository root")
    content: str = Field(..., description="Complete UTF-8 file content")


class WriteFileTool(BaseTool):
    name: str = "write_file"
    description: str = "Write a UTF-8 file below the current repository root."
    args_schema: Type[BaseModel] = WriteFileInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _root_dir: Path = PrivateAttr()

    def __init__(self, root_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = root_dir.resolve()

    def _resolve_path(self, path: str) -> Path:
        candidate = (self._root_dir / path).resolve()
        if self._root_dir not in candidate.parents or candidate == self._root_dir:
            raise ValueError("Path traversal detected.")
        return candidate

    def _run(self, path: str, content: str, run_manager: CallbackManagerForToolRun | None = None) -> str:
        try:
            file_path = self._resolve_path(path)
        except ValueError as exc:
            return f"Write failed: {exc}"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content.encode('utf-8'))} bytes to {path}"

    async def _arun(self, path: str, content: str, run_manager: AsyncCallbackManagerForToolRun | None = None) -> str:
        return await asyncio.to_thread(self._run, path, content, None)
