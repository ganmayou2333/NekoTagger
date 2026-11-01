"""数据层（语料 / 种子 / Steam / override）的离线单测。

全部使用临时目录与注入的版本信息读取器，不依赖本机真实应用。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from nekotagger.categories import load_categories
from nekotagger.classify import classify
from nekotagger.corpus import load_corpus
from nekotagger.normalize import normalize_exe_path
from nekotagger.overrides import OverrideDataError, load_overrides
from nekotagger.seeds import SeedDataError, load_seeds, normalize_display_name, tokenize
from nekotagger.steam import SteamDataError, load_steam_index
from nekotagger.wininfo import VersionInfo, read_version_info

REPO_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = load_categories(REPO_ROOT / "categories.json")


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, relative: str, payload: object) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return target

    def touch_exe(self, relative: str) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"MZ")  # 内容无所谓，只要求 .exe 文件存在
        return target

    def seed_file(self, categories: dict[str, list[str]]) -> Path:
        document = {
            "_meta": {
                "harvestedAt": "2026-11-01T12:19:34",
                "categories": {
                    label: {"count": len(names), "names": names}
                    for label, names in categories.items()
                },
            }
        }
        return self.write("seed.json", document)


class SeedLoadingTests(TempDirCase):
    def test_generic_names_are_never_usable(self) -> None:
        path = self.seed_file({"未分类": ["Microsoft", "Windows"]})
        index = load_seeds(path, CATEGORIES)
        self.assertEqual(index.match("Microsoft"), None)
        self.assertEqual(index.match("Windows 11"), None)

    def test_same_name_in_two_categories_is_ambiguous(self) -> None:
        path = self.seed_file({
            "开发": ["Cursor"],
            "工具": ["Cursor"],
            "未分类": ["QQ"],
        })
        index = load_seeds(path, CATEGORIES)
        self.assertIn(normalize_display_name("Cursor"), index.ambiguous)
        self.assertIsNone(index.match("Cursor"))
        self.assertIsNotNone(index.match("QQ"))

    def test_exact_and_word_boundary(self) -> None:
        path = self.seed_file({"开发": ["OBS Studio"], "通讯": ["QQ"]})
        index = load_seeds(path, CATEGORIES)
        exact = index.match("OBS Studio")
        self.assertIsNotNone(exact)
        self.assertEqual(exact.kind, "exact")
        word = index.match("OBS Studio 64")
        self.assertIsNotNone(word)
        self.assertEqual(word.kind, "word")
        # 子串不是词边界：`OBSStudioPro` 不能算命中。
        self.assertIsNone(index.match("OBSStudioPro"))

    def test_cjk_seed_is_exact_only(self) -> None:
        # 「INF 默认安装」切词后只剩 ("inf",)，不许靠它去撞路径里的 inf。
        path = self.seed_file({"未分类": ["INF 默认安装"]})
        index = load_seeds(path, CATEGORIES)
        self.assertIsNone(index.match("oem1.inf_amd64"))
        self.assertIsNotNone(index.match("INF 默认安装"))

    def test_short_single_token_seed_is_not_word_matched(self) -> None:
        # 3 字符以下的单 token 种子只允许精确匹配；4 字符（如 Code）仍然可以词边界。
        path = self.seed_file({"工具": ["INF"], "开发": ["Code"]})
        index = load_seeds(path, CATEGORIES)
        self.assertIsNone(index.match("some-inf-file"))
        self.assertIsNotNone(index.match("INF"))
        self.assertIsNotNone(index.match("Visual Studio Code 64"))

    def test_unknown_category_label_is_skipped(self) -> None:
        path = self.seed_file({"不存在的类别": ["QQ"], "通讯": ["WeChat"]})
        index = load_seeds(path, CATEGORIES)
        self.assertIsNotNone(index.match("WeChat"))
        self.assertIsNone(index.match("QQ"))

    def test_missing_seed_file_is_a_readable_error(self) -> None:
        with self.assertRaises(SeedDataError) as caught:
            load_seeds(REPO_ROOT / "nope.json", CATEGORIES)
        self.assertIn("找不到种子文件", str(caught.exception))


class DisplayNameNormalizationTests(unittest.TestCase):
    def test_strips_brackets_and_version(self) -> None:
        self.assertEqual(normalize_display_name("Foo (x64)"), "foo")
        self.assertEqual(normalize_display_name("Bar v1.2.3"), "bar")
        self.assertEqual(normalize_display_name("Ｂaz 1.2.3.4"), "baz")

    def test_tokenize_splits_latin_only(self) -> None:
        self.assertEqual(tokenize("OBS Studio 64"), ("obs", "studio", "64"))
        self.assertEqual(tokenize("三角洲行动"), ())


class CorpusTests(TempDirCase):
    def _root_with_app_icons(self, entries: dict[str, object]) -> Path:
        for name, document in entries.items():
            self.write(f"nekotrak/caches/app-icons/{name}.json", document)
        return self.root / "nekotrak"

    def test_filters_and_counts_every_reason(self) -> None:
        existing = self.touch_exe("apps/good.exe")
        installer = self.touch_exe("apps/setup_good.exe")
        root = self._root_with_app_icons({
            "a": {"cache_format_version": 1, "normalized_exe_path": str(existing)},
            "b": {"cache_format_version": 1, "normalized_exe_path": str(self.root / "gone.exe")},
            "c": {"cache_format_version": 1, "normalized_exe_path": str(self.root / "x.tmp")},
            "d": {"cache_format_version": 1, "normalized_exe_path": str(installer)},
            "e": {"cache_format_version": 1, "normalized_exe_path": str(existing)},
            "f": {"cache_format_version": 99, "normalized_exe_path": str(existing)},
        })
        corpus = load_corpus(root)
        self.assertEqual(len(corpus.paths), 1)
        self.assertEqual(len(corpus.skipped_missing), 1)
        self.assertEqual(len(corpus.skipped_non_exe), 1)
        self.assertEqual(len(corpus.skipped_installer), 1)
        self.assertEqual(corpus.duplicates, 1)
        self.assertTrue(any("cache_format_version" in warning for warning in corpus.warnings))

    def test_paths_from_is_merged_and_deduplicated(self) -> None:
        first = self.touch_exe("apps/one.exe")
        second = self.touch_exe("apps/two.exe")
        root = self._root_with_app_icons({
            "a": {"cache_format_version": 1, "normalized_exe_path": str(first)},
        })
        listing = self.write("extra.txt", f"# comment\n{second}\n{first}\n")
        corpus = load_corpus(root, paths_from=listing)
        self.assertEqual(set(corpus.paths), {normalize_exe_path(first), normalize_exe_path(second)})
        self.assertEqual(corpus.sources.get("paths_from"), 2)
        self.assertEqual(corpus.duplicates, 1)

    def test_missing_cache_directory_degrades(self) -> None:
        corpus = load_corpus(self.root / "no-such-nekotrak")
        self.assertEqual(corpus.paths, ())
        self.assertTrue(corpus.warnings)


class SteamTests(TempDirCase):
    def _index(self) -> Path:
        return self.write("steam.json", {
            "apps": [
                {"appid": 1, "name": "Outer Game", "installDir": r"D:\SteamLibrary\steamapps\common\Game"},
                {"appid": 2, "name": "Inner Game",
                 "installDir": r"D:\SteamLibrary\steamapps\common\Game\DLC"},
            ]
        })

    def test_longest_install_dir_wins(self) -> None:
        index = load_steam_index(self._index())
        inner = index.match(normalize_exe_path(r"D:\SteamLibrary\steamapps\common\Game\DLC\bin\g.exe"))
        self.assertIsNotNone(inner)
        self.assertEqual(inner.appid, 2)
        outer = index.match(normalize_exe_path(r"D:\SteamLibrary\steamapps\common\Game\bin\g.exe"))
        self.assertEqual(outer.appid, 1)
        self.assertIsNone(index.match(normalize_exe_path(r"D:\other\g.exe")))

    def test_missing_index_is_a_readable_error(self) -> None:
        with self.assertRaises(SteamDataError):
            load_steam_index(self.root / "nope.json")


class OverrideTests(TempDirCase):
    def test_keys_are_normalized_and_precedence_is_highest(self) -> None:
        exe = self.touch_exe("apps/thing.exe")
        overrides_path = self.write(
            "overrides.json", {str(exe).upper(): "dev"}
        )
        overrides = load_overrides(overrides_path)
        self.assertIn(normalize_exe_path(str(exe)), overrides)

        result = classify(exe, categories=CATEGORIES, overrides=overrides, now=0)
        self.assertEqual(result["source"], "override")
        self.assertEqual(result["category"], "dev")

    def test_missing_file_is_empty_and_invalid_json_raises(self) -> None:
        self.assertEqual(load_overrides(self.root / "none.json"), {})
        broken = self.write("broken.json", "{nope")
        with self.assertRaises(OverrideDataError):
            load_overrides(broken)


class SeedIntegrationTests(TempDirCase):
    def test_seed_hit_beats_rule_and_reports_certain(self) -> None:
        exe = self.touch_exe("apps/qq.exe")
        index = load_seeds(self.seed_file({"通讯": ["QQ"]}), CATEGORIES)

        # 注入的 reader 模拟版本信息里的 FileDescription。
        result = classify(
            exe,
            categories=CATEGORIES,
            seeds=index,
            info_reader=lambda path: VersionInfo(file_description="QQ", product_name="QQ"),
            now=0,
        )
        self.assertEqual(result["category"], "comm")
        self.assertEqual(result["source"], "seed")
        self.assertEqual(result["confidence"], "certain")
        self.assertIn("seed[exact]", result["matched_by"])

    def test_seed_misses_fall_through_to_rule(self) -> None:
        exe = self.touch_exe("apps/whatever.exe")
        index = load_seeds(self.seed_file({"通讯": ["QQ"]}), CATEGORIES)
        result = classify(
            exe,
            categories=CATEGORIES,
            seeds=index,
            info_reader=lambda path: VersionInfo(file_description="Unrelated", product_name=None),
            now=0,
        )
        self.assertEqual(result["source"], "unknown")

    def test_steam_layer_is_used_when_nothing_else_matches(self) -> None:
        # 路径里故意不含 "steam"：否则规则层的 game pattern 会先命中，
        # 这个测试就测不到 Steam 层了。
        exe = self.touch_exe("library/common/SomeGame/bin/game.exe")
        steam = load_steam_index(self.write("steam.json", {
            "apps": [{"appid": 42, "name": "Some Game",
                      "installDir": str(self.root / "library/common/SomeGame")}],
        }))
        result = classify(exe, categories=CATEGORIES, steam=steam, now=0)
        self.assertEqual(result["category"], "game")
        self.assertEqual(result["source"], "steam")
        self.assertIn("42", result["matched_by"])


class VersionInfoSmokeTests(unittest.TestCase):
    def test_reading_a_real_system_exe_never_raises(self) -> None:
        if not sys.platform.startswith("win"):
            self.skipTest("Windows only")
        target = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "cmd.exe"
        info = read_version_info(target)
        # 版本资源可能缺失；重要的是不抛异常，且缺失时返回 None。
        self.assertTrue(info is None or isinstance(info, VersionInfo))

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(read_version_info(r"C:\definitely\not\here.exe"))


if __name__ == "__main__":
    unittest.main()
