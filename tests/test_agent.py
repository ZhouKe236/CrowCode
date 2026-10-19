"""agent ReAct 循环单测（用 fake provider 驱动，不联网）。"""

import asyncio
import json
import tempfile

import pytest

from crowcode.agent import (
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_UNKNOWN_TOOLS,
    Agent,
    Phase,
)
from crowcode.conversation import Conversation
from crowcode.llm import StreamEvent, ToolCall
from crowcode.llm import Usage as LLMUsage
from crowcode.permission import Outcome, PermissionMode, new_checker
from crowcode.prompt import plan_reminder
from crowcode.tool import Registry, Result, new_default_registry

_VERSION = "0.1.0"  # 测试用应用版本


def _checker(tmp_path=None):
    """构造一个根于临时目录的空规则 checker。"""
    root = str(tmp_path) if tmp_path is not None else tempfile.mkdtemp()
    checker, _ = new_checker(root)
    return checker


def text(s: str) -> StreamEvent:
    return StreamEvent(text=s)


def done() -> StreamEvent:
    return StreamEvent(done=True)


def tc(name: str, input: str = "{}", id: str = "1") -> StreamEvent:
    return StreamEvent(tool_calls=[ToolCall(id=id, name=name, input=input)])


def usage_ev(inp: int, out: int, cw: int, cr: int) -> StreamEvent:
    """带缓存用量的 usage 事件（llm.Usage 语义）。"""
    return StreamEvent(
        usage=LLMUsage(
            input_tokens=inp, output_tokens=out, cache_write=cw, cache_read=cr
        )
    )


class FakeTool:
    """计数工具：记录被执行次数，返回固定成功结果。"""

    read_only = True

    def __init__(self) -> None:
        self.calls = 0

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "fake"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, args: str, timeout: float = 30.0) -> Result:
        self.calls += 1
        return Result(content="内容", is_error=False)


class FakeWriteTool:
    """假写工具（read_only=False）：记录执行次数，不落地文件。"""

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


class FakeProvider:
    """按调用次数回放脚本；``always`` 非空时忽略脚本耗尽，恒返该序列。

    每次 ``stream(req)`` 记录收到的 :class:`~crowcode.llm.Request`（system/tools/reminder）。
    """

    def __init__(
        self,
        scripts: list[list[StreamEvent]] | None = None,
        *,
        always: list[StreamEvent] | None = None,
    ) -> None:
        self.scripts = scripts or []
        self.always = always
        self.call = 0
        self.received_requests: list = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.received_requests.append(req)
        idx = self.call
        self.call += 1
        if self.always is not None:
            script = self.always
        elif idx < len(self.scripts):
            script = self.scripts[idx]
        else:
            script = [done()]
        for ev in script:
            yield ev


# ── 场景 A：多轮链路（AC1）────────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_round_chain():
    tool = FakeTool()
    registry = Registry()
    registry.register(tool)
    provider = FakeProvider(
        [
            [text("让我读一下"), tc("read_file", input='{"path": "x.txt"}'), done()],
            [text("文件已读取"), done()],
        ]
    )
    conv = Conversation()
    conv.add_user("读文件")
    events = [
        ev
        async for ev in Agent(provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.DEFAULT, asyncio.Event()
        )
    ]

    iters = [e.iter for e in events if e.iter > 0]
    assert iters == [1, 2]
    starts = [e for e in events if e.tool and e.tool.phase is Phase.START]
    ends = [e for e in events if e.tool and e.tool.phase is Phase.END]
    assert len(starts) == 1 and len(ends) == 1
    assert ends[0].tool.result == "内容"
    assert any(e.text == "文件已读取" for e in events)
    assert any(e.done for e in events)
    assert tool.calls == 1

    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[-1].content == "文件已读取"


# ── 场景 B：迭代上限（AC3）────────────────────────────────────────


@pytest.mark.asyncio
async def test_iteration_limit():
    tool = FakeTool()
    registry = Registry()
    registry.register(tool)
    provider = FakeProvider(always=[tc("read_file")])
    conv = Conversation()
    conv.add_user("反复干活")
    events = [
        ev
        async for ev in Agent(provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.DEFAULT, asyncio.Event()
        )
    ]

    assert provider.call == MAX_ITERATIONS
    assert any(e.notice == NOTICE_MAX_ITER for e in events)
    assert conv.last_role() == "assistant"
    assert any(e.done for e in events)


