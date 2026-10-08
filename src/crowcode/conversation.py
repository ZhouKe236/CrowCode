"""会话层：进程内维护单会话多轮历史（user / assistant 交替）。"""

from __future__ import annotations

from crowcode.llm import (
    ROLE_ASSISTANT,
    ROLE_USER,
    ROLE_TOOL,
    Message,
    ToolCall,
    ToolResult,
)


class Conversation:
    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        self._messages.append(Message(role=ROLE_USER, content=text))

    def add_assistant(self, text: str) -> None:
        self._messages.append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        """assistant 工具调用回合。"""
        self._messages.append(
            Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls))
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """ROLE_TOOL 结果回合。"""
        self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))

    def messages(self) -> list[Message]:
        """返回消息列表的副本，避免外部误改内部状态。"""
        return list(self._messages)

    def last_role(self) -> str:
        """返回最后一条消息的 role；空历史返回 ""。"""
        return self._messages[-1].role if self._messages else ""
