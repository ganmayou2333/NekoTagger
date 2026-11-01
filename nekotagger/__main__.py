"""命令行入口：``python -m nekotagger classify <path>``。

只做一件事，尽量薄：解析参数 → 调 :func:`nekotagger.classify` → 打印。
所有判断都在库里，方便别处直接 import 复用。
"""

from __future__ import annotations

import argparse
import json
import sys

from .categories import CategoryConfigError, load_categories
from .classify import CONTRACT_KEYS, classify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nekotagger",
        description="把可执行文件归到 NekoTrak 的类别里。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser("classify", help="分类一条 exe 路径")
    classify_parser.add_argument("path", help="可执行文件路径（可带引号、正斜杠）")
    classify_parser.add_argument("--json", action="store_true", help="输出契约 JSON 而不是人读格式")
    classify_parser.add_argument(
        "--categories",
        default=None,
        metavar="PATH",
        help="类别清单路径（默认仓库根的 categories.json）",
    )
    return parser


def _format_human(result: dict[str, object], path: str) -> str:
    return "\t".join(
        [
            path,
            str(result["category"]),
            f"{result['source']}/{result['confidence']}",
            str(result["matched_by"]),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command != "classify":  # pragma: no cover - argparse 已经拦住
        return 2

    try:
        categories = load_categories(args.categories)
        result = classify(args.path, categories=categories)
    except CategoryConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"错误：路径无效：{exc}", file=sys.stderr)
        return 2

    if args.json:
        ordered = {key: result[key] for key in CONTRACT_KEYS}
        print(json.dumps(ordered, ensure_ascii=False))
    else:
        print(_format_human(result, args.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
