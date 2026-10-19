"""规则与匹配单测：parse_rule、glob 匹配、规则引擎 deny 优先（AC3/AC4/F3）。"""

from crowcode.permission.rules import (
    Rule,
    RuleEngine,
    RuleSet,
    escape_glob,
    match_pattern,
    parse_rule,
)


def test_parse_rule_with_pattern():
    rule, ok = parse_rule("Bash(git *)")
    assert ok is True
    assert rule.tool == "Bash"
    assert rule.pattern == "git *"


def test_parse_rule_no_pattern():
    rule, ok = parse_rule("Read")
    assert ok is True
    assert rule.tool == "Read"
    assert rule.pattern == ""


def test_parse_rule_invalid():
    assert parse_rule("Bash(git")[1] is False  # 括号不配对
    assert parse_rule("")[1] is False
    assert parse_rule("(git)")[1] is False  # 缺工具名


def test_match_pattern_command_glob():
    assert match_pattern("git *", "git status", is_command=True) is True
    assert match_pattern("git *", "git checkout -b foo", is_command=True) is True
    assert match_pattern("git *", "npm i", is_command=True) is False


def test_match_pattern_file_glob():
    assert match_pattern("src/**", "src/a/b.py") is True
    assert match_pattern("src/**", "docs/x") is False
    assert match_pattern("src/*", "src/a/b.py") is False  # * 段内不跨目录
    assert match_pattern("src/*", "src/a.py") is True


def test_match_pattern_empty_matches_all():
    assert match_pattern("", "anything") is True


def test_rule_engine_evaluate_deny_priority():
    engine = RuleEngine(
        user=RuleSet(),
        project=RuleSet(),
        local=RuleSet(
            allow=[Rule("Bash", "git *", True)],
            deny=[Rule("Bash", "git push", False)],
        ),
        local_path=None,
    )
    assert engine.evaluate("Bash", "git push", is_command=True) == "deny"
    assert engine.evaluate("Bash", "git status", is_command=True) == "allow"
    assert engine.evaluate("Bash", "npm i", is_command=True) is None


def test_escape_glob():
    assert escape_glob("rm *") == r"rm \*"
    assert escape_glob("git status") == "git status"
