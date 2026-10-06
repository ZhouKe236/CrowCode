"""provider 选择：构造 OptionList 选项。"""

from __future__ import annotations

from crowcode.config import ProviderConfig


def build_provider_options(providers: list[ProviderConfig]) -> list[str]:
    """把 providers 列表映射为 OptionList 的选项文本（无 name 时仅显示模型）。"""
    return [f"{p.name} ({p.model})" if p.name else p.model for p in providers]
