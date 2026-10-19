"""MCP 连接管理器单测：并发连接、失败隔离、超时兜底、关闭与协议回退。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import mcp.types as mtypes
import pytest

from crowcode.mcp import manager as manager_mod
from crowcode.mcp.config import Config, ServerConfig
from crowcode.mcp.manager import new_manager

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "legacy_mcp_server.py"


def _remote(name: str = "echo") -> mtypes.Tool:
    return mtypes.Tool(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object"},
    )


class FakeClient:
    """最小 Client stub：只实现管理器和工具适配用到的接口。"""

    def __init__(
        self,
        tools: list[mtypes.Tool] | None = None,
        protocol_version: str = "2026-07-28",
        list_error: Exception | None = None,
    ) -> None:
        self._tools = tools or []
        self.protocol_version = protocol_version
        self._list_error = list_error

    async def list_tools(self) -> SimpleNamespace:
        if self._list_error is not None:
            raise self._list_error
        return SimpleNamespace(tools=self._tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mtypes.CallToolResult:
        return mtypes.CallToolResult(
            content=[mtypes.TextContent(type="text", text=name)], is_error=False
        )


class FakeContext:
    """可配置延迟 / 异常 / 阻塞的异步上下文。"""

    def __init__(
        self,
        client: FakeClient | None = None,
        enter_error: Exception | None = None,
        enter_hang: bool = False,
        exit_hang: bool = False,
    ) -> None:
        self.client = client
        self.enter_error = enter_error
        self.enter_hang = enter_hang
        self.exit_hang = exit_hang
        self.closed = False

    async def __aenter__(self) -> FakeClient:
        if self.enter_hang:
            await asyncio.Event().wait()
        if self.enter_error is not None:
            raise self.enter_error
        assert self.client is not None
        return self.client

    async def __aexit__(self, *exc: object) -> None:
        if self.exit_hang:
            await asyncio.Event().wait()
        self.closed = True


def _patch_open(
    monkeypatch: pytest.MonkeyPatch, mapping: dict[str, FakeContext]
) -> None:
    monkeypatch.setattr(
        manager_mod, "_open_client", lambda srv, version: mapping[srv.command]
    )


def _cfg(**servers: str) -> Config:
    return Config(
        servers={
            name: ServerConfig(type="stdio", command=command)
            for name, command in servers.items()
        }
    )


@pytest.mark.asyncio
async def test_empty_config():
    manager = await new_manager(Config(), "0.1.0")
    assert manager.tools() == []
    assert manager.server_count == 0
    await manager.close()


@pytest.mark.asyncio
async def test_connect_and_sort_tools(monkeypatch):
    ctx = FakeContext(FakeClient([_remote("z"), _remote("a")]))
    _patch_open(monkeypatch, {"fake": ctx})
    manager = await new_manager(_cfg(demo="fake"), "0.1.0")
    try:
        assert [t.name() for t in manager.tools()] == [
            "mcp__demo__a",
            "mcp__demo__z",
        ]
        assert manager._sessions["demo"].protocol_version == "2026-07-28"
        assert manager.server_count == 1
        assert ctx.closed is False
    finally:
        await manager.close()
    assert ctx.closed is True


@pytest.mark.asyncio
async def test_failure_isolation(monkeypatch, capsys):
    """AC8：单 server 失败只跳过自身。"""
    good = FakeContext(FakeClient([_remote("ok")]))
    bad = FakeContext(enter_error=RuntimeError("spawn failed"))
    _patch_open(monkeypatch, {"good": good, "bad": bad})
    manager = await new_manager(_cfg(good="good", bad="bad"), "0.1.0")
    try:
        assert [t.name() for t in manager.tools()] == ["mcp__good__ok"]
        assert "bad" not in manager._sessions
        assert manager.server_count == 1
    finally:
        await manager.close()
    assert "[mcp] warn: connect server bad failed" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_list_tools_failure_isolation(monkeypatch, capsys):
    good = FakeContext(FakeClient([_remote("ok")]))
    broken = FakeContext(FakeClient(list_error=RuntimeError("list failed")))
    _patch_open(monkeypatch, {"good": good, "broken": broken})
    manager = await new_manager(_cfg(good="good", broken="broken"), "0.1.0")
    try:
        assert [t.name() for t in manager.tools()] == ["mcp__good__ok"]
    finally:
        await manager.close()
    assert "list failed" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_connect_timeout(monkeypatch, capsys):
    """AC8：连接卡住时在超时窗口内收尾。"""
    monkeypatch.setattr(manager_mod, "connect_timeout", 0.2)
    slow = FakeContext(enter_hang=True)
    good = FakeContext(FakeClient([_remote("ok")]))
    _patch_open(monkeypatch, {"slow": slow, "good": good})
    start = time.monotonic()
    manager = await new_manager(_cfg(slow="slow", good="good"), "0.1.0")
    elapsed = time.monotonic() - start
    try:
        assert elapsed < 1.0
        assert [t.name() for t in manager.tools()] == ["mcp__good__ok"]
    finally:
        await manager.close()
    assert "timeout after 0.2s" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_close_timeout_does_not_block(monkeypatch, capsys):
    """AC10：某 server 关闭卡住时 5s 兜底返回（测试中缩短）。"""
    ctx = FakeContext(FakeClient([_remote()]), exit_hang=True)
    _patch_open(monkeypatch, {"fake": ctx})
    manager = await new_manager(_cfg(demo="fake"), "0.1.0")
    monkeypatch.setattr(manager_mod, "close_timeout", 0.2)
    start = time.monotonic()
    await manager.close()
    elapsed = time.monotonic() - start
    await asyncio.sleep(0.05)
    assert elapsed < 1.0
    assert "close timeout" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_duplicate_tool_names_deduped(monkeypatch, capsys):
    """AC7/F8：同 server 自报同名工具时后注册者保留并告警。"""
    ctx = FakeContext(FakeClient([_remote("echo"), _remote("echo")]))
    _patch_open(monkeypatch, {"fake": ctx})
    manager = await new_manager(_cfg(demo="fake"), "0.1.0")
    try:
        assert len(manager.tools()) == 1
    finally:
        await manager.close()
    assert "duplicate tool mcp__demo__echo" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_auto_falls_back_to_initialize():
    """AC16：只认 initialize 的 server 自动回退，工具可列出、可调用。"""
    cfg = Config(
        servers={
            "legacy": ServerConfig(
                type="stdio",
                command=sys.executable,
                args=[str(FIXTURE_SERVER)],
                env={"CROWCODE_TEST_ENV": "injected"},
            )
        }
    )
    manager = await new_manager(cfg, "0.1.0")
    try:
        tools = manager.tools()
        assert [t.name() for t in tools] == [
            "mcp__legacy__echo",
            "mcp__legacy__env",
        ]
        assert manager._sessions["legacy"].protocol_version == "2025-11-25"
        result = await tools[0].execute('{"text": "hi"}', timeout=5)
        assert result.is_error is False
        assert result.content == "hi"
        # AC4：配置的 env 已注入子进程。
        env_result = await tools[1].execute("{}", timeout=5)
        assert env_result.content == "injected"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_http_transport_headers_and_modern_protocol(monkeypatch):
    """AC5：HTTP 传输、自定义 headers 与现代协议头。"""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        payload = json.loads(request.content)
        method = payload.get("method")
        req_id = payload.get("id")
        if method == "server/discover":
            result: dict[str, Any] = {
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
                "ttlMs": 0,
                "cacheScope": "private",
                "resultType": "complete",
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "echo",
                        "inputSchema": {"type": "object"},
                    }
                ],
                "ttlMs": 0,
                "cacheScope": "private",
                "resultType": "complete",
            }
        elif method == "tools/call":
            result = {
                "content": [{"type": "text", "text": "http-ok"}],
                "isError": False,
                "resultType": "complete",
            }
        else:
            return httpx2.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "no"},
                },
            )
        return httpx2.Response(
            200, json={"jsonrpc": "2.0", "id": req_id, "result": result}
        )

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        manager_mod.httpx2,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx2.MockTransport(handler), **kw),
    )
    cfg = Config(
        servers={
            "web": ServerConfig(
                type="http",
                url="https://example.test/mcp",
                headers={"Authorization": "Bearer tok"},
            )
        }
    )
    manager = await new_manager(cfg, "0.1.0")
    try:
        assert manager._sessions["web"].protocol_version == "2026-07-28"
        tools = manager.tools()
        assert [t.name() for t in tools] == ["mcp__web__echo"]
        result = await tools[0].execute("{}", timeout=5)
        assert result.content == "http-ok"
    finally:
        await manager.close()

    for request in requests:
        assert request.headers["authorization"] == "Bearer tok"
    by_method = {
        json.loads(request.content).get("method"): request for request in requests
    }
    discover = by_method["server/discover"]
    assert discover.headers["mcp-method"] == "server/discover"
    assert discover.headers["mcp-protocol-version"] == "2026-07-28"
    meta = json.loads(discover.content)["params"]["_meta"]
    assert "io.modelcontextprotocol/protocolVersion" in meta
    call = by_method["tools/call"]
    assert call.headers["mcp-method"] == "tools/call"
    assert call.headers["mcp-name"] == "echo"
