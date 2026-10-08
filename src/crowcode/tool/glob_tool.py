"""glob 工具：按模式找文件。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .base import DEFAULT_TIMEOUT, Result, _parse_args

_MAX_RESULTS = 100


class GlobTool:
    """按模式找文件：返回匹配的文件路径列表（≤100，排序）。"""

    read_only = True

    def name(self) -> str:
        return "glob"

    def description(self) -> str:
        return "按 glob 模式（如 ``**/*.py``）查找匹配的文件，返回路径列表。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "glob 模式，如 ``**/*.py``",
                },
                "path": {
                    "type": "string",
                    "description": "起始目录，默认当前工作目录",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        data = _parse_args(args)
        if isinstance(data, Result):
            return data
        pattern = data.get("pattern")
        if not pattern:
            return Result(content="缺少参数 pattern", is_error=True)
        root = Path(data.get("path") or ".")
        matches: list[str] = []
        for i, p in enumerate(root.glob(pattern)):
            if i % 100 == 0:
                await asyncio.sleep(0)
            if p.is_file():
                matches.append(str(p))
        if not matches:
            return Result(content="无匹配")
        matches = sorted(matches)[:_MAX_RESULTS]
        return Result(content="\n".join(matches))
