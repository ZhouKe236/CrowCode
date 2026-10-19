"""沙箱单测：项目内外判定、符号链接逃逸、新建文件祖先回退（AC2/N2）。"""

import pytest

from crowcode.permission.sandbox import PathSandbox, resolve_root, to_rel_path


def _sb(tmp_path):
    return PathSandbox(resolve_root(str(tmp_path)))


def test_resolve_root(tmp_path):
    assert resolve_root(str(tmp_path)) == str(tmp_path.resolve())


def test_sandbox_inside(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    sb = _sb(tmp_path)
    assert sb.check(str(tmp_path / "a.txt"))[0] is True
    assert sb.check("a.txt")[0] is True  # 相对路径


def test_sandbox_outside_absolute(tmp_path):
    sb = _sb(tmp_path)
    assert sb.check("/etc/passwd")[0] is False


def test_sandbox_outside_relative(tmp_path):
    sb = _sb(tmp_path)
    assert sb.check("../outside")[0] is False


def test_sandbox_new_file_ancestor(tmp_path):
    """目标含尚未创建的多级中间目录：回退到最近已存在祖先后仍判通过。"""
    sb = _sb(tmp_path)
    target = tmp_path / "a" / "b" / "c.txt"
    assert sb.check(str(target))[0] is True


def test_sandbox_symlink_escape(tmp_path):
    """项目内软链接指向项目外目录：先解析再比对，判拒绝。"""
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持符号链接")
    sb = _sb(tmp_path)
    assert sb.check(str(link / "secret.txt"))[0] is False


def test_to_rel_path(tmp_path):
    root = str(tmp_path.resolve())
    assert to_rel_path(root, str(tmp_path / "src" / "a.py")) == "src/a.py"
    assert to_rel_path(root, "src/b.py") == "src/b.py"
