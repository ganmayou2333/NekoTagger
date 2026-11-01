"""Steam 库索引：把「装在 Steam 库里的 exe」判成游戏，并带上 appid。

实测这层对现有语料新增 0 个 exe（6 个命中都已被精确层覆盖），所以它是补充：
真正的主力是种子与规则。保留它是因为 appid 对后续细分有用，且读取成本几乎为零。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .normalize import normalize_exe_path


class SteamDataError(ValueError):
    """Steam 索引缺失或非法。"""


@dataclass(frozen=True)
class SteamApp:
    appid: int
    name: str
    install_dir: str  # 规范化后、不带尾随反斜杠


class SteamIndex:
    def __init__(self, apps: list[SteamApp]) -> None:
        # 最长前缀优先：嵌套安装目录时取更具体的那个。
        self._apps = tuple(sorted(apps, key=lambda app: len(app.install_dir), reverse=True))

    def __len__(self) -> int:
        return len(self._apps)

    def match(self, normalized_path: str) -> SteamApp | None:
        for app in self._apps:
            if normalized_path == app.install_dir:
                return app
            if normalized_path.startswith(app.install_dir + "\\"):
                return app
        return None


def load_steam_index(path: str | os.PathLike[str]) -> SteamIndex:
    target = Path(path)
    try:
        document = json.loads(target.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SteamDataError(f"找不到 Steam 索引：{target}") from exc
    except json.JSONDecodeError as exc:
        raise SteamDataError(f"Steam 索引不是合法 JSON：{target}：{exc}") from exc
    except OSError as exc:
        raise SteamDataError(f"Steam 索引读取失败：{target}：{exc}") from exc

    raw_apps = document.get("apps") if isinstance(document, dict) else None
    if not isinstance(raw_apps, list):
        raise SteamDataError(f"Steam 索引缺少 apps 数组：{target}")

    apps: list[SteamApp] = []
    for item in raw_apps:
        if not isinstance(item, dict):
            continue
        appid = item.get("appid")
        name = item.get("name")
        install_dir = item.get("installDir")
        if not isinstance(appid, int) or not isinstance(name, str) or not isinstance(install_dir, str):
            continue
        try:
            normalized_dir = normalize_exe_path(install_dir)
        except ValueError:
            continue
        apps.append(SteamApp(appid=appid, name=name, install_dir=normalized_dir))
    return SteamIndex(apps)
