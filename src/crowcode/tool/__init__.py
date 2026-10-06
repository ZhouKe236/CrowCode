"""工具层：统一工具抽象、执行结果、注册中心与 6 个核心工具。

所有工具执行失败都以 :class:`Result`（``is_error=True``）返回，从不向调用方抛异常
（F9/N4）；耗时工具通过 ``timeout`` 参数自行控制超时（bash 超时即终止并回收子进程）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from crowcode.llm import ToolDefinition

# 单个工具执行的默认超时秒数（N1，不可配）。
DEFAULT_TIMEOUT: float = 30.0


@dataclass
class Result:
    """工具执行结果——永远以值类型返回，从不抛 Python 异常给上层。"""

    content: str  # 回灌给模型的文本（已截断/带行号等）
    is_error: bool = False  # True 表示结构化错误，content 即错误描述


@runtime_checkable
class Tool(Protocol):
    """统一工具抽象（F1）。"""

    read_only: bool  # True=只读工具（可并发执行 & Plan Mode 放行）

    def name(self) -> str:
        """模型看到的工具名，如 ``read_file``。"""
        ...

    def description(self) -> str:
        """给模型的用途说明。"""
        ...

    def parameters(self) -> dict[str, Any]:
        """手写 JSON Schema（type/properties/required/description）。"""
        ...

    async def execute(self, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        """执行工具；``args`` 是 raw JSON 字符串，``timeout`` 为该工具的耗时上限。"""
        ...


def _parse_args(args: str) -> dict[str, Any] | Result:
    """把 raw JSON 参数字符串解析为 dict；失败返回 ``is_error`` 结果。空串归一为 ``{}``。"""
    try:
        data = json.loads(args or "{}")
    except json.JSONDecodeError as e:
        return Result(content=f"参数解析失败: {e}", is_error=True)
    if not isinstance(data, dict):
        return Result(content="参数应为 JSON 对象", is_error=True)
    return data


def _truncate(s: str, max_lines: int, max_chars: int) -> str:
    """超长文本截断：先按行、再按字符；发生截断时尾部追加 ``[truncated]`` 标注。"""
    truncated = False
    lines = s.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars]
        truncated = True
    if truncated:
        out += "\n[truncated]"
    return out


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
