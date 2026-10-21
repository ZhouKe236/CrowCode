"""TUI 单测：Shift+Tab 循环切换、待批准态按键回传、Esc 取消、状态栏模式、模式跨轮保持。"""

import asyncio

import pytest
from textual.widgets import RichLog

from crowcode.agent import ApprovalRequest
from crowcode.config import ProviderConfig
from crowcode.permission import Outcome, PermissionMode, new_checker
from crowcode.tool import new_default_registry
from crowcode.tui.app import CrowCodeApp, SessionState, next_mode
from crowcode.tui.view import mcp_summary_block


def _cfg() -> list[ProviderConfig]:
    return [
        ProviderConfig(protocol="openai", api_key="k", model="modelABC", name="prov")
    ]


def _cfg_two() -> list[ProviderConfig]:
    return [
        ProviderConfig(protocol="openai", api_key="k", model="a", name="a"),
        ProviderConfig(protocol="openai", api_key="k", model="b", name="b"),
    ]


def test_next_mode_cycle():
    m = PermissionMode.DEFAULT
    seq = [next_mode(m)]
    for _ in range(3):
        seq.append(next_mode(seq[-1]))
    assert seq == [
        PermissionMode.ACCEPT_EDITS,
        PermissionMode.PLAN,
        PermissionMode.BYPASS,
        PermissionMode.DEFAULT,
    ]


