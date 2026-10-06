"""openai 适配器：封装 ``AsyncOpenAI``，统一吐出文本增量与工具调用。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import openai

from crowcode.config import ProviderConfig
from crowcode.llm import Request, StreamEvent, ToolCall, ToolDefinition, Usage


def _to_openai_tools(tools: list[ToolDefinition]) -> list[dict]:
    """把协议无关工具定义转为 openai 的 ``tools`` 参数形态。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _to_openai_messages(req: Request) -> list[dict]:
    """把内部消息转为 openai 线格式（assistant.tool_calls / tool 消息）。

    稳定段居系统消息前缀（端点前缀缓存自动命中稳定部分）；环境段拼入同一条 system 消息；
    reminder 追加为尾部 user 消息。
    """
    system_text = req.system.stable
    if req.system.environment:
        system_text = system_text + "\n\n" + req.system.environment
    out: list[dict] = [{"role": "system", "content": system_text}]
    for m in req.messages:
        if m.role == "tool":
            for r in m.tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": r.tool_call_id,
                        "content": r.content,
                    }
                )
        elif m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.input or "{}"},
                        }
                        for c in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    if req.reminder:
        out.append({"role": "user", "content": req.reminder})
    return out


class OpenAIProvider:
    def __init__(self, cfg: ProviderConfig) -> None:
        self._name = cfg.name
        self._model = cfg.model
        # thinking 字段对 openai 协议无意义，忽略。
        self._client = openai.AsyncOpenAI(
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
        messages = _to_openai_messages(req)
        params: dict = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if req.tools:
            params["tools"] = _to_openai_tools(req.tools)
        try:
            stream = await self._client.chat.completions.create(**params)
            tool_calls_buf: dict[int, dict[str, str]] = {}
            async for chunk in stream:
                # 流末尾出现 choices 为空但带 usage 的 chunk（include_usage=True 由 SDK 透传）。
                if chunk.usage is not None:
                    cached = (
                        getattr(
                            getattr(chunk.usage, "prompt_tokens_details", None),
                            "cached_tokens",
                            0,
                        )
                        or 0
                    )
                    yield StreamEvent(
                        usage=Usage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cache_write=0,
                            cache_read=cached,
                        )
                    )
                    continue
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    yield StreamEvent(text=delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        buf = tool_calls_buf.setdefault(
                            idx, {"id": "", "name": "", "args": ""}
                        )
                        if tc.id:
                            buf["id"] = tc.id
                        if tc.function and tc.function.name:
                            buf["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            buf["args"] += tc.function.arguments
            if tool_calls_buf:
                calls = [
                    ToolCall(
                        id=v["id"],
                        name=v["name"],
                        input=v["args"] or "{}",
                    )
                    for _, v in sorted(tool_calls_buf.items())
                ]
                yield StreamEvent(tool_calls=calls)
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 统一转成 err 事件，由上层展示
            yield StreamEvent(err=e)
