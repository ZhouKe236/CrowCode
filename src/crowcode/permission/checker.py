"""权限判定编排入口：PermissionChecker 与 Decision 数据类，前四层防御链。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from crowcode.llm import ToolCall

from .dangerous import DangerousCommandDetector
from .modes import (
    Category,
    DecisionEffect,
    PermissionMode,
    mode_decide,
    parse_mode,
)
from .rules import (
    RuleEngine,
    Settings,
    SettingsError,
    categorize,
    extract_content,
    friendly_name,
    load_settings,
    rule_for,
    to_rule_set,
)
from .sandbox import PathSandbox, resolve_root, to_rel_path


@dataclass
class Decision:
    """一次权限判定：效果（allow/deny/ask）与原因。"""

    effect: DecisionEffect
    reason: str


# 类别中文标签（供 Ask 原因文案）。
_CATEGORY_LABEL = {
    Category.READ: "只读",
    Category.WRITE: "文件写",
    Category.EXEC: "命令执行",
}


class PermissionChecker:
    """前四层防御编排：黑名单 → 沙箱 → 规则引擎 → 模式兜底（逐层短路）。

    三个组件（detector / sandbox / rule_engine）由外部注入，便于测试替换。
    ``start_mode`` 为启动默认模式（供 UI 初始化），``check`` 仍按运行时
    ``mode`` 判定（因 TUI 可动态切换模式）。
    """

    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        start_mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.start_mode = start_mode

    def check(self, mode: PermissionMode, call: ToolCall, read_only: bool) -> Decision:
        """前四层判定（agent 每次执行工具前调用），短路返回 Decision。"""
        cat = categorize(call.name, read_only)
        friendly = friendly_name(call.name)
        content, is_file, ok = extract_content(call)

        # ① 黑名单（仅命令执行类；最高优先，bypass 也拦，N1）
        if cat is Category.EXEC and content:
            hit, _reason = self.detector.detect(content)
            if hit:
                return Decision("deny", f"命中危险命令黑名单：{content}")

        # ② 沙箱（仅文件类；先解析符号链接再前缀比对，N2）
        if is_file:
            if not ok:
                return Decision("deny", "无法解析文件路径参数，安全拒绝")
            in_sandbox, reason = self.sandbox.check(content)
            if not in_sandbox:
                return Decision("deny", reason)

        # ③ 规则引擎：deny 全局优先（跨层先查 deny 再查 allow，层级不参与裁决）
        match_target = (
            to_rel_path(self.sandbox.project_root, content) if is_file else content
        )
        is_command = cat is Category.EXEC
        effect = self.rule_engine.evaluate(friendly, match_target, is_command)
        if effect == "deny":
            return Decision("deny", f"匹配 deny 规则：{friendly}({match_target})")
        if effect == "allow":
            return Decision("allow", "")

        # ④ 模式兜底（只产 allow/ask）
        effect = mode_decide(mode, cat)
        if effect == "ask":
            return Decision(
                "ask", f"{mode.value} 模式下 {_CATEGORY_LABEL[cat]} 类操作需确认"
            )
        return Decision("allow", "")

    def persist_local_allow(self, call: ToolCall) -> None:
        """把一条精确 allow 规则写入本地层文件并同步内存（人在回路「永久」调用）。"""
        rule, _yaml_str, ok = rule_for(self.sandbox.project_root, call)
        if not ok:
            return
        self.rule_engine.append_local_rule(rule)


def new_checker(root: str) -> tuple[PermissionChecker, Exception | None]:
    """构造编排入口：解析项目根、加载三级配置、组装 detector/sandbox/rule_engine。

    配置文件格式错误只降级该文件为空（不抛致命错）；仅项目根不可解析时返回
    非 None 的 err + 安全默认 checker。
    """
    err: Exception | None = None
    try:
        resolved_root = resolve_root(root)
    except Exception as e:  # noqa: BLE001 —— 根不可解析时退化为传入值
        err = e
        resolved_root = root

    user_path = os.path.join(str(Path.home()), ".crowcode", "settings.yaml")
    project_path = os.path.join(resolved_root, ".crowcode", "settings.yaml")
    local_path = os.path.join(resolved_root, ".crowcode", "settings.local.yaml")

    settings = {
        "user": _load_or_empty(user_path),
        "project": _load_or_empty(project_path),
        "local": _load_or_empty(local_path),
    }

    start_mode = PermissionMode.DEFAULT
    for key in ("local", "project", "user"):
        mode, ok = parse_mode(settings[key].default_mode)
        if ok:
            start_mode = mode
            break

    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(resolved_root),
        rule_engine=RuleEngine(
            user=to_rule_set(settings["user"]),
            project=to_rule_set(settings["project"]),
            local=to_rule_set(settings["local"]),
            local_path=local_path,
        ),
        start_mode=start_mode,
    )
    return checker, err


def _load_or_empty(path: str) -> Settings:
    """加载单个配置文件；格式错误降级为空（N5）。"""
    try:
        return load_settings(path)
    except SettingsError:
        return Settings()
