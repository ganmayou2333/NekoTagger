"""NekoTagger — 把应用归到 NekoTrak 那套类别里。

对外只有两样东西：:func:`classify` 和它返回的契约结构；其余是数据层，
供 CLI 与测试使用。
"""

from __future__ import annotations

from .categories import CategoryConfigError, CategorySet, load_categories
from .classify import CONTRACT_KEYS, classify
from .corpus import Corpus, load_corpus
from .normalize import normalize_exe_path
from .overrides import OverrideDataError, load_overrides
from .seeds import SeedDataError, SeedIndex, load_seeds
from .steam import SteamDataError, SteamIndex, load_steam_index
from .wininfo import VersionInfo, read_version_info

__all__ = [
    "CONTRACT_KEYS",
    "CategoryConfigError",
    "CategorySet",
    "Corpus",
    "OverrideDataError",
    "SeedDataError",
    "SeedIndex",
    "SteamDataError",
    "SteamIndex",
    "VersionInfo",
    "classify",
    "load_categories",
    "load_corpus",
    "load_overrides",
    "load_seeds",
    "load_steam_index",
    "normalize_exe_path",
    "read_version_info",
]
