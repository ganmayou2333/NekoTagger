"""MCP 工具集的测试。

业务逻辑（Toolset）不依赖 SDK，直接在进程内测；协议层（FastMCP）若没装 SDK 就跳过，
并在需要时用真实 stdio 会话做一次冒烟。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from nekotagger.cache import Cache
from nekotagger.categories import load_categories
from nekotagger.mcp_server import ToolError, Toolset, build_toolset

REPO_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = load_categories(REPO_ROOT / "categories.json")

try:  # SDK 可选：没装也不影响业务测试
    import mcp  # noqa: F401

    HAVE_MCP = True
except ImportError:  # pragma: no cover - 取决于环境
    HAVE_MCP = False


class ToolsetCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cache_path = self.root / "cache.json"
        self.overrides_path = self.root / "overrides.json"
        self.toolset = Toolset(
            categories=CATEGORIES,
            cache=Cache.load(self.cache_path),
            overrides_path=self.overrides_path,
        )

    def touch_exe(self, name: str) -> Path:
        target = self.root / "apps" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"MZ")
        return target


class ReadonlyToolsTests(ToolsetCase):
    def test_classify_path_returns_contract(self) -> None:
        result = self.toolset.classify_path(r"C:\Windows\System32\openwith.exe")
        self.assertEqual(result["category"], "tool")
        self.assertEqual(result["source"], "rule")
        self.assertEqual(
            set(result), {"category", "confidence", "source", "matched_by", "classified_at"}
        )

    def test_classify_many_keeps_order_and_paths(self) -> None:
        paths = [r"C:\Windows\a.exe", r"D:\unknown\thing.exe"]
        results = self.toolset.classify_many(paths)
        self.assertEqual([row["path"] for row in results], paths)

    def test_cache_hit_is_reported_as_cache(self) -> None:
        exe = self.touch_exe("cached.exe")
        from nekotagger.normalize import normalize_exe_path

        self.toolset.cache.put(normalize_exe_path(exe), {
            "category": "dev", "confidence": "likely", "source": "ai",
            "matched_by": "ai: looked like an editor", "classified_at": 5,
        })
        result = self.toolset.classify_path(str(exe))
        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["category"], "dev")

    def test_list_categories_exposes_ids(self) -> None:
        rows = self.toolset.list_categories()
        ids = [row["id"] for row in rows]
        self.assertIn("game", ids)
        self.assertIn("uncategorized", ids)

    def test_cache_stats(self) -> None:
        stats = self.toolset.cache_stats()
        self.assertTrue(stats["available"])
        self.assertEqual(stats["entries"], 0)


class CacheFreshnessTests(ToolsetCase):
    def test_stats_see_changes_written_by_another_process(self) -> None:
        from nekotagger.normalize import normalize_exe_path

        self.assertEqual(self.toolset.cache_stats()["entries"], 0)

        # 模拟 CLI 在别处写缓存
        writer = Cache.load(self.cache_path, CATEGORIES)
        writer.put(normalize_exe_path(r"D:\apps\fresh.exe"), {
            "category": "tool", "confidence": "certain", "source": "rule",
            "matched_by": "x", "classified_at": 1,
        })
        writer.save()

        # 常驻的 toolset 必须看到新数据，而不是启动时的快照
        stats = self.toolset.cache_stats()
        self.assertEqual(stats["entries"], 1)
        self.assertEqual(self.toolset.classify_path(r"D:\apps\fresh.exe")["source"], "cache")


class AgentClassifierTests(ToolsetCase):
    def _pending(self, path: str) -> None:
        from nekotagger.normalize import normalize_exe_path

        self.toolset.cache.mark_pending(normalize_exe_path(path), now=1)
        self.toolset.cache.save()

    def test_pending_items_lists_what_needs_a_decision(self) -> None:
        self._pending(r"D:\apps\mystery-one.exe")
        rows = self.toolset.pending_items()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "mystery-one.exe")
        self.assertIn("attempts", rows[0])

    def test_submit_classifications_writes_ai_entries(self) -> None:
        exe = self.touch_exe("agent-judged.exe")
        self._pending(str(exe))

        result = self.toolset.submit_classifications([
            {"path": str(exe), "category": "dev", "reason": "looks like an editor"},
        ])
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["remaining_pending"], 0)

        from nekotagger.normalize import normalize_exe_path

        stored = self.toolset.cache.get(normalize_exe_path(exe))
        self.assertEqual(stored["source"], "ai")
        self.assertIn("dsh-agent", stored["matched_by"])
        self.assertIn("editor", stored["matched_by"])

    def test_submit_rejects_unknown_categories_without_partial_write(self) -> None:
        good = self.touch_exe("good.exe")
        bad = self.touch_exe("bad.exe")
        result = self.toolset.submit_classifications([
            {"path": str(good), "category": "dev", "reason": "ok"},
            {"path": str(bad), "category": "invented", "reason": "nope"},
        ])
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["rejected_items"][0]["path"], str(bad))


class WriteToolsTests(ToolsetCase):
    def test_override_writes_and_takes_effect_immediately(self) -> None:
        exe = self.touch_exe("needs-override.exe")
        result = self.toolset.override_path(str(exe), "dev")
        self.assertEqual(result["category"], "dev")
        self.assertEqual(result["override_count"], 1)
        self.assertEqual(self.toolset.classify_path(str(exe))["source"], "override")

        document = json.loads(self.overrides_path.read_text(encoding="utf-8"))
        self.assertEqual(list(document.values()), ["dev"])

    def test_override_rejects_unknown_category(self) -> None:
        exe = self.touch_exe("x.exe")
        with self.assertRaises(ToolError) as caught:
            self.toolset.override_path(str(exe), "no-such-category")
        self.assertIn("未知类别", str(caught.exception))

    def test_export_writes_category_ids_only(self) -> None:
        from nekotagger.normalize import normalize_exe_path

        exe = self.touch_exe("exported.exe")
        self.toolset.cache.put(normalize_exe_path(exe), {
            "category": "tool", "confidence": "certain", "source": "rule",
            "matched_by": "x", "classified_at": 1,
        })
        target = self.root / "out.json"
        result = self.toolset.export_results(str(target))
        self.assertEqual(result["count"], 1)
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["entries"][normalize_exe_path(exe)], "tool")

    def test_export_source_filter(self) -> None:
        target = self.root / "ai-only.json"
        result = self.toolset.export_results(str(target), source="ai")
        self.assertEqual(result["count"], 0)


class BuildToolsetTests(ToolsetCase):
    def test_build_toolset_loads_defaults_and_warns_without_seeds(self) -> None:
        toolset = build_toolset(
            seeds_path=self.root / "no-seeds.json",
            overrides_path=self.overrides_path,
            cache_path=self.cache_path,
        )
        self.assertIsNone(toolset.seeds)
        self.assertTrue(any("种子" in warning for warning in toolset.warnings))


@unittest.skipUnless(HAVE_MCP, "没有安装 mcp SDK")
class ProtocolSmokeTests(ToolsetCase):
    def test_server_exposes_read_tools_without_write_permission(self) -> None:
        from nekotagger.mcp_server import create_server

        server = create_server(toolset=self.toolset, allow_write=False)
        tools = _tool_names(server)
        self.assertEqual(set(tools), {"classify_path", "classify_many", "list_categories", "cache_stats", "pending_items"})

    def test_unknown_category_returns_actionable_payload(self) -> None:
        from nekotagger.mcp_server import create_server

        server = create_server(toolset=self.toolset, allow_write=True)
        manager = getattr(server, "_tool_manager", None)
        tools = getattr(manager, "_tools", {}) if manager is not None else {}
        override = tools["override_path"]
        result = override.fn("D:\\apps\\x.exe", "no-such-category")
        self.assertFalse(result["ok"])
        self.assertIn("game", result["valid_categories"])

    def test_server_exposes_write_tools_when_allowed(self) -> None:
        from nekotagger.mcp_server import create_server

        server = create_server(toolset=self.toolset, allow_write=True)
        tools = _tool_names(server)
        self.assertIn("override_path", tools)
        self.assertIn("export_results", tools)
        self.assertIn("submit_classifications", tools)


def _tool_names(server) -> list[str]:
    """取已注册工具名；兼容 SDK 1.x / 2.x 的内部结构。"""

    manager = getattr(server, "_tool_manager", None)
    if manager is not None:
        tools = getattr(manager, "_tools", None)
        if isinstance(tools, dict):
            return list(tools)
    tools = getattr(server, "_tools", None)
    if isinstance(tools, dict):
        return list(tools)
    import asyncio

    listed = asyncio.run(server.list_tools())
    return [getattr(tool, "name", str(tool)) for tool in listed]


if __name__ == "__main__":
    unittest.main()
