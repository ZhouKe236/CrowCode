"""权限模式与判定枚举：四档模式、三档类别、人在回路结果、模式兜底矩阵。"""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Literal

# 判定效果：与 ``Decision.effect`` 一致，用字符串表达 allow/deny/ask，
# 免去枚举与配置字符串之间的转换。
DecisionEffect = Literal["allow", "deny", "ask"]


class PermissionMode(str, Enum):
    """四档权限模式；枚举值即配置层字符串（可直接与配置文件比对）。"""

    DEFAULT = "default"  # 只读 Allow / 文件写 Ask / 命令执行 Ask
    ACCEPT_EDITS = "acceptEdits"  # 文件写 Allow / 命令执行 Ask
    PLAN = "plan"  # 仅只读工具可见；矩阵同 default 作防御兜底
    BYPASS = "bypassPermissions"  # 全 Allow（黑名单/沙箱仍拦）


class Category(IntEnum):
    """工具副作用类别，供模式兜底矩阵裁决。"""

    READ = 0  # 只读
    WRITE = 1  # 文件写
    EXEC = 2  # 命令执行


class Outcome(IntEnum):
    """人在回路三选一结果。"""

    DENY_ONCE = 0  # 拒绝本次
    ALLOW_ONCE = 1  # 允许本次（不留规则）
    ALLOW_FOREVER = 2  # 永久允许（写本地层文件，精确匹配）


def parse_mode(s: str) -> tuple[PermissionMode, bool]:
    """大小写不敏感识别四档名；未知/空返回 ``(PermissionMode.DEFAULT, False)``。"""
    table = {
        "default": PermissionMode.DEFAULT,
        "acceptedits": PermissionMode.ACCEPT_EDITS,
        "plan": PermissionMode.PLAN,
        "bypasspermissions": PermissionMode.BYPASS,
    }
    mode = table.get(s.strip().lower())
    if mode is None:
        return (PermissionMode.DEFAULT, False)
    return (mode, True)


# 模式兜底矩阵：四档 × 三类显式填全（含 PLAN，取值同 DEFAULT 作防御兜底）。
# 只读任意档 → allow；bypass 任意类 → allow；acceptEdits+文件写 → allow；其余 → ask（永不 deny）。
_MODE_MATRIX: dict[PermissionMode, dict[Category, DecisionEffect]] = {
    PermissionMode.DEFAULT: {
        Category.READ: "allow",
        Category.WRITE: "ask",
        Category.EXEC: "ask",
    },
    PermissionMode.ACCEPT_EDITS: {
        Category.READ: "allow",
        Category.WRITE: "allow",
        Category.EXEC: "ask",
    },
    PermissionMode.PLAN: {
        Category.READ: "allow",
        Category.WRITE: "ask",
        Category.EXEC: "ask",
    },
    PermissionMode.BYPASS: {
        Category.READ: "allow",
        Category.WRITE: "allow",
        Category.EXEC: "allow",
    },
}


def mode_decide(mode: PermissionMode, category: Category) -> DecisionEffect:
    """模式兜底裁决：一行查表，返回 ``"allow"`` 或 ``"ask"``（绝不 ``"deny"``）。"""
    return _MODE_MATRIX[mode][category]
