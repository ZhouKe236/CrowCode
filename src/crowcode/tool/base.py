"""工具层基础：统一工具抽象、执行结果与通用参数/截断辅助。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

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
