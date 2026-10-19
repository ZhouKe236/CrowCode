"""MCP 权限适配单测：通配符展开、连字符规则、永久允许与既有链路复用。"""

from __future__ import annotations

from pathlib import Path

import pytest

from crowcode.llm import ToolCall
from crowcode.mcp import install_permission
from crowcode.permission import PermissionMode, new_checker


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _call(name: str, payload: str = "{}") -> ToolCall:
    return ToolCall(id="1", name=name, input=payload)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 HOME，避免读到真实用户级权限配置。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    root = tmp_path / "project"
    root.mkdir()
    return root


def _checker(root: Path, tool_names: list[str]):
    base, err = new_checker(str(root))
    assert err is None
    return install_permission(base, str(root), tool_names)


def test_wildcard_expands_and_deny_wins(root: Path):
    """AC11：mcp__server__* 展开生效；deny 仍全局优先。"""
    tools = ["mcp__demo__echo", "mcp__demo__read", "mcp__demo__danger"]
    _write(
        root / ".crowcode" / "settings.yaml",
        """
permissions:
  allow:
    - mcp__demo__*
  deny:
    - mcp__demo__danger
""",
    )
    base, _ = new_checker(str(root))
    # 未适配前：权限包无法解析带 * 的工具名，回落到模式兜底 ask。
    assert base.check(PermissionMode.DEFAULT, _call(tools[0]), False).effect == "ask"

    checker = install_permission(base, str(root), tools)
    assert (
        checker.check(PermissionMode.DEFAULT, _call(tools[0]), False).effect == "allow"
    )
    assert (
        checker.check(PermissionMode.DEFAULT, _call(tools[2]), False).effect == "deny"
    )
    # 不在通配范围内的 MCP 工具仍走 Exec 兜底。
    other = "mcp__other__echo"
    assert checker.check(PermissionMode.DEFAULT, _call(other), False).effect == "ask"


def test_read_only_mcp_tool_allowed_by_mode_fallback(root: Path):
    """AC11：readOnlyHint=True 的 MCP 工具按只读类放行。"""
    checker = _checker(root, ["mcp__demo__echo"])
    decision = checker.check(PermissionMode.DEFAULT, _call("mcp__demo__echo"), True)
    assert decision.effect == "allow"


def test_hyphenated_exact_rule(root: Path):
    """AC11：含连字符的 server 名规则由 MCP 层补上。"""
    tool = "mcp__my-server__echo"
    _write(
        root / ".crowcode" / "settings.yaml",
        """
permissions:
  allow:
    - mcp__my-server__echo
""",
    )
    base, _ = new_checker(str(root))
    assert base.check(PermissionMode.DEFAULT, _call(tool), False).effect == "ask"
    checker = install_permission(base, str(root), [tool])
    assert checker.check(PermissionMode.DEFAULT, _call(tool), False).effect == "allow"


def test_permanent_allow_persists_and_reloads(root: Path):
    """AC11/场景4：永久允许写入本地层，重启后规则仍生效。"""
    tool = "mcp__my-server__echo"
    checker = _checker(root, [tool])
    checker.persist_local_allow(_call(tool))

    local = root / ".crowcode" / "settings.local.yaml"
    assert f"{tool}()" in local.read_text(encoding="utf-8")

    # 重新加载：权限包会跳过含连字符的工具名，MCP 层再次注入。
    reloaded = _checker(root, [tool])
    assert reloaded.check(PermissionMode.DEFAULT, _call(tool), False).effect == "allow"


def test_non_mcp_rules_still_delegate_to_permission(root: Path):
    """N4：非 MCP 工具的永久允许仍走权限包原逻辑。"""
    checker = _checker(root, ["mcp__demo__echo"])
    checker.persist_local_allow(_call("bash", '{"command": "ls"}'))
    local = root / ".crowcode" / "settings.local.yaml"
    assert "Bash(ls)" in local.read_text(encoding="utf-8")


def test_blacklist_and_sandbox_skip_mcp_tools(root: Path):
    """AC11：MCP 工具不命中黑名单 / 沙箱，只走模式兜底。"""
    checker = _checker(root, ["mcp__demo__echo"])
    decision = checker.check(
        PermissionMode.DEFAULT,
        _call("mcp__demo__echo", '{"path": "/etc/passwd"}'),
        False,
    )
    assert decision.effect == "ask"


def test_no_mcp_tools_returns_original_checker(root: Path):
    base, _ = new_checker(str(root))
    assert install_permission(base, str(root), []) is base
