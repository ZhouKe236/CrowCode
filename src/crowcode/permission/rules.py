"""规则引擎与配置加载：以「工具名(模式)」声明的 allow/deny 规则、精确 + glob 匹配、
三级配置 YAML 加载、内容提取与精确规则生成。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from crowcode.llm import ToolCall

from .modes import Category, DecisionEffect
from .sandbox import to_rel_path

# ── 规则数据结构 ─────────────────────────────────────────────────


@dataclass
class Rule:
    """一条规则：工具友好名 + 模式段（"" 表示匹配该工具全部调用）。"""

    tool: str
    pattern: str
    allow: bool  # True=allow，False=deny


@dataclass
class RuleSet:
    """一层规则集：allow 与 deny 分开存放，deny 优先。"""

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match_deny(self, friendly: str, target: str, is_command: bool = False) -> bool:
        return any(
            r.tool == friendly and match_pattern(r.pattern, target, is_command)
            for r in self.deny
        )

    def match_allow(self, friendly: str, target: str, is_command: bool = False) -> bool:
        return any(
            r.tool == friendly and match_pattern(r.pattern, target, is_command)
            for r in self.allow
        )


# ── 规则解析与匹配 ───────────────────────────────────────────────


def parse_rule(s: str) -> tuple[Rule, bool]:
    """解析 ``Tool(pattern)`` 或 ``Tool``；非法返回 ``(Rule("","",False), False)``。"""
    s = s.strip()
    if not s:
        return (Rule("", "", False), False)
    m = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*(?:\((.*)\))?$", s, re.DOTALL)
    if m is None:
        return (Rule("", "", False), False)
    tool = m.group(1)
    pattern = (m.group(2) or "").strip()
    return (Rule(tool=tool, pattern=pattern, allow=True), True)


def match_pattern(pattern: str, target: str, is_command: bool = False) -> bool:
    """glob 匹配：``pattern == ""`` 恒真；命令串 ``*`` 匹配任意字符（含空格/斜杠），
    ``**`` 等价 ``*``；文件路径 ``*`` 段内、``**`` 跨段。"""
    if pattern == "":
        return True
    rx = _command_glob_regex(pattern) if is_command else _file_glob_regex(pattern)
    return rx.fullmatch(target) is not None


def escape_glob(s: str) -> str:
    """转义字面 glob 元字符（``*`` 与 ``\\``），防止精确规则被泛化。"""
    return s.replace("\\", "\\\\").replace("*", "\\*")


def _command_glob_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if c == "*":
            while i < n and pattern[i] == "*":
                i += 1
            out.append(".*")
            continue
        out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _file_glob_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


# ── 规则引擎 ─────────────────────────────────────────────────────


class RuleEngine:
    """三级规则集（user/project/local）+ 本地层持久化。

    规则在构造时一次性加载；``evaluate`` 裁决 deny 全局优先于 allow，层级不
    参与裁决。``append_local_rule`` 把永久放行规则写入本地层文件并同步内存。
    """

    def __init__(
        self,
        user: RuleSet,
        project: RuleSet,
        local: RuleSet,
        local_path: str | None = None,
    ) -> None:
        self.user = user
        self.project = project
        self.local = local
        self.local_path = local_path

    def evaluate(
        self, friendly: str, target: str, is_command: bool = False
    ) -> DecisionEffect | None:
        """在三级规则集上裁决，返回 ``"deny"`` / ``"allow"`` / ``None``（未命中）。

        deny 跨层优先：先按 local → project → user 查 deny，命中即 deny；再按
        同序查 allow，命中即 allow；均未命中返回 None 交模式兜底。
        """
        for rs in (self.local, self.project, self.user):
            if rs.match_deny(friendly, target, is_command):
                return "deny"
        for rs in (self.local, self.project, self.user):
            if rs.match_allow(friendly, target, is_command):
                return "allow"
        return None

    def append_local_rule(self, rule: Rule) -> None:
        """把一条 allow 规则写入本地层文件并同步内存（去重，幂等）。"""
        if self.local_path is None:
            return
        yaml_str = f"{rule.tool}({rule.pattern})"
        try:
            settings = load_settings(self.local_path)
        except SettingsError:
            settings = Settings()
        if yaml_str not in settings.permissions.allow:
            settings.permissions.allow.append(yaml_str)
        _write_settings(self.local_path, settings)
        self.local.allow.append(rule)


# ── 工具名映射与内容提取 ─────────────────────────────────────────


# 友好名 ↔ 内部名映射（规则面向用户的工具名）。
_FRIENDLY = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "glob": "Glob",
    "grep": "Grep",
}


def friendly_name(internal: str) -> str:
    """内部工具名 → 友好名；未知原样返回。"""
    return _FRIENDLY.get(internal, internal)


def categorize(internal: str, read_only: bool) -> Category:
    """按副作用归类；``read_only`` 优先，其次按名字（未知/未注册 → Exec，N7 最严）。"""
    if read_only:
        return Category.READ
    if internal in ("write_file", "edit_file"):
        return Category.WRITE
    return Category.EXEC


def extract_content(call: ToolCall) -> tuple[str, bool, bool]:
    """提取工具调用的目标内容，返回 ``(content, is_file, ok)``。

    ``ok=False`` 表示参数无法解析或缺必填字段；文件类取 ``path``（glob/grep 取
    搜索根，空→``.``），bash 取 ``command``，未知工具返回空且 ``ok=False``。
    """
    name = call.name
    is_file = name in ("read_file", "write_file", "edit_file", "glob", "grep")
    data = _parse_input(call.input)
    if data is None:
        return ("", is_file, False)
    if name in ("read_file", "write_file", "edit_file"):
        path = data.get("path")
        if not isinstance(path, str) or not path:
            return ("", True, False)
        return (path, True, True)
    if name in ("glob", "grep"):
        path = data.get("path")
        if path is None or path == "":
            path = "."
        if not isinstance(path, str):
            return ("", True, False)
        return (path, True, True)
    if name == "bash":
        command = data.get("command")
        if not isinstance(command, str):
            return ("", False, False)
        return (command, False, True)
    return ("", False, False)


def _parse_input(raw: object) -> dict | None:
    """把 ``call.input``（str 或 dict）解析为 dict；失败返回 None。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None
    return None


