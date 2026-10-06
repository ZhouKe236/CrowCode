"""系统提示包：模块化装配、环境采集与补充消息构造。

对外产出三类文本：稳定系统提示（可缓存）、环境信息段（不缓存）、
system-reminder 包裹的补充指令。本包不依赖 llm 包（避免循环依赖）。
"""

from __future__ import annotations

from .environment import Environment, gather_environment
from .modules import Module, fixed_modules, optional_modules
from .reminder import EXECUTE_DIRECTIVE, plan_reminder, system_reminder

# ASCII 乌鸦图案（原始字符串，避免反斜杠转义）。
CROW_BANNER = r"""
    _
   /.)
  /)\|
 //)/
/'"^"
""".strip("\n")


def assemble_system(mods: list[Module]) -> str:
    """按 ``priority`` 升序、跳过空 ``content``、以 ``"\\n\\n"`` 连接模块。"""
    ordered = sorted(mods, key=lambda m: m.priority)
    return "\n\n".join(m.content for m in ordered if m.content)


def build_system_prompt() -> str:
    """装配完整系统提示：固定模块 + 可选空槽（空槽内容为空，自动跳过）。"""
    return assemble_system(fixed_modules() + optional_modules())


__all__ = [
    "CROW_BANNER",
    "EXECUTE_DIRECTIVE",
    "Environment",
    "Module",
    "assemble_system",
    "build_system_prompt",
    "fixed_modules",
    "gather_environment",
    "optional_modules",
    "plan_reminder",
    "system_reminder",
]
