"""agent 包：ReAct 循环编排（想 → 调工具 → 看结果 → 边做边调整，直到任务完成）。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum, IntEnum

from crowcode.conversation import Conversation
from crowcode.llm import Provider, Request, System, ToolCall, ToolDefinition, ToolResult
from crowcode.llm import Usage as LLMUsage
from crowcode.prompt import build_system_prompt, gather_environment, plan_reminder
from crowcode.tool import DEFAULT_TIMEOUT, Registry, Result

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


class Mode(IntEnum):
    NORMAL = 0  # 普通模式：全工具
    PLAN = 1  # 计划模式：仅只读工具 + 计划态系统后缀


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


class Agent:
    """持有 provider 与注册中心，执行 ReAct 循环。"""

    def __init__(self, provider: Provider, registry: Registry, version: str) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version

    async def run(
        self,
        conv: Conversation,
        mode: Mode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event]:
        """执行 Agent Loop，async generator 吐出事件流。

        ``mode`` 决定工具集；``cancel`` 为 per-turn 取消事件，调用方触发
        ``cancel.set()`` 即中断本轮（不退出程序）。
        """
        env_text = gather_environment(self._version, self._provider.model).render()
        sys_prompt = build_system_prompt()
        if mode is Mode.PLAN:
            defs = self._registry.read_only_definitions()
        else:
            defs = self._registry.definitions()

        unknown_run = 0
        for it in range(1, MAX_ITERATIONS + 1):
            yield Event(iter=it)
            if cancel.is_set():
                self._finish_cancelled(conv)
                return

            reminder = ""
            if mode is Mode.PLAN:
                full = it == 1 or (it - 1) % PLAN_REMINDER_INTERVAL == 0
                reminder = plan_reminder(full)

            holder: list[tuple[str, list[ToolCall], LLMUsage | None, bool]] = []
            async for ev in self._stream_once(
                conv, sys_prompt, env_text, defs, reminder, cancel, holder
            ):
                yield ev
            text, calls, usage, ok = holder[0]
            if not ok:
                if cancel.is_set():
                    self._finish_cancelled(conv)
                    return
                # 流出错：err 事件已在 stream_once 内 yield。
                self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                return

            if usage is not None:
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
            async for ev in self._execute_batched(calls, cancel, batched_holder):
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

        以 async generator 形式把文本 / 出错事件 yield 出去；结束时把
        ``(text, calls, usage, ok)`` 写入 ``holder``（Python 异步生成器不能 ``return value``，
        用可变容器回传结果）。
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
                    holder.append(("", [], None, False))
                    return
                if se.err is not None:
                    yield Event(err=se.err)
                    holder.append(("", [], None, False))
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
        except Exception as e:  # noqa: BLE001 —— 转成 err 事件，不中断会话
            yield Event(err=e)
            holder.append(("", [], None, False))
            return
        if cancel.is_set():
            holder.append(("", [], None, False))
            return
        holder.append(("".join(text_parts), calls, usage, True))

    async def _execute_batched(
        self,
        calls: list[ToolCall],
        cancel: asyncio.Event,
        holder: list,
    ):
        """保序分批执行：连续只读并发、有副作用串行。

        yield 工具开始/结束事件；结束时把 ``(results, completed)`` 写入 ``holder``。
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
                # 连续只读批：并发执行，各 task 只写自己下标 results[k]。
                await asyncio.gather(
                    *[self._run_one(calls[k], k, results) for k in range(i, j)]
                )
            else:
                # 有副作用：串行执行单个。
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
