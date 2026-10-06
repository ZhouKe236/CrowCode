"""anthropic 适配器序列化单测：稳定块缓存断点 + 环境块 + reminder 织入（守护回归）。"""

from __future__ import annotations

from crowcode.llm import Request, System
from crowcode.llm.anthropic_provider import (
    _append_reminder_anthropic,
    _build_system_blocks,
)


def _req(stable: str = "STABLE", environment: str = "ENV") -> Request:
    return Request(system=System(stable=stable, environment=environment))


def test_system_blocks_cache_control():
    """稳定块带 cache_control 断点、环境块不带（AC4/F3）。"""
    assert _build_system_blocks(_req()) == [
        {"type": "text", "text": "STABLE", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "ENV"},
    ]


def test_system_blocks_empty_environment():
    """环境段为空时不产生第二个块。"""
    assert _build_system_blocks(_req(environment="")) == [
        {"type": "text", "text": "STABLE", "cache_control": {"type": "ephemeral"}},
    ]


def test_system_blocks_empty_all():
    """稳定段与环境段都空 → 空列表。"""
    assert _build_system_blocks(_req(stable="", environment="")) == []


def test_append_reminder_to_user_string():
    """末条 user 为纯字符串时，reminder 追加为 content 数组第二块。"""
    messages = [{"role": "user", "content": "hello"}]
    _append_reminder_anthropic(messages, "<system-reminder>R</system-reminder>")
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "<system-reminder>R</system-reminder>"},
    ]


def test_append_reminder_to_user_blocks():
    """末条 user 已是 content 数组（tool_result）时，reminder 追加为尾部块。"""
    messages = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "1", "content": "r"}],
        }
    ]
    _append_reminder_anthropic(messages, "REM")
    assert messages[-1]["content"][-1] == {"type": "text", "text": "REM"}


def test_append_reminder_to_assistant_tail():
    """末条为 assistant（极端情形）时，新起一条 user 消息承载 reminder。"""
    messages = [{"role": "assistant", "content": "done"}]
    _append_reminder_anthropic(messages, "REM")
    assert messages[-1] == {
        "role": "user",
        "content": [{"type": "text", "text": "REM"}],
    }


def test_append_reminder_to_empty():
    """空历史时新起一条 user 消息。"""
    messages: list[dict] = []
    _append_reminder_anthropic(messages, "REM")
    assert messages == [{"role": "user", "content": [{"type": "text", "text": "REM"}]}]