@pytest.mark.asyncio
async def test_shift_tab_cycles_mode(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        assert app.mode is PermissionMode.DEFAULT
        await pilot.press("shift+tab")
        assert app.mode is PermissionMode.ACCEPT_EDITS
        await pilot.press("shift+tab")
        assert app.mode is PermissionMode.PLAN
        await pilot.press("shift+tab")
        assert app.mode is PermissionMode.BYPASS
        await pilot.press("shift+tab")
        assert app.mode is PermissionMode.DEFAULT


def _enter_approving(app) -> asyncio.Future:
    fut = asyncio.get_running_loop().create_future()
    app.pending = ApprovalRequest(
        name="write_file",
        args='{"path": "a.txt"}',
        reason="default 模式下 文件写 类操作需确认",
        respond=fut,
    )
    app.approve_cursor = 0
    app.state = SessionState.APPROVING
    app._input.blur()
    app._refresh_approval()
    return fut


@pytest.mark.asyncio
async def test_approval_navigation_and_allow_forever(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        fut = _enter_approving(app)
        assert app.approve_cursor == 0  # 默认高亮「允许本次」

        await pilot.press("down")
        assert app.approve_cursor == 1
        await pilot.press("enter")
        assert fut.result() is Outcome.ALLOW_FOREVER
        assert app.state is SessionState.STREAMING
        assert app.pending is None


@pytest.mark.asyncio
async def test_approval_enter_works_when_input_focused(tmp_path):
    """输入框仍持有焦点时（blur 未生效的场景），Enter 仍应触发三选一。"""
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        fut = asyncio.get_running_loop().create_future()
        app.pending = ApprovalRequest(
            name="write_file",
            args='{"path": "a.txt"}',
            reason="default 模式下 文件写 类操作需确认",
            respond=fut,
        )
        app.approve_cursor = 0
        app.state = SessionState.APPROVING
        app._refresh_approval()
        # 不调用 _input.blur()，保持输入框焦点（复现真实终端焦点未清除的场景）。
        await pilot.press("enter")
        assert fut.result() is Outcome.ALLOW_ONCE  # 默认高亮「允许本次」


@pytest.mark.asyncio
async def test_approval_number_keys(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        fut = _enter_approving(app)
        await pilot.press("1")
        assert fut.result() is Outcome.ALLOW_ONCE

        fut2 = _enter_approving(app)
        await pilot.press("3")
        assert fut2.result() is Outcome.DENY_ONCE


@pytest.mark.asyncio
async def test_approval_escape_cancels(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        fut = _enter_approving(app)
        app.turn_cancel = asyncio.Event()
        await pilot.press("escape")
        assert fut.result() is Outcome.DENY_ONCE
        assert app.state is SessionState.STREAMING


@pytest.mark.asyncio
async def test_app_initial_mode_from_engine(tmp_path):
    (tmp_path / ".crowcode").mkdir(exist_ok=True)
    (tmp_path / ".crowcode" / "settings.yaml").write_text(
        "default_mode: acceptEdits\n", encoding="utf-8"
    )
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    assert app.mode is PermissionMode.ACCEPT_EDITS


@pytest.mark.asyncio
async def test_mode_persists_across_turn_end(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test():
        app.mode = PermissionMode.BYPASS
        app._stop_streaming()  # 模拟一轮结束
        assert app.mode is PermissionMode.BYPASS  # 跨轮保持，不被重置


@pytest.mark.parametrize(
    "mode,label",
    [
        (PermissionMode.DEFAULT, "DEFAULT"),
        (PermissionMode.ACCEPT_EDITS, "ACCEPT EDITS"),
        (PermissionMode.PLAN, "PLAN"),
        (PermissionMode.BYPASS, "BYPASS"),
    ],
)
def test_mode_status_label(mode, label):
    from crowcode.tui.view import mode_status

    text = mode_status(mode)
    assert f"[{label}]" in text.plain
    if mode is PermissionMode.DEFAULT:
        assert "(shift+tab to cycle)" not in text.plain
    else:
        assert "(shift+tab to cycle)" in text.plain


def test_model_status_plain():
    from crowcode.tui.view import model_status

    assert "gpt-4o-mini" in model_status("gpt-4o-mini").plain
    assert "—" in model_status(None).plain


def test_render_header_includes_model_and_usage():
    from crowcode.tui.view import render_header

    plain = render_header("0.1.0", "/proj", "gpt-4o-mini", 1200, 50).plain
    assert "gpt-4o-mini" in plain
    assert "↑1.2k ↓50 tok" in plain
    # 乌鸦图标与右侧文字之间隔四个空格。
    assert "   /.)    Crow Code v0.1.0" in plain


@pytest.mark.asyncio
async def test_status_bar_on_screen(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        mode_label = app.query_one("#mode-label")
        model_info = app.query_one("#model-info")
        # 模式标签与模型信息都落在屏幕内（修复前 mode-label 会被顶到 y=24 出界）。
        assert mode_label.region.bottom <= 24
        assert model_info.region.bottom <= 24
        assert model_info.region.width > 0
        # 状态栏右侧应显示模型名（不含 provider 名、不含 token）。
        from crowcode.tui.view import model_status

        assert "modelABC" in model_status(app.provider.model).plain


# ── MCP 初始化汇总 ──────────────────────────────────────────────


def test_mcp_summary_block_plain():
    text = mcp_summary_block(1, 2)
    assert text.plain == "— Connected to 1 MCP server(s), 2 tools registered"
    assert text.style == "dim italic"


@pytest.mark.asyncio
async def test_startup_mcp_summary_in_log(tmp_path):
    """启动时日志区先空一行，再显示 MCP 汇总。"""
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(
        _cfg(),
        new_default_registry(),
        checker,
        mcp_server_count=1,
        mcp_tool_count=2,
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        log = app.query_one("#log", RichLog)
        texts = [strip.text for strip in log.lines]
        assert texts[-2:] == [
            "",
            "— Connected to 1 MCP server(s), 2 tools registered",
        ]


@pytest.mark.asyncio
async def test_no_mcp_summary_when_zero_servers(tmp_path):
    """没有成功连接的 server 时不产生任何启动日志。"""
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.query_one("#log", RichLog).lines == []


@pytest.mark.asyncio
async def test_mcp_summary_visible_after_provider_selection(tmp_path):
    """多 provider 选择页选中后，排队的汇总日志可见。"""
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(
        _cfg_two(),
        new_default_registry(),
        checker,
        mcp_server_count=1,
        mcp_tool_count=2,
    )
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.state is SessionState.SELECTING
        log = app.query_one("#log", RichLog)
        assert str(log.styles.display) == "none"
        assert log.lines == []

        await pilot.press("enter")
        await pilot.pause()
        assert app.state is SessionState.IDLE
        assert str(log.styles.display) == "block"
        texts = [strip.text for strip in log.lines]
        assert texts[-2:] == [
            "",
            "— Connected to 1 MCP server(s), 2 tools registered",
        ]


# ── 命令分发与上下文压缩 UI ────────────────────────────────────────


from crowcode.agent import CompactEvent, CompactPhase, Event
from crowcode.tui.commands import (
    BUILTIN_COMMANDS,
    dispatch_command,
    format_compact_notice,
)


class MockAgent:
    """记录调用的假 Agent，用于命令路径测试。"""

    def __init__(self, events=None, force_result=(120000, 42000), force_error=None):
        self.events = events or []
        self.force_result = force_result
        self.force_error = force_error
        self.run_calls = 0
        self.force_calls = 0
        self.received_defs = []

    async def run(self, conv, mode, cancel):
        self.run_calls += 1
        for event in self.events:
            yield event

    async def run_force_compact(self, conv, defs):
        self.force_calls += 1
        self.received_defs.append(defs)
        if self.force_error is not None:
            raise self.force_error
        return self.force_result


def _log_texts(app) -> list[str]:
    return [strip.text for strip in app.query_one("#log", RichLog).lines]


def test_dispatch_registry_has_four_commands():
    assert set(BUILTIN_COMMANDS) == {"/exit", "/plan", "/do", "/compact"}
    for name, handler in BUILTIN_COMMANDS.items():
        assert dispatch_command(name) == (handler, True)


def test_dispatch_unknown_and_plain_text():
    handler, ok = dispatch_command("/unknown")
    assert ok is True and handler is not None
    assert dispatch_command("hello") == (None, False)


def test_format_compact_notice():
    assert (
        format_compact_notice(CompactEvent(phase=CompactPhase.BEFORE_AUTO))
        == "正在压缩上下文..."
    )
    assert (
        format_compact_notice(CompactEvent(phase=CompactPhase.BEFORE_EMERGENCY))
        == "上下文撞墙，自动压缩中..."
    )
    assert (
        format_compact_notice(
            CompactEvent(phase=CompactPhase.AFTER_AUTO, before=100, after=10)
        )
        == "已压缩，token 从 100 降至 10"
    )
    assert (
        format_compact_notice(
            CompactEvent(phase=CompactPhase.AFTER_AUTO, err=RuntimeError("boom"))
        )
        == "压缩失败：boom"
    )


@pytest.mark.asyncio
async def test_tui_slash_compact_routes_to_command(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        agent = MockAgent()
        app.agent = agent
        await app.submit("/compact")
        await pilot.pause()

        assert agent.force_calls == 1
        assert agent.run_calls == 0
        assert app.conv.length() == 0  # 命令不写入对话历史
        texts = _log_texts(app)
        assert "— 已压缩，token 从 120000 降至 42000" in texts


@pytest.mark.asyncio
async def test_tui_unknown_slash_command_friendly(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        agent = MockAgent()
        app.agent = agent
        await app.submit("/unknown")
        await pilot.pause()

        assert agent.run_calls == 0
        assert agent.force_calls == 0
        assert any("未知命令: /unknown" in text for text in _log_texts(app))


@pytest.mark.asyncio
async def test_tui_compact_failure_shown(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        app.agent = MockAgent(force_error=RuntimeError("bad"))
        await app.submit("/compact")
        await pilot.pause()
        assert any("压缩失败：bad" in text for text in _log_texts(app))


@pytest.mark.asyncio
async def test_tui_migrated_plan_still_works(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        agent = MockAgent()
        app.agent = agent
        await app.submit("/plan")
        await pilot.pause()
        assert app.mode is PermissionMode.PLAN
        assert agent.run_calls == 0
        assert any("Entered plan mode" in text for text in _log_texts(app))


@pytest.mark.asyncio
async def test_tui_migrated_do_still_works(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        agent = MockAgent(events=[Event(done=True)])
        app.agent = agent
        await app.submit("/do")
        await pilot.pause()
        assert app.mode is PermissionMode.DEFAULT
        assert agent.run_calls == 1


@pytest.mark.asyncio
async def test_tui_renders_compact_events(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    async with app.run_test() as pilot:
        app.agent = MockAgent(
            events=[
                Event(compact=CompactEvent(phase=CompactPhase.BEFORE_AUTO)),
                Event(
                    compact=CompactEvent(
                        phase=CompactPhase.AFTER_AUTO, before=167000, after=12000
                    )
                ),
                Event(done=True),
            ]
        )
        await app.submit("hello")
        await pilot.pause()

        texts = _log_texts(app)
        assert "— 正在压缩上下文..." in texts
        assert "— 已压缩，token 从 167000 降至 12000" in texts
        # 压缩提示不写入对话历史。
        assert all("压缩" not in m.content for m in app.conv.messages())


@pytest.mark.asyncio
async def test_handle_exit_calls_quit():
    from crowcode.tui.commands import handle_exit

    class Stub:
        exited = False

        async def action_quit(self):
            self.exited = True

    stub = Stub()
    await handle_exit(stub)  # type: ignore[arg-type]
    assert stub.exited is True


def test_app_runtime_context_window_from_provider(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    app = CrowCodeApp(_cfg(), new_default_registry(), checker)
    assert app.runtime.context_window == 128000  # openai 默认
