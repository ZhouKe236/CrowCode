"""conversation 模块单测。"""

from crowcode.conversation import Conversation
from crowcode.llm import Message, ToolCall, ToolResult


def test_sequence_and_roles():
    conv = Conversation()
    conv.add_user("hi")
    conv.add_assistant("hello")
    conv.add_user("bye")
    msgs = conv.messages()
    assert [(m.role, m.content) for m in msgs] == [
        ("user", "hi"),
        ("assistant", "hello"),
        ("user", "bye"),
    ]


def test_messages_returns_copy():
    conv = Conversation()
    conv.add_user("a")
    msgs = conv.messages()
    msgs.clear()
    assert len(conv.messages()) == 1


def test_tool_turn_sequence():
    conv = Conversation()
    conv.add_user("读文件")
    conv.add_assistant_with_tool_calls(
        "好", [ToolCall(id="c1", name="read_file", input='{"path": "a.txt"}')]
    )
    conv.add_tool_results([ToolResult(tool_call_id="c1", content="内容")])
    conv.add_assistant("已读")
    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].tool_calls[0].name == "read_file"
    assert msgs[1].tool_calls[0].input == '{"path": "a.txt"}'
    assert msgs[2].tool_results[0].tool_call_id == "c1"
    assert msgs[2].tool_results[0].content == "内容"


def test_replace_history_deep_copy():
    conv = Conversation()
    conv.add_user("old")
    msgs = [Message(role="user", content="new")]
    conv.replace_history(msgs)
    msgs[0].content = "changed"
    assert conv.messages()[0].content == "new"
    assert conv.length() == 1


def test_replace_history_empty():
    conv = Conversation()
    conv.add_user("old")
    conv.replace_history(None)
    assert conv.messages() == []
    conv.replace_history([])
    assert conv.length() == 0


def test_last_role():
    conv = Conversation()
    assert conv.last_role() == ""
    conv.add_user("hi")
    assert conv.last_role() == "user"
    conv.add_assistant_with_tool_calls(
        "好", [ToolCall(id="c1", name="read_file", input="{}")]
    )
    assert conv.last_role() == "assistant"
    conv.add_tool_results([ToolResult(tool_call_id="c1", content="内容")])
    assert conv.last_role() == "tool"
    conv.add_assistant("已读")
    assert conv.last_role() == "assistant"
