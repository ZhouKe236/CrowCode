"""配置加载与映射单测：三级 YAML、友好名/类别/内容提取（AC4/AC6/N5/F4）。"""

import json

import pytest

from crowcode.llm import ToolCall
from crowcode.permission import Category
from crowcode.permission.rules import (
    PermissionsBlock,
    Settings,
    SettingsError,
    categorize,
    extract_content,
    friendly_name,
    load_settings,
    to_rule_set,
)


def test_load_settings_missing(tmp_path):
    s = load_settings(str(tmp_path / "nope.yaml"))
    assert s.default_mode == ""
    assert s.permissions.allow == []
    assert s.permissions.deny == []


def test_load_settings_invalid_yaml(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("allow: [unclosed\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(str(p))


def test_load_settings_valid(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "default_mode: acceptEdits\n"
        "permissions:\n"
        "  allow:\n    - Bash(git *)\n"
        "  deny:\n    - Read(.env)\n",
        encoding="utf-8",
    )
    s = load_settings(str(p))
    assert s.default_mode == "acceptEdits"
    assert s.permissions.allow == ["Bash(git *)"]
    assert s.permissions.deny == ["Read(.env)"]


def test_to_rule_set_skips_invalid():
    s = Settings(
        permissions=PermissionsBlock(
            allow=["Bash(git *)", "not valid("],
            deny=["Read(.env)"],
        )
    )
    rs = to_rule_set(s)
    assert len(rs.allow) == 1
    assert len(rs.deny) == 1


def test_friendly_name():
    assert friendly_name("bash") == "Bash"
    assert friendly_name("read_file") == "Read"
    assert friendly_name("write_file") == "Write"
    assert friendly_name("edit_file") == "Edit"
    assert friendly_name("glob") == "Glob"
    assert friendly_name("grep") == "Grep"
    assert friendly_name("unknown") == "unknown"


def test_categorize():
    assert categorize("read_file", True) is Category.READ
    assert categorize("write_file", False) is Category.WRITE
    assert categorize("edit_file", False) is Category.WRITE
    assert categorize("bash", False) is Category.EXEC
    assert categorize("unknown", False) is Category.EXEC
    assert categorize("bash", True) is Category.READ  # read_only 优先


def test_extract_content_file():
    call = ToolCall(id="1", name="read_file", input=json.dumps({"path": "a.txt"}))
    assert extract_content(call) == ("a.txt", True, True)


def test_extract_content_bash():
    call = ToolCall(id="1", name="bash", input=json.dumps({"command": "git status"}))
    assert extract_content(call) == ("git status", False, True)


def test_extract_content_bash_missing_command():
    call = ToolCall(id="1", name="bash", input="{}")
    target, is_file, ok = extract_content(call)
    assert target == ""
    assert is_file is False
    assert ok is False


def test_extract_content_file_parse_fail():
    call = ToolCall(id="1", name="write_file", input="not json")
    _target, is_file, ok = extract_content(call)
    assert is_file is True
    assert ok is False


def test_extract_content_file_missing_path():
    call = ToolCall(id="1", name="read_file", input="{}")
    _target, is_file, ok = extract_content(call)
    assert is_file is True
    assert ok is False


def test_extract_content_glob_default_root():
    call = ToolCall(id="1", name="glob", input=json.dumps({"pattern": "*.py"}))
    target, is_file, ok = extract_content(call)
    assert (target, is_file, ok) == (".", True, True)


def test_extract_content_unknown():
    call = ToolCall(id="1", name="nope", input="{}")
    assert extract_content(call) == ("", False, False)
