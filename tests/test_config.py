"""config 模块单测。"""

import pytest

from crowcode.config import ConfigError, load


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


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
