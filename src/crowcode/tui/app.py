"""终端层：Textual App，承载选择 / 对话 / 流式 / 工具行 / 待批准 / 错误的全部交互。"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import OptionList, RichLog, Static, TextArea

from crowcode import __version__
from crowcode.agent import Agent, ApprovalRequest, Phase
from crowcode.agent.runtime import SessionRuntime, new_session_runtime
from crowcode.config import ProviderConfig, effective_context_window
from crowcode.conversation import Conversation
from crowcode.llm import Provider, new_provider
from crowcode.permission import Outcome, PermissionChecker, PermissionMode
from crowcode.tool import Registry

from .commands import dispatch_command, format_compact_notice
from .select import build_provider_options
from .view import (
    approval_block,
    assistant_markdown,
    error_block,
    mcp_summary_block,
    mode_status,
    model_status,
    render_header,
    system_block,
    tool_line,
    tool_result_summary,
    user_block,
)


class SessionState(Enum):
    SELECTING = "selecting"  # 多 provider 时的选择界面
    IDLE = "idle"  # 等待用户输入
    STREAMING = "streaming"  # 等待 / 接收模型流
    APPROVING = "approving"  # 人在回路：等待用户三选一


# 待批准菜单下标 → Outcome（0=允许本次、1=永久、2=拒绝本次）。
_APPROVAL_OUTCOMES = (Outcome.ALLOW_ONCE, Outcome.ALLOW_FOREVER, Outcome.DENY_ONCE)


_MODE_ORDER = (
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
)


def next_mode(m: PermissionMode) -> PermissionMode:
    """Shift+Tab 循环：DEFAULT → ACCEPT_EDITS → PLAN → BYPASS → DEFAULT。"""
    return _MODE_ORDER[(_MODE_ORDER.index(m) + 1) % len(_MODE_ORDER)]


@dataclass
class ToolDisplay:
    """执行中工具的展示信息（name/args，非 None 即在 streaming 区渲染执行行）。"""

    name: str
    args: str


class ChatInput(TextArea):
    """多行输入框：Enter 提交，Alt+Enter 换行。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.submit_callback: Callable[[str], Awaitable[None]] | None = None

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            # 仅空闲态把 Enter 当作「提交」；人在回路等非空闲态不拦截，
            # 让事件冒泡到 App.on_key 处理（三选一等）。
            app = self.app
            if isinstance(app, CrowCodeApp) and app.state is not SessionState.IDLE:
                return
            event.stop()
            event.prevent_default()
            await self._submit()
            return
        if event.key == "alt+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)

    async def _submit(self) -> None:
        text = self.text
        if self.submit_callback is not None:
            await self.submit_callback(text)


