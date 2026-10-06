"""配置层：读取并校验 ``.crowcode/config.yaml``，产出 providers 列表。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

Protocol = Literal["anthropic", "openai"]


class ConfigError(Exception):
    """配置错误：携带可读信息，用于启动期终止。"""


@dataclass
class ProviderConfig:
    protocol: Protocol
    api_key: str
    model: str
    name: str = ""  # 可选：可读名称，仅用于选择列表等展示，缺省为空
    base_url: str | None = None  # None 则用 SDK 默认端点
    thinking: bool = False  # 仅 anthropic 生效


@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)


def load(path: str) -> Config:
    """加载并校验配置文件，失败抛 :class:`ConfigError`。"""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件解析失败: {e}") from e
    return _from_dict(raw, path)


def _from_dict(raw: object, path: str) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件 {path} 顶层应为 mapping（含 providers 列表）")
    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, list) or not providers_raw:
        raise ConfigError("providers 列表不能为空")
    providers = [_parse_provider(i, item) for i, item in enumerate(providers_raw)]
    return Config(providers=providers)


def _parse_provider(i: int, item: object) -> ProviderConfig:
    prefix = f"providers[{i}]"
    if not isinstance(item, dict):
        raise ConfigError(f"{prefix} 应为 mapping")

    for key in ("protocol", "api_key", "model"):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{prefix}.{key} 不能为空")

    protocol = item["protocol"]
    if protocol not in ("anthropic", "openai"):
        raise ConfigError(
            f"{prefix}.protocol 非法: {protocol!r}（应为 anthropic 或 openai）"
        )

    base_url = item.get("base_url")
    if base_url is not None and (not isinstance(base_url, str) or not base_url.strip()):
        raise ConfigError(f"{prefix}.base_url 不能为空字符串")

    thinking = item.get("thinking", False)
    if not isinstance(thinking, bool):
        raise ConfigError(f"{prefix}.thinking 应为布尔值")

    name = item.get("name")
    name = name.strip() if isinstance(name, str) else ""

    return ProviderConfig(
        protocol=protocol,  # type: ignore[arg-type]  # 已在上方校验
        api_key=item["api_key"].strip(),
        model=item["model"].strip(),
        name=name,
        base_url=base_url.strip()
        if isinstance(base_url, str) and base_url.strip()
        else None,
        thinking=thinking,
    )
