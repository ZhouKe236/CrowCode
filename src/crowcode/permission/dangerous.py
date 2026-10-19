"""危险命令黑名单：内置正则，命中即 Deny。

本层是**启发式、非完备、不可配置放开**（N1）：不追求穷尽所有危险命令，
只拦截已知高危模式。用户不可增删或关闭黑名单——任何规则、模式（含
bypassPermissions）都无法放开黑名单命中的命令。
"""

from __future__ import annotations

import re

# 内置高危模式（按序编译，命中任一即 Deny），附中文拒绝理由：
#   - 递归强删根/家目录：rm -rf / ｜ rm -fr ~ ｜ rm -rf $HOME ｜ rm -rf /*
#   - 直接写磁盘设备：dd ... of=/dev/...
#   - fork 炸弹：:(){ :|:& };:
#   - 格式化文件系统：mkfs.*
#   - 重定向覆盖磁盘设备：> /dev/sda 等
#   - 递归 777 根目录：chmod -R 777 /
_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\brm\s+(-[A-Za-z]*[rf][A-Za-z]*\s+)+(~|\$HOME|/\*|/(\s|$))"),
        "递归强制删除根目录或家目录",
    ),
    (re.compile(r"\bdd\s+.*\bof=/dev/"), "直接写磁盘设备"),
    (re.compile(r":\(\)\s*\{.*\|.*&\s*\}"), "fork 炸弹"),
    (re.compile(r"\bmkfs\b"), "格式化文件系统"),
    (re.compile(r">\s*/dev/(sd[a-z]+|hd[a-z]+|nvme\d+n\d+|disk)"), "覆盖磁盘设备"),
    (re.compile(r"\bchmod\s+(-[A-Za-z]*\s+)*0?777\s+/"), "递归修改根目录权限"),
]


class DangerousCommandDetector:
    """危险命令检测：对命令串做黑名单正则子串匹配，命中即返回 ``(True, 理由)``。

    支持 ``extra_patterns`` 在构造时注入额外危险模式，便于测试与扩展。
    """

    def __init__(self, extra_patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns: list[tuple[re.Pattern[str], str]] = list(_DANGEROUS_PATTERNS)
        if extra_patterns:
            for regex_str, reason in extra_patterns:
                self._patterns.append((re.compile(regex_str), reason))

    def detect(self, command: str) -> tuple[bool, str]:
        """命令串命中任一黑名单模式返回 ``(True, 理由)``，否则 ``(False, "")``。"""
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        return False, ""
