"""read_file 工具：读取文件并带行号返回文本。"""

from __future__ import annotations

from pathlib import Path

from crowcode.tool import DEFAULT_TIMEOUT, Result, _parse_args, _truncate

_MAX_LINES = 2000
_MAX_CHARS = 256 * 1024


class ReadFileTool:
    """读文件：带行号；超出行/字节上限截断并标注 ``[truncated]``（N5/AC2）。"""

    read_only = True

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "读取指定文件的文本内容（带行号），用于查看代码或文档。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径"},
            },
            "required": ["path"],
        }

    async def execute(self, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        data = _parse_args(args)
        if isinstance(data, Result):
            return data
        path = data.get("path")
        if not path:
            return Result(content="缺少参数 path", is_error=True)
        p = Path(path)
        if p.is_dir():
            return Result(content=f"{path} 是一个目录，请指定具体文件", is_error=True)
        if not p.exists():
            return Result(content=f"文件不存在: {path}", is_error=True)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return Result(content=f"读取失败: {e}", is_error=True)
        numbered = "\n".join(
            f"{n:6d}\t{line}" for n, line in enumerate(text.splitlines(), 1)
        )
        return Result(content=_truncate(numbered, _MAX_LINES, _MAX_CHARS))
