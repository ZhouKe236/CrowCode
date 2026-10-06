"""grep 工具：在文件内容中按正则搜索。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from crowcode.tool import DEFAULT_TIMEOUT, Result, _parse_args

_MAX_HITS = 100
_MAX_LINE_CHARS = 1024 * 1024  # 单行超过约 1MB 视为超长行


class GrepTool:
    """搜代码内容：返回 ``file:line:content`` 命中列表（≤100，超出标注）。"""

    read_only = True

    def name(self) -> str:
        return "grep"

    def description(self) -> str:
        return "在文件内容中按 Python 正则搜索，返回 file:line:content 命中列表。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python 正则表达式"},
                "path": {"type": "string", "description": "起始目录，默认当前工作目录"},
                "glob": {"type": "string", "description": "文件名过滤，如 ``*.py``"},
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
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return Result(content=f"正则非法: {e}", is_error=True)

        root = Path(data.get("path") or ".")
        glob_pat = data.get("glob")
        files = root.rglob(glob_pat) if glob_pat else root.rglob("*")

        hits: list[str] = []
        truncated = False
        for f in files:
            if not f.is_file():
                continue
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:  # noqa: ASYNC230 —— 逐行读，配合 sleep(0) 让出 event loop
                    for lineno, line in enumerate(fh, 1):
                        if len(line) > _MAX_LINE_CHARS:
                            hits.append(f"{f}:{lineno}:[该行过长，未完整搜索]")
                        elif rx.search(line):
                            hits.append(f"{f}:{lineno}:{line.rstrip()}")
                        if len(hits) >= _MAX_HITS:
                            truncated = True
                            break
            except OSError:
                continue
            await asyncio.sleep(0)
            if truncated:
                break
        if not hits:
            return Result(content="无命中")
        if truncated:
            hits.append("... 命中过多，已截断")
        return Result(content="\n".join(hits))
