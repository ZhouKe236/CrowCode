"""补充消息构造：system-reminder 标签包裹与规划模式提醒。"""

from __future__ import annotations


def system_reminder(body: str) -> str:
    """用 ``<system-reminder>`` 标签包裹补充指令，使模型视其为系统上下文而非用户提问。"""
    return f"<system-reminder>\n{body}\n</system-reminder>"


# 规划模式提醒：完整版（首轮 / 间隔轮注入）。
_PLAN_REMINDER_FULL = (
    "You are currently in PLAN MODE. You may use ONLY the read-only tools "
    "(read_file, glob, grep) to investigate the codebase. You must NOT write files, "
    "edit files, or run shell commands. Produce a clear, step-by-step plan for the task, "
    "then stop and wait for the user to approve it with /do before doing any work."
)

# 规划模式提醒：精简版（其余轮注入，控制频率）。
_PLAN_REMINDER_CONCISE = (
    "Still in PLAN MODE: read-only tools only, keep investigating and refining the plan. "
    "Wait for /do to start executing."
)


def plan_reminder(full: bool) -> str:
    """返回包好标签的规划模式提醒；``full=True`` 完整版，否则精简版。"""
    return system_reminder(_PLAN_REMINDER_FULL if full else _PLAN_REMINDER_CONCISE)


# EXECUTE_DIRECTIVE：/do 注入的用户消息——指示模型按上文已确认的计划开始执行。
EXECUTE_DIRECTIVE = "请按上面的计划开始执行。"
