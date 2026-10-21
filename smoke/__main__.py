"""非交互冒烟脚本（``python -m smoke``）。

以 ``Mode.BYPASS`` 跑一轮「写文件」的 ReAct 循环：BYPASS 下写文件不触发人在
回路（Ask），Loop 应直接跑完而不阻塞。用脚本化 fake provider 驱动，不联网。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from crowcode.agent import Agent, ApprovalRequest, new_session_runtime
from crowcode.conversation import Conversation
from crowcode.llm import StreamEvent, ToolCall
from crowcode.permission import PermissionMode, new_checker
from crowcode.tool import Registry, Result


class _FakeWriteTool:
    """假写工具：只记录执行次数，不落地文件。"""

    read_only = False

    def __init__(self) -> None:
        self.calls = 0

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "fake write"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, args: str, timeout: float = 30.0) -> Result:
        self.calls += 1
        return Result(content="已写入", is_error=False)


class _FakeProvider:
    """脚本化 provider：首轮请求写文件，次轮纯文本收尾。"""

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.call = 0

    @property
    def name(self) -> str:
        return "smoke"

    @property
    def model(self) -> str:
        return "smoke-model"

    async def stream(self, req):
        idx = self.call
        self.call += 1
        script = (
            self.scripts[idx] if idx < len(self.scripts) else [StreamEvent(done=True)]
        )
        for ev in script:
            yield ev


async def _run() -> None:
    root = str(Path.cwd().resolve())
    checker, err = new_checker(root)
    if err is not None:
        print(f"权限引擎降级: {err}", file=sys.stderr)

    tool = _FakeWriteTool()
    registry = Registry()
    registry.register(tool)
    provider = _FakeProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            input='{"path": "smoke_out.txt", "content": "x"}',
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="完成"), StreamEvent(done=True)],
        ]
    )

    conv = Conversation()
    conv.add_user("写一个文件")
    runtime = new_session_runtime(root, context_window=200000)
    approvals = 0
    async for ev in Agent(provider, registry, "dev", checker, runtime=runtime).run(
        conv, PermissionMode.BYPASS, asyncio.Event()
    ):
        if isinstance(ev.approval, ApprovalRequest):
            approvals += 1

    assert approvals == 0, "BYPASS 模式下写文件不应触发人在回路"
    assert tool.calls == 1, "写工具应被执行一次"
    assert any(m.role == "tool" for m in conv.messages()), "应有一轮工具结果回合"
    print("smoke OK：BYPASS 模式下写文件不阻塞、Loop 完成")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
