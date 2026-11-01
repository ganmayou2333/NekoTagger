"""NekoTagger — 把应用归到 NekoTrak 那套类别里。

对外只有两样东西：:func:`classify` 和它返回的契约结构。
"""

from __future__ import annotations

from .categories import CategoryConfigError, CategorySet, load_categories
from .classify import CONTRACT_KEYS, classify
from .normalize import normalize_exe_path

__all__ = [
    "CONTRACT_KEYS",
    "CategoryConfigError",
    "CategorySet",
    "classify",
    "load_categories",
    "normalize_exe_path",
]
