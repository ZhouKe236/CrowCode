"""agent 包：ReAct 循环编排（想 → 调工具 → 看结果 → 边做边调整，直到任务完成）。"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from crowcode.compact import ManageInput, TriggerKind, manage_context
from crowcode.compact.const import (
    AUTO_SAFETY_MARGIN,
    MANUAL_SAFETY_MARGIN,
    SUMMARY_RESERVE,
)
from crowcode.compact.token import estimate_tokens
from crowcode.compact.token import usage_anchor as usage_to_anchor
from crowcode.conversation import Conversation
from crowcode.llm import (
    PromptTooLongError,
    Provider,
    Request,
    System,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from crowcode.llm import Usage as LLMUsage
from crowcode.permission import Outcome, PermissionChecker, PermissionMode
from crowcode.prompt import build_system_prompt, gather_environment, plan_reminder
from crowcode.tool import DEFAULT_TIMEOUT, Registry, Result

from .event import CompactEvent, CompactPhase
from .runtime import SessionRuntime, new_session_runtime

logger = logging.getLogger(__name__)

# 迭代与停止常量（内置，不可配）。
MAX_ITERATIONS: int = 25  # 迭代上限兜底（F2）
MAX_UNKNOWN_RUN: int = 3  # 连续「整轮只产生未知工具调用」的迭代数上限（F2）

# 规划模式提醒注入节奏：首轮完整，之后每隔该轮次重复完整，其余精简（F7）。
PLAN_REMINDER_INTERVAL: int = 4

# 停止/收尾提示文案——既作为 Event(notice=...) 推给 UI，也作为 ensure_assistant_tail
# 写入历史的兜底文本。
NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"
NOTICE_DENIED_BY_USER = "（用户拒绝了该工具调用。）"

# 自然完成但文本为空时的占位（避免空 assistant 回合破坏下一轮请求）。
_EMPTY_FINAL = "（本轮结束）"

_ARGS_PREVIEW_CHARS = 80


class Phase(Enum):
    START = "start"  # 工具开始执行
    END = "end"  # 工具执行完毕


@dataclass
class ToolEvent:
    """一次工具调用的开始/结束（供 TUI 渲染工具行与结果摘要）。"""

    name: str
    args: str = ""  # 参数预览（用于 ● name(args)）
    phase: Phase = Phase.START
    result: str = ""  # phase=END：结果摘要
    is_error: bool = False  # phase=END：是否错误


@dataclass
class Usage:
    """一轮请求的 token 用量（透传 llm.Usage 语义）。"""

    input: int = 0
    output: int = 0
    cache_write: int = 0  # 缓存写入 token（Anthropic 有值，OpenAI 恒 0）
    cache_read: int = 0  # 缓存读取 token（Anthropic / OpenAI 各自解析）


@dataclass
class ApprovalRequest:
    """人在回路待批准请求：TUI 收到后须 ``respond.set_result(Outcome)`` 才能续跑。"""

    name: str  # 工具内部名（用于展示 ● name(args)）
    args: str  # 参数预览
    reason: str  # 触发 Ask 的原因（模式 + 类别）
    respond: asyncio.Future[Outcome]  # 单次未来量：TUI 回传用户选择


@dataclass
class Event:
    """对外事件流元素，TUI 据非默认字段分派渲染。"""

    text: str = ""  # 模型文本增量（preamble 或最终答复）
    tool: ToolEvent | None = None  # 工具调用开始/结束
    usage: Usage | None = None  # 本轮 token 用量（每轮 stream 结束后一次）
    iter: int = 0  # >0：进入第 iter 轮迭代（进度提示）
    notice: str = ""  # 系统提示（停止原因等），仅用于 UI 展示，不入对话历史
    done: bool = False  # 本轮（整个 Loop）结束
    err: Exception | None = None  # 出错（不中断会话）
    approval: ApprovalRequest | None = None  # 人在回路待批准请求（非 None 即暂停 Loop）
    compact: CompactEvent | None = None  # 压缩生命周期事件


class Agent:
    """持有 provider 与注册中心与权限引擎，执行 ReAct 循环。"""

    def __init__(
        self,
        provider: Provider,
        registry: Registry,
        version: str,
        checker: PermissionChecker,
        *,
        runtime: SessionRuntime | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version
        self._checker = checker
        # 未注入 runtime 时是测试 / 嵌入式用法，落盘目录放系统临时目录，
        # 避免在项目根目录堆出大量会话子目录。
        self._runtime = runtime or new_session_runtime(workspace=tempfile.gettempdir())
        # 保证 run 与 run_force_compact 不并发。
        self._run_lock = asyncio.Lock()

    @property
    def runtime(self) -> SessionRuntime:
        """跨 run 持有的会话状态。"""
        return self._runtime

    async def run(
        self,
        conv: Conversation,
        mode: PermissionMode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event]:
        """执行 Agent Loop，async generator 吐出事件流。

        ``mode`` 决定工具集；``cancel`` 为 per-turn 取消事件，调用方触发
        ``cancel.set()`` 即中断本轮（不退出程序）。
        """
        async with self._run_lock:
            async for event in self._run_locked(conv, mode, cancel):
                yield event

    async def run_force_compact(
        self, conv: Conversation, tool_defs: list[ToolDefinition]
    ) -> tuple[int, int]:
        """手动 ``/compact`` 入口：等待主循环空闲后无条件摘要一次。"""
        async with self._run_lock:
            in_ = self._manage_input(conv, tool_defs, TriggerKind.MANUAL)
            out = await manage_context(in_)
            return out.before_tokens, out.after_tokens

    async def _run_locked(
        self,
        conv: Conversation,
        mode: PermissionMode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event]:
        env_text = gather_environment(self._version, self._provider.model).render()
        sys_prompt = build_system_prompt()

        unknown_run = 0
        for it in range(1, MAX_ITERATIONS + 1):
            yield Event(iter=it)
            if cancel.is_set():
                self._finish_cancelled(conv)
                return

            if mode is PermissionMode.PLAN:
                defs = self._registry.read_only_definitions()
            else:
                defs = self._registry.definitions()

            reminder = ""
            if mode is PermissionMode.PLAN:
                full = it == 1 or (it - 1) % PLAN_REMINDER_INTERVAL == 0
                reminder = plan_reminder(full)

            # 本轮请求前的上下文管理：第 1 层始终执行，必要时触发第 2 层。
            in_ = self._manage_input(conv, defs, TriggerKind.AUTO)
            will_summarize = (
                self._runtime.context_window > SUMMARY_RESERVE + AUTO_SAFETY_MARGIN
                and in_.estimated_token >= self._auto_threshold()
            )
            if will_summarize:
                yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_AUTO))

            manage_out = None
            manage_err: Exception | None = None
            try:
                manage_out = await manage_context(in_)
            except Exception as e:  # noqa: BLE001 —— 压缩失败转成事件
                manage_err = e

            if will_summarize:
                yield Event(
                    compact=CompactEvent(
                        phase=CompactPhase.AFTER_AUTO,
                        before=(
                            manage_out.before_tokens
                            if manage_out is not None
                            else in_.estimated_token
                        ),
                        after=(
                            manage_out.after_tokens if manage_out is not None else 0
                        ),
                        err=manage_err,
                    )
                )
            if manage_err is not None:
                yield Event(err=manage_err)
                self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                return
            if cancel.is_set():
                self._finish_cancelled(conv)
                return

            emergency_retried = False
            holder: list[
                tuple[str, list[ToolCall], LLMUsage | None, Exception | None]
            ] = []
            async for ev in self._stream_once(
                conv, sys_prompt, env_text, defs, reminder, cancel, holder
            ):
                yield ev
            text, calls, usage, err = holder[0]

            if (
                err is not None
                and isinstance(err, PromptTooLongError)
                and not emergency_retried
            ):
                yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_EMERGENCY))
                emergency_in = self._manage_input(conv, defs, TriggerKind.EMERGENCY)
                emergency_out = None
                emergency_err: Exception | None = None
                try:
                    emergency_out = await manage_context(emergency_in)
                except Exception as e:  # noqa: BLE001 —— 转成事件
                    emergency_err = e
                yield Event(
                    compact=CompactEvent(
                        phase=CompactPhase.AFTER_EMERGENCY,
                        before=(
                            emergency_out.before_tokens
                            if emergency_out is not None
                            else emergency_in.estimated_token
                        ),
                        after=(
                            emergency_out.after_tokens
                            if emergency_out is not None
                            else 0
                        ),
                        err=emergency_err,
                    )
                )
                if emergency_err is not None:
                    yield Event(err=emergency_err)
                    self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                    return

                # 对话已重建，重置锚点并用新消息重新估算。
                self._runtime.usage_anchor = 0
                self._runtime.anchor_msg_len = 0
                est2 = estimate_tokens(0, conv.messages(), 0)
                if est2 >= self._runtime.context_window - MANUAL_SAFETY_MARGIN:
                    yield Event(err=err)
                    self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                    return

                emergency_retried = True
                holder = []
                async for ev in self._stream_once(
                    conv, sys_prompt, env_text, defs, reminder, cancel, holder
                ):
                    yield ev
                text, calls, usage, err = holder[0]

            if err is not None:
                if cancel.is_set():
                    self._finish_cancelled(conv)
                    return
                yield Event(err=err)
                self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                return
            if cancel.is_set():
                self._finish_cancelled(conv)
                return

            if usage is not None:
                # 锚点用最近一次主对话路径真实 usage 替换，不累加。
                self._runtime.usage_anchor = usage_to_anchor(usage)
                self._runtime.anchor_msg_len = conv.length()
                yield Event(
                    usage=Usage(
                        input=usage.input_tokens,
                        output=usage.output_tokens,
                        cache_write=usage.cache_write,
                        cache_read=usage.cache_read,
                    )
                )

            if not calls:
                # 自然完成：纯文本即最终答复。
                if not text:
                    text = _EMPTY_FINAL
                    yield Event(text=text)
                conv.add_assistant(text)
                yield Event(done=True)
                return

            conv.add_assistant_with_tool_calls(text, calls)

            unknown_run = unknown_run + 1 if self._all_unknown(calls) else 0

            batched_holder: list[tuple[list[ToolResult], bool]] = []
            async for ev in self._execute_batched(calls, mode, cancel, batched_holder):
                yield ev
            results, completed = batched_holder[0]
            conv.add_tool_results(results)

            if not completed:
                # 执行中被取消：最高优先级终止，跳过未知工具计数与迭代上限检查。
                self._ensure_assistant_tail(conv, NOTICE_CANCELLED)
                return

            if unknown_run >= MAX_UNKNOWN_RUN:
                yield Event(notice=NOTICE_UNKNOWN_TOOLS)
                self._ensure_assistant_tail(conv, NOTICE_UNKNOWN_TOOLS)
                yield Event(done=True)
                return

        # 循环正常走完（触达迭代上限）。
        yield Event(notice=NOTICE_MAX_ITER)
        self._ensure_assistant_tail(conv, NOTICE_MAX_ITER)
        yield Event(done=True)

    def _auto_threshold(self) -> int:
        """自动摘要触发阈值。"""
        return self._runtime.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN

    def _manage_input(
        self,
        conv: Conversation,
        tool_defs: list[ToolDefinition],
        trigger: TriggerKind,
    ) -> ManageInput:
        """用当前会话状态构造一次上下文管理入参。"""
        msgs = conv.messages()
        anchor = self._runtime.usage_anchor
        anchor_len = self._runtime.anchor_msg_len
        return ManageInput(
            conv=conv,
            provider=self._provider,
            context_window=self._runtime.context_window,
            tool_defs=tool_defs,
            replacement=self._runtime.replacement,
            recovery=self._runtime.recovery,
            auto_tracking=self._runtime.auto_tracking,
            session=self._runtime.session,
            usage_anchor=anchor,
            anchor_msg_len=anchor_len,
            estimated_token=estimate_tokens(anchor, msgs, anchor_len),
            trigger=trigger,
        )

    async def _stream_once(
        self,
        conv: Conversation,
        sys_prompt: str,
        env_text: str,
        defs: list[ToolDefinition],
        reminder: str,
        cancel: asyncio.Event,
        holder: list,
    ):
        """单轮请求：转发文本增量、收集工具调用与用量。

        以 async generator 形式把文本事件 yield 出去；结束时把
        ``(text, calls, usage, err)`` 写入 ``holder``。errors 不在这里 yield，
        由主循环统一处理，保证出错时累加的文本不写回 Conversation。
        """
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        usage = None  # llm.Usage | None
        req = Request(
            messages=conv.messages(),
            tools=defs,
            system=System(stable=sys_prompt, environment=env_text),
            reminder=reminder,
        )
        try:
            async for se in self._provider.stream(req):
                if cancel.is_set():
                    holder.append(("", [], None, None))
                    return
                if se.err is not None:
                    holder.append(("", [], None, se.err))
                    return
                if se.usage is not None:
                    usage = se.usage
                if se.tool_calls:
                    calls.extend(se.tool_calls)
                if se.text:
                    text_parts.append(se.text)
                    yield Event(text=se.text)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 转成 err，不中断会话
            holder.append(("", [], None, e))
            return
        if cancel.is_set():
            holder.append(("", [], None, None))
            return
        holder.append(("".join(text_parts), calls, usage, None))

    async def _execute_batched(
        self,
        calls: list[ToolCall],
        mode: PermissionMode,
        cancel: asyncio.Event,
        holder: list,
    ):
        """保序分批执行：连续只读并发、有副作用串行；执行前先过权限判定。

        yield 工具开始/结束事件与人机批准事件；结束时把 ``(results, completed)``
        写入 ``holder``。
        """
        results: list[ToolResult | None] = [None] * len(calls)
        n = len(calls)
        i = 0
        while i < n:
            if cancel.is_set():
                holder.append((self._finalize(results, calls), False))
                return

            ro = self._registry.is_read_only(calls[i].name)
            j = i + 1
            if ro:
                while j < n and self._registry.is_read_only(calls[j].name):
                    j += 1

            # 先按调用序 yield 开始事件（动态区列出多个在执行的工具行）。
            for k in range(i, j):
                preview = calls[k].input[:_ARGS_PREVIEW_CHARS]
                yield Event(
                    tool=ToolEvent(name=calls[k].name, args=preview, phase=Phase.START)
                )

            if ro:
                # 连续只读批：逐个权限判定（只读永不 Ask），被拒预置错误结果，
                # 放行的纳入并发执行（不因权限检查退化为串行）。
                for k in range(i, j):
                    decision = self._checker.check(mode, calls[k], True)
                    if decision.effect == "deny":
                        results[k] = ToolResult(
                            tool_call_id=calls[k].id,
                            content=decision.reason,
                            is_error=True,
                        )
                await asyncio.gather(
                    *[
                        self._run_one(calls[k], k, results)
                        for k in range(i, j)
                        if results[k] is None
                    ]
                )
            else:
                # 有副作用：串行执行单个（权限判定 → Allow 执行 / Deny 回灌 / Ask 人在回路）。
                decision = self._checker.check(mode, calls[i], False)
                if decision.effect == "deny":
                    results[i] = ToolResult(
                        tool_call_id=calls[i].id, content=decision.reason, is_error=True
                    )
                elif decision.effect == "ask":
                    a_holder: list[Outcome] = []
                    async for ev in self._await_approval(
                        calls[i], decision.reason, a_holder
                    ):
                        yield ev
                    outcome = a_holder[0]
                    if outcome is Outcome.ALLOW_FOREVER:
                        try:
                            self._checker.persist_local_allow(calls[i])
                        except Exception as e:  # noqa: BLE001 —— 写本地层失败不阻断执行
                            logger.warning("写入本地权限配置失败: %s", e)
                    if outcome in (Outcome.ALLOW_ONCE, Outcome.ALLOW_FOREVER):
                        await self._run_one(calls[i], i, results)
                    else:
                        results[i] = ToolResult(
                            tool_call_id=calls[i].id,
                            content=NOTICE_DENIED_BY_USER,
                            is_error=True,
                        )
                else:
                    await self._run_one(calls[i], i, results)

            # 再按调用序 yield 结束事件（逐个把工具行 + 结果摘要提交 scrollback）。
            for k in range(i, j):
                preview = calls[k].input[:_ARGS_PREVIEW_CHARS]
                r = results[k]
                assert r is not None
                yield Event(
                    tool=ToolEvent(
                        name=calls[k].name,
                        args=preview,
                        phase=Phase.END,
                        result=r.content,
                        is_error=r.is_error,
                    )
                )

            i = j

        holder.append((self._finalize(results, calls), True))

    async def _await_approval(
        self, call: ToolCall, reason: str, holder: list
    ) -> AsyncIterator[Event]:
        """第五层人在回路：发待批准事件并 await 用户决策，结果写入 ``holder``。

        取消时 ``CancelledError`` 由上层（TUI 兜底 ``respond.set_result`` 或任务取消）
        处理，不在此处吞掉。
        """
        respond: asyncio.Future[Outcome] = asyncio.get_running_loop().create_future()
        yield Event(
            approval=ApprovalRequest(
                name=call.name,
                args=call.input[:_ARGS_PREVIEW_CHARS],
                reason=reason,
                respond=respond,
            )
        )
        holder.append(await respond)

    async def _run_one(
        self,
        call: ToolCall,
        idx: int,
        results: list[ToolResult | None],
    ) -> None:
        """执行单个工具并写入 results[idx]；超时包成结构化错误结果。"""
        try:
            r = await asyncio.wait_for(
                self._registry.execute(call.name, call.input, DEFAULT_TIMEOUT),
                timeout=DEFAULT_TIMEOUT,
            )
        except TimeoutError:
            r = Result(
                content=f"工具 {call.name} 执行超时（{DEFAULT_TIMEOUT}s）",
                is_error=True,
            )
        results[idx] = ToolResult(
            tool_call_id=call.id, content=r.content, is_error=r.is_error
        )
        if not r.is_error and call.name == "read_file":
            # 同一 task 内顺序完成，保证下一次 manage_context 能看到记录。
            await self._record_read_file(call)

    async def _record_read_file(self, call: ToolCall) -> None:
        """用纯净字节（不带行号前缀）记录一次成功的文件读取。"""
        try:
            args = json.loads(call.input or "{}")
        except json.JSONDecodeError:
            return
        if not isinstance(args, dict):
            return
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return
        try:
            abs_path = str(Path(path).resolve())
        except (OSError, RuntimeError):
            return
        try:
            data = await asyncio.to_thread(Path(abs_path).read_bytes)
        except OSError:
            return
        self._runtime.recovery.record_file(
            abs_path, data.decode("utf-8", errors="replace")
        )

    def _finalize(
        self,
        results: list[ToolResult | None],
        calls: list[ToolCall],
    ) -> list[ToolResult]:
        """把未执行的下标补成「已取消」结果，返回无 None 的结果列表。"""
        return [
            r
            if r is not None
            else ToolResult(
                tool_call_id=calls[k].id, content=NOTICE_CANCELLED, is_error=True
            )
            for k, r in enumerate(results)
        ]

    def _all_unknown(self, calls: list[ToolCall]) -> bool:
        """全部工具都未注册才返回 True；混入任一已注册工具视为有进展。"""
        return bool(calls) and all(self._registry.get(c.name) is None for c in calls)

    def _ensure_assistant_tail(self, conv: Conversation, fallback: str) -> None:
        """保证历史以 assistant 文本回合收尾，使下一轮请求角色交替合法。"""
        if conv.last_role() != "assistant":
            conv.add_assistant(fallback)

    def _finish_cancelled(self, conv: Conversation) -> None:
        """取消路径统一收尾（不 yield notice，generator 终结即视为本轮结束）。"""
        self._ensure_assistant_tail(conv, NOTICE_CANCELLED)
