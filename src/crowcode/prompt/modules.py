"""系统提示模块：固定模块与可选空槽的内容常量。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    """系统提示的一个模块；按 ``priority`` 升序装配，``content`` 为空则跳过。"""

    name: str  # 模块标识（身份、系统约束 …），仅用于可读性与测试断言
    priority: int  # 数值越小优先级越高、排越前；固定模块 10..70，可选模块 80..100
    content: str  # 模块正文；为空则装配时跳过（可选空槽）


def fixed_modules() -> list[Module]:
    """七个固定模块，按优先级 10..70 排列（身份 → 文本输出）。"""
    return [
        Module(
            name="身份",
            priority=10,
            content=(
                "You are CrowCode, a terminal-based AI coding assistant. "
                "You help the user write, debug, and understand code through concise, "
                "accurate, and helpful answers. Respond in the language the user writes in."
            ),
        ),
        Module(
            name="系统约束",
            priority=20,
            content=(
                "Operate within the user's working directory. Do not leak API keys or "
                "secrets. Be cautious with destructive or hard-to-reverse actions; confirm "
                "before doing them unless explicitly authorized."
            ),
        ),
        Module(
            name="任务模式",
            priority=30,
            content=(
                "Work in a ReAct loop: break the task into steps, read before you edit, and "
                "keep using tools across multiple steps to make progress. Only give your "
                "final concise answer once the task is complete."
            ),
        ),
        Module(
            name="动作执行",
            priority=40,
            content=(
                "Call the appropriate tool when the request requires reading, writing, or "
                "editing files, running a shell command, finding files by pattern, or "
                "searching code content. Independent read-only calls may run together; be "
                "careful with actions that have side effects."
            ),
        ),
        Module(
            name="工具使用",
            priority=50,
            content=(
                "Prefer the dedicated tools (read_file, glob, grep) over assembling shell "
                "commands with bash. Before editing a file, read it first to confirm the "
                "target content is unique."
            ),
        ),
        Module(
            name="语气风格",
            priority=60,
            content="Be concise, direct, and helpful. Do not flatter or over-apologize.",
        ),
        Module(
            name="文本输出",
            priority=70,
            content=(
                "Use Markdown (code blocks, lists) when it helps. Keep the final answer "
                "concise."
            ),
        ),
    ]


def optional_modules() -> list[Module]:
    """三个可选空槽：自定义指令 / 已激活 Skill / 长期记忆（本章留空，后续章节填充）。"""
    return [
        Module(name="自定义指令", priority=80, content=""),
        Module(name="已激活 Skill", priority=90, content=""),
        Module(name="长期记忆", priority=100, content=""),
    ]
