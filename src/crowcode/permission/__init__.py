"""权限系统：前四层防御（黑名单/沙箱/规则/模式兜底）与配置加载。

一次工具调用在真正执行前依次穿过 黑名单 → 沙箱 → 规则引擎 → 模式兜底 四层，
判定产三种值 allow/deny/ask；模式兜底层只产 allow/ask。第五层（人在回路）由
agent 在收到 ask 后编排驱动，见 :mod:`crowcode.agent`。
"""

from .checker import Decision, PermissionChecker, new_checker
from .modes import Category, DecisionEffect, Outcome, PermissionMode, parse_mode

__all__ = [
    "Category",
    "Decision",
    "DecisionEffect",
    "Outcome",
    "PermissionChecker",
    "PermissionMode",
    "new_checker",
    "parse_mode",
]
