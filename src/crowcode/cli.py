"""入口：加载配置、连接 MCP、构造权限引擎、启动 TUI。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from crowcode import __version__
from crowcode import mcp as mcp_client
from crowcode.agent.runtime import new_session_runtime
from crowcode.config import (
    ConfigError,
    effective_context_window,
    load_with_fallback,
)
from crowcode.permission import new_checker
from crowcode.tool import new_default_registry
from crowcode.tui import CrowCodeApp


def _force_utf8() -> None:
    """把标准流切到 UTF-8。

    中文 Windows 的默认控制台编码常被检测为 GBK（``sys.stdout.encoding == "gbk"``），
    而 Textual / Rich 输出 UTF-8，工具结果里的中文会因此乱码。启动前把三个标准流都
    重配为 UTF-8，与 UTF-8 终端（Windows Terminal 等）对齐。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            # 非文本流（如被重定向为 None 或二进制）没有 reconfigure，忽略即可。
            pass


async def _amain() -> int:
    """异步主流程：MCP 连接与关闭必须发生在同一个事件循环里。"""
    try:
        cfg = load_with_fallback()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1

    root = str(Path.cwd().resolve())
    runtime = new_session_runtime(
        workspace=root,
        context_window=effective_context_window(cfg.providers[0]),
    )
    registry = new_default_registry()
    mcp_cfg = mcp_client.load_config(root)
    manager = await mcp_client.new_manager(mcp_cfg, version=__version__)
    try:
        mcp_tools = manager.tools()
        for tool in mcp_tools:
            registry.register(tool)
        checker, err = new_checker(root)
        if err is not None:
            print("Warning: permission engine degraded:", err, file=sys.stderr)
        checker = mcp_client.install_permission(
            checker, root, [tool.name() for tool in mcp_tools]
        )
        app = CrowCodeApp(
            cfg.providers,
            registry,
            checker,
            runtime=runtime,
            mcp_server_count=manager.server_count,
            mcp_tool_count=len(mcp_tools),
        )
        await app.run_async()
    finally:
        await manager.close()
    return 0


def main() -> None:
    _force_utf8()
    raise SystemExit(asyncio.run(_amain()))
