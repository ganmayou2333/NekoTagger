"""一次性脚本：给 NekoTrak「未分类」里的一批显示名建议类别。

分类器本身按 exe 路径工作；这里的输入只有显示名（很多还是网页标题），所以单独
走一个「按名字判类」的流程：

- 分批（默认 20 条/批）调用已配置的 OpenAI 兼容后端；
- 类别必须命中 categories.json 的枚举，否则丢弃；
- 输出 ``{name: {category, confidence, reason}}``，供人工在 NekoTrak 里批量处理。

用法：

    python scripts/suggest_categories.py data/uncategorized.json \
        --out data/uncategorized-suggestions.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nekotagger.ai import (  # noqa: E402
    AiConfigError,
    HttpBackend,
    _extract_json,
    load_ai_config,
)
from nekotagger.categories import load_categories  # noqa: E402
from nekotagger.seeds import load_seeds  # noqa: E402

BATCH_SIZE = 20

SYSTEM_PROMPT = (
    "You sort desktop-application and website names into fixed categories. "
    "Answer with JSON only: {\"results\": [{\"name\": string, \"category\": string, "
    "\"confidence\": \"certain\"|\"likely\"|\"guess\", \"reason\": string}]}. "
    "Use only the category ids provided. Names may be Chinese, English, website "
    "titles or system utility names. If you cannot tell, use \"uncategorized\"."
)


def build_messages(names: list[str], categories, seeds) -> list[dict[str, str]]:
    menu = "\n".join(f"- {c.id} ({c.label})" for c in categories)
    lines = [f"Categories:\n{menu}", "", "Names to sort:"]
    for name in names:
        lines.append(f"- {name}")
    if seeds is not None:
        hints = []
        for name in names:
            for seed in seeds.hints_for(name, limit=2):
                hints.append(f"- {seed.name} => {seed.category_id}")
        if hints:
            lines.extend(["", "The user previously sorted these (reference only):",
                          *list(dict.fromkeys(hints))[:20]])
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse(raw, categories, expected: set[str]) -> dict[str, dict]:
    rows = raw.get("results") if isinstance(raw, dict) else raw
    out: dict[str, dict] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        name, category = row.get("name"), row.get("category")
        if not isinstance(name, str) or name not in expected:
            continue
        if not isinstance(category, str) or category not in categories:
            continue
        confidence = row.get("confidence")
        out[name] = {
            "category": category,
            "confidence": confidence if confidence in {"certain", "likely", "guess"} else "likely",
            "reason": str(row.get("reason") or "").strip()[:160],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给一批显示名建议类别")
    parser.add_argument("input", help="含 names 数组的 JSON")
    parser.add_argument("--out", required=True, help="输出 JSON")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条（试水用）")
    parser.add_argument("--categories", default=None)
    args = parser.parse_args(argv)

    categories = load_categories(args.categories)
    config = load_ai_config()
    if not config.usable:
        print("错误：没有配置 api_key（见 %LOCALAPPDATA%\\NekoTagger\\config.local.json）",
              file=sys.stderr)
        return 2
    backend = HttpBackend(config, timeout=30.0)

    seeds_path = REPO_ROOT / "data" / "seed-categories.json"
    seeds = load_seeds(seeds_path, categories) if seeds_path.exists() else None

    document = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    names = [n for n in document.get("names", []) if isinstance(n, str) and n.strip()]
    if args.limit:
        names = names[: args.limit]

    results: dict[str, dict] = {}
    batches = 0
    for start in range(0, len(names), args.batch_size):
        chunk = names[start : start + args.batch_size]
        batches += 1
        try:
            content = backend._post(build_messages(chunk, categories, seeds))
            parsed = parse(_extract_json(content), categories, set(chunk))
        except Exception as exc:  # noqa: BLE001 - 单批失败不该中断整轮
            print(f"  批次 {batches} 失败：{exc}", file=sys.stderr)
            continue
        results.update(parsed)
        print(f"  批次 {batches}: {len(chunk)} 条 -> 接受 {len(parsed)}", flush=True)
        time.sleep(0.2)

    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": config.model,
        "input_count": len(names),
        "classified_count": len(results),
        "batches": batches,
        "suggestions": results,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    counts: dict[str, int] = {}
    for item in results.values():
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print(f"\n完成：{len(results)}/{len(names)} 条有建议，{batches} 批")
    for category, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {category:14} {count}")
    print(f"输出：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
