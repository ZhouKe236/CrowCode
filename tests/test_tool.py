"""tool 模块单测：注册中心 + 6 个核心工具。"""

import json
import sys

import pytest

from crowcode.tool import new_default_registry


@pytest.mark.asyncio
async def test_definitions_six_ordered():
    """注册中心导出恰好 6 条定义且名称有序（AC1）。"""
    registry = new_default_registry()
    defs = registry.definitions()
    assert [d.name for d in defs] == [
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "glob",
        "grep",
    ]


@pytest.mark.asyncio
async def test_registry_get():
    registry = new_default_registry()
    assert registry.get("read_file") is not None
    assert registry.get("nope") is None


@pytest.mark.asyncio
async def test_registry_unknown_tool():
    registry = new_default_registry()
    r = await registry.execute("nope", "{}")
    assert r.is_error is True
    assert "未知工具" in r.content


def test_read_only_definitions():
    """只读工具定义只含 read_file/glob/grep，且保持注册顺序。"""
    registry = new_default_registry()
    names = [d.name for d in registry.read_only_definitions()]
    assert names == ["read_file", "glob", "grep"]


def test_is_read_only():
    registry = new_default_registry()
    assert registry.is_read_only("read_file") is True
    assert registry.is_read_only("glob") is True
    assert registry.is_read_only("grep") is True
    assert registry.is_read_only("write_file") is False
    assert registry.is_read_only("edit_file") is False
    assert registry.is_read_only("bash") is False
    assert registry.is_read_only("nope") is False


@pytest.mark.asyncio
async def test_read_file_ok(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello\nworld\n", encoding="utf-8")
    registry = new_default_registry()
    r = await registry.execute("read_file", json.dumps({"path": str(p)}))
    assert r.is_error is False
    assert "hello" in r.content
    assert "world" in r.content


@pytest.mark.asyncio
async def test_read_file_missing():
    registry = new_default_registry()
    r = await registry.execute("read_file", json.dumps({"path": "no/such/file.txt"}))
    assert r.is_error is True
    assert "不存在" in r.content


@pytest.mark.asyncio
async def test_write_file_nested(tmp_path):
    target = tmp_path / "a" / "b" / "c.txt"
    registry = new_default_registry()
    r = await registry.execute(
        "write_file", json.dumps({"path": str(target), "content": "hello"})
    )
    assert r.is_error is False
    assert target.read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_edit_file_unique(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("hello world\n", encoding="utf-8")
    registry = new_default_registry()
    r = await registry.execute(
        "edit_file",
        json.dumps({"path": str(p), "old_string": "world", "new_string": "there"}),
    )
    assert r.is_error is False
    assert p.read_text(encoding="utf-8") == "hello there\n"


@pytest.mark.asyncio
async def test_edit_file_zero(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("hello\n", encoding="utf-8")
    registry = new_default_registry()
    r = await registry.execute(
        "edit_file",
        json.dumps({"path": str(p), "old_string": "xyz", "new_string": "abc"}),
    )
    assert r.is_error is True
    assert "未找到" in r.content


@pytest.mark.asyncio
async def test_edit_file_many(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("aa aa aa\n", encoding="utf-8")
    registry = new_default_registry()
    r = await registry.execute(
        "edit_file",
        json.dumps({"path": str(p), "old_string": "aa", "new_string": "bb"}),
    )
    assert r.is_error is True
    assert "3" in r.content  # 匹配到 3 处
    assert "不唯一" in r.content


@pytest.mark.asyncio
async def test_bash_echo():
    registry = new_default_registry()
    r = await registry.execute("bash", json.dumps({"command": "echo hi"}))
    assert r.is_error is False
    assert "hi" in r.content
    assert "exit_code: 0" in r.content


@pytest.mark.asyncio
async def test_bash_timeout():
    registry = new_default_registry()
    cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    r = await registry.execute("bash", json.dumps({"command": cmd}), timeout=0.2)
    assert r.is_error is True
    assert "超时" in r.content


@pytest.mark.asyncio
async def test_glob_py(tmp_path):
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.txt").write_text("", encoding="utf-8")
    registry = new_default_registry()
    r = await registry.execute(
        "glob", json.dumps({"pattern": "*.py", "path": str(tmp_path)})
    )
    assert r.is_error is False
    assert "a.py" in r.content
    assert "b.txt" not in r.content


@pytest.mark.asyncio
async def test_grep_keyword(tmp_path):
    (tmp_path / "x.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    registry = new_default_registry()
    r = await registry.execute(
        "grep", json.dumps({"pattern": "foo", "path": str(tmp_path)})
    )
    assert r.is_error is False
    assert "x.py" in r.content
    assert "def foo" in r.content


@pytest.mark.asyncio
async def test_grep_invalid_regex():
    registry = new_default_registry()
    r = await registry.execute("grep", json.dumps({"pattern": "["}))
    assert r.is_error is True
    assert "正则非法" in r.content
