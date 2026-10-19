"""MCP 配置层单测：两层合并、变量展开、字段校验与降级。"""

from __future__ import annotations

from pathlib import Path

import pytest

from crowcode.mcp import config as mcp_config
from crowcode.mcp import load_config


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把用户级配置指向临时目录，返回 (用户级路径, 项目根, 项目级路径)。"""
    user = tmp_path / "home" / ".crowcode" / "config.yaml"
    monkeypatch.setattr(mcp_config, "_user_config_path", lambda: user)
    root = tmp_path / "project"
    root.mkdir()
    return user, root, root / ".crowcode.yaml"


def test_both_files_missing(layout):
    """AC1：两文件缺失 → 零个 server，不抛异常。"""
    _user, root, _project = layout
    cfg = load_config(str(root))
    assert cfg.servers == {}


def test_user_only(layout):
    user, root, _project = layout
    _write(
        user,
        """
mcp_servers:
  demo:
    type: stdio
    command: python
""",
    )
    cfg = load_config(str(root))
    assert set(cfg.servers) == {"demo"}
    assert cfg.servers["demo"].command == "python"


def test_project_overrides_user_whole_server(layout):
    """AC1：同名 server 项目级完整覆盖用户级（不做字段级合并）。"""
    user, root, project = layout
    _write(
        user,
        """
mcp_servers:
  demo:
    type: stdio
    command: old
    args: ["--old"]
  keep:
    type: stdio
    command: keep-user
""",
    )
    _write(
        project,
        """
mcp_servers:
  demo:
    type: stdio
    command: new
  added:
    type: http
    url: "https://example.test/mcp"
""",
    )
    cfg = load_config(str(root))
    assert set(cfg.servers) == {"demo", "keep", "added"}
    # 项目级完整覆盖：args 不再保留用户级的 --old。
    assert cfg.servers["demo"].command == "new"
    assert cfg.servers["demo"].args == []


def test_invalid_user_file_is_skipped(layout, capsys):
    """AC1：格式非法只跳过该层并告警，另一层正常加载。"""
    user, root, project = layout
    _write(user, "mcp_servers: [unclosed")
    _write(
        project,
        """
mcp_servers:
  ok:
    type: stdio
    command: python
""",
    )
    cfg = load_config(str(root))
    assert set(cfg.servers) == {"ok"}
    assert "[mcp] warn: load" in capsys.readouterr().err


def test_non_mapping_mcp_servers_is_skipped(layout, capsys):
    _user, root, project = layout
    _write(project, "mcp_servers: 42\n")
    assert load_config(str(root)).servers == {}
    assert "mcp_servers should be a mapping" in capsys.readouterr().err


def test_expand_vars_in_env_and_headers(layout, monkeypatch):
    """AC3：env / headers 的值展开 ${VAR}。"""
    _user, root, project = layout
    monkeypatch.setenv("TOKEN", "secret")
    _write(
        project,
        """
mcp_servers:
  s:
    type: stdio
    command: python
    env:
      API_KEY: "${TOKEN}"
  h:
    type: http
    url: "https://example.test/mcp"
    headers:
      Authorization: "Bearer ${TOKEN}"
""",
    )
    cfg = load_config(str(root))
    assert cfg.servers["s"].env == {"API_KEY": "secret"}
    assert cfg.servers["h"].headers == {"Authorization": "Bearer secret"}


def test_undefined_var_becomes_empty_and_warns_once(layout, monkeypatch, capsys):
    """AC3：未定义变量 → 空串 + 每 server 每变量只告警一次。"""
    _user, root, project = layout
    monkeypatch.delenv("MISSING", raising=False)
    _write(
        project,
        """
mcp_servers:
  s:
    type: stdio
    command: python
    env:
      A: "${MISSING}"
      B: "${MISSING}"
""",
    )
    cfg = load_config(str(root))
    assert cfg.servers["s"].env == {"A": "", "B": ""}
    err = capsys.readouterr().err
    assert err.count("undefined env var ${MISSING}") == 1


def test_command_and_args_are_not_expanded(layout, monkeypatch):
    """AC3：command / args 不展开，保留字面量。"""
    _user, root, project = layout
    monkeypatch.setenv("CMD", "expanded")
    _write(
        project,
        """
mcp_servers:
  s:
    type: stdio
    command: "${CMD}"
    args: ["${CMD}"]
""",
    )
    cfg = load_config(str(root))
    assert cfg.servers["s"].command == "${CMD}"
    assert cfg.servers["s"].args == ["${CMD}"]


def test_field_validation_skips_only_bad_servers(layout, capsys):
    """AC2：type / 必填字段非法只跳过该 server。"""
    _user, root, project = layout
    _write(
        project,
        """
mcp_servers:
  no-type:
    command: python
  bad-type:
    type: sse
    url: "https://example.test"
  no-command:
    type: stdio
  no-url:
    type: http
  good-stdio:
    type: stdio
    command: python
    args: ["-m", "x"]
    env: {A: "1"}
  good-http:
    type: http
    url: "https://example.test/mcp"
    headers: {Authorization: "Bearer x"}
""",
    )
    cfg = load_config(str(root))
    assert set(cfg.servers) == {"good-stdio", "good-http"}
    err = capsys.readouterr().err
    for name in ("no-type", "bad-type", "no-command", "no-url"):
        assert f"skip server {name}" in err


def test_invalid_optional_field_types(layout, capsys):
    _user, root, project = layout
    _write(
        project,
        """
mcp_servers:
  bad-args:
    type: stdio
    command: python
    args: "oops"
  bad-env:
    type: stdio
    command: python
    env: [1, 2]
  bad-headers:
    type: http
    url: "https://example.test"
    headers: "oops"
  ok:
    type: stdio
    command: python
""",
    )
    cfg = load_config(str(root))
    assert set(cfg.servers) == {"ok"}
    err = capsys.readouterr().err
    assert "args should be a list of strings" in err
    assert "env should be a mapping of strings" in err
    assert "headers should be a mapping of strings" in err


def test_example_config_file_parses(layout, monkeypatch):
    """T6：仓库内配置示例可被完整解析。"""
    _user, root, _project = layout
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("EXAMPLE_TOKEN", "y")
    example = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "mcp"
        / ("mcp-servers.example.yaml")
    )
    text = example.read_text(encoding="utf-8")
    _write(root / ".crowcode.yaml", text)
    cfg = load_config(str(root))
    assert set(cfg.servers) == {"github", "local-sqlite", "example-http"}
    assert cfg.servers["github"].env["GITHUB_TOKEN"] == "x"
    assert cfg.servers["example-http"].headers["Authorization"] == "Bearer y"
