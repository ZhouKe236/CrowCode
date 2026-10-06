"""环境信息采集与渲染（供系统提示第二段，不缓存）。"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class Environment:
    """运行环境快照；任一项取不到时降级为空字符串（N4）。"""

    working_dir: str = ""  # os.getcwd()
    platform: str = ""  # sys.platform
    date: str = ""  # 当前日期（ISO 格式）
    git_status: str = ""  # git status 摘要；非 git 目录 / 取不到则留空
    version: str = ""  # 应用版本（从 agent 透传）
    model: str = ""  # provider.model

    def render(self) -> str:
        """渲染为「环境信息」段——逐行 ``Key: Value``，空值项省略。"""
        lines: list[str] = []
        if self.working_dir:
            lines.append(f"Working directory: {self.working_dir}")
        if self.platform:
            lines.append(f"Platform: {self.platform}")
        if self.date:
            lines.append(f"Date: {self.date}")
        if self.git_status:
            lines.append(f"Git status: {self.git_status}")
        if self.version:
            lines.append(f"Version: {self.version}")
        if self.model:
            lines.append(f"Model: {self.model}")
        return "\n".join(lines)


def _git_status() -> str:
    """``git status --porcelain`` 摘要；非 git 目录 / git 不可用 / 超时均降级为空串。"""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return "clean"
    return f"{len(lines)} changed file(s)"


def gather_environment(version: str, model: str) -> Environment:
    """采集运行环境；不读取任何环境变量（N5）。"""
    try:
        working_dir = os.getcwd()
    except OSError:
        working_dir = ""
    return Environment(
        working_dir=working_dir,
        platform=sys.platform,
        date=datetime.datetime.now(datetime.UTC).astimezone().date().isoformat(),
        git_status=_git_status(),
        version=version,
        model=model,
    )
