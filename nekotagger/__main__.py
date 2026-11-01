"""命令行入口。

五条命令：``classify`` / ``corpus`` / ``show`` / ``override`` / ``export``。
命令本身只做参数解析与打印，判断逻辑都在库里（便于测试与复用）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .ai import AiBatchItem, AiConfig, AiConfigError, HttpBackend, load_ai_config
from .cache import Cache, default_cache_path
from .categories import (
    DEFAULT_CATEGORIES_PATH,
    CategoryConfigError,
    CategorySet,
    load_categories,
)
from .classify import CONTRACT_KEYS, _display_candidates, classify
from .corpus import find_nekotrak_root, load_corpus
from .normalize import normalize_exe_path
from .overrides import OverrideDataError, load_overrides
from .pipeline import run_corpus
from .seeds import SeedDataError, SeedIndex, load_seeds
from .steam import SteamDataError, SteamIndex, load_steam_index
from .wininfo import read_version_info

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEEDS_PATH = REPO_ROOT / "data" / "seed-categories.json"
DEFAULT_OVERRIDES_PATH = REPO_ROOT / "overrides.json"

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2


@dataclass
class Context:
    categories: CategorySet
    seeds: SeedIndex | None
    steam: SteamIndex | None
    overrides: dict[str, str]
    cache: Cache
    config: AiConfig
    warnings: list[str] = field(default_factory=list)


def _build_context(args: argparse.Namespace) -> Context:
    warnings: list[str] = []
    categories = load_categories(args.categories)

    seeds: SeedIndex | None = None
    seeds_path = Path(args.seeds) if args.seeds else DEFAULT_SEEDS_PATH
    if seeds_path.exists():
        seeds = load_seeds(seeds_path, categories)
    else:
        warnings.append(f"没有种子文件，跳过种子匹配：{seeds_path}")

    steam: SteamIndex | None = None
    root = find_nekotrak_root(args.nekotrak_root)
    if root is not None:
        index_path = root / "caches" / "steam-library-index.json"
        if index_path.exists():
            steam = load_steam_index(index_path)

    overrides_path = Path(args.overrides) if args.overrides else DEFAULT_OVERRIDES_PATH
    overrides = load_overrides(overrides_path)

    cache = Cache.load(args.cache, categories)
    warnings.extend(cache.warnings)
    config = load_ai_config(args.config)

    return Context(
        categories=categories,
        seeds=seeds,
        steam=steam,
        overrides=overrides,
        cache=cache,
        config=config,
        warnings=warnings,
    )


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"警告：{warning}", file=sys.stderr)


def _emit(result: dict[str, object], path: str, as_json: bool) -> None:
    if as_json:
        ordered = {key: result[key] for key in CONTRACT_KEYS}
        print(json.dumps(ordered, ensure_ascii=False))
    else:
        print(
            "\t".join(
                [
                    path,
                    str(result["category"]),
                    f"{result['source']}/{result['confidence']}",
                    str(result["matched_by"]),
                ]
            )
        )


def _cached_result(cached: dict) -> dict[str, object]:
    return {
        "category": cached.get("category"),
        "confidence": cached.get("confidence") or "guess",
        "source": "cache",
        "matched_by": f"cached {cached.get('source')}: {cached.get('matched_by')}",
        "classified_at": cached.get("classified_at") or 0,
    }


def cmd_classify(args: argparse.Namespace) -> int:
    context = _build_context(args)
    _print_warnings(context.warnings)

    normalized = normalize_exe_path(args.path)
    result = classify(
        args.path,
        categories=context.categories,
        seeds=context.seeds,
        steam=context.steam,
        overrides=context.overrides,
    )

    if result["source"] == "unknown":
        cached = context.cache.get(normalized)
        if cached is not None:
            result = _cached_result(cached)
        elif args.ai:
            if not context.config.usable:
                print("提示：没有配置 api_key，跳过 AI（见 config.local.json 或 NEKOTAGGER_API_KEY）",
                      file=sys.stderr)
            else:
                candidates = _display_candidates(normalized, read_version_info)
                batch = [AiBatchItem(normalized, candidates[0] if candidates else normalized)]
                try:
                    accepted = HttpBackend(context.config).classify(batch, context.categories, context.seeds)
                except Exception as exc:  # noqa: BLE001 - 单条 AI 失败不该崩
                    print(f"提示：AI 调用失败，保持未分类：{exc}", file=sys.stderr)
                    accepted = {}
                record = accepted.get(normalized)
                if record is not None:
                    result = {
                        "category": record["category"],
                        "confidence": record.get("confidence") or "likely",
                        "source": "ai",
                        "matched_by": f"ai: {record.get('reason') or ''}".strip(),
                        "classified_at": int(time.time()),
                    }
                    context.cache.put(normalized, result)
                    context.cache.save()
                else:
                    context.cache.mark_pending(normalized)
                    context.cache.save()
        else:
            context.cache.mark_pending(normalized)
            context.cache.save()
    else:
        context.cache.put(normalized, result)
        context.cache.save()

    _emit(result, args.path, args.json)
    return EXIT_OK


def cmd_corpus(args: argparse.Namespace) -> int:
    context = _build_context(args)
    if not context.config.usable:
        context.warnings.append("未配置 api_key，AI 阶段将跳过")

    corpus = load_corpus(args.nekotrak_root, paths_from=args.paths_from)
    context.warnings.extend(corpus.warnings)

    if not corpus.paths:
        _print_warnings(context.warnings)
        print("语料为空：检查 NekoTrak 安装目录或 --paths-from。")
        return EXIT_OK

    backend = None
    if context.config.usable and not args.no_ai:
        backend = HttpBackend(context.config, timeout=args.timeout)

    run = run_corpus(
        corpus.paths,
        categories=context.categories,
        seeds=context.seeds,
        steam=context.steam,
        overrides=context.overrides,
        cache=context.cache,
        backend=backend,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
    )

    _print_warnings(context.warnings + run.warnings)
    summary = corpus.summary()
    print("语料：" + "  ".join(f"{key}={value}" for key, value in summary.items()))
    if corpus.sources:
        print("来源：" + "  ".join(f"{key}={value}" for key, value in corpus.sources.items()))
    print("分类：" + "  ".join(f"{key}={value}" for key, value in run.by_source.most_common()))
    print("类别：" + "  ".join(f"{key}={value}" for key, value in run.by_category.most_common()))
    print("AI：" + run.summary())
    print(f"缓存：{context.cache.path}（{len(context.cache.entries)} 条）")
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    context = _build_context(args)
    _print_warnings(context.warnings)
    stats = context.cache.stats(context.categories)
    print(f"缓存文件：{context.cache.path}")
    print(f"条目：{stats['entries']}  pending：{stats['pending']}（可重试 {stats['pending_eligible']}）")
    print("按来源：" + "  ".join(f"{k}={v}" for k, v in sorted(stats["by_source"].items())))
    print("按类别：" + "  ".join(f"{k}={v}" for k, v in sorted(stats["by_category"].items())))
    ai_count = stats["by_source"].get("ai", 0)
    if ai_count:
        print(f"AI 判过 {ai_count} 条，可用 `nekotagger export --source ai` 抽查")
    return EXIT_OK


def cmd_override(args: argparse.Namespace) -> int:
    context = _build_context(args)
    _print_warnings(context.warnings)
    category = args.category.strip()
    if category not in context.categories:
        print(f"错误：未知类别 {category!r}，可用：{', '.join(context.categories.ids())}",
              file=sys.stderr)
        return EXIT_CONFIG_ERROR

    path = Path(args.overrides) if args.overrides else DEFAULT_OVERRIDES_PATH
    document: dict[str, str] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                document = {str(k): str(v) for k, v in loaded.items()}
        except (OSError, json.JSONDecodeError) as exc:
            print(f"错误：现有覆盖文件无法解析，拒绝覆盖：{exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR

    document[normalize_exe_path(args.path)] = category
    payload = json.dumps(dict(sorted(document.items())), ensure_ascii=False, indent=1) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
    print(f"已写入 {path}：{normalize_exe_path(args.path)} -> {category}")
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    context = _build_context(args)
    _print_warnings(context.warnings)
    entries = {
        path: entry.get("category")
        for path, entry in context.cache.entries.items()
        if args.source is None or entry.get("source") == args.source
    }
    document = {
        "version": 1,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": args.source or "all",
        "count": len(entries),
        "entries": dict(sorted(entries.items())),
    }
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"已导出 {len(entries)} 条到 {target}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nekotagger", description="把可执行文件归到 NekoTrak 的类别里。"
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--categories", default=None, help=f"类别清单（默认 {DEFAULT_CATEGORIES_PATH}）")
    common.add_argument("--seeds", default=None, help=f"种子文件（默认 {DEFAULT_SEEDS_PATH}）")
    common.add_argument("--overrides", default=None, help=f"覆盖文件（默认 {DEFAULT_OVERRIDES_PATH}）")
    common.add_argument("--cache", default=None, help="缓存路径（默认 %%LOCALAPPDATA%%\\NekoTagger\\cache.json）")
    common.add_argument("--config", default=None, help="AI 配置（默认 %%LOCALAPPDATA%%\\NekoTagger\\config.local.json）")
    common.add_argument("--nekotrak-root", default=None,
                        help="NekoTrak 安装目录（读 caches/；也可用 NEKOTAGGER_NEKOTRAK_ROOT）")

    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser("classify", parents=[common], help="分类一条 exe 路径")
    classify_parser.add_argument("path")
    classify_parser.add_argument("--json", action="store_true")
    classify_parser.add_argument("--ai", action="store_true", help="未命中时允许问一次模型")
    classify_parser.set_defaults(func=cmd_classify)

    corpus_parser = subparsers.add_parser("corpus", parents=[common], help="批量冷启动")
    corpus_parser.add_argument("--paths-from", default=None, help="额外路径清单（每行一个）")
    corpus_parser.add_argument("--batch-size", type=int, default=20)
    corpus_parser.add_argument("--max-batches", type=int, default=None)
    corpus_parser.add_argument("--timeout", type=float, default=10.0)
    corpus_parser.add_argument("--no-ai", action="store_true", help="只跑确定性部分")
    corpus_parser.set_defaults(func=cmd_corpus)

    show_parser = subparsers.add_parser("show", parents=[common], help="打印缓存统计")
    show_parser.set_defaults(func=cmd_show)

    override_parser = subparsers.add_parser("override", parents=[common], help="写人工覆盖")
    override_parser.add_argument("path")
    override_parser.add_argument("category")
    override_parser.set_defaults(func=cmd_override)

    export_parser = subparsers.add_parser("export", parents=[common], help="导出分类结果")
    export_parser.add_argument("--out", required=True)
    export_parser.add_argument("--source", default=None, help="只导出某个来源（如 ai）")
    export_parser.set_defaults(func=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (CategoryConfigError, SeedDataError, SteamDataError, OverrideDataError, AiConfigError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValueError as exc:
        print(f"错误：路径无效：{exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
