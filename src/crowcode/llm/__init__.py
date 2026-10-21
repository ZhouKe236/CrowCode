"""LLM 协议层：协议无关的 Provider 抽象与统一消息/流式事件类型。"""

from .base import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    PromptTooLongError,
    Provider,
    Request,
    Role,
    StreamEvent,
    System,
    ToolCall,
    ToolDefinition,
    ToolResult,
    Usage,
    new_provider,
)

__all__ = [
    "ROLE_ASSISTANT",
    "ROLE_TOOL",
    "ROLE_USER",
    "Message",
    "PromptTooLongError",
    "Provider",
    "Request",
    "Role",
    "StreamEvent",
    "System",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "Usage",
    "new_provider",
]