def rule_for(root: str, call: ToolCall) -> tuple[Rule, str, bool]:
    """据一次调用生成精确 allow 规则，返回 ``(Rule, YAML 串, 是否成功)``。

    文件类生成 ``Write(<relpath>)`` / ``Read(<relpath>)`` 等（相对 root 的 slash
    路径）；bash 生成 ``Bash(<command>)``（命令串转义 glob 元字符防泛化）。
    解析失败/未知工具返回 ``(Rule("","",False), "", False)``。
    """
    friendly = friendly_name(call.name)
    target, is_file, ok = extract_content(call)
    if is_file:
        if not ok:
            return (Rule("", "", False), "", False)
        pattern = to_rel_path(root, target)
    else:
        if not ok:
            return (Rule("", "", False), "", False)
        pattern = escape_glob(target)
    rule = Rule(tool=friendly, pattern=pattern, allow=True)
    return (rule, f"{friendly}({pattern})", True)


# ── 配置加载 ─────────────────────────────────────────────────────


class SettingsError(Exception):
    """配置格式错误；调用方据此把该文件降级为空规则集（N5）。"""


@dataclass
class PermissionsBlock:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class Settings:
    default_mode: str = ""  # 可选：default/acceptEdits/plan/bypassPermissions
    permissions: PermissionsBlock = field(default_factory=PermissionsBlock)


def load_settings(path: str) -> Settings:
    """读取单个配置文件；文件缺失→空 Settings；格式非法→抛 SettingsError（N5）。"""
    p = Path(path)
    if not p.exists():
        return Settings()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SettingsError(f"配置文件解析失败: {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SettingsError(f"配置文件顶层应为 mapping: {path}")

    default_mode = raw.get("default_mode")
    if not isinstance(default_mode, str):
        default_mode = ""
    perms = raw.get("permissions")
    if not isinstance(perms, dict):
        perms = {}
    allow = perms.get("allow")
    deny = perms.get("deny")
    allow = [x for x in allow if isinstance(x, str)] if isinstance(allow, list) else []
    deny = [x for x in deny if isinstance(x, str)] if isinstance(deny, list) else []
    return Settings(
        default_mode=default_mode,
        permissions=PermissionsBlock(allow=allow, deny=deny),
    )


def to_rule_set(s: Settings) -> RuleSet:
    """把 Settings 转成 RuleSet；非法规则条目跳过（N5）。"""
    rs = RuleSet()
    for text in s.permissions.allow:
        rule, ok = parse_rule(text)
        if ok:
            rule.allow = True
            rs.allow.append(rule)
    for text in s.permissions.deny:
        rule, ok = parse_rule(text)
        if ok:
            rule.allow = False
            rs.deny.append(rule)
    return rs


def _write_settings(path: str, settings: Settings) -> None:
    """把 Settings 序列化写回 YAML（默认模式为空则省略该键）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "permissions": {
            "allow": settings.permissions.allow,
            "deny": settings.permissions.deny,
        }
    }
    if settings.default_mode:
        data["default_mode"] = settings.default_mode
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    p.write_text(text, encoding="utf-8")