# ── 场景 C：连续未知工具（AC4）────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_tools_stop():
    registry = new_default_registry()
    provider = FakeProvider(always=[tc("nope")])
    conv = Conversation()
    conv.add_user("x")
    events = [
        ev
        async for ev in Agent(provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.BYPASS, asyncio.Event()
        )
    ]

    assert provider.call == MAX_UNKNOWN_RUN
    assert any(e.notice == NOTICE_UNKNOWN_TOOLS for e in events)
    assert conv.last_role() == "assistant"


@pytest.mark.asyncio
async def test_unknown_tools_reset_on_known():
    registry = new_default_registry()
    provider = FakeProvider(
        [
            [tc("nope")],
            [tc("read_file")],  # 已知工具，计数重置
            [tc("nope")],
            [tc("nope")],
            [tc("nope")],
        ]
    )
    conv = Conversation()
    conv.add_user("x")
    events = [
        ev
        async for ev in Agent(provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.BYPASS, asyncio.Event()
        )
    ]

    assert provider.call == 5  # 重置后再 3 轮才停
    assert any(e.notice == NOTICE_UNKNOWN_TOOLS for e in events)


# ── 场景 D：保序分批并发（AC8）────────────────────────────────────


class _Probe:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.log: list[tuple] = []


class ReadOnlyProbeTool:
    read_only = True

    def __init__(self, probe: _Probe) -> None:
        self.probe = probe

    def name(self) -> str:
        return "ro"

    def description(self) -> str:
        return "probe"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str, timeout: float = 30.0) -> Result:
        self.probe.active += 1
        self.probe.peak = max(self.probe.peak, self.probe.active)
        await asyncio.sleep(0.1)
        self.probe.active -= 1
        self.probe.log.append(("ro_done",))
        return Result(content="ro", is_error=False)


class SideEffectProbeTool:
    read_only = False

    def __init__(self, probe: _Probe) -> None:
        self.probe = probe

    def name(self) -> str:
        return "rw"

    def description(self) -> str:
        return "probe"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str, timeout: float = 30.0) -> Result:
        self.probe.log.append(("rw_start",))
        await asyncio.sleep(0.01)
        return Result(content="rw", is_error=False)


@pytest.mark.asyncio
async def test_concurrent_batch():
    probe = _Probe()
    registry = Registry()
    registry.register(ReadOnlyProbeTool(probe))
    registry.register(SideEffectProbeTool(probe))
    calls = [
        ToolCall(id="c1", name="ro", input="{}"),
        ToolCall(id="c2", name="ro", input="{}"),
        ToolCall(id="c3", name="rw", input="{}"),
    ]
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=calls), done()],
            [text("全部完成"), done()],
        ]
    )
    conv = Conversation()
    conv.add_user("做三件事")
    events = [
        ev
        async for ev in Agent(provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.BYPASS, asyncio.Event()
        )
    ]

    # 两只读确实并发，且 rw 在两只读完成后才开始。
    assert probe.peak >= 2
    assert [e[0] for e in probe.log] == ["ro_done", "ro_done", "rw_start"]

    # 结果按调用序回灌（按 tool_call_id 比对）。
    msgs = conv.messages()
    tool_msg = next(m for m in msgs if m.role == "tool")
    assert [r.tool_call_id for r in tool_msg.tool_results] == ["c1", "c2", "c3"]

    assert any(e.text == "全部完成" for e in events)
    assert any(e.done for e in events)


# ── 场景 E：取消历史一致（AC9）────────────────────────────────────


class BlockingTool:
    read_only = False

    def __init__(self) -> None:
        self.calls = 0

    def name(self) -> str:
        return "block"

    def description(self) -> str:
        return "blocking"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str, timeout: float = 30.0) -> Result:
        self.calls += 1
        await asyncio.sleep(0.2)
        return Result(content="done", is_error=False)


@pytest.mark.asyncio
async def test_cancel_history_consistent():
    tool = BlockingTool()
    registry = Registry()
    registry.register(tool)
    provider = FakeProvider(
        [
            [tc("block", id="c1"), done()],
            [text("继续回答"), done()],
        ]
    )
    conv = Conversation()
    conv.add_user("跑慢工具")
    cancel = asyncio.Event()
    agent = Agent(provider, registry, _VERSION, _checker())

    async def drive() -> None:
        async for ev in agent.run(conv, PermissionMode.BYPASS, cancel):
            if ev.tool is not None and ev.tool.phase is Phase.START:
                await asyncio.sleep(0.05)
                cancel.set()

    await drive()

    msgs = conv.messages()
    roles = [m.role for m in msgs]
    assert roles[-1] == "assistant"
    assert msgs[-1].content == NOTICE_CANCELLED
    assert "tool" in roles  # 有工具结果回合，无悬空 tool_use
    tool_msg = msgs[roles.index("tool")]
    assert [r.tool_call_id for r in tool_msg.tool_results] == ["c1"]

    # 追加一轮纯文本能正常跑（角色交替未坏）。
    events2 = [
        ev
        async for ev in Agent(provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.BYPASS, asyncio.Event()
        )
    ]
    assert any(e.done for e in events2)
    assert conv.messages()[-1].content == "继续回答"


