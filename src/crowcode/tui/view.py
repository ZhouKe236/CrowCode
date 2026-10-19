"""渲染辅助：头部 logo、用户块、助手 markdown、错误块、工具行、待批准块。"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

from crowcode.agent import ApprovalRequest
from crowcode.permission import PermissionMode
from crowcode.prompt import CROW_BANNER


def _fmt_tokens(n: int) -> str:
    """token 用量紧凑格式：≥1000 显示为 1.2k。"""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


_MODE_LABELS = {
    PermissionMode.DEFAULT: "DEFAULT",
    PermissionMode.ACCEPT_EDITS: "ACCEPT EDITS",
    PermissionMode.PLAN: "PLAN",
    PermissionMode.BYPASS: "BYPASS",
}

_MODE_STYLES = {
    PermissionMode.DEFAULT: "green",
    PermissionMode.ACCEPT_EDITS: "cyan",
    PermissionMode.PLAN: "yellow",
    PermissionMode.BYPASS: "red",
}


def mode_status(mode: PermissionMode) -> Text:
    """状态栏左下角常驻的权限模式标签；非默认档追加 shift+tab 切换提示。"""
    out = Text(f"[{_MODE_LABELS[mode]}]", style=_MODE_STYLES[mode])
    if mode is not PermissionMode.DEFAULT:
        out.append("  (shift+tab to cycle)", style="dim")
    return out


def render_header(
    version: str,
    cwd: str,
    model: str | None,
    usage_in: int = 0,
    usage_out: int = 0,
) -> Text:
    """头部 logo：蓝色乌鸦，右侧分行显示版本 / 模型 / 用量 / 项目路径。"""
    crow_lines = CROW_BANNER.splitlines()
    crow_width = max(len(line) for line in crow_lines)
    model_line = Text(model if model else "model: —", style="grey93 dim")
    info_lines: list[Text] = [
        Text(),
        Text(f"Crow Code v{version}", style="grey93 dim"),
        model_line,
        Text(
            f"↑{_fmt_tokens(usage_in)} ↓{_fmt_tokens(usage_out)} tok",
            style="grey93 dim",
        ),
        Text(f"{cwd}", style="grey93 dim"),
    ]
    out = Text()
    for i in range(max(len(crow_lines), len(info_lines))):
        if i:
            out.append("\n")
        crow_part = crow_lines[i] if i < len(crow_lines) else ""
        out.append(crow_part.ljust(crow_width + 4), style="bold blue")
        out.append(info_lines[i] if i < len(info_lines) else Text())
    return out


def model_status(model: str | None) -> Text:
    """状态栏右侧：模型名"""
    name = model if model else "—"
    return Text(name, style="grey93 dim")


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
    """系统提示块：灰色斜体，用于 /plan /do 提示、模式切换与停止通知。"""
    return Text("— " + text, style="dim italic")


def mcp_summary_block(server_count: int, tool_count: int) -> Text:
    """MCP 初始化汇总：灰色斜体系统提示。"""
    return system_block(
        f"Connected to {server_count} MCP server(s), {tool_count} tools registered"
    )


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


_APPROVAL_OPTIONS = (
    "Allow this once",
    "Allow forever (write to local config)",
    "Deny this once",
)


def approval_block(req: ApprovalRequest, cursor: int) -> Text:
    """人在回路待批准块：工具名 + 参数预览 + 触发原因 + 三选菜单（光标高亮）。"""
    out = Text()
    out.append("● ", style="bold yellow")
    out.append(f"{req.name}({req.args})\n", style="bold")
    out.append("Reason: ", style="dim italic")
    out.append(f"{req.reason}\n", style="dim")
    out.append("Continue?\n", style="bold")
    for idx, label in enumerate(_APPROVAL_OPTIONS):
        if idx == cursor:
            out.append(f"> {idx + 1}. {label}\n", style="bold green")
        else:
            out.append(f"  {idx + 1}. {label}\n", style="dim")
    out.append("↑↓ select · Enter confirm · 1/2/3 · Esc cancel", style="dim")
    return out
