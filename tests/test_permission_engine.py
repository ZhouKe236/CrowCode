"""Checker 单测：模式矩阵、前四层流水线短路、deny 全局优先、配置降级、默认模式（AC5/AC6/AC7/AC8/AC15/AC18）。"""

import json
from pathlib import Path

from crowcode.llm import ToolCall
from crowcode.permission import Category, PermissionMode, new_checker
from crowcode.permission.modes import mode_decide


def _call(name, **kwargs):
    return ToolCall(id="1", name=name, input=json.dumps(kwargs))


# ── 模式矩阵（AC7/F5）────────────────────────────────────────────


def test_mode_matrix():
    # 只读
    assert mode_decide(PermissionMode.DEFAULT, Category.READ) == "allow"
    assert mode_decide(PermissionMode.BYPASS, Category.READ) == "allow"
    # 文件写
    assert mode_decide(PermissionMode.DEFAULT, Category.WRITE) == "ask"
    assert mode_decide(PermissionMode.ACCEPT_EDITS, Category.WRITE) == "allow"
    assert mode_decide(PermissionMode.PLAN, Category.WRITE) == "ask"
    assert mode_decide(PermissionMode.BYPASS, Category.WRITE) == "allow"
    # 命令执行
    assert mode_decide(PermissionMode.DEFAULT, Category.EXEC) == "ask"
    assert mode_decide(PermissionMode.ACCEPT_EDITS, Category.EXEC) == "ask"
    assert mode_decide(PermissionMode.PLAN, Category.EXEC) == "ask"
    assert mode_decide(PermissionMode.BYPASS, Category.EXEC) == "allow"


# ── 前四层短路与跳层（AC8/F6）────────────────────────────────────


def test_check_blacklist_deny_even_bypass(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(PermissionMode.BYPASS, _call("bash", command="rm -rf /"), False)
    assert d.effect == "deny"
    assert "黑名单" in d.reason


def test_check_sandbox_deny(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(
        PermissionMode.DEFAULT, _call("read_file", path="/etc/passwd"), True
    )
    assert d.effect == "deny"
    assert "项目目录之外" in d.reason


def test_check_read_only_allow(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(PermissionMode.DEFAULT, _call("read_file", path="a.txt"), True)
    assert d.effect == "allow"


def test_check_write_ask_default(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(PermissionMode.DEFAULT, _call("write_file", path="a.txt"), False)
    assert d.effect == "ask"
    assert "需确认" in d.reason


def test_check_bash_ask_default(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(
        PermissionMode.DEFAULT, _call("bash", command="git status"), False
    )
    assert d.effect == "ask"


def test_check_bash_not_blacklisted_by_sandbox(tmp_path):
    """命令执行不被沙箱误拦（Bash 不走沙箱）。"""
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(PermissionMode.DEFAULT, _call("bash", command="echo hi"), False)
    assert d.effect == "ask"  # 未被沙箱 Deny，落到模式兜底 Ask


# ── 规则与优先级（AC3/AC5/F4）────────────────────────────────────


def _write_cfg(tmp_path, name, text):
    d = tmp_path / ".crowcode"
    d.mkdir(exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


def test_check_allow_rule_beats_ask(tmp_path):
    _write_cfg(tmp_path, "settings.yaml", "permissions:\n  allow:\n    - Bash(git *)\n")
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(
        PermissionMode.DEFAULT, _call("bash", command="git status"), False
    )
    assert d.effect == "allow"  # allow 规则命中，不进模式兜底


def test_check_deny_rule(tmp_path):
    _write_cfg(
        tmp_path, "settings.yaml", "permissions:\n  deny:\n    - Bash(git push)\n"
    )
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(PermissionMode.DEFAULT, _call("bash", command="git push"), False)
    assert d.effect == "deny"
    assert "deny" in d.reason


def test_check_deny_global_priority(tmp_path):
    """项目层 allow 与本地层 deny 冲突：deny 全局优先（层级不参与裁决）。"""
    _write_cfg(tmp_path, "settings.yaml", "permissions:\n  allow:\n    - Bash(git *)\n")
    _write_cfg(
        tmp_path, "settings.local.yaml", "permissions:\n  deny:\n    - Bash(git push)\n"
    )
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(PermissionMode.DEFAULT, _call("bash", command="git push"), False)
    assert d.effect == "deny"


def test_check_blacklist_before_allow_rule(tmp_path):
    _write_cfg(tmp_path, "settings.yaml", "permissions:\n  allow:\n    - Bash(rm *)\n")
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(PermissionMode.BYPASS, _call("bash", command="rm -rf /"), False)
    assert d.effect == "deny"  # 黑名单先于规则，allow 规则也拦不住


# ── 安全默认（AC15/N7）───────────────────────────────────────────


def test_check_unknown_tool_ask(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    d = checker.check(PermissionMode.DEFAULT, _call("nope", path="x"), False)
    assert d.effect == "ask"  # 未知工具归 Exec，不直接 Allow


def test_check_file_parse_fail_deny(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    call = ToolCall(id="1", name="write_file", input="not json")
    d = checker.check(PermissionMode.DEFAULT, call, False)
    assert d.effect == "deny"
    assert "无法解析" in d.reason


def test_check_file_missing_path_deny(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    call = ToolCall(id="1", name="read_file", input="{}")
    d = checker.check(PermissionMode.DEFAULT, call, True)
    assert d.effect == "deny"


# ── 配置加载降级与默认模式（AC6/AC18/F4）─────────────────────────


def test_new_checker_invalid_config_degrades(tmp_path):
    _write_cfg(tmp_path, "settings.yaml", "allow: [unclosed\n")
    checker, err = new_checker(str(tmp_path))
    assert err is None  # 配置格式错不致命
    assert checker.rule_engine.project.allow == []
    assert checker.rule_engine.project.deny == []


def test_new_checker_resolve_root_fails(tmp_path):
    checker, err = new_checker(str(tmp_path / "nonexistent" / "deep"))
    assert checker is not None
    assert err is not None  # 根不可解析时返回 err + 空规则 checker


def test_start_mode_priority(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    (fake_home / ".crowcode").mkdir(parents=True)
    (fake_home / ".crowcode" / "settings.yaml").write_text(
        "default_mode: plan\n", encoding="utf-8"
    )
    _write_cfg(tmp_path, "settings.yaml", "default_mode: acceptEdits\n")
    _write_cfg(tmp_path, "settings.local.yaml", "default_mode: bypassPermissions\n")
    checker, _ = new_checker(str(tmp_path))
    assert checker.start_mode is PermissionMode.BYPASS  # local 优先


def test_start_mode_default_when_absent(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    assert checker.start_mode is PermissionMode.DEFAULT
