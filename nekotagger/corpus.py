"""语料：这台机器上「跑过的应用」清单。

来源是 NekoTrak 安装目录下的 ``caches/app-icons/*.json``（每个应用一份，含
``normalized_exe_path``），可以合并一份外部路径清单。

实测（2026-11-01）：181 条路径里只有 114 条文件仍存在，2 条不是 .exe，另有
13 个命中其实是安装包/临时文件。所以加载必须**过滤并计数**，而不是假装全都能用。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .normalize import exe_basename, normalize_exe_path

#: 指向 NekoTrak 安装目录（含 caches/ 的那层）的环境变量。
NEKOTRAK_ROOT_ENV = "NEKOTAGGER_NEKOTRAK_ROOT"

#: 卸载/安装/更新程序，分类器关心的是「装了什么应用」，不是安装包。
INSTALLER_HINTS = ("setup", "install", "unins", "update")

#: 临时与下载目录里的可执行文件同理。只检查 exe 自己的两级目录名：
#: 用「路径里出现过 temp」来判定会误伤整个用户目录都叫 Temp 的场景。
TEMP_DIR_NAMES = frozenset({"temp", "tmp", "downloads", "download"})


@dataclass
class Corpus:
    """加载结果：可分类的路径 + 各类跳过原因。"""

    paths: tuple[str, ...] = ()
    skipped_missing: tuple[str, ...] = ()
    skipped_non_exe: tuple[str, ...] = ()
    skipped_installer: tuple[str, ...] = ()
    duplicates: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "paths": len(self.paths),
            "skipped_missing": len(self.skipped_missing),
            "skipped_non_exe": len(self.skipped_non_exe),
            "skipped_installer": len(self.skipped_installer),
            "duplicates": self.duplicates,
        }


def find_nekotrak_root(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """定位 NekoTrak 安装目录：显式参数 > 环境变量 > 放弃（返回 None）。"""

    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_dir() else None
    from_env = os.environ.get(NEKOTRAK_ROOT_ENV)
    if from_env:
        candidate = Path(from_env)
        return candidate if candidate.is_dir() else None
    return None


def _read_app_icon_paths(root: Path, warnings: list[str]) -> list[str]:
    cache_dir = root / "caches" / "app-icons"
    if not cache_dir.is_dir():
        warnings.append(f"没有找到 NekoTrak 的 app-icons 缓存目录：{cache_dir}")
        return []

    paths: list[str] = []
    for manifest in sorted(cache_dir.glob("*.json")):
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"跳过损坏的缓存文件 {manifest.name}：{exc}")
            continue
        if not isinstance(document, dict):
            continue
        # 目前是 1；将来格式变了就当没读到，别硬解释。
        version = document.get("cache_format_version")
        if version not in (None, 1):
            warnings.append(
                f"跳过未知 cache_format_version={version!r} 的缓存文件 {manifest.name}"
            )
            continue
        path = document.get("normalized_exe_path")
        if isinstance(path, str) and path.strip():
            paths.append(path)
    return paths


def _read_paths_file(path: str | os.PathLike[str], warnings: list[str]) -> list[str]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        warnings.append(f"读取路径清单失败：{path}：{exc}")
        return []
    paths = []
    for line in text.splitlines():
        stripped = line.strip().strip('"')
        if stripped and not stripped.startswith("#"):
            paths.append(stripped)
    return paths


def _is_installer_or_temp(normalized: str) -> bool:
    basename = exe_basename(normalized)
    if any(hint in basename for hint in INSTALLER_HINTS):
        return True
    segments = [part for part in normalized.split("\\") if part][:-1]
    return any(segment in TEMP_DIR_NAMES for segment in segments[-2:])


def load_corpus(
    nekotrak_root: str | os.PathLike[str] | None = None,
    paths_from: str | os.PathLike[str] | None = None,
) -> Corpus:
    """加载并过滤语料。任何 IO 问题都降级为警告 + 空/部分语料。"""

    warnings: list[str] = []
    sources: dict[str, int] = {}

    raw: list[tuple[str, str]] = []
    root = find_nekotrak_root(nekotrak_root)
    if root is not None:
        nekotrak_paths = _read_app_icon_paths(root, warnings)
        sources["nekotrak"] = len(nekotrak_paths)
        raw.extend((path, "nekotrak") for path in nekotrak_paths)
    elif nekotrak_root is not None:
        warnings.append(f"指定的 NekoTrak 目录不存在：{nekotrak_root}")

    if paths_from is not None:
        extra = _read_paths_file(paths_from, warnings)
        sources["paths_from"] = len(extra)
        raw.extend((path, "paths_from") for path in extra)

    paths: list[str] = []
    skipped_missing: list[str] = []
    skipped_non_exe: list[str] = []
    skipped_installer: list[str] = []
    seen: set[str] = set()
    duplicates = 0

    for original, _source in raw:
        try:
            normalized = normalize_exe_path(original)
        except ValueError:
            skipped_non_exe.append(str(original))
            continue

        if not normalized.endswith(".exe"):
            skipped_non_exe.append(normalized)
            continue
        if not os.path.isfile(normalized):
            skipped_missing.append(normalized)
            continue
        if _is_installer_or_temp(normalized):
            skipped_installer.append(normalized)
            continue
        if normalized in seen:
            duplicates += 1
            continue
        seen.add(normalized)
        paths.append(normalized)

    return Corpus(
        paths=tuple(paths),
        skipped_missing=tuple(skipped_missing),
        skipped_non_exe=tuple(skipped_non_exe),
        skipped_installer=tuple(skipped_installer),
        duplicates=duplicates,
        sources=sources,
        warnings=warnings,
    )