class CrowCodeApp(App):
    """CrowCode 主界面。"""

    CSS = """
    Screen { background: transparent; }
    #header { height: auto; padding: 0 1 1 1; }
    #log { height: 1fr; padding: 0 1; background: transparent; }
    #streaming { height: auto; min-height: 1; padding: 0 1; }
    #input-row { height: auto; border-top: solid $primary; padding: 1; }
    #status-bar { height: auto; border-top: solid $primary; padding: 0 1; }
    #mode-label { width: auto; }
    #model-info { width: 1fr; content-align: right middle; }
    #prompt { width: 2; color: green; }
    #input { width: 1fr; height: auto; min-height: 1; max-height: 8; border: none; padding: 0 1; }
    #options { height: 1fr; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "interrupt", "Quit", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle mode", priority=True),
    ]

    def __init__(
        self,
        providers: list[ProviderConfig],
        registry: Registry,
        checker: PermissionChecker,
        *,
        runtime: SessionRuntime | None = None,
        mcp_server_count: int = 0,
        mcp_tool_count: int = 0,
    ) -> None:
        super().__init__()
        self.providers = providers
        # 命名 _tool_registry 而非 _registry：后者是 Textual App 内部 DOM 注册表，冲突。
        self._tool_registry = registry
        self.checker = checker
        # MCP 初始化汇总（0 表示没有成功连接，不展示）。
        self.mcp_server_count = mcp_server_count
        self.mcp_tool_count = mcp_tool_count
        self.provider: Provider | None = None
        # 跨 run 复用的压缩状态；未注入时按当前工作目录新建一套。
        # CLI 会显式注入 runtime；未注入时用系统临时目录，避免污染项目根目录。
        self.runtime = runtime or new_session_runtime(
            workspace=tempfile.gettempdir(),
            context_window=effective_context_window(providers[0]),
        )
        self.agent: Agent | None = None
        self.conv = Conversation()
        self.state = SessionState.SELECTING if len(providers) > 1 else SessionState.IDLE
        self.cur_reply = ""
        self.turn_start = 0.0
        self.mode: PermissionMode = checker.start_mode
        self.iter: int = 0
        self.usage_in: int = 0
        self.usage_out: int = 0
        self.cur_tools: list[ToolDisplay] = []
        self.turn_cancel: asyncio.Event | None = None
        self._stream_task: asyncio.Task | None = None
        self._timer: Timer | None = None
        self._input: ChatInput | None = None
        # 人在回路待批准请求与菜单光标。
        self.pending: ApprovalRequest | None = None
        self.approve_cursor: int = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        yield RichLog(id="log", wrap=True, markup=False, auto_scroll=True)
        yield Static("", id="streaming")
        with Horizontal(id="input-row"):
            yield Static(">", id="prompt")
            yield ChatInput(
                id="input",
                placeholder="Send a message...",
                soft_wrap=True,
                show_line_numbers=False,
            )
        with Horizontal(id="status-bar"):
            yield Static("", id="mode-label")
            yield Static("", id="model-info")
        yield OptionList(*build_provider_options(self.providers), id="options")

    def on_mount(self) -> None:
        self._input = self.query_one("#input", ChatInput)
        self._input.submit_callback = self.submit
        # 写在状态分支之前：SELECTING 时日志被隐藏，写入会排队，选中 provider
        # 后随 resize 一起显示；只写 else 分支会让多 provider 用户永远看不到。
        if self.mcp_server_count > 0:
            log = self.query_one("#log", RichLog)
            log.write("")  # 先空一行
            log.write(mcp_summary_block(self.mcp_server_count, self.mcp_tool_count))
        if self.state is SessionState.SELECTING:
            self._set_selecting(True)
            self.query_one("#options", OptionList).focus()
        else:
            self.provider = new_provider(self.providers[0])
            self._render_header()
            self._update_mode_label()
            self._update_model_info()
            self._set_selecting(False)

    # ── provider 选择 ──────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        cfg = self.providers[event.option_index]
        self.provider = new_provider(cfg)
        self.agent = None
        self.runtime.context_window = effective_context_window(cfg)
        self.state = SessionState.IDLE
        self._render_header()
        self._update_mode_label()
        self._update_model_info()
        self._set_selecting(False)
        if self._input is not None:
            self._input.focus()

    # ── 提交与流式 ────────────────────────────────────────────────

    async def submit(self, text: str) -> None:
        text = text.strip()
        if not text:
            return

        handler, is_command = dispatch_command(text)
        if is_command:
            assert handler is not None
            if self._input is not None:
                self._input.clear()
            await handler(self)
            return

        if self.state is not SessionState.IDLE:
            return
        self.conv.add_user(text)
        self.query_one("#log", RichLog).write(user_block(text))
        await self._begin_turn()

    async def _begin_turn(self) -> None:
        """启动一轮 Agent Loop（命令路径可复用，不写入用户消息）。"""
        assert self.provider is not None
        assert self._input is not None
        self._input.clear()
        self._input.blur()  # 提交后立即失焦，避免流式/待批准态下输入框吞掉按键
        self.cur_reply = ""
        self.cur_tools = []
        self.iter = 0
        self.turn_start = time.monotonic()
        self.turn_cancel = asyncio.Event()
        self.state = SessionState.STREAMING
        self._refresh_streaming_view()
        self._stream_task = asyncio.create_task(self._consume_agent_events())
        self._timer = self.set_interval(0.1, self._tick)

    def _ensure_agent(self) -> Agent:
        """按需构造一次 Agent；跨 run 复用其压缩状态。"""
        if self.agent is None:
            assert self.provider is not None
            self.agent = Agent(
                self.provider,
                self._tool_registry,
                __version__,
                self.checker,
                runtime=self.runtime,
            )
        return self.agent

    async def _consume_agent_events(self) -> None:
        assert self.turn_cancel is not None
        agent = self._ensure_agent()
        log = self.query_one("#log", RichLog)
        try:
            async for ev in agent.run(self.conv, self.mode, self.turn_cancel):
                if ev.compact is not None:
                    log.write(system_block(format_compact_notice(ev.compact)))
                    continue
                if ev.approval is not None:
                    # 人在回路：进入待批准态，暂停读取后续事件（agent 正 await respond）。
                    self.pending = ev.approval
                    self.approve_cursor = 0
                    self.state = SessionState.APPROVING
                    self._refresh_approval()
                    self.screen.set_focus(None)  # 待批准态强制失焦，按键直达 App.on_key
                    continue
                if ev.err is not None:
                    self._finish_with_error(ev.err)
                    return
                if ev.tool is not None and ev.tool.phase is Phase.START:
                    # 先把 preamble 定型提交，再显示工具行。
                    if self.cur_reply:
                        log.write(assistant_markdown(self.cur_reply))
                        self.cur_reply = ""
                    self.cur_tools.append(ToolDisplay(ev.tool.name, ev.tool.args))
                    self._refresh_streaming_view()
                elif ev.tool is not None and ev.tool.phase is Phase.END:
                    log.write(tool_line(ev.tool.name, ev.tool.args))
                    log.write(tool_result_summary(ev.tool.result, ev.tool.is_error))
                    if self.cur_tools:
                        self.cur_tools.pop(0)
                    self._refresh_streaming_view()
                elif ev.usage is not None:
                    self.usage_in += ev.usage.input
                    self.usage_out += ev.usage.output
                    self._render_header()
                elif ev.notice:
                    log.write(system_block(ev.notice))
                elif ev.iter > 0:
                    self.iter = ev.iter
                    self._refresh_streaming_view()
                elif ev.done:
                    if self.cur_reply:
                        log.write(assistant_markdown(self.cur_reply))
                        self.cur_reply = ""
                    self._stop_streaming()
                    return
                elif ev.text:
                    self.cur_reply += ev.text
                    self._refresh_streaming_view()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 统一转错误展示，不退出
            self._finish_with_error(e)
            return
        # 生成器正常耗尽（如取消路径未 yield done）：收尾回 IDLE。
        self._stop_streaming()

    def _tick(self) -> None:
        if self.state is SessionState.STREAMING:
            self._refresh_streaming_view()

    def _refresh_streaming_view(self) -> None:
        out = Text()
        if self.cur_reply:
            out.append("● ", style="blue")
            out.append(self.cur_reply)
        if self.state is SessionState.STREAMING:
            if out:
                out.append("\n")
            if self.cur_tools:
                lines = "\n".join(
                    f"● {td.name}({td.args}) Running…" for td in self.cur_tools
                )
                out.append(lines, style="bold cyan")
            else:
                elapsed = int(time.monotonic() - self.turn_start)
                iter_suffix = f" · round {self.iter}" if self.iter > 0 else ""
                out.append(f"Imagining… ({elapsed}s{iter_suffix})")
        self.query_one("#streaming", Static).update(out)

    def _refresh_approval(self) -> None:
        if self.pending is not None:
            self.query_one("#streaming", Static).update(
                approval_block(self.pending, self.approve_cursor)
            )

    def _finish_with_error(self, err: Exception) -> None:
        self.query_one("#log", RichLog).write(error_block(err))
        self._stop_streaming()

    def _stop_streaming(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._stream_task = None
        self.cur_reply = ""
        self.cur_tools = []
        self.iter = 0
        self.turn_cancel = None
        self.pending = None
        self.approve_cursor = 0
        self.state = SessionState.IDLE
        if self._input is not None:
            self._input.focus()
        self._refresh_streaming_view()

    # ── 人在回路 ──────────────────────────────────────────────────

    def action_cycle_mode(self) -> None:
        """Shift+Tab：仅空闲态循环切换权限模式。"""
        if self.state is SessionState.IDLE:
            self._cycle_mode()

    def _cycle_mode(self) -> None:
        self.mode = next_mode(self.mode)
        self._update_mode_label()

    def _approve_choose(self, index: int) -> None:
        self._resolve_approval(_APPROVAL_OUTCOMES[index])
        self._refresh_streaming_view()

    def _resolve_approval(self, outcome: Outcome) -> None:
        pending = self.pending
        self.pending = None
        self.approve_cursor = 0
        self.state = SessionState.STREAMING
        if pending is not None and not pending.respond.done():
            pending.respond.set_result(outcome)

    # ── 状态栏与显示切换 ──────────────────────────────────────────

    def _render_header(self) -> None:
        model = self.provider.model if self.provider is not None else None
        self.query_one("#header", Static).update(
            render_header(
                __version__, os.getcwd(), model, self.usage_in, self.usage_out
            )
        )

    def _update_mode_label(self) -> None:
        self.query_one("#mode-label", Static).update(mode_status(self.mode))

    def _update_model_info(self) -> None:
        model = self.provider.model if self.provider is not None else None
        self.query_one("#model-info", Static).update(model_status(model))

    def _set_selecting(self, selecting: bool) -> None:
        chat_display = "none" if selecting else "block"
        for wid in ("#header", "#log", "#streaming", "#input-row", "#status-bar"):
            self.query_one(wid).styles.display = chat_display
        self.query_one("#options").styles.display = "block" if selecting else "none"

    # ── 按键与退出 ────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        # 待批准态：光标移动 + 三选一（Esc/Ctrl+C 走全局 Binding）。
        if self.state is SessionState.APPROVING:
            if event.key in ("up", "k"):
                self.approve_cursor = (self.approve_cursor - 1) % 3
                self._refresh_approval()
            elif event.key in ("down", "j"):
                self.approve_cursor = (self.approve_cursor + 1) % 3
                self._refresh_approval()
            elif event.key in ("enter", "space"):
                self._approve_choose(self.approve_cursor)
            elif event.key in ("1", "2", "3"):
                self._approve_choose(int(event.key) - 1)
            elif event.key == "y":
                self._approve_choose(0)
            elif event.key in ("n", "d"):
                self._approve_choose(2)
            else:
                return
            event.prevent_default()
            event.stop()

    def action_interrupt(self) -> None:
        """Ctrl+C：流式/待批准态取消本轮（不退出）；空闲态退出程序。"""
        if self.state in (SessionState.STREAMING, SessionState.APPROVING):
            if self.state is SessionState.APPROVING:
                self._resolve_approval(Outcome.DENY_ONCE)
            if self.turn_cancel is not None:
                self.turn_cancel.set()
        else:
            self.exit()

    def action_cancel(self) -> None:
        """Esc：流式/待批准态取消本轮；其余忽略。"""
        if self.state in (SessionState.STREAMING, SessionState.APPROVING):
            if self.state is SessionState.APPROVING:
                self._resolve_approval(Outcome.DENY_ONCE)
            if self.turn_cancel is not None:
                self.turn_cancel.set()

    async def action_quit(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
        self.exit()


def run(
    providers: list[ProviderConfig],
    registry: Registry,
    checker: PermissionChecker,
    *,
    runtime: SessionRuntime | None = None,
    mcp_server_count: int = 0,
    mcp_tool_count: int = 0,
) -> None:
    """启动 CrowCode TUI。"""
    CrowCodeApp(
        providers,
        registry,
        checker,
        runtime=runtime,
        mcp_server_count=mcp_server_count,
        mcp_tool_count=mcp_tool_count,
    ).run()
