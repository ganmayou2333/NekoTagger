"""缓存：判过一次就不再问模型。

位置在 ``%LOCALAPPDATA%\\NekoTagger\\cache.json``（可用 ``--cache`` 覆盖）。缓存
是**这台机器的运行状态**，不该跟着代码仓库走。

两个关键行为：

- **清单变更只失效受影响条目**：加载时发现某条目的 category 已不在当前
  ``categories.json`` 里，就打回 pending 重判，其余条目原样保留。
- **原子写**：先写同目录临时文件再替换，中途失败不会留下半截 JSON。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .categories import CategorySet
from .locking import file_lock

CACHE_VERSION = 1

#: pending 条目最多重试几次，超过就接受「未分类」不再重试，避免每轮刷接口。
MAX_PENDING_ATTEMPTS = 3

ENTRY_FIELDS = ("category", "confidence", "source", "matched_by", "classified_at")


def default_cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "NekoTagger" / "cache.json"


@dataclass
class Cache:
    path: Path
    entries: dict[str, dict] = field(default_factory=dict)
    pending: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    dirty: bool = False

    # ---------------------------------------------------------------- loading

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None, categories: CategorySet | None = None) -> "Cache":
        target = Path(path) if path is not None else default_cache_path()
        cache = cls(path=target)

        if not target.exists():
            return cache

        try:
            with file_lock(target):
                text = target.read_text(encoding="utf-8-sig")
            document = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            cache.warnings.append(f"缓存损坏，已按空缓存处理：{exc}")
            return cache

        if not isinstance(document, dict):
            cache.warnings.append("缓存根部不是对象，已按空缓存处理")
            return cache

        version = document.get("version")
        if version != CACHE_VERSION:
            cache.warnings.append(f"缓存版本 {version!r} 不认识，已按空缓存处理")
            return cache

        entries = document.get("entries")
        if isinstance(entries, dict):
            for key, value in entries.items():
                if isinstance(key, str) and isinstance(value, dict) and isinstance(value.get("category"), str):
                    cache.entries[key] = {name: value.get(name) for name in ENTRY_FIELDS if name in value}

        pending = document.get("pending")
        if isinstance(pending, dict):
            for key, value in pending.items():
                if isinstance(key, str):
                    attempts = value.get("attempts") if isinstance(value, dict) else 0
                    cache.pending[key] = {
                        "attempts": attempts if isinstance(attempts, int) else 0,
                        "lastAttemptAt": (value or {}).get("lastAttemptAt"),
                    }

        if categories is not None:
            cache._invalidate_unknown_categories(categories)

        return cache

    def _invalidate_unknown_categories(self, categories: CategorySet) -> None:
        stale = [
            key
            for key, entry in self.entries.items()
            if entry.get("category") not in categories
        ]
        for key in stale:
            entry = self.entries.pop(key)
            self.pending.setdefault(key, {"attempts": 0, "lastAttemptAt": None})
            self.warnings.append(
                f"类别 {entry.get('category')!r} 已不在 categories.json 中，"
                f"条目打回 pending：{key}"
            )
            self.dirty = True

    # ---------------------------------------------------------------- access

    def get(self, normalized_path: str) -> dict | None:
        return self.entries.get(normalized_path)

    def put(self, normalized_path: str, result: dict) -> None:
        self.entries[normalized_path] = {name: result.get(name) for name in ENTRY_FIELDS}
        self.pending.pop(normalized_path, None)
        self.dirty = True

    def mark_pending(self, normalized_path: str, now: float | None = None) -> None:
        previous = self.pending.get(normalized_path, {})
        attempts = int(previous.get("attempts", 0)) + 1
        self.pending[normalized_path] = {
            "attempts": attempts,
            "lastAttemptAt": int(now) if now is not None else None,
        }
        self.entries.pop(normalized_path, None)
        self.dirty = True

    def is_pending_eligible(self, normalized_path: str) -> bool:
        """还有重试预算吗（没记录 = 有）。"""

        record = self.pending.get(normalized_path)
        if record is None:
            return True
        return int(record.get("attempts", 0)) < MAX_PENDING_ATTEMPTS

    def pending_paths(self) -> list[str]:
        return [key for key in self.pending if self.is_pending_eligible(key)]

    # ---------------------------------------------------------------- persist

    def to_dict(self) -> dict:
        return {
            "version": CACHE_VERSION,
            "entries": self.entries,
            "pending": self.pending,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=1, sort_keys=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        # 跨进程锁：MCP 常驻进程与 CLI 可能同时写同一份缓存。
        with file_lock(self.path):
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)  # 同卷原子替换
        self.dirty = False

    # ---------------------------------------------------------------- stats

    def stats(self, categories: CategorySet | None = None) -> dict[str, object]:
        by_source: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for entry in self.entries.values():
            source = str(entry.get("source") or "unknown")
            by_source[source] = by_source.get(source, 0) + 1
            category = str(entry.get("category") or "uncategorized")
            by_category[category] = by_category.get(category, 0) + 1
        return {
            "entries": len(self.entries),
            "pending": len(self.pending),
            "pending_eligible": len(self.pending_paths()),
            "by_source": by_source,
            "by_category": by_category,
        }