# ── 场景 F：Plan 工具集（AC13）────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_mode_read_only_tools():
    registry = new_default_registry()
    provider = FakeProvider([[text("计划如下…"), done()]])
    conv = Conversation()
    conv.add_user("给方案")
    events = [
        ev
        async for ev in Agent(provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.PLAN, asyncio.Event()
        )
    ]

    req = provider.received_requests[0]
    assert [t.name for t in req.tools] == ["read_file", "glob", "grep"]
    # 规划提醒已移出系统通道，改为按轮次注入（首轮完整）。
    assert req.reminder == plan_reminder(True)
    assert any(e.done for e in events)


# ── 场景 G：系统提示两段 + 跨模式一致（F7/N1）────────────────────


@pytest.mark.asyncio
async def test_system_prompt_two_segments_consistent_across_modes():
    registry = new_default_registry()
    normal_provider = FakeProvider([[text("ok"), done()]])
    plan_provider = FakeProvider([[text("ok"), done()]])

    conv = Conversation()
    conv.add_user("x")
    [
        ev
        async for ev in Agent(normal_provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.DEFAULT, asyncio.Event()
        )
    ]
    conv2 = Conversation()
    conv2.add_user("x")
    [
        ev
        async for ev in Agent(plan_provider, registry, _VERSION, _checker()).run(
            conv2, PermissionMode.PLAN, asyncio.Event()
        )
    ]

    normal_req = normal_provider.received_requests[0]
    plan_req = plan_provider.received_requests[0]
    assert normal_req.system.stable  # 稳定段非空
    assert normal_req.system.environment  # 环境段非空
    assert normal_req.system.stable == plan_req.system.stable  # 跨模式一致
    assert plan_req.system.environment


# ── 场景 H：规划模式按轮次注入 + reminder 不入历史（AC9/F6/F7）────


@pytest.mark.asyncio
async def test_plan_mode_reminder_per_iteration_and_not_persisted():
    tool = FakeTool()
    registry = Registry()
    registry.register(tool)
    provider = FakeProvider(always=[tc("read_file")])
    conv = Conversation()
    conv.add_user("做个计划")
    [
        ev
        async for ev in Agent(provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.PLAN, asyncio.Event()
        )
    ]

    reqs = provider.received_requests
    # 首轮完整、间隔轮（每 4）完整、其余精简。
    assert reqs[0].reminder == plan_reminder(True)
    assert reqs[1].reminder == plan_reminder(False)
    assert reqs[2].reminder == plan_reminder(False)
    assert reqs[3].reminder == plan_reminder(False)
    assert reqs[4].reminder == plan_reminder(True)

    # reminder 不写入持久历史。
    for m in conv.messages():
        assert "<system-reminder>" not in m.content


# ── 场景 I：缓存用量透传（AC6/F4）─────────────────────────────────


@pytest.mark.asyncio
async def test_cache_usage_passthrough():
    registry = new_default_registry()
    provider = FakeProvider([[text("hi"), usage_ev(100, 10, 30, 20), done()]])
    conv = Conversation()
    conv.add_user("x")
    events = [
        ev
        async for ev in Agent(provider, registry, _VERSION, _checker()).run(
            conv, PermissionMode.DEFAULT, asyncio.Event()
        )
    ]

    u = next(e.usage for e in events if e.usage is not None)
    assert u.input == 100
    assert u.output == 10
    assert u.cache_write == 30
    assert u.cache_read == 20


# ── 权限集成：Deny 回灌不中断 / 保序 / Ask / 永久 / 只读并发 / 取消 ──


@pytest.mark.asyncio
async def test_permission_deny_loop_continues(tmp_path):
    """沙箱外写文件被 Deny，结果回灌错误，Loop 继续到次轮（AC11/F9）。"""
    checker, _ = new_checker(str(tmp_path))
    registry = new_default_registry()
    provider = FakeProvider(
        [
            [
                tc(
                    "write_file",
                    input=json.dumps({"path": "/etc/passwd", "content": "x"}),
                ),
                done(),
            ],
            [text("改项目内路径重试"), done()],
        ]
    )
    conv = Conversation()
    conv.add_user("写文件")
    events = [
        ev
        async for ev in Agent(provider, registry, _VERSION, checker).run(
            conv, PermissionMode.DEFAULT, asyncio.Event()
        )
    ]

    msgs = conv.messages()
    tool_msg = next(m for m in msgs if m.role == "tool")
    assert tool_msg.tool_results[0].is_error is True
    assert "项目目录之外" in tool_msg.tool_results[0].content
    assert any(e.text == "改项目内路径重试" for e in events)  # Loop 继续


