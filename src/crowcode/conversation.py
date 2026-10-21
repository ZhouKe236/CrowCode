"""会话层：进程内维护单会话多轮历史（user / assistant 交替）。"""

from __future__ import annotations

import copy
import threading

from crowcode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolCall,
    ToolResult,
)


class Conversation:
    def __init__(self) -> None:
        # 摘要替换与主循环可能来自不同任务/线程，用可重入锁保护列表读写。
        self._lock = threading.RLock()
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        with self._lock:
            self._messages.append(Message(role=ROLE_USER, content=text))

    def add_assistant(self, text: str) -> None:
        with self._lock:
            self._messages.append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        """assistant 工具调用回合。"""
        with self._lock:
            self._messages.append(
                Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls))
            )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """ROLE_TOOL 结果回合。"""
        with self._lock:
            self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))

    def messages(self) -> list[Message]:
        """返回消息列表的副本，避免外部误改内部状态。"""
        with self._lock:
            return list(self._messages)

    def replace_history(self, msgs: list[Message] | None) -> None:
        """整体替换内存历史；深拷贝避免外部继续持有并修改同一对象。"""
        with self._lock:
            self._messages = copy.deepcopy(msgs or [])

    def length(self) -> int:
        """返回消息条数。"""
        with self._lock:
            return len(self._messages)

    def last_role(self) -> str:
        """返回最后一条消息的 role；空历史返回 ""。"""
        with self._lock:
            return self._messages[-1].role if self._messages else ""
