"""MCP 工具适配单测：命名、字段映射与 execute 各分支。"""

from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as mtypes
import pytest

from crowcode.mcp import tool as tool_mod
from crowcode.mcp.tool import adapt_tool


class StubSession:
    """可注入的 CallerSession stub。"""

    def __init__(
        self,
        result: mtypes.CallToolResult | None = None,
        exc: Exception | None = None,
        hang: bool = False,
    ) -> None:
        self.result = result
        self.exc = exc
        self.hang = hang
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mtypes.CallToolResult:
        self.calls.append((name, arguments))
        if self.hang:
            await asyncio.Event().wait()
        if self.exc is not None:
            raise self.exc
        assert self.result is not None
        return self.result


def _tool(
    name: str = "echo",
    description: str | None = "回声",
    schema: dict[str, Any] | None = None,
    read_only: bool | None = None,
) -> mtypes.Tool:
    annotations = (
        None if read_only is None else mtypes.ToolAnnotations(read_only_hint=read_only)
    )
    return mtypes.Tool(
        name=name,
        description=description,
        input_schema=schema if schema is not None else {"type": "object"},
        annotations=annotations,
    )


def _result(*blocks: Any, is_error: bool = False) -> mtypes.CallToolResult:
    return mtypes.CallToolResult(content=list(blocks), is_error=is_error)


def _text(content: str) -> mtypes.TextContent:
    return mtypes.TextContent(type="text", text=content)


def test_adapt_name_description_schema():
    """AC6：命名拼接、描述透传、schema 透传。"""
    tool = adapt_tool("demo", _tool(schema={"type": "object", "a": 1}), StubSession())
    assert tool is not None
    assert tool.name() == "mcp__demo__echo"
    assert tool.description() == "回声"
    assert tool.parameters() == {"type": "object", "a": 1}


def test_adapt_empty_description_and_schema():
    tool = adapt_tool("demo", _tool(description="", schema={}), StubSession())
    assert tool is not None
    assert "demo" in tool.description()
    assert tool.parameters() == {"type": "object"}


def test_adapt_illegal_name(capsys):
    """AC7：拼接后含非法字符 → 跳过 + 告警。"""
    assert adapt_tool("bad.name", _tool(), StubSession()) is None
    assert adapt_tool("demo", _tool(name="a.b"), StubSession()) is None
    err = capsys.readouterr().err
    assert "illegal characters" in err


def test_adapt_read_only_is_strict():
    """AC6：仅 readOnlyHint is True 才算只读。"""
    assert adapt_tool("s", _tool(), StubSession()).read_only is False
    assert adapt_tool("s", _tool(read_only=False), StubSession()).read_only is False
    assert adapt_tool("s", _tool(read_only=True), StubSession()).read_only is True


@pytest.mark.asyncio
async def test_execute_joins_text_blocks_in_order():
    session = StubSession(_result(_text("a"), _text("b")))
    tool = adapt_tool("demo", _tool(), session)
    assert tool is not None
    result = await tool.execute("{}", timeout=5)
    assert result.content == "a\nb"
    assert result.is_error is False
    assert session.calls == [("echo", None)]


@pytest.mark.asyncio
async def test_execute_passes_arguments():
    session = StubSession(_result(_text("ok")))
    tool = adapt_tool("demo", _tool(), session)
    assert tool is not None
    await tool.execute('{"x": 1}', timeout=5)
    assert session.calls == [("echo", {"x": 1})]


@pytest.mark.asyncio
async def test_execute_invalid_json():
    tool = adapt_tool("demo", _tool(), StubSession())
    assert tool is not None
    result = await tool.execute("not-json", timeout=5)
    assert result.is_error is True
    assert "参数解析失败" in result.content


@pytest.mark.asyncio
async def test_execute_remote_is_error():
    """AC6：远端 isError 映射到 Result.is_error。"""
    session = StubSession(_result(_text("boom"), is_error=True))
    tool = adapt_tool("demo", _tool(), session)
    assert tool is not None
    result = await tool.execute("{}", timeout=5)
    assert result.is_error is True
    assert result.content == "boom"


@pytest.mark.asyncio
async def test_execute_protocol_exception():
    """AC9：协议错转结构化错误。"""
    session = StubSession(exc=RuntimeError("transport down"))
    tool = adapt_tool("demo", _tool(), session)
    assert tool is not None
    result = await tool.execute("{}", timeout=5)
    assert result.is_error is True
    assert "MCP 工具调用失败" in result.content
    assert "transport down" in result.content


@pytest.mark.asyncio
async def test_execute_timeout(monkeypatch):
    """AC9：调用超时转结构化错误。"""
    monkeypatch.setattr(tool_mod, "call_timeout", 0.05)
    session = StubSession(hang=True)
    tool = adapt_tool("demo", _tool(), session)
    assert tool is not None
    result = await tool.execute("{}")
    assert result.is_error is True
    assert "超时" in result.content


@pytest.mark.asyncio
async def test_execute_drops_non_text_and_warns_once(monkeypatch, capsys):
    """AC6：非 text 块丢弃；同一工具只告警一次。"""
    monkeypatch.setattr(tool_mod, "_non_text_warn_once", set())
    image = mtypes.ImageContent(type="image", data="", mime_type="image/png")
    session = StubSession(_result(_text("only"), image))
    tool = adapt_tool("demo", _tool(), session)
    assert tool is not None
    first = await tool.execute("{}", timeout=5)
    second = await tool.execute("{}", timeout=5)
    assert first.content == "only"
    assert second.content == "only"
    err = capsys.readouterr().err
    assert err.count("non-text content blocks") == 1
