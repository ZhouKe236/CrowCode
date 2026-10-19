"""持久化单测：永久放行写本地层、重载生效、幂等、相对路径（AC10/F8）。"""

import json

from crowcode.llm import ToolCall
from crowcode.permission import PermissionMode, new_checker


def test_persist_local_allow_bash(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    call = ToolCall(id="1", name="bash", input=json.dumps({"command": "git push"}))
    checker.persist_local_allow(call)

    p = tmp_path / ".crowcode" / "settings.local.yaml"
    assert p.exists()
    assert "Bash(git push)" in p.read_text(encoding="utf-8")

    # 重载后同调用放行
    checker2, _ = new_checker(str(tmp_path))
    d = checker2.check(PermissionMode.DEFAULT, call, False)
    assert d.effect == "allow"


def test_persist_local_allow_file_relpath(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    call = ToolCall(
        id="1",
        name="write_file",
        input=json.dumps({"path": str(tmp_path / "src" / "a.py"), "content": "x"}),
    )
    checker.persist_local_allow(call)
    p = tmp_path / ".crowcode" / "settings.local.yaml"
    assert "Write(src/a.py)" in p.read_text(encoding="utf-8")


def test_persist_local_allow_idempotent(tmp_path):
    checker, _ = new_checker(str(tmp_path))
    call = ToolCall(id="1", name="bash", input=json.dumps({"command": "git status"}))
    checker.persist_local_allow(call)
    checker.persist_local_allow(call)  # 幂等：不抛、不重复
    p = tmp_path / ".crowcode" / "settings.local.yaml"
    assert p.read_text(encoding="utf-8").count("Bash(git status)") == 1
