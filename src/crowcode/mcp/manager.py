"""MCP 连接管理器：并发连接、协议协商、列工具与统一关闭。

每个 server 由一个独立的 keeper 任务持有完整的连接上下文。MCP SDK 的传输层
用 anyio task group 实现，而 anyio 要求「进入/退出 cancel scope 必须在同一个
asyncio 任务内」，因此连接不能像普通资源一样在主任务里统一 exit——必须由
创建它的任务自己退出。keeper 任务在连接成功后等待停止信号，收到信号后在同一
任务内退出上下文，从而既并发连接又干净收尾。
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import httpx2
import mcp.types as mtypes
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from .config import Config, ServerConfig
from .tool import McpTool, adapt_tool

# 内置不可配超时；模块级变量便于单测临时改小。
connect_timeout: float = 30.0
close_timeout: float = 5.0


def _warn(msg: str) -> None:
    print(f"[mcp] warn: {msg}", file=sys.stderr)


@dataclass
class _Session:
    """一个成功建立的连接（供 close 发停止信号）。"""

    name: str
    client: Any
    protocol_version: str
    stop: asyncio.Event
    task: asyncio.Task[None]


class _HttpClientContext:
    """HTTP transport 上下文：同时持有 httpx2 客户端与 MCP Client。

    ``streamable_http_client`` 只使用外部传入的 httpx 客户端、不负责关闭它，
    所以这里用同一个 ExitStack 管理两者，确保退出时 HTTP 连接被释放。
    """

    def __init__(self, srv: ServerConfig, info: mtypes.Implementation) -> None:
        self._srv = srv
        self._info = info
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> Client:
        stack = AsyncExitStack()
        self._stack = stack
        try:
            await stack.__aenter__()
            http_client = await stack.enter_async_context(
                httpx2.AsyncClient(headers=self._srv.headers)
            )
            transport = streamable_http_client(self._srv.url, http_client=http_client)
            return await stack.enter_async_context(
                Client(transport, mode="auto", client_info=self._info)
            )
        except BaseException:
            await stack.aclose()
            raise

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()


def _open_client(srv: ServerConfig, version: str):
    """按类型构造连接上下文；返回 ``Client`` 或 ``_HttpClientContext``。"""
    info = mtypes.Implementation(name="crowcode", version=version)
    if srv.type == "stdio":
        params = StdioServerParameters(
            command=srv.command,
            args=list(srv.args),
            env={**os.environ, **srv.env},
        )
        return Client(params, mode="auto", client_info=info)
    return _HttpClientContext(srv, info)


class Manager:
    """所有 MCP 连接与适配工具的生命周期管理者。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[str, _Session] = {}
        self._tools: list[McpTool] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = False

    def tools(self) -> list[McpTool]:
        """返回适配好的工具副本（已按全名排序、已去重）。"""
        return list(self._tools)

    @property
    def server_count(self) -> int:
        """成功建立连接的 server 数；``new_manager`` 返回后不再变化。"""
        return len(self._sessions)

    async def close(self) -> None:
        """通知所有 keeper 退出上下文；整体受 5s 兜底约束。"""
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.stop.set()
        pending = [task for task in self._tasks if not task.done()]
        if not pending:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=close_timeout,
            )
        except TimeoutError:
            _warn(f"close timeout ({close_timeout:g}s), some sessions may leak")
            for task in pending:
                task.cancel()

    async def _register(
        self, name: str, client: Any, adapted: list[McpTool], stop: asyncio.Event
    ) -> None:
        """把一次成功连接的会话与工具写入共享状态（加锁）。"""
        async with self._lock:
            task = asyncio.current_task()
            assert task is not None
            self._sessions[name] = _Session(
                name=name,
                client=client,
                protocol_version=client.protocol_version,
                stop=stop,
                task=task,
            )
            self._tools.extend(adapted)


async def new_manager(cfg: Config, version: str) -> Manager:
    """并发连接所有 server；阻塞到所有尝试结束（成功 / 失败 / 超时）。

    单个 server 的连接 + 协议协商 + 列工具整体受 ``connect_timeout`` 约束；
    失败只跳过它自身。
    """
    manager = Manager()
    if not cfg.servers:
        return manager

    ready_events: list[asyncio.Event] = []
    for name in sorted(cfg.servers):
        ready = asyncio.Event()
        ready_events.append(ready)
        task = asyncio.create_task(
            _keeper(manager, name, cfg.servers[name], version, ready)
        )
        manager._tasks.append(task)

    await asyncio.gather(*(event.wait() for event in ready_events))
    manager._tools = _dedupe_tools(manager._tools)
    manager._tools.sort(key=lambda tool: tool.full_name)
    return manager


async def _keeper(
    manager: Manager,
    name: str,
    srv: ServerConfig,
    version: str,
    ready: asyncio.Event,
) -> None:
    """单 server 的常驻任务：连接 → 列工具 → 等待停止 → 退出上下文。"""
    stop = asyncio.Event()
    context: Any = None
    entered = False
    client: Any = None
    try:
        context = _open_client(srv, version)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + connect_timeout
        try:
            async with asyncio.timeout_at(deadline):
                client = await context.__aenter__()
                entered = True
                listed = await client.list_tools()
                adapted = _adapt_listing(name, listed, client)
        except TimeoutError:
            _warn(f"connect server {name} timeout after {connect_timeout:g}s")
            return
        except Exception as e:  # noqa: BLE001 —— 单 server 失败只跳过自身
            _warn(f"connect server {name} failed: {e}")
            return

        await manager._register(name, client, adapted, stop)
        print(
            f"[mcp] server {name} connected "
            f"(protocol={client.protocol_version}, tools={len(adapted)})",
            file=sys.stderr,
        )
        # 先通知 new_manager 可以继续，再常驻等待关闭信号。
        ready.set()
        await stop.wait()
    finally:
        ready.set()
        if entered and context is not None:
            try:
                await context.__aexit__(None, None, None)
            except Exception as e:  # noqa: BLE001 —— 清理失败不阻塞退出
                _warn(f"close server {name} failed: {e}")


def _adapt_listing(server_name: str, listed: Any, client: Any) -> list[McpTool]:
    """把 ``list_tools`` 结果逐项适配；单个工具非法只跳过它自身。"""
    adapted: list[McpTool] = []
    for remote in getattr(listed, "tools", []) or []:
        tool = adapt_tool(server_name, remote, client)
        if tool is not None:
            adapted.append(tool)
    return adapted


def _dedupe_tools(tools: list[McpTool]) -> list[McpTool]:
    """全局去重：同全名时后注册者保留并告警（F8 边界情形）。"""
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        if tool.full_name in by_name:
            _warn(f"duplicate tool {tool.full_name}, keeping the later one")
        by_name[tool.full_name] = tool
    return list(by_name.values())