@pytest.mark.asyncio
async def test_permission_mixed_batch_ordering(tmp_path):
    """只读批中「被沙箱拒 + 放行」按调用序配对回灌、互不串位（AC11/N3）。"""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    checker, _ = new_checker(str(tmp_path))
    registry = new_default_registry()
    calls = [
        ToolCall(id="c1", name="read_file", input=json.dumps({"path": "/etc/passwd"})),
        ToolCall(
            id="c2",
            name="read_file",
            input=json.dumps({"path": str(tmp_path / "a.txt")}),
        ),
    ]
    provider = FakeProvider(
        [[StreamEvent(tool_calls=calls), done()], [text("ok"), done()]]
    )
    conv = Conversation()
    conv.add_user("读两个文件")
    approvals = [
        ev
        async for ev in Agent(provider, registry, _VERSION, checker).run(
            conv, PermissionMode.DEFAULT, asyncio.Event()
        )
        if ev.approval is not None
    ]
    assert approvals == []  # 只读永不 Ask

    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    results = tool_msg.tool_results
    assert [r.tool_call_id for r in results] == ["c1", "c2"]
    assert results[0].is_error is True
    assert results[1].is_error is False


@pytest.mark.asyncio
async def test_permission_ask_allow_once(tmp_path):
    tool = FakeWriteTool()
    registry = Registry()
    registry.register(tool)
    checker, _ = new_checker(str(tmp_path))
    provider = FakeProvider(
        [
            [tc("write_file", input='{"path": "a.txt", "content": "x"}'), done()],
            [text("完成"), done()],
        ]
    )
    conv = Conversation()
    conv.add_user("写文件")
    saw_ask = False
    async for ev in Agent(provider, registry, _VERSION, checker).run(
        conv, PermissionMode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            saw_ask = True
            assert ev.approval.name == "write_file"
            ev.approval.respond.set_result(Outcome.ALLOW_ONCE)

    assert saw_ask is True
    assert tool.calls == 1  # 允许本次后执行


@pytest.mark.asyncio
async def test_permission_ask_deny_once(tmp_path):
    tool = FakeWriteTool()
    registry = Registry()
    registry.register(tool)
    checker, _ = new_checker(str(tmp_path))
    provider = FakeProvider(
        [[tc("write_file", input='{"path": "a.txt"}'), done()], [text("好"), done()]]
    )
    conv = Conversation()
    conv.add_user("写文件")
    async for ev in Agent(provider, registry, _VERSION, checker).run(
        conv, PermissionMode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            ev.approval.respond.set_result(Outcome.DENY_ONCE)

    assert tool.calls == 0  # 拒绝本次不执行
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    assert tool_msg.tool_results[0].is_error is True


@pytest.mark.asyncio
async def test_permission_ask_allow_forever(tmp_path):
    tool = FakeWriteTool()
    registry = Registry()
    registry.register(tool)
    checker, _ = new_checker(str(tmp_path))
    provider = FakeProvider(
        [[tc("write_file", input='{"path": "a.txt"}'), done()], [text("好"), done()]]
    )
    conv = Conversation()
    conv.add_user("写文件")
    async for ev in Agent(provider, registry, _VERSION, checker).run(
        conv, PermissionMode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            ev.approval.respond.set_result(Outcome.ALLOW_FOREVER)

    assert tool.calls == 1  # 永久也执行
    assert (tmp_path / ".crowcode" / "settings.local.yaml").exists()


@pytest.mark.asyncio
async def test_permission_cancel_during_approval(tmp_path):
    tool = FakeWriteTool()
    registry = Registry()
    registry.register(tool)
    checker, _ = new_checker(str(tmp_path))
    provider = FakeProvider(
        [[tc("write_file", input='{"path": "a.txt"}'), done()], [text("继续"), done()]]
    )
    conv = Conversation()
    conv.add_user("写文件")
    cancel = asyncio.Event()
    agent = Agent(provider, registry, _VERSION, checker)

    async def drive() -> None:
        async for ev in agent.run(conv, PermissionMode.DEFAULT, cancel):
            if ev.approval is not None:
                # 模拟用户在待批准态按 Esc：兜底 DENY_ONCE + 取消本轮
                ev.approval.respond.set_result(Outcome.DENY_ONCE)
                cancel.set()

    await drive()
    assert tool.calls == 0
    assert conv.last_role() == "assistant"
