"""MCP 工具适配：把远端工具包装成 crowcode ``Tool`` 协议对象。"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Protocol

import mcp.types as mtypes

from crowcode.tool import DEFAULT_TIMEOUT, Result

# LLM 工具名允许的字符集（与 OpenAI / Anthropic 的共同安全子集一致）。
_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

# 单次 call_tool 的超时（F10，内置不可配；模块级便于单测临时改小）。
call_timeout: float = 30.0

# 已告警过「返回非文本内容块」的工具全名，避免每轮刷屏。
_non_text_warn_once: set[str] = set()


class CallerSession(Protocol):
    """工具执行所需的最小会话协议；生产环境由 SDK ``Client`` 实现。"""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mtypes.CallToolResult: ...


@dataclass
class McpTool:
    """一个远端 MCP 工具在 crowcode 中的适配视图。"""

    full_name: str
    remote_name: str
    _description: str
    _parameters: dict[str, Any]
    read_only: bool
    caller: CallerSession

    def name(self) -> str:
        """模型看到的工具名：``mcp__<server>__<tool>``。"""
        return self.full_name

    def description(self) -> str:
        """给模型的用途说明。"""
        return self._description

    def parameters(self) -> dict[str, Any]:
        """远端 ``inputSchema`` 透传（JSON Schema）。"""
        return self._parameters

    async def execute(self, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        """解析 raw JSON 参数并调用远端工具；所有错误均转为结构化结果。"""
        parsed = _parse_args(args)
        if isinstance(parsed, Result):
            return parsed
        arg_map = parsed or None
        effective = min(timeout, call_timeout) if timeout else call_timeout
        try:
            remote = await asyncio.wait_for(
                self.caller.call_tool(self.remote_name, arg_map),
                timeout=effective,
            )
        except TimeoutError:
            return Result(content=f"MCP 工具调用超时（{effective:g}s）", is_error=True)
        except Exception as e:  # noqa: BLE001 —— 协议错统一回灌给模型
            return Result(content=f"MCP 工具调用失败: {e}", is_error=True)

        texts: list[str] = []
        dropped = False
        for block in remote.content or []:
            if isinstance(block, mtypes.TextContent):
                texts.append(block.text)
            else:
                dropped = True
        if dropped and self.full_name not in _non_text_warn_once:
            _non_text_warn_once.add(self.full_name)
            print(
                f"[mcp] warn: tool {self.full_name} returned non-text "
                "content blocks (dropped)",
                file=sys.stderr,
            )
        return Result(content="\n".join(texts), is_error=bool(remote.is_error))


def _parse_args(args: str) -> dict[str, Any] | Result:
    """把 raw JSON 字符串解析为 dict；失败返回结构化错误。"""
    try:
        data = json.loads(args or "{}")
    except (json.JSONDecodeError, TypeError) as e:
        return Result(content=f"参数解析失败: {e}", is_error=True)
    if not isinstance(data, dict):
        return Result(content="参数应为 JSON 对象", is_error=True)
    return data


def adapt_tool(
    server_name: str, remote: mtypes.Tool, client: CallerSession
) -> McpTool | None:
    """把 SDK 的 ``Tool`` 适配为 :class:`McpTool`；名字非法时返回 ``None``。

    - 名字：``mcp__<server>__<tool>``，拼接后必须只含 ``[A-Za-z0-9_-]``；
    - 描述：直接取远端描述，空则给含 server 名的兜底说明；
    - 参数：``inputSchema`` 透传，空则兜底 ``{"type": "object"}``；
    - 只读：仅当 ``annotations.readOnlyHint is True`` 时为 True（安全默认）。
    """
    full_name = f"mcp__{server_name}__{remote.name}"
    if _VALID_NAME.fullmatch(full_name) is None:
        print(
            f"[mcp] warn: skip tool {full_name}: name contains illegal characters",
            file=sys.stderr,
        )
        return None

    description = remote.description or (
        f"来自 MCP server {server_name} 的工具 {remote.name}"
    )
    schema = dict(remote.input_schema or {})
    if not schema:
        schema = {"type": "object"}
    annotations = remote.annotations
    read_only = bool(annotations is not None and annotations.read_only_hint is True)
    return McpTool(
        full_name=full_name,
        remote_name=remote.name,
        _description=description,
        _parameters=schema,
        read_only=read_only,
        caller=client,
    )
