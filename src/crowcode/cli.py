"""入口：加载配置、启动 TUI。"""

from __future__ import annotations

import sys

from crowcode.config import ConfigError, load
from crowcode.tool import new_default_registry
from crowcode.tui import run


def _force_utf8() -> None:
    """把标准流切到 UTF-8。

    中文 Windows 的默认控制台编码常被检测为 GBK（``sys.stdout.encoding == "gbk"``），
    而 Textual / Rich 输出 UTF-8，工具结果里的中文会因此乱码。启动前把三个标准流都
    重配为 UTF-8，与 UTF-8 终端（Windows Terminal 等）对齐。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            # 非文本流（如被重定向为 None 或二进制）没有 reconfigure，忽略即可。
            pass


def main() -> None:
    _force_utf8()
    try:
        # cfg = load(".crowcode/config.yaml")
        cfg = load("D:\develop\code\codePython\crowcode\.crowcode\config.yaml")
    except ConfigError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    run(cfg.providers, new_default_registry())
