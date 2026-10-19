"""MCP 配置层：两层 YAML 加载、按 server 名合并、``${VAR}`` 展开与字段校验。

配置来源只有两层：

- 用户级 ``~/.crowcode/config.yaml``
- 项目级 ``<root>/.crowcode.yaml``

同名 server 由项目级完整覆盖用户级。任何文件缺失都视为空层；文件格式非法时
跳过该层并告警，绝不让启动失败。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

# ``${VAR}``：只认常见的环境变量名字符。
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class ServerConfig:
    """单个 MCP server 的完整定义（已展开 ``${VAR}``、已校验）。"""

    type: Literal["stdio", "http"]
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    """``mcp_servers`` 在内存中的归一化形式（已合并、已校验）。"""

    servers: dict[str, ServerConfig] = field(default_factory=dict)


@dataclass
class _RawServer:
    """YAML 中的原始 server 定义；字段尚未校验，类型均为 ``Any``。"""

    type: Any = None
    command: Any = None
    args: Any = None
    env: Any = None
    url: Any = None
    headers: Any = None


def _warn(msg: str) -> None:
    """统一的 stderr 告警前缀。"""
    print(f"[mcp] warn: {msg}", file=sys.stderr)


def _user_config_path() -> Path:
    """用户级配置路径；``Path.home()`` 失败时返回一个不存在的路径（降级为空层）。"""
    try:
        return Path.home() / ".crowcode" / "config.yaml"
    except (OSError, RuntimeError):
        return Path("__crowcode_no_home__") / "config.yaml"


def _project_config_path(root: str) -> Path:
    """项目级配置路径。"""
    return Path(root) / ".crowcode.yaml"


def _load_file(path: Path) -> dict[str, _RawServer]:
    """读取单个配置文件中的 ``mcp_servers`` 段。

    文件不存在 → ``{}``；读取 / 解析失败 → 告警后返回 ``{}``（调用方降级）。
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as e:
        _warn(f"load {path} failed: {e}")
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        _warn(f"load {path} failed: top-level value should be a mapping")
        return {}
    raw_servers = data.get("mcp_servers")
    if raw_servers is None:
        return {}
    if not isinstance(raw_servers, dict):
        _warn(f"load {path} failed: mcp_servers should be a mapping")
        return {}
    servers: dict[str, _RawServer] = {}
    for name, value in raw_servers.items():
        if not isinstance(name, str):
            _warn(f"skip server {name!r}: server name should be a string")
            continue
        if not isinstance(value, dict):
            _warn(f"skip server {name}: definition should be a mapping")
            continue
        servers[name] = _RawServer(
            type=value.get("type"),
            command=value.get("command"),
            args=value.get("args"),
            env=value.get("env"),
            url=value.get("url"),
            headers=value.get("headers"),
        )
    return servers


def _expand_vars(s: str) -> tuple[str, list[str]]:
    """展开字符串中的 ``${VAR}``，返回 ``(展开结果, 未定义变量名列表)``。"""
    undefined: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            undefined.append(name)
            return ""
        return os.environ[name]

    return _VAR_RE.sub(replace, s), undefined


def _apply_expansion(name: str, srv: _RawServer) -> None:
    """只对 ``env`` / ``headers`` 的值展开；同 server 同变量只告警一次。"""
    warned: set[str] = set()
    for attr in ("env", "headers"):
        mapping = getattr(srv, attr)
        if not isinstance(mapping, dict):
            continue
        expanded: dict[Any, Any] = {}
        for key, value in mapping.items():
            if not isinstance(value, str):
                expanded[key] = value
                continue
            new_value, undefined = _expand_vars(value)
            expanded[key] = new_value
            for var in undefined:
                if var not in warned:
                    warned.add(var)
                    _warn(f"undefined env var ${{{var}}} referenced by server {name}")
        setattr(srv, attr, expanded)


def _merge_servers(
    user: dict[str, _RawServer], project: dict[str, _RawServer]
) -> dict[str, _RawServer]:
    """按 server 名合并：项目级同名 server 完整覆盖用户级。"""
    merged = dict(user)
    merged.update(project)
    return merged


def _string_map(value: Any) -> dict[str, str] | None:
    """校验并复制 ``dict[str, str]``；非法返回 ``None``。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        return None
    out: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            return None
        out[key] = item
    return out


def _string_list(value: Any) -> list[str] | None:
    """校验并复制 ``list[str]``；非法返回 ``None``。"""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        return None
    return list(value)


def _validate_server(name: str, srv: _RawServer) -> ServerConfig | None:
    """字段校验：非法 server 返回 ``None`` 并告警，不影响其它 server。"""
    if srv.type not in ("stdio", "http"):
        _warn(f"skip server {name}: type should be 'stdio' or 'http'")
        return None
    if srv.type == "stdio":
        if not isinstance(srv.command, str) or not srv.command:
            _warn(f"skip server {name}: stdio server requires a non-empty command")
            return None
        args = _string_list(srv.args)
        if args is None:
            _warn(f"skip server {name}: args should be a list of strings")
            return None
        env = _string_map(srv.env)
        if env is None:
            _warn(f"skip server {name}: env should be a mapping of strings")
            return None
        return ServerConfig(type="stdio", command=srv.command, args=args, env=env)

    if not isinstance(srv.url, str) or not srv.url:
        _warn(f"skip server {name}: http server requires a non-empty url")
        return None
    headers = _string_map(srv.headers)
    if headers is None:
        _warn(f"skip server {name}: headers should be a mapping of strings")
        return None
    return ServerConfig(type="http", url=srv.url, headers=headers)


def load_config(root: str) -> Config:
    """加载两层配置、合并、展开并校验，永不抛出。

    - 任一文件缺失 → 该层为空；
    - 文件格式非法 → 跳过该层并 stderr 告警；
    - 字段非法的 server → 单独跳过并告警。
    """
    user_raw = _load_file(_user_config_path())
    project_raw = _load_file(_project_config_path(root))

    for name, srv in user_raw.items():
        _apply_expansion(name, srv)
    for name, srv in project_raw.items():
        _apply_expansion(name, srv)

    merged = _merge_servers(user_raw, project_raw)
    servers: dict[str, ServerConfig] = {}
    for name, raw in merged.items():
        validated = _validate_server(name, raw)
        if validated is not None:
            servers[name] = validated
    return Config(servers=servers)
