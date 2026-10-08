"""系统提示包：模块化装配、环境采集与补充消息构造。

对外产出三类文本：稳定系统提示（可缓存）、环境信息段（不缓存）、
system-reminder 包裹的补充指令。本包不依赖 llm 包（避免循环依赖）。
"""

from .environment import Environment, gather_environment
from .modules import Module, fixed_modules, optional_modules
from .reminder import EXECUTE_DIRECTIVE, plan_reminder, system_reminder
from .system import CROW_BANNER, assemble_system, build_system_prompt

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
