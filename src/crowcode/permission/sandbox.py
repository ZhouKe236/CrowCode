"""路径沙箱：把文件类工具限定在项目根目录内（先解析符号链接、再做前缀判断）。"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_root(root: str) -> str:
    """解析项目根：展开 ``~``、解析为绝对路径并强制符号链接解析。"""
    return str(Path(root).expanduser().resolve(strict=True))


def _eval_symlinks_or_ancestor(abs_path: str) -> str:
    """解析符号链接得到真实路径。

    目标存在则直接 ``resolve(strict=True)``；不存在（如新建文件、含未创建的
    中间目录）则逐级回退到最近**已存在**的祖先目录，解析后再拼回剩余段——
    避免因目标不存在而误判。
    """
    p = Path(abs_path)
    if p.exists():
        return str(p.resolve(strict=True))

    tail: list[str] = []
    existing = p
    while not existing.exists():
        tail.append(existing.name)
        parent = existing.parent
        if parent == existing:
            # 已到文件系统根仍不存在：直接返回原路径，交由前缀判断兜底。
            return str(p)
        existing = parent

    resolved = str(existing.resolve(strict=True))
    for seg in reversed(tail):
        resolved = os.path.join(resolved, seg)
    return resolved


def to_rel_path(root: str, target: str) -> str:
    """把文件目标规整为相对 ``root`` 的 slash 路径（供规则匹配与精确规则生成）。"""
    p = Path(target)
    if not p.is_absolute():
        p = Path(root) / p
    try:
        rel = os.path.relpath(p, root)
    except ValueError:  # Windows 跨盘符等无法相对化的情形
        rel = str(p)
    return rel.replace(os.sep, "/")


class PathSandbox:
    """路径沙箱：判断文件目标是否落在项目根内（空路径视为根；解析失败判拒绝）。"""

    def __init__(self, project_root: str) -> None:
        # project_root 应已由 resolve_root 解析为绝对路径。
        self.project_root = project_root

    def check(self, path: str) -> tuple[bool, str]:
        """判断 ``path`` 是否落在项目根内；返回 ``(在沙箱内?, 越界理由)``。"""
        if not path:
            abs_path = self.project_root
        else:
            pp = Path(path)
            abs_path = (
                str(Path(self.project_root) / pp) if not pp.is_absolute() else str(pp)
            )
        try:
            resolved = _eval_symlinks_or_ancestor(abs_path)
        except (OSError, RuntimeError):
            return False, f"路径 {path} 超出沙箱范围"
        root = self.project_root
        if resolved == root or resolved.startswith(root + os.sep):
            return True, ""
        return False, f"路径在项目目录之外：{path}"
