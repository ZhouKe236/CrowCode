"""prompt 包单测：模块装配、空槽跳过、N1 确定性、F5 双重强化、环境与 reminder。"""

from __future__ import annotations

import datetime

from crowcode.prompt import (
    Module,
    assemble_system,
    build_system_prompt,
    fixed_modules,
    gather_environment,
    optional_modules,
    plan_reminder,
    system_reminder,
)
from crowcode.tool import new_default_registry


def test_fixed_modules_order():
    """七个固定模块按优先级升序、名称序列符合预期（AC1）。"""
    mods = fixed_modules()
    assert [m.name for m in mods] == [
        "身份",
        "系统约束",
        "任务模式",
        "动作执行",
        "工具使用",
        "语气风格",
        "文本输出",
    ]
    priorities = [m.priority for m in mods]
    assert priorities == sorted(priorities)


def test_build_system_prompt_order_and_separator():
    """身份段在工具使用段之前；模块间以空行分隔（AC1）。"""
    text = build_system_prompt()
    assert text.index("CrowCode") < text.index("Prefer the dedicated tools")
    # 七个固定模块之间恰有 6 处空行分隔；可选空槽不产生额外空行。
    assert text.count("\n\n") == 6


def test_optional_slots_skipped():
    """可选空槽 content 为空，装配时跳过、不留多余空行（AC2）。"""
    assert [m.content for m in optional_modules()] == ["", "", ""]
    text = build_system_prompt()
    assert "\n\n\n" not in text
    assert not text.endswith("\n\n")


def test_assemble_system_skips_empty_and_orders_by_priority():
    """含空 content 的模块被跳过；额外模块按优先级插入正确位置（AC1/AC2）。"""
    extra = Module(name="额外", priority=15, content="EXTRA")
    mods = fixed_modules() + [Module(name="空", priority=85, content="")] + [extra]
    out = assemble_system(mods)
    assert "EXTRA" in out
    assert "\n\n\n" not in out
    # extra 落在身份(10) 之后、系统约束(20) 之前。
    assert out.index("EXTRA") < out.index("Operate within")


def test_n1_deterministic():
    """两次装配稳定系统提示逐字节相等；环境/时间相关内容不进稳定块（N1/AC5）。"""
    a = build_system_prompt()
    b = build_system_prompt()
    assert a == b
    assert "2026" not in a  # 日期不进稳定块
    assert "Working directory" not in a  # 环境不进稳定块


def test_f5_double_reinforcement_in_system_prompt():
    """关键约定（优先专用工具、编辑前先读）在系统提示模块中出现（AC7/F5）。"""
    text = build_system_prompt()
    assert "Prefer the dedicated tools" in text
    assert "read_file" in text and "glob" in text and "grep" in text
    assert "Before editing" in text


def test_f5_double_reinforcement_in_tool_descriptions():
    """关键约定在 edit_file / bash 工具描述中同步出现（AC7/F5）。"""
    registry = new_default_registry()
    by_name = {d.name: d.description for d in registry.definitions()}
    assert "read_file" in by_name["edit_file"]  # 编辑前先读
    assert "read_file" in by_name["bash"]  # 优先专用工具
    assert "glob" in by_name["bash"]
    assert "grep" in by_name["bash"]


def test_environment_render():
    """环境段渲染含工作目录/平台/日期/版本/模型，空项省略（AC3）。"""
    env = gather_environment("dev", "m")
    out = env.render()
    assert "Working directory:" in out
    assert "Platform:" in out
    assert "Date:" in out
    assert "Version: dev" in out
    assert "Model: m" in out
    datetime.date.fromisoformat(env.date)  # 日期为 ISO 格式


def test_gather_environment_non_git(tmp_path, monkeypatch):
    """非 git 目录下降级：git_status 为空且不抛异常（AC13/N4）。"""
    monkeypatch.chdir(tmp_path)
    env = gather_environment("dev", "m")
    assert env.git_status == ""


def test_system_reminder_wraps():
    """补充消息以 <system-reminder> 标签包裹（AC8/F6）。"""
    out = system_reminder("hello")
    assert out.startswith("<system-reminder>")
    assert out.endswith("</system-reminder>")
    assert "hello" in out


def test_plan_reminder_full_vs_concise():
    """规划提醒 full/concise 详略区分，均带标签（AC9/F7）。"""
    full = plan_reminder(True)
    concise = plan_reminder(False)
    assert "<system-reminder>" in full and "<system-reminder>" in concise
    assert "PLAN MODE" in full
    assert "PLAN MODE" in concise
    assert full != concise
    assert len(full) > len(concise)
