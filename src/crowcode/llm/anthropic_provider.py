"""anthropic 适配器：封装 ``AsyncAnthropic``，统一吐出文本增量与工具调用（思考增量丢弃）。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import anthropic

from crowcode.config import ProviderConfig

from .base import (
    Message,
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    Usage,
)


def _wrap_prompt_too_long(err: Exception) -> Exception:
    """把 anthropic 的「上下文过长」错误包装成统一哨兵异常。"""
    if not isinstance(err, anthropic.BadRequestError):
        return err
    text = str(err).lower()
    if "prompt is too long" in text or "context length" in text:
        wrapped = PromptTooLongError("anthropic prompt too long")
        wrapped.__cause__ = err
        return wrapped
    return err


def _to_anthropic_tools(tools: list[ToolDefinition]) -> list[dict]:
    """把协议无关工具定义转为 anthropic 的 ``tools`` 参数形态。"""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


def _has_tool_history(msgs: list[Message]) -> bool:
    """历史中是否已含工具交互；这类续答请求需关闭 thinking（见 plan 决策）。"""
    return any(m.tool_calls or m.tool_results for m in msgs)


def _build_system_blocks(req: Request) -> list[dict]:
    """构造 anthropic ``system`` 入参：稳定块打缓存断点，环境块不打。"""
    blocks: list[dict] = []
    if req.system.stable:
        blocks.append(
            {
                "type": "text",
                "text": req.system.stable,
                "cache_control": {"type": "ephemeral"},
            }
        )
    if req.system.environment:
        blocks.append({"type": "text", "text": req.system.environment})
    return blocks


def _append_reminder_anthropic(messages: list[dict], reminder: str) -> None:
    """把 reminder 文本块追加到最后一条消息的 content，确保末条仍为 user 回合（N3）。

    循环中末条恒为 user（或 tool_result→user），追加文本块仍是合法 user 消息；
    极端情形（末条为 assistant）则新起一条 user 消息。
    """
    block = {"type": "text", "text": reminder}
    if not messages:
        messages.append({"role": "user", "content": [block]})
        return
    last = messages[-1]
    if last["role"] == "user":
        content = last["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        content.append(block)
        last["content"] = content
    else:
        messages.append({"role": "user", "content": [block]})


def _to_anthropic_messages(msgs: list[Message]) -> list[dict]:
    """把内部消息转为 anthropic 线格式（tool_use / tool_result 展开为 content 数组）。"""
    out: list[dict] = []
    for m in msgs:
        if m.role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.tool_call_id,
                            "content": r.content,
                            "is_error": r.is_error,
                        }
                        for r in m.tool_results
                    ],
                }
            )
        elif m.tool_calls:
            content: list[dict] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for c in m.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": c.id,
                        "name": c.name,
                        "input": json.loads(c.input or "{}"),
                    }
                )
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


class AnthropicProvider:
    def __init__(self, cfg: ProviderConfig) -> None:
        self._name = cfg.name
        self._model = cfg.model
        self._thinking = cfg.thinking
        self._client = anthropic.AsyncAnthropic(
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        messages = _to_anthropic_messages(req.messages)
        if req.reminder:
            _append_reminder_anthropic(messages, req.reminder)
        system_blocks = _build_system_blocks(req)
        params: dict = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system_blocks:
            params["system"] = system_blocks
        if req.tools:
            params["tools"] = _to_anthropic_tools(req.tools)
        if self._thinking and not _has_tool_history(req.messages):
            params["thinking"] = {"type": "enabled", "budget_tokens": 2048}

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        yield StreamEvent(text=event.delta.text)
                # 流结束：SDK 内部累加器已保留完整 input JSON 与聚合用量。
                final_message = await stream.get_final_message()
                if final_message.usage is not None:
                    u = final_message.usage
                    yield StreamEvent(
                        usage=Usage(
                            input_tokens=u.input_tokens,
                            output_tokens=u.output_tokens,
                            cache_write=getattr(u, "cache_creation_input_tokens", 0)
                            or 0,
                            cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                        )
                    )
                if final_message.stop_reason == "tool_use":
                    calls = [
                        ToolCall(
                            id=block.id,
                            name=block.name,
                            input=json.dumps(block.input),
                        )
                        for block in final_message.content
                        if block.type == "tool_use"
                    ]
                    if calls:
                        yield StreamEvent(tool_calls=calls)
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 统一转成 err 事件，由上层展示
            yield StreamEvent(err=_wrap_prompt_too_long(e))
