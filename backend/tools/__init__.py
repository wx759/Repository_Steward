from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool

from tools.read_file_tool import ReadFileTool
from tools.terminal_tool import TerminalTool


def get_all_tools(base_dir: Path) -> list[BaseTool]:
    return [
        ReadFileTool(root_dir=base_dir),
        TerminalTool(root_dir=base_dir),
    ]
