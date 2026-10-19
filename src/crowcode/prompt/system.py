"""系统提示装配：稳定段按优先级拼接模块、产出完整系统提示。"""

from __future__ import annotations

from .modules import Module, fixed_modules, optional_modules

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
