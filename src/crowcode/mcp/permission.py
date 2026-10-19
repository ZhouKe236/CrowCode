"""MCP 权限适配：在 permission 包源码零改动的前提下补齐 MCP 规则语义。

权限包的 ``parse_rule`` 只接受 ``[A-Za-z][A-Za-z0-9_]*`` 的工具名，因此
``mcp__<server>__*`` 这样的工具名通配符、含连字符的 server 名，以及未知工具的
「永久允许」都无法由权限包自身表达。这里在 MCP 层做三件事：

1. 读取三层 settings 中所有 ``mcp__`` 前缀的规则；
2. 把工具名通配符按启动时已知的 MCP 工具展开成精确规则，注入同一个
   ``RuleEngine``（deny 优先、层级顺序均沿用权限包既有语义）；
3. 用 :class:`McpPermissionChecker` 覆盖「永久允许」的持久化，为 MCP 工具写入
   ``mcp__<server>__<tool>()`` 形式的精确规则。

既没有改 permission 包源码，也没有绕过它的任何一层（黑名单 / 沙箱对 MCP 工具
天然不命中，规则引擎与模式兜底照常裁决）。
"""

from __future__ import annotations

import re
from pathlib import Path

from crowcode.llm import ToolCall
from crowcode.permission import PermissionChecker
from crowcode.permission.rules import (
    Rule,
    RuleSet,
    Settings,
    SettingsError,
    load_settings,
)

_MCP_PREFIX = "mcp__"

# MCP 规则的工具名允许字母、数字、下划线、连字符与通配符 *。
_RULE_RE = re.compile(r"^\s*([A-Za-z0-9_*\-]+)\s*(?:\((.*)\))?\s*$", re.DOTALL)


class McpPermissionChecker(PermissionChecker):
    """在权限包行为之上，补上 MCP 工具的「永久允许」持久化。"""

    def persist_local_allow(self, call: ToolCall) -> None:
        if not call.name.startswith(_MCP_PREFIX):
            super().persist_local_allow(call)
            return
        # MCP 工具没有可提取的「目标内容」，用空模式匹配该工具的全部调用。
        self.rule_engine.append_local_rule(Rule(tool=call.name, pattern="", allow=True))


def install_permission(
    checker: PermissionChecker, root: str, tool_names: list[str]
) -> PermissionChecker:
    """把 settings 中的 MCP 规则展开并注入 checker，返回 MCP 感知的 checker。"""
    mcp_tools = [name for name in tool_names if name.startswith(_MCP_PREFIX)]
    if not mcp_tools:
        return checker

    result = McpPermissionChecker(
        detector=checker.detector,
        sandbox=checker.sandbox,
        rule_engine=checker.rule_engine,
        start_mode=checker.start_mode,
    )
    for layer, path in _settings_paths(root).items():
        settings = _safe_load(path)
        if settings is None:
            continue
        rule_set: RuleSet = getattr(result.rule_engine, layer)
        for text in settings.permissions.allow:
            _inject(rule_set.allow, text, mcp_tools, allow=True)
        for text in settings.permissions.deny:
            _inject(rule_set.deny, text, mcp_tools, allow=False)
    return result


def _settings_paths(root: str) -> dict[str, Path]:
    """三层 settings 路径（与 permission.new_checker 保持一致）。"""
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        home = Path("__crowcode_no_home__")
    return {
        "user": home / ".crowcode" / "settings.yaml",
        "project": Path(root) / ".crowcode" / "settings.yaml",
        "local": Path(root) / ".crowcode" / "settings.local.yaml",
    }


def _safe_load(path: Path) -> Settings | None:
    """加载单个 settings；缺失/非法返回 ``None``（与权限包降级语义一致）。"""
    try:
        return load_settings(str(path))
    except SettingsError:
        return None


def _inject(rules: list[Rule], text: str, tool_names: list[str], allow: bool) -> None:
    """解析一条原始规则；仅处理 ``mcp__`` 前缀，展开通配符后注入。"""
    match = _RULE_RE.match(text)
    if match is None:
        return
    pattern = match.group(1)
    if not pattern.startswith(_MCP_PREFIX):
        return
    for name in _match_tools(pattern, tool_names):
        rule = Rule(tool=name, pattern="", allow=allow)
        if not any(r.tool == rule.tool and r.pattern == rule.pattern for r in rules):
            rules.append(rule)


def _match_tools(pattern: str, tool_names: list[str]) -> list[str]:
    """把 ``mcp__server__*`` 展开为当前已知的精确工具名。"""
    if "*" not in pattern:
        return [pattern] if pattern in tool_names else []
    regex = "^" + ".*".join(re.escape(p) for p in pattern.split("*")) + "$"
    compiled = re.compile(regex)
    return [name for name in tool_names if compiled.match(name) is not None]


__all__ = ["McpPermissionChecker", "install_permission"]
