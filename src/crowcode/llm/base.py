"""LLM 协议层：协议无关的 Provider 抽象与统一消息/流式事件类型。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from crowcode.config import ProviderConfig


class PromptTooLongError(Exception):
    """Provider 上报上下文超出窗口时统一抛出的哨兵异常。"""


Role = Literal["user", "assistant", "tool"]

# 消息角色。
ROLE_USER: Role = "user"
ROLE_ASSISTANT: Role = "assistant"
ROLE_TOOL: Role = "tool"  # 携带工具执行结果的回合


@dataclass
class ToolCall:
    """协议无关地承载模型发起的一次工具调用（流式拼接完成后）。"""

    id: str  # provider 侧调用 id；回灌结果时配对
    name: str  # 工具名（注册中心按名查找）
    input: str  # 拼接完成的 JSON 参数字符串（raw JSON）


@dataclass
class ToolResult:
    """协议无关地承载一次工具执行结果。"""

    tool_call_id: str  # 对应 ToolCall.id
    content: str  # 执行产出（成功内容或结构化错误文本）
    is_error: bool = False  # 是否为错误结果（F9）


@dataclass
class ToolDefinition:
    """注册中心导出的协议无关工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any]  # 完整 JSON Schema：type/properties/required


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)  # 仅 assistant
    tool_results: list[ToolResult] = field(default_factory=list)  # 仅 ROLE_TOOL


@dataclass
class Usage:
    """协议无关地承载一轮请求的 token 用量。"""

    input_tokens: int = 0  # 本轮请求输入（含完整历史）token 数
    output_tokens: int = 0  # 本轮响应输出 token 数
    cache_write: int = (
        0  # 缓存写入 token（Anthropic: cache_creation_input_tokens；OpenAI: 恒 0）
    )
    cache_read: int = (
        0  # 缓存读取 token（Anthropic: cache_read_input_tokens；OpenAI: cached_tokens）
    )


@dataclass
class System:
    """系统提示两段：稳定段（可缓存）与环境段（不缓存）。"""

    stable: str = ""  # 可缓存：装配好的稳定系统提示
    environment: str = ""  # 不缓存：环境信息段


@dataclass
class Request:
    """一次请求的全部入参；``stream`` 只收这一个对象。"""

    messages: list[Message] = field(
        default_factory=list
    )  # 持久对话历史（不含本轮 reminder）
    tools: list[ToolDefinition] = field(
        default_factory=list
    )  # 本轮工具集（普通=全量 / 规划=只读）
    system: System = field(default_factory=System)  # 稳定系统提示 + 环境段
    reminder: str = ""  # 本轮 system-reminder 内容（已含标签；空=不注入）


@dataclass
class StreamEvent:
    text: str = ""  # 文本增量
    tool_calls: list[ToolCall] = field(
        default_factory=list
    )  # 非空：本轮模型请求执行这些工具（done 之前发出）
    usage: Usage | None = None  # 非空：本轮 token 用量（done 之前一次性发出）
    done: bool = False  # 本轮正常结束
    err: Exception | None = None  # 出错（与 done 互斥）


class Provider(Protocol):
    """协议无关的流式对话接口；思考增量在适配器内部丢弃。

    ``stream`` 接收 :class:`Request`，由其承载消息、工具、系统提示（稳定段 + 环境段）
    与本轮补充消息（reminder）。
    """

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def stream(self, req: Request) -> AsyncIterator[StreamEvent]: ...


def new_provider(cfg: ProviderConfig) -> Provider:
    """按 ``cfg.protocol`` 构造对应适配器。"""
    if cfg.protocol == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    if cfg.protocol == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)
    raise ValueError(f"未知协议: {cfg.protocol!r}")
