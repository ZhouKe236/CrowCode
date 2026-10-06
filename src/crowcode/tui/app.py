"""终端层：Textual App，承载选择 / 对话 / 流式 / 工具行 / 错误的全部交互。"""

from __future__ import annotations

import asyncio
import os
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
from crowcode.agent import Agent, Mode, Phase
from crowcode.config import ProviderConfig
from crowcode.conversation import Conversation
from crowcode.llm import Provider, new_provider
from crowcode.prompt import EXECUTE_DIRECTIVE
from crowcode.tool import Registry

from .select import build_provider_options
from .view import (
    assistant_markdown,
    error_block,
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
    #prompt { width: 2; color: green; }
    #input { width: 1fr; height: auto; min-height: 1; max-height: 8; border: none; padding: 0 1; }
    #options { height: 1fr; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "interrupt", "中断/退出", priority=True),
        Binding("escape", "cancel", "取消本轮", priority=True),
    ]

    def __init__(self, providers: list[ProviderConfig], registry: Registry) -> None:
        super().__init__()
        self.providers = providers
        # 命名 _tool_registry 而非 _registry：后者是 Textual App 内部 DOM 注册表，冲突。
        self._tool_registry = registry
        self.provider: Provider | None = None
        self.conv = Conversation()
        self.state = SessionState.SELECTING if len(providers) > 1 else SessionState.IDLE
        self.cur_reply = ""
        self.turn_start = 0.0
        self.mode: Mode = Mode.NORMAL
        self.iter: int = 0
        self.usage_in: int = 0
        self.usage_out: int = 0
        self.cur_tools: list[ToolDisplay] = []
        self.turn_cancel: asyncio.Event | None = None
        self._stream_task: asyncio.Task | None = None
        self._timer: Timer | None = None
        self._input: ChatInput | None = None

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
        yield OptionList(*build_provider_options(self.providers), id="options")

    def on_mount(self) -> None:
        self._input = self.query_one("#input", ChatInput)
        self._input.submit_callback = self.submit
        if self.state is SessionState.SELECTING:
            self._set_selecting(True)
            self.query_one("#options", OptionList).focus()
        else:
            self.provider = new_provider(self.providers[0])
            self._render_header()
            self._set_selecting(False)

    # ── provider 选择 ──────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.provider = new_provider(self.providers[event.option_index])
        self.state = SessionState.IDLE
        self._render_header()
        self._set_selecting(False)
        if self._input is not None:
            self._input.focus()

    # ── 提交与流式 ────────────────────────────────────────────────

    async def submit(self, text: str) -> None:
        text = text.strip()
        if text == "/exit":
            await self.action_quit()
            return
        if not text or self.state is not SessionState.IDLE:
            return
        log = self.query_one("#log", RichLog)

        if text == "/plan":
            self.mode = Mode.PLAN
            log.write(system_block("已进入计划模式（仅只读工具）。"))
            self._render_header()
            return
        if text == "/do":
            self.mode = Mode.NORMAL
            log.write(system_block("已切换回普通模式，按计划开始执行。"))
            self.conv.add_user(EXECUTE_DIRECTIVE)
            self._render_header()
        else:
            self.conv.add_user(text)
            log.write(user_block(text))

        assert self.provider is not None
        assert self._input is not None
        self._input.clear()
        self.cur_reply = ""
        self.cur_tools = []
        self.iter = 0
        self.turn_start = time.monotonic()
        self.turn_cancel = asyncio.Event()
        self.state = SessionState.STREAMING
        self._refresh_streaming_view()
        self._stream_task = asyncio.create_task(self._consume_agent_events())
        self._timer = self.set_interval(0.1, self._tick)

    async def _consume_agent_events(self) -> None:
        assert self.provider is not None
        assert self.turn_cancel is not None
        agent = Agent(self.provider, self._tool_registry, __version__)
        log = self.query_one("#log", RichLog)
        try:
            async for ev in agent.run(self.conv, self.mode, self.turn_cancel):
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
                iter_suffix = f" · 第 {self.iter} 轮" if self.iter > 0 else ""
                out.append(f"Imagining… ({elapsed}s{iter_suffix})")
        self.query_one("#streaming", Static).update(out)

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
        self.state = SessionState.IDLE
        self._refresh_streaming_view()

    # ── 状态栏与显示切换 ──────────────────────────────────────────

    def _render_header(self) -> None:
        model = self.provider.model if self.provider is not None else None
        self.query_one("#header", Static).update(
            render_header(
                __version__,
                os.getcwd(),
                model,
                self.mode is Mode.PLAN,
                self.usage_in,
                self.usage_out,
            )
        )

    def _set_selecting(self, selecting: bool) -> None:
        chat_display = "none" if selecting else "block"
        for wid in ("#header", "#log", "#streaming", "#input-row"):
            self.query_one(wid).styles.display = chat_display
        self.query_one("#options").styles.display = "block" if selecting else "none"

    # ── 按键与退出 ────────────────────────────────────────────────

    def action_interrupt(self) -> None:
        """Ctrl+C：流式态取消本轮（不退出）；空闲态退出程序。"""
        if self.state is SessionState.STREAMING and self.turn_cancel is not None:
            self.turn_cancel.set()
        else:
            self.exit()

    def action_cancel(self) -> None:
        """Esc：流式态取消本轮；其余忽略。"""
        if self.state is SessionState.STREAMING and self.turn_cancel is not None:
            self.turn_cancel.set()

    async def action_quit(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
        self.exit()


def run(providers: list[ProviderConfig], registry: Registry) -> None:
    """启动 CrowCode TUI。"""
    CrowCodeApp(providers, registry).run()
