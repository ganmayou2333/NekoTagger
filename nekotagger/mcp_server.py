"""MCP 服务：把 NekoTagger 暴露成工具。

设计要点（来自确认过的决策）：

- **stdio 传输**：进程随客户端会话起停，不占端口。
- **默认只读**：``override_path`` / ``export_results`` 只有在 ``allow_write=True``
  时才会注册，工具列表本身就体现了权限，而不是运行时才报错。
- **缓存并发**：每次工具调用前按 mtime 重载缓存，写入走 ``locking.file_lock``，
  所以 MCP 常驻也不会把 CLI 的结果覆盖掉。

工具的实现在 :class:`Toolset` 里，MCP 只是薄壳——这样测试不用起协议、不用装 SDK
也能验证业务行为。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cache import Cache
from .categories import DEFAULT_CATEGORIES_PATH, CategorySet, load_categories
from .classify import CONTRACT_KEYS, classify
from .normalize import normalize_exe_path
from .overrides import load_overrides
from .seeds import SeedIndex, load_seeds
from .steam import SteamIndex, load_steam_index
from .wininfo import read_version_info

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEEDS_PATH = REPO_ROOT / "data" / "seed-categories.json"
DEFAULT_OVERRIDES_PATH = REPO_ROOT / "overrides.json"


class ToolError(RuntimeError):
    """工具层面的可预期错误（会作为 MCP 错误返回，不是崩溃）。"""


@dataclass
class Toolset:
    """MCP 工具的实际实现。"""

    categories: CategorySet
    seeds: SeedIndex | None = None
    steam: SteamIndex | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    cache: Cache | None = None
    overrides_path: Path = DEFAULT_OVERRIDES_PATH
    warnings: list[str] = field(default_factory=list)
    _cache_signature: tuple[int, int] | None = field(default=None, repr=False)

    def _refresh_cache(self) -> None:
        """磁盘上的缓存被 CLI 改过时重新加载。

        常驻进程持有的是启动时的快照；不重载就会一直报告旧数据（实测：CLI
        重建缓存后 MCP 仍显示 100 条，磁盘其实只有 94 条）。
        """

        if self.cache is None:
            return
        try:
            stat = self.cache.path.stat()
        except OSError:
            return
        signature = (stat.st_mtime_ns, stat.st_size)
        if self._cache_signature == signature:
            return
        self.cache = Cache.load(self.cache.path, self.categories)
        self.warnings.extend(self.cache.warnings)
        self._cache_signature = signature

    def _touch_cache_signature(self) -> None:
        if self.cache is None:
            return
        try:
            stat = self.cache.path.stat()
        except OSError:
            self._cache_signature = None
            return
        self._cache_signature = (stat.st_mtime_ns, stat.st_size)

    # ------------------------------------------------------------------ 只读

    def classify_path(self, path: str) -> dict[str, object]:
        """把一条 exe 路径分到某个类别，返回契约结构。"""
        self._refresh_cache()

        result = classify(
            path,
            categories=self.categories,
            seeds=self.seeds,
            steam=self.steam,
            overrides=self.overrides,
        )
        cached = self.cache.get(normalize_exe_path(path)) if self.cache else None
        if result["source"] == "unknown" and cached is not None:
            return {
                "category": cached.get("category"),
                "confidence": cached.get("confidence") or "guess",
                "source": "cache",
                "matched_by": f"cached {cached.get('source')}: {cached.get('matched_by')}",
                "classified_at": cached.get("classified_at") or 0,
            }
        return result

    def classify_many(self, paths: list[str]) -> list[dict[str, object]]:
        """批量分类，逐条返回契约结构（不调模型，缓存命中用缓存）。"""
        self._refresh_cache()

        return [{"path": path, **self.classify_path(path)} for path in paths]

    def list_categories(self) -> list[dict[str, object]]:
        return [
            {
                "id": category.id,
                "label": category.label,
                "emoji": category.emoji,
                "patterns": len(category.patterns),
                "notes": category.notes,
            }
            for category in self.categories
        ]

    def cache_stats(self) -> dict[str, object]:
        self._refresh_cache()
        if self.cache is None:
            return {"available": False}
        stats = self.cache.stats(self.categories)
        return {
            "available": True,
            "cache_path": str(self.cache.path),
            "entries": stats["entries"],
            "pending": stats["pending"],
            "by_source": stats["by_source"],
            "by_category": stats["by_category"],
        }

    # ------------------------------------------------------------------ 写

    def override_path(self, path: str, category: str) -> dict[str, object]:
        """写一条人工覆盖。类别必须是清单里已有的 id。"""
        self._refresh_cache()

        if category not in self.categories:
            raise ToolError(
                f"未知类别 {category!r}；可用：{', '.join(self.categories.ids())}"
            )
        normalized = normalize_exe_path(path)

        document: dict[str, str] = {}
        target = self.overrides_path
        if target.exists():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ToolError(f"现有覆盖文件无法解析，拒绝覆盖：{exc}") from exc
            if isinstance(loaded, dict):
                document = {str(key): str(value) for key, value in loaded.items()}

        document[normalized] = category
        payload = json.dumps(dict(sorted(document.items())), ensure_ascii=False, indent=1) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)

        self.overrides[normalized] = category  # 立刻生效，不用重启
        return {"path": normalized, "category": category, "override_count": len(document)}

    def export_results(self, out: str, source: str | None = None) -> dict[str, object]:
        """把缓存导出成只含类别 id 的 JSON。"""
        self._refresh_cache()

        if self.cache is None:
            raise ToolError("没有可用的缓存")
        entries = {
            path: entry.get("category")
            for path, entry in self.cache.entries.items()
            if source is None or entry.get("source") == source
        }
        document = {
            "version": 1,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": source or "all",
            "count": len(entries),
            "entries": dict(sorted(entries.items())),
        }
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return {"out": str(target), "count": len(entries), "source": source or "all"}


def build_toolset(
    *,
    categories_path: str | Path | None = None,
    seeds_path: str | Path | None = None,
    overrides_path: str | Path | None = None,
    cache_path: str | Path | None = None,
    nekotrak_root: str | Path | None = None,
) -> Toolset:
    """按 CLI 同样的默认值搭一份工具集。"""

    warnings: list[str] = []
    categories = load_categories(categories_path or DEFAULT_CATEGORIES_PATH)

    seeds: SeedIndex | None = None
    seed_target = Path(seeds_path) if seeds_path else DEFAULT_SEEDS_PATH
    if seed_target.exists():
        seeds = load_seeds(seed_target, categories)
    else:
        warnings.append(f"没有种子文件：{seed_target}")

    steam: SteamIndex | None = None
    root = Path(nekotrak_root) if nekotrak_root else None
    if root is not None:
        index_path = root / "caches" / "steam-library-index.json"
        if index_path.exists():
            steam = load_steam_index(index_path)

    overrides_target = Path(overrides_path) if overrides_path else DEFAULT_OVERRIDES_PATH
    overrides = load_overrides(overrides_target)

    cache = Cache.load(cache_path, categories)
    warnings.extend(cache.warnings)

    toolset = Toolset(
        categories=categories,
        seeds=seeds,
        steam=steam,
        overrides=overrides,
        cache=cache,
        overrides_path=overrides_target,
        warnings=warnings,
    )
    toolset._touch_cache_signature()
    return toolset


def _server_class():
    """兼容 SDK 两代：2.x 叫 MCPServer，1.x 叫 FastMCP。"""

    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2

        return MCPServer
    except ImportError:  # pragma: no cover - 取决于安装版本
        from mcp.server.fastmcp import FastMCP  # mcp < 2

        return FastMCP


def create_server(*, toolset: Toolset | None = None, allow_write: bool = False):
    """构造 MCP 服务。``allow_write`` 决定写工具注册与否。"""

    tools = toolset if toolset is not None else build_toolset()
    server = _server_class()("nekotagger")

    @server.tool()
    def classify_path(path: str) -> dict:
        """把一条可执行文件路径归到 NekoTrak 的类别里，返回契约结构。"""
        return tools.classify_path(path)

    @server.tool()
    def classify_many(paths: list[str]) -> list[dict]:
        """批量分类多条路径（不调模型，只用规则/种子/Steam/缓存）。"""
        return tools.classify_many(paths)

    @server.tool()
    def list_categories() -> list[dict]:
        """列出当前类别清单（id / 中文名 / emoji / patterns 数量）。"""
        return tools.list_categories()

    @server.tool()
    def cache_stats() -> dict:
        """缓存统计：条目数、pending、按来源与按类别的分布。"""
        return tools.cache_stats()

    if allow_write:

        @server.tool()
        def override_path(path: str, category: str) -> dict:
            """写一条人工覆盖（会修改 overrides.json）。类别必须是已有 id。"""
            # 客户端通常只显示 "Error executing tool"，所以把可执行的提示
            # （合法类别列表）放进返回体，而不是靠异常文本。
            try:
                return tools.override_path(path, category)
            except ToolError as exc:
                return {
                    "ok": False,
                    "error": str(exc),
                    "valid_categories": list(tools.categories.ids()),
                }

        @server.tool()
        def export_results(out: str, source: str | None = None) -> dict:
            """把缓存导出成 JSON（只含类别 id）；source 可限定为 ai 等。"""
            try:
                return tools.export_results(out, source)
            except ToolError as exc:
                return {"ok": False, "error": str(exc)}

    return server


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nekotagger-mcp", description="NekoTagger 的 MCP 服务（stdio）")
    parser.add_argument("--allow-write", action="store_true", help="注册写工具（override / export）")
    parser.add_argument("--categories", default=None)
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--overrides", default=None)
    parser.add_argument("--cache", default=None)
    parser.add_argument("--nekotrak-root", default=None)
    args = parser.parse_args(argv)

    toolset = build_toolset(
        categories_path=args.categories,
        seeds_path=args.seeds,
        overrides_path=args.overrides,
        cache_path=args.cache,
        nekotrak_root=args.nekotrak_root,
    )
    server = create_server(toolset=toolset, allow_write=args.allow_write)
    try:
        server.run(transport="stdio")  # mcp >= 2
    except TypeError:  # pragma: no cover - mcp < 2
        server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
