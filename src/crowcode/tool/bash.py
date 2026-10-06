"""bash 工具：在工作目录执行 shell 命令，返回 stdout/stderr/退出码。"""

from __future__ import annotations

import asyncio
import locale
import subprocess
import tempfile
from pathlib import Path

from crowcode.tool import DEFAULT_TIMEOUT, Result, _parse_args, _truncate

_MAX_LINES = 10000
_MAX_CHARS = 30000


def _decode_output(raw: bytes) -> str:
    """按 UTF-8 → 系统首选编码的顺序解码子进程输出。

    Windows 原生命令（echo / dir 等）按控制台代码页（中文系统为 GBK）输出字节，
    而 git、python（UTF-8 模式）等输出 UTF-8；依次尝试即可正确还原中文。
    """
    for enc in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class BashTool:
    """执行命令：超时即终止；非零退出按结果回灌让模型判断（AC5/N1）。

    Windows 上 ``asyncio`` 子进程 + ``wait_for`` 无法及时中断（shell 的子进程继承
    管道句柄，杀掉 shell 后管道仍被占用）。故改用 ``subprocess.Popen`` + 输出重定向
    到临时文件 + 线程内 ``proc.wait(timeout)``，超时直接 ``kill()`` 立即返回。
    """

    read_only = False

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return (
            "在工作目录执行一条 shell 命令，返回标准输出、标准错误与退出码。"
            "读文件、找文件、搜内容请优先用 read_file / glob / grep，不要用 bash 拼凑。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        }

    async def execute(self, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        data = _parse_args(args)
        if isinstance(data, Result):
            return data
        command = data.get("command")
        if not command:
            return Result(content="缺少参数 command", is_error=True)

        # 输出写临时文件而非管道，避免子进程占用管道导致回收阻塞（见类 docstring）。
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmpdir = Path(tmp)
            out_path = tmpdir / "stdout.txt"
            err_path = tmpdir / "stderr.txt"
            with out_path.open("wb") as out_f, err_path.open("wb") as err_f:
                try:
                    proc = subprocess.Popen(  # noqa: ASYNC220 —— Popen 仅 spawn 不等待；阻塞的 wait(timeout) 已放入 to_thread
                        command, shell=True, stdout=out_f, stderr=err_f
                    )
                except OSError as e:
                    return Result(content=f"命令启动失败: {e}", is_error=True)
            try:
                returncode = await asyncio.to_thread(proc.wait, timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait)
                return Result(content=f"命令执行超时（{timeout}s）", is_error=True)
            stdout = _decode_output(out_path.read_bytes())
            stderr = _decode_output(err_path.read_bytes())

        text = f"exit_code: {returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        return Result(content=_truncate(text, _MAX_LINES, _MAX_CHARS))
