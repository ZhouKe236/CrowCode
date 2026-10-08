"""edit_file 工具：对唯一匹配的原文片段做替换。"""

from __future__ import annotations

from pathlib import Path

from .base import DEFAULT_TIMEOUT, Result, _parse_args


class EditFileTool:
    """改文件：唯一匹配替换；0 处或多于 1 处返回含匹配数的可区分错误（AC4）。"""

    read_only = False

    def name(self) -> str:
        return "edit_file"

    def description(self) -> str:
        return (
            "将文件中唯一匹配的原文片段替换为新片段；匹配不唯一或不存在时返回错误。"
            "编辑前请先用 read_file 读取目标文件，确认 old_string 唯一。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要修改的文件路径"},
                "old_string": {
                    "type": "string",
                    "description": "要替换的原文片段（须在文件中唯一匹配）",
                },
                "new_string": {"type": "string", "description": "替换后的新片段"},
            },
            "required": ["path", "old_string", "new_string"],
        }

    async def execute(self, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        data = _parse_args(args)
        if isinstance(data, Result):
            return data
        path = data.get("path")
        old_string = data.get("old_string")
        new_string = data.get("new_string")
        if not isinstance(path, str) or not path:
            return Result(content="缺少参数 path", is_error=True)
        if not isinstance(old_string, str) or not old_string:
            return Result(content="缺少参数 old_string", is_error=True)
        if not isinstance(new_string, str):
            return Result(content="缺少参数 new_string", is_error=True)
        p = Path(path)
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return Result(content=f"读取失败: {e}", is_error=True)
        n = content.count(old_string)
        if n == 0:
            return Result(content="未找到匹配的内容", is_error=True)
        if n > 1:
            return Result(
                content=f"匹配到 {n} 处，old_string 不唯一，请提供更长上下文使其唯一",
                is_error=True,
            )
        new_content = content.replace(old_string, new_string, 1)
        try:
            p.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return Result(content=f"写入失败: {e}", is_error=True)
        return Result(content=f"已修改 {path}")
