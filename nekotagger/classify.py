"""分类主流程：把一条路径变成契约结构。

判定顺序（前一条命中即返回）：

1. ``override`` —— 人工指定，最高优先级。
2. ``seed`` —— 种子显示名与 exe 的版本信息/文件名**精确或词边界**匹配。
3. ``rule`` —— 系统路径 / 安装器 → 工具；``categories.json`` 的 patterns。
4. ``steam`` —— 落在 Steam 安装目录下 → 游戏，带 appid。
5. ``unknown`` —— 兜底未分类（缓存与 AI 在后续 ticket 接入）。

规则层是纯函数；只有 seed 这一步需要读文件的版本信息，且通过 ``info_reader``
注入，测试可以完全不碰真实文件。
"""

from __future__ import annotations

import re
import time
from typing import Callable, Mapping

from .categories import CategorySet, load_categories
from .normalize import exe_basename, normalize_exe_path
from .seeds import SeedIndex
from .steam import SteamIndex
from .wininfo import VersionInfo, read_version_info

#: 契约里必须恰好出现的键，顺序即 CLI --json 的输出顺序。
CONTRACT_KEYS = ("category", "confidence", "source", "matched_by", "classified_at")

#: 系统目录一律归「工具」——NekoTrak 没有单独的「系统」类别。
#: 用正则而不是固定 c: 前缀：本机就有装在 H: 的系统盘。
SYSTEM_PATH_RE = re.compile(r"^[a-z]:\\windows\\")

#: 安装器 / 卸载器 / 更新器：出现在哪都是「工具」。
INSTALLER_HINTS = ("setup.exe", "install.exe", "installer.exe", "unins", "uninstall", "update.exe")

InfoReader = Callable[[str], "VersionInfo | None"]


def _match_system_path(normalized_path: str) -> str | None:
    """系统路径或安装器，返回可读的命中原因。"""

    if SYSTEM_PATH_RE.match(normalized_path):
        return f"path: {normalized_path[:3]}windows\\*"

    basename = exe_basename(normalized_path)
    for hint in INSTALLER_HINTS:
        if hint in basename:
            return f"name: contains {hint!r}"
    return None


def _resolve_category(categories: CategorySet, category_id: str) -> str:
    """命中一个可能已被用户从清单里删掉的 id 时，退到兜底类别。"""

    if category_id in categories:
        return category_id
    return categories.fallback.id


def _display_candidates(
    normalized_path: str, info_reader: InfoReader | None
) -> tuple[str, ...]:
    """候选显示名：版本信息优先，其次文件名，最后父目录名。"""

    candidates: list[str] = []
    if info_reader is not None:
        try:
            info = info_reader(normalized_path)
        except Exception:  # noqa: BLE001 - 读版本信息失败不该影响分类
            info = None
        if info is not None:
            candidates.extend(info.candidates())

    basename = exe_basename(normalized_path)
    stem = basename[: -len(".exe")] if basename.endswith(".exe") else basename
    if stem:
        candidates.append(stem)

    parts = [part for part in normalized_path.split("\\") if part]
    if len(parts) >= 2 and parts[-2]:
        candidates.append(parts[-2])

    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        lowered = candidate.strip().lower()
        if lowered and lowered not in seen:
            seen.add(lowered)
            ordered.append(candidate)
    return tuple(ordered)


def classify(
    path: object,
    *,
    categories: CategorySet | None = None,
    overrides: Mapping[str, str] | None = None,
    seeds: SeedIndex | None = None,
    steam: SteamIndex | None = None,
    info_reader: InfoReader | None = read_version_info,
    now: float | None = None,
) -> dict[str, object]:
    """把一个 exe 路径分到某个类别，返回契约结构。

    Args:
        path: 原始路径，可以带引号、正斜杠、大小写混乱。
        categories: 已加载的类别清单；缺省从仓库根 ``categories.json`` 读。
        overrides: 规范化路径 → 类别 id。
        seeds: 种子索引；None 表示跳过种子匹配（也不读版本信息）。
        steam: Steam 索引；None 表示跳过。
        info_reader: 版本信息读取器，注入以便测试；None 表示不读文件。
        now: 注入时间戳（测试用）。

    Raises:
        ValueError: 路径为空或不是字符串。
        CategoryConfigError: 类别清单缺失或非法。
    """

    category_set = categories if categories is not None else load_categories()
    normalized = normalize_exe_path(path)
    classified_at = int(now if now is not None else time.time())

    if overrides:
        override = overrides.get(normalized)
        if override is not None:
            return {
                "category": _resolve_category(category_set, override),
                "confidence": "certain",
                "source": "override",
                "matched_by": "overrides.json",
                "classified_at": classified_at,
            }

    if seeds is not None:
        match = seeds.best(_display_candidates(normalized, info_reader))
        if match is not None:
            return {
                "category": _resolve_category(category_set, match.seed.category_id),
                "confidence": "certain",
                "source": "seed",
                "matched_by": f"seed[{match.kind}]: {match.seed.name!r}",
                "classified_at": classified_at,
            }

    system_reason = _match_system_path(normalized)
    if system_reason is not None:
        return {
            "category": _resolve_category(category_set, "tool"),
            "confidence": "certain",
            "source": "rule",
            "matched_by": system_reason,
            "classified_at": classified_at,
        }

    matched = category_set.match_name(normalized)
    if matched is not None:
        category, pattern, where = matched
        return {
            "category": category.id,
            "confidence": "likely",
            "source": "rule",
            "matched_by": f"{where}: contains {pattern!r}",
            "classified_at": classified_at,
        }

    if steam is not None:
        app = steam.match(normalized)
        if app is not None:
            return {
                "category": _resolve_category(category_set, "game"),
                "confidence": "likely",
                "source": "steam",
                "matched_by": f"steam: {app.name!r} (appid {app.appid})",
                "classified_at": classified_at,
            }

    return {
        "category": category_set.fallback.id,
        "confidence": "guess",
        "source": "unknown",
        "matched_by": "no rule matched",
        "classified_at": classified_at,
    }
