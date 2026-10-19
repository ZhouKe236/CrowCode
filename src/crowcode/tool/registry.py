"""注册中心：集中登记、按名查找、导出定义、按名执行。"""

from __future__ import annotations

from crowcode.llm import ToolDefinition

from .base import DEFAULT_TIMEOUT, Result, Tool


class Registry:
    """集中登记、按名查找、导出定义、按名执行。"""

    def __init__(self) -> None:
        self._order: list[str] = []  # 保持注册顺序，导出稳定
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None:
        """登记工具；重名抛 :class:`ValueError`。"""
        name = t.name()
        if name in self._tools:
            raise ValueError(f"工具名重复: {name}")
        self._order.append(name)
        self._tools[name] = t

    def get(self, name: str) -> Tool | None:
        """按名查找，未登记返回 ``None``。"""
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        """按注册顺序导出协议无关的工具定义列表（F3/AC1）。"""
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
        ]

    def read_only_definitions(self) -> list[ToolDefinition]:
        """Plan Mode：只导出 ``read_only is True`` 的工具定义，保留注册顺序。"""
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
            if self._tools[name].read_only
        ]

    def is_read_only(self, name: str) -> bool:
        """分批判定；未知工具返回 False（按串行处理）。"""
        t = self.get(name)
        return t is not None and t.read_only

    async def execute(
        self, name: str, args: str, timeout: float = DEFAULT_TIMEOUT
    ) -> Result:
        """按名执行工具，受超时保护；未知工具 / 超时 / 异常均包成 ``is_error`` 结果（F5/F9）。"""
        tool = self.get(name)
        if tool is None:
            return Result(content=f"未知工具: {name}", is_error=True)
        try:
            return await tool.execute(args, timeout)
        except TimeoutError:
            return Result(content=f"工具 {name} 执行超时（{timeout}s）", is_error=True)
        except Exception as e:  # noqa: BLE001 —— 工具内部任何异常兜底为结构化错误
            return Result(content=f"工具 {name} 异常: {e}", is_error=True)


def new_default_registry() -> Registry:
    """构造并注册 6 个核心工具，返回 :class:`Registry`（F2）。"""
    from .bash import BashTool
    from .edit_file import EditFileTool
    from .glob_tool import GlobTool
    from .grep_tool import GrepTool
    from .read_file import ReadFileTool
    from .write_file import WriteFileTool

    registry = Registry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    return registry
