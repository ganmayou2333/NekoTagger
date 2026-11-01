"""语料批处理：把确定性判断和 AI 兜底串起来。

这是 ``corpus`` 命令的实现体，但它不碰 argparse、不打印——所以测试可以直接
注入假后端，断言「批次数」与「哪些条目进了 pending」。
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .ai import AiBackend, AiBatchItem, DEFAULT_BATCH_SIZE
from .cache import Cache
from .categories import CategorySet
from .classify import _display_candidates, classify
from .normalize import normalize_exe_path
from .seeds import SeedIndex
from .steam import SteamIndex
from .wininfo import read_version_info


def _chunks(sequence: Sequence[AiBatchItem], size: int) -> Iterable[Sequence[AiBatchItem]]:
    for start in range(0, len(sequence), size):
        yield sequence[start : start + size]


@dataclass
class CorpusRun:
    total: int = 0
    by_source: Counter = field(default_factory=Counter)
    by_category: Counter = field(default_factory=Counter)
    ai_batches: int = 0
    ai_failures: int = 0
    ai_skipped: str | None = None
    pending: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"paths={self.total}",
            f"ai_batches={self.ai_batches}",
            f"ai_failures={self.ai_failures}",
            f"pending={self.pending}",
        ]
        if self.ai_skipped:
            parts.append(f"AI 跳过（{self.ai_skipped}）")
        return "  ".join(parts)


def run_corpus(
    paths: Iterable[str],
    *,
    categories: CategorySet,
    seeds: SeedIndex | None = None,
    steam: SteamIndex | None = None,
    overrides: dict[str, str] | None = None,
    cache: Cache | None = None,
    backend: AiBackend | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
    info_reader=read_version_info,
    now: float | None = None,
) -> CorpusRun:
    run = CorpusRun()
    timestamp = int(now if now is not None else time.time())
    sources: dict[str, str] = {}
    pending_items: list[AiBatchItem] = []

    for raw in paths:
        normalized = normalize_exe_path(raw)
        run.total += 1
        result = classify(
            normalized,
            categories=categories,
            seeds=seeds,
            steam=steam,
            overrides=overrides,
            info_reader=info_reader,
            now=timestamp,
        )

        if result["source"] != "unknown":
            sources[normalized] = str(result["source"])
            run.by_category[str(result["category"])] += 1
            if cache is not None:
                cache.put(normalized, result)
            continue

        cached = cache.get(normalized) if cache is not None else None
        if cached is not None and cached.get("category"):
            sources[normalized] = "cache"
            run.by_category[str(cached["category"])] += 1
            continue

        if cache is not None and not cache.is_pending_eligible(normalized):
            # 重试预算用完：接受未分类，不再进 AI。
            sources[normalized] = "unknown"
            run.by_category[str(categories.fallback.id)] += 1
            continue

        display = _display_candidates(normalized, info_reader)[:1]
        pending_items.append(
            AiBatchItem(path=normalized, display=display[0] if display else normalized)
        )

    if pending_items:
        if backend is None:
            run.ai_skipped = "未配置 API key"
            for item in pending_items:
                sources[item.path] = "unknown"
                run.by_category[str(categories.fallback.id)] += 1
                if cache is not None:
                    cache.mark_pending(item.path, timestamp)
        else:
            for chunk in _chunks(pending_items, max(1, batch_size)):
                if max_batches is not None and run.ai_batches >= max_batches:
                    for item in chunk:
                        sources[item.path] = "unknown"
                        run.by_category[str(categories.fallback.id)] += 1
                        if cache is not None:
                            cache.mark_pending(item.path, timestamp)
                    continue

                run.ai_batches += 1
                try:
                    accepted = backend.classify(list(chunk), categories, seeds)
                except Exception as exc:  # noqa: BLE001 - 一次失败不该中断整批流程
                    run.ai_failures += 1
                    run.warnings.append(f"AI 批次失败：{exc}")
                    for item in chunk:
                        sources[item.path] = "unknown"
                        run.by_category[str(categories.fallback.id)] += 1
                        if cache is not None:
                            cache.mark_pending(item.path, timestamp)
                    continue

                for item in chunk:
                    record = accepted.get(item.path)
                    if not isinstance(record, dict) or record.get("category") not in categories:
                        sources[item.path] = "unknown"
                        run.by_category[str(categories.fallback.id)] += 1
                        if cache is not None:
                            cache.mark_pending(item.path, timestamp)
                        run.warnings.append(f"AI 未给出有效类别：{item.path}")
                        continue
                    reason = str(record.get("reason") or "").strip()
                    result = {
                        "category": record["category"],
                        "confidence": record.get("confidence") or "likely",
                        "source": "ai",
                        "matched_by": f"ai: {reason}" if reason else "ai",
                        "classified_at": timestamp,
                    }
                    sources[item.path] = "ai"
                    run.by_category[str(result["category"])] += 1
                    if cache is not None:
                        cache.put(item.path, result)

    run.by_source.update(Counter(sources.values()))
    run.pending = len(cache.pending) if cache is not None else 0
    if cache is not None and cache.dirty:
        cache.save()
    return run
