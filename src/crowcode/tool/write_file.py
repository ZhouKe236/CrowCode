"""write_file 工具：覆盖写文件，父目录不存在时自动创建。"""

from __future__ import annotations

from pathlib import Path

from crowcode.tool import DEFAULT_TIMEOUT, Result, _parse_args


class WriteFileTool:
    """写文件：覆盖写；父目录缺失时自动创建（AC3）。"""

    read_only = False

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "将内容写入（覆盖）指定文件；父目录不存在时自动创建。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的完整内容"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        data = _parse_args(args)
        if isinstance(data, Result):
            return data
        path = data.get("path")
        content = data.get("content")
        if not path:
            return Result(content="缺少参数 path", is_error=True)
        if content is None:
            return Result(content="缺少参数 content", is_error=True)
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as e:
            return Result(content=f"写入失败: {e}", is_error=True)
        return Result(content=f"已写入 {path}（{len(content.encode('utf-8'))} 字节）")
