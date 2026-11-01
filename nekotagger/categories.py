"""类别清单的加载与校验。

类别是用户可改的（NekoTrak 里本来就能自己建类别），所以清单放在仓库根的
``categories.json``，代码不硬编码任何类别名。加载时严格校验：宁可启动就报错，
也不要带着半个清单去分类，然后把错误结果写进缓存。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_CATEGORIES_PATH = Path(__file__).resolve().parent.parent / "categories.json"

REQUIRED_FALLBACK_ID = "uncategorized"

#: patterns 短于这个长度只允许匹配文件名。像 ``qq`` / ``7z`` / ``gh`` 这种短词
#: 一旦允许匹配完整路径，会在目录名上大量误伤。
MIN_PATH_PATTERN_LENGTH = 4

#: 这些词在任何路径里都太常见，不允许靠"出现在路径里"命中类别。
GENERIC_PATH_TOKENS = frozenset(
    {"microsoft", "windows", "x64", "app", "apps", "setup", "install", "application",
     "desktop", "download", "downloads", "program", "programs", "temp", "users"}
)


class CategoryConfigError(ValueError):
    """类别清单缺失或非法。"""


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    emoji: str | None
    patterns: tuple[str, ...]
    notes: str | None = None


class CategorySet:
    """一份已校验的类别清单。"""

    def __init__(self, version: int, categories: Iterable[Category], source: Path | None = None):
        self.version = version
        self.source = source
        self._categories: tuple[Category, ...] = tuple(categories)
        self._by_id = {category.id: category for category in self._categories}
        if REQUIRED_FALLBACK_ID not in self._by_id:
            raise CategoryConfigError(f"类别清单必须包含兜底类别 {REQUIRED_FALLBACK_ID!r}")

    def __iter__(self):
        return iter(self._categories)

    def __len__(self) -> int:
        return len(self._categories)

    def __contains__(self, category_id: object) -> bool:
        return category_id in self._by_id

    def ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    def get(self, category_id: str) -> Category | None:
        return self._by_id.get(category_id)

    @property
    def fallback(self) -> Category:
        return self._by_id[REQUIRED_FALLBACK_ID]

    def match_name(self, normalized_path: str) -> tuple[Category, str, str] | None:
        r"""按进程名或路径片段匹配类别，返回 (类别, 命中的片段, 命中位置)。

        纯函数：只看传入的清单与路径，不碰文件、不联网。清单顺序即优先级，
        兜底类别不参与匹配。

        ``patterns`` 同时匹配文件名和完整路径：像
        ``e:\huorong\sysdiag\bin\hipsmain.exe`` 这种「目录名才是线索」的
        情况，只看文件名会漏掉。
        """

        lowered = normalized_path.lower()
        basename = lowered.rsplit("\\", 1)[-1]
        for category in self._categories:
            if category.id == REQUIRED_FALLBACK_ID:
                continue
            for pattern in category.patterns:
                needle = pattern.lower()
                if not needle:
                    continue
                if needle in basename:
                    return category, pattern, "name"
                # 短词与通用词只认文件名，避免 ``e:\qq\...``、``...\microsoft\...``
                # 这类目录名造成的批量误判。
                if len(needle) < MIN_PATH_PATTERN_LENGTH or needle in GENERIC_PATH_TOKENS:
                    continue
                if needle in lowered:
                    return category, pattern, "path"
        return None


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise CategoryConfigError(message)


def _parse_category(raw: Any, index: int) -> Category:
    where = f"categories[{index}]"
    _expect(isinstance(raw, dict), f"{where} 必须是对象")

    category_id = raw.get("id")
    _expect(isinstance(category_id, str) and category_id.strip(), f"{where}.id 必须是非空字符串")
    label = raw.get("label")
    _expect(isinstance(label, str) and label.strip(), f"{where}.label 必须是非空字符串")

    patterns = raw.get("patterns", [])
    _expect(isinstance(patterns, list), f"{where}.patterns 必须是数组")
    for position, pattern in enumerate(patterns):
        _expect(
            isinstance(pattern, str) and pattern.strip(),
            f"{where}.patterns[{position}] 必须是非空字符串",
        )

    emoji = raw.get("emoji")
    _expect(emoji is None or isinstance(emoji, str), f"{where}.emoji 必须是字符串")
    notes = raw.get("notes")
    _expect(notes is None or isinstance(notes, str), f"{where}.notes 必须是字符串")

    return Category(
        id=category_id.strip(),
        label=label.strip(),
        emoji=emoji,
        patterns=tuple(pattern.strip() for pattern in patterns),
        notes=notes,
    )


def load_categories(path: str | Path | None = None) -> CategorySet:
    """读取并校验类别清单。

    Raises:
        CategoryConfigError: 文件缺失、不是合法 JSON、结构不对、id 重复、
            或缺少兜底类别。
    """

    target = Path(path) if path is not None else DEFAULT_CATEGORIES_PATH
    try:
        text = target.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise CategoryConfigError(f"找不到类别清单：{target}") from exc
    except OSError as exc:
        raise CategoryConfigError(f"类别清单读取失败：{target}：{exc}") from exc

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CategoryConfigError(
            f"类别清单不是合法 JSON：{target}：第 {exc.lineno} 行 {exc.msg}"
        ) from exc

    _expect(isinstance(document, dict), f"类别清单根部必须是对象：{target}")

    version = document.get("version", 1)
    _expect(isinstance(version, int) and not isinstance(version, bool), "version 必须是整数")

    raw_categories = document.get("categories")
    _expect(isinstance(raw_categories, list) and raw_categories, "categories 必须是非空数组")

    categories = [_parse_category(item, index) for index, item in enumerate(raw_categories)]

    seen: set[str] = set()
    for category in categories:
        _expect(category.id not in seen, f"类别 id 重复：{category.id}")
        seen.add(category.id)

    return CategorySet(version=version, categories=categories, source=target)
