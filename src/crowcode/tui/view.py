"""渲染辅助：头部 logo、用户块、助手 markdown、错误块、工具行。"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

from crowcode.prompt import CROW_BANNER


def _fmt_tokens(n: int) -> str:
    """token 用量紧凑格式：≥1000 显示为 1.2k。"""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def render_header(
    version: str,
    cwd: str,
    model: str | None,
    plan: bool = False,
    usage_in: int = 0,
    usage_out: int = 0,
) -> Text:
    """头部 logo：蓝色乌鸦，右侧分行显示版本 / 模型（含模式徽标）/ 用量 / 项目路径。"""
    crow_lines = CROW_BANNER.splitlines()
    crow_width = max(len(line) for line in crow_lines)
    model_line = f"{model}" if model else "model: —"
    if plan:
        model_line += "  [PLAN]"
    info_lines = [
        "",
        f"Crow Code v{version}",
        model_line,
        f"↑{_fmt_tokens(usage_in)} ↓{_fmt_tokens(usage_out)} tok",
        f"{cwd}",
    ]
    out = Text()
    for i in range(max(len(crow_lines), len(info_lines))):
        if i:
            out.append("\n")
        crow_part = crow_lines[i] if i < len(crow_lines) else ""
        out.append(crow_part.ljust(crow_width + 2), style="bold blue")
        info_part = info_lines[i] if i < len(info_lines) else ""
        out.append(info_part, style="grey93 dim")
    return out


def user_block(text: str) -> Text:
    """用户输入块：绿色 > 前缀，加粗正文。"""
    out = Text("> ", style="bold green")
    out.append(text, style="bold white")
    return out


def assistant_markdown(text: str) -> Group:
    """助手回复定型渲染为 markdown，带蓝色 ● 前缀。"""
    return Group(Text("● ", style="blue"), Markdown(text))


def error_block(err: Exception) -> Text:
    """错误块：红色加粗，可区分于正常回复。"""
    return Text("● " + str(err), style="bold red")


def system_block(text: str) -> Text:
    """系统提示块：灰色斜体，用于 /plan /do 提示与停止通知。"""
    return Text("— " + text, style="dim italic")


def _clip_lines(text: str, max_lines: int) -> str:
    """按行截断，超出时尾部追加省略号。"""
    lines = text.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + "\n…"
    return text


def tool_line(name: str, args: str) -> RenderableType:
    """工具行：青色 ● + 加粗 ``name(args)``（Claude Code 风格）。"""
    out = Text("● ", style="bold cyan")
    out.append(f"{name}({args})", style="bold")
    return out


def tool_result_summary(result: str, is_error: bool) -> RenderableType:
    """工具结果摘要：缩进 ⎿ 前缀，红色（错误）/ 灰色（正常），UI 截断约 8 行。"""
    style = "red" if is_error else "dim"
    return Padding(Text("⎿ " + _clip_lines(result, 8), style=style), (0, 0, 0, 2))
