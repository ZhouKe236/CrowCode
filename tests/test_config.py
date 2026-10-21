"""config 模块单测。"""

from pathlib import Path

import pytest

from crowcode.config import (
    DEFAULT_ANTHROPIC_CONTEXT_WINDOW,
    DEFAULT_OPENAI_CONTEXT_WINDOW,
    ConfigError,
    ProviderConfig,
    effective_context_window,
    load,
    load_with_fallback,
)


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _write_at(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    """隔离真实用户目录，并返回临时 HOME。"""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    return h


def test_valid_single_provider(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: claude
    protocol: anthropic
    api_key: sk-test
    model: claude-sonnet
    thinking: true
""",
    )
    cfg = load(path)
    assert len(cfg.providers) == 1
    p = cfg.providers[0]
    assert p.name == "claude"
    assert p.protocol == "anthropic"
    assert p.api_key == "sk-test"
    assert p.model == "claude-sonnet"
    assert p.thinking is True
    assert p.base_url is None


def test_valid_multiple_providers(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: a, protocol: anthropic, api_key: k1, model: m1}
  - {name: b, protocol: openai, api_key: k2, model: m2, base_url: "https://x"}
""",
    )
    cfg = load(path)
    assert len(cfg.providers) == 2
    assert cfg.providers[1].base_url == "https://x"
    assert cfg.providers[1].thinking is False


def test_missing_field(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: a
    protocol: anthropic
    model: m
""",
    )
    with pytest.raises(ConfigError, match="api_key"):
        load(path)


def test_invalid_protocol(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: a
    protocol: foo
    api_key: k
    model: m
""",
    )
    with pytest.raises(ConfigError, match="protocol"):
        load(path)


def test_name_optional(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - protocol: anthropic
    api_key: k
    model: m
""",
    )
    cfg = load(path)
    assert cfg.providers[0].name == ""


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load(str(tmp_path / "nope.yaml"))


def test_empty_providers(tmp_path):
    path = _write(tmp_path, "providers: []\n")
    with pytest.raises(ConfigError, match="不能为空"):
        load(path)


def test_invalid_yaml(tmp_path):
    path = _write(tmp_path, "providers: [unclosed\n")
    with pytest.raises(ConfigError, match="解析失败"):
        load(path)


# ── 项目级优先、缺失时回退用户级 ────────────────────────────────


def test_provider_falls_back_to_user_config(home, tmp_path):
    """项目级缺失时读取用户级 provider 配置。"""
    user = home / ".crowcode" / "config.yaml"
    _write_at(
        user,
        """
providers:
  - {name: global, protocol: openai, api_key: gk, model: global-model}
""",
    )
    project = tmp_path / "project" / ".crowcode" / "config.yaml"
    cfg = load_with_fallback(str(project))
    assert len(cfg.providers) == 1
    assert cfg.providers[0].model == "global-model"


def test_project_provider_wins_over_user_config(home, tmp_path):
    """两层都存在时项目级胜出。"""
    _write_at(
        home / ".crowcode" / "config.yaml",
        """
providers:
  - {name: global, protocol: openai, api_key: gk, model: global-model}
""",
    )
    project = _write_at(
        tmp_path / "project" / ".crowcode" / "config.yaml",
        """
providers:
  - {name: local, protocol: openai, api_key: lk, model: local-model}
""",
    )
    cfg = load_with_fallback(str(project))
    assert len(cfg.providers) == 1
    assert cfg.providers[0].model == "local-model"


def test_provider_both_missing_raises(home, tmp_path):
    """两层都缺失时报错，并列出两条路径。"""
    project = tmp_path / "project" / ".crowcode" / "config.yaml"
    with pytest.raises(ConfigError) as excinfo:
        load_with_fallback(str(project))
    message = str(excinfo.value)
    assert str(project) in message
    assert str(home / ".crowcode" / "config.yaml") in message


def test_invalid_project_does_not_fall_back(home, tmp_path):
    """项目级存在但非法时直接报错，不回退用户级。"""
    _write_at(
        home / ".crowcode" / "config.yaml",
        """
providers:
  - {name: global, protocol: openai, api_key: gk, model: global-model}
""",
    )
    project = _write_at(
        tmp_path / "project" / ".crowcode" / "config.yaml",
        "providers: [unclosed\n",
    )
    with pytest.raises(ConfigError, match="解析失败"):
        load_with_fallback(str(project))


def test_empty_project_providers_does_not_fall_back(home, tmp_path):
    """项目级存在但 providers 为空时报错，不回退用户级。"""
    _write_at(
        home / ".crowcode" / "config.yaml",
        """
providers:
  - {name: global, protocol: openai, api_key: gk, model: global-model}
""",
    )
    project = _write_at(
        tmp_path / "project" / ".crowcode" / "config.yaml",
        "providers: []\n",
    )
    with pytest.raises(ConfigError, match="不能为空"):
        load_with_fallback(str(project))


def test_context_window_decoded(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {protocol: anthropic, api_key: k, model: m, context_window: 80000}
""",
    )
    cfg = load(path)
    assert cfg.providers[0].context_window == 80000


def test_context_window_invalid(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {protocol: anthropic, api_key: k, model: m, context_window: -1}
""",
    )
    with pytest.raises(ConfigError, match="context_window"):
        load(path)


def test_effective_context_window_unconfigured():
    p = ProviderConfig(protocol="anthropic", api_key="k", model="m")
    assert effective_context_window(p) == DEFAULT_ANTHROPIC_CONTEXT_WINDOW == 200000


def test_effective_context_window_zero():
    p = ProviderConfig(protocol="openai", api_key="k", model="m", context_window=0)
    assert effective_context_window(p) == DEFAULT_OPENAI_CONTEXT_WINDOW == 128000


def test_effective_context_window_positive():
    p = ProviderConfig(
        protocol="anthropic", api_key="k", model="m", context_window=80000
    )
    assert effective_context_window(p) == 80000


def test_effective_context_window_unknown_protocol():
    p = ProviderConfig(
        protocol="other",  # type: ignore[arg-type]
        api_key="k",
        model="m",
    )
    assert effective_context_window(p) == 200000


def test_config_example_parses():
    import yaml

    raw = yaml.safe_load(
        Path(".crowcode/config.yaml.example").read_text(encoding="utf-8")
    )
    assert isinstance(raw, dict)


def test_user_config_can_contain_mcp_servers(home, tmp_path):
    """全局文件同时含 providers 与 mcp_servers 时，provider 加载器忽略后者。"""
    _write_at(
        home / ".crowcode" / "config.yaml",
        """
providers:
  - {name: global, protocol: openai, api_key: gk, model: global-model}
mcp_servers:
  context7:
    type: stdio
    command: npx
    args: ["-y", "@upstash/context7-mcp"]
""",
    )
    cfg = load_with_fallback(str(tmp_path / "project" / ".crowcode" / "config.yaml"))
    assert len(cfg.providers) == 1
    assert cfg.providers[0].model == "global-model"
