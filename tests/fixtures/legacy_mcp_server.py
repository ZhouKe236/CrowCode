"""最小 legacy MCP server：只认 initialize 握手，用于验证自动回退。

它通过 stdin/stdout 收发 newline-delimited JSON-RPC：
- 对 server/discover 一律回 JSON-RPC 错误（触发客户端回退）；
- 对 initialize / tools/list / tools/call 给出最小可用结果。
"""

from __future__ import annotations

import json
import os
import sys


def _respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        msg_id = message.get("id")
        if method == "server/discover":
            _respond(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
            )
        elif method == "initialize":
            _respond(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "legacy-stub", "version": "1.0"},
                    },
                }
            )
        elif method == "tools/list":
            _respond(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "echo text",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                },
                            },
                            {
                                "name": "env",
                                "description": "read test env var",
                                "inputSchema": {"type": "object"},
                            },
                        ]
                    },
                }
            )
        elif method == "tools/call":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            if params.get("name") == "env":
                text = os.environ.get("CROWCODE_TEST_ENV", "")
            else:
                text = str(arguments.get("text", ""))
            _respond(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": False,
                    },
                }
            )
        elif msg_id is not None:
            _respond(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
            )


if __name__ == "__main__":
    main()
