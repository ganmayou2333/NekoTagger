"""规则层与契约的离线单测。

只用标准库 unittest，不联网、不碰真实 NekoTrak 数据。
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from nekotagger import __main__ as cli
from nekotagger.categories import CategoryConfigError, load_categories
from nekotagger.classify import CONTRACT_KEYS, classify
from nekotagger.normalize import exe_basename, normalize_exe_path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = load_categories(REPO_ROOT / "categories.json")


def _write_categories(document: object) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    with handle as stream:
        json.dump(document, stream, ensure_ascii=False)
    return Path(handle.name)


class NormalizeTests(unittest.TestCase):
    def test_quotes_slashes_case_and_trailing_space(self) -> None:
        self.assertEqual(
            normalize_exe_path('  "C:/Program Files/App/APP.EXE"  '),
            r"c:\program files\app\app.exe",
        )

    def test_duplicate_separators_collapse(self) -> None:
        self.assertEqual(normalize_exe_path(r"c:\\a\\\b.exe"), r"c:\a\b.exe")

    def test_unc_prefix_is_preserved(self) -> None:
        self.assertEqual(normalize_exe_path(r"\\server\\share\\x.exe"), r"\\server\share\x.exe")

    def test_empty_input_is_rejected(self) -> None:
        for value in ("", "   ", '""', None, 42):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_exe_path(value)

    def test_basename(self) -> None:
        self.assertEqual(exe_basename(r"c:\a\b\app.exe"), "app.exe")


class RuleTests(unittest.TestCase):
    def test_system_path_maps_to_tool_and_is_certain(self) -> None:
        result = classify(r"C:\Windows\System32\openwith.exe", categories=CATEGORIES, now=1)
        self.assertEqual(result["category"], "tool")
        self.assertEqual(result["source"], "rule")
        self.assertEqual(result["confidence"], "certain")
        self.assertIn("c:\\windows\\", result["matched_by"])
        self.assertEqual(result["classified_at"], 1)

    def test_installer_maps_to_tool(self) -> None:
        result = classify(r"D:\downloads\setup.exe", categories=CATEGORIES, now=0)
        self.assertEqual(result["category"], "tool")
        self.assertEqual(result["confidence"], "certain")

    def test_browser_name_maps_to_browse_but_only_likely(self) -> None:
        result = classify(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         categories=CATEGORIES, now=0)
        self.assertEqual(result["category"], "browse")
        self.assertEqual(result["source"], "rule")
        self.assertEqual(result["confidence"], "likely")
        self.assertIn("msedge", result["matched_by"])

    def test_directory_name_can_be_the_only_clue(self) -> None:
        # hipsmain.exe tells you nothing; the directory does. Matching the full
        # path is why this is a rule hit instead of an unknown.
        result = classify(r"e:\huorong\sysdiag\bin\hipsmain.exe", categories=CATEGORIES, now=0)
        self.assertEqual(result["category"], "tool")
        self.assertEqual(result["source"], "rule")
        self.assertIn("path: contains 'huorong'", result["matched_by"])

    def test_unknown_falls_back_to_uncategorized(self) -> None:
        result = classify(r"D:\some\strange\thing.exe", categories=CATEGORIES, now=0)
        self.assertEqual(result["category"], "uncategorized")
        self.assertEqual(result["source"], "unknown")
        self.assertEqual(result["confidence"], "guess")

    def test_override_wins_over_every_rule(self) -> None:
        path = r"C:\Windows\notepad.exe"
        result = classify(
            path,
            categories=CATEGORIES,
            overrides={normalize_exe_path(path): "dev"},
            now=0,
        )
        self.assertEqual(result["category"], "dev")
        self.assertEqual(result["source"], "override")
        self.assertEqual(result["confidence"], "certain")

    def test_result_has_exactly_the_contract_keys(self) -> None:
        result = classify(r"C:\Windows\System32\openwith.exe", categories=CATEGORIES, now=0)
        self.assertEqual(tuple(result), CONTRACT_KEYS)

    def test_classification_is_deterministic(self) -> None:
        first = classify(r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                         categories=CATEGORIES, now=123)
        second = classify('"C:/Program Files/Google/Chrome/Application/CHROME.exe"',
                          categories=CATEGORIES, now=123)
        self.assertEqual(first, second)


class CategoryConfigTests(unittest.TestCase):
    def test_shipped_categories_load(self) -> None:
        self.assertIn("uncategorized", CATEGORIES)
        self.assertGreaterEqual(len(CATEGORIES), 8)
        self.assertEqual(CATEGORIES.fallback.id, "uncategorized")

    def test_missing_file_is_a_readable_error(self) -> None:
        with self.assertRaises(CategoryConfigError) as caught:
            load_categories(REPO_ROOT / "does-not-exist.json")
        self.assertIn("找不到类别清单", str(caught.exception))

    def test_invalid_json_is_a_readable_error(self) -> None:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        with handle as stream:
            stream.write("{not json")
        with self.assertRaises(CategoryConfigError) as caught:
            load_categories(handle.name)
        self.assertIn("不是合法 JSON", str(caught.exception))
        Path(handle.name).unlink(missing_ok=True)

    def test_missing_fallback_category_is_rejected(self) -> None:
        path = _write_categories({"version": 1, "categories": [{"id": "dev", "label": "开发"}]})
        with self.assertRaises(CategoryConfigError) as caught:
            load_categories(path)
        self.assertIn("uncategorized", str(caught.exception))
        path.unlink(missing_ok=True)

    def test_duplicate_ids_are_rejected(self) -> None:
        path = _write_categories({
            "version": 1,
            "categories": [
                {"id": "uncategorized", "label": "未分类"},
                {"id": "dev", "label": "开发"},
                {"id": "dev", "label": "又一个开发"},
            ],
        })
        with self.assertRaises(CategoryConfigError) as caught:
            load_categories(path)
        self.assertIn("重复", str(caught.exception))
        path.unlink(missing_ok=True)


class CliTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_json_output_is_the_contract(self) -> None:
        code, out, err = self._run(
            ["classify", r"C:\Windows\System32\openwith.exe", "--json"]
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(tuple(payload), CONTRACT_KEYS)
        self.assertEqual(payload["category"], "tool")

    def test_human_output_has_category_and_source(self) -> None:
        code, out, err = self._run(["classify", r"C:\Windows\win.ini.exe"])
        self.assertEqual(code, 0, err)
        self.assertIn("tool", out)
        self.assertIn("rule/certain", out)

    def test_missing_categories_file_exits_non_zero(self) -> None:
        code, _, err = self._run(
            ["classify", r"C:\x.exe", "--categories", str(REPO_ROOT / "nope.json")]
        )
        self.assertEqual(code, 2)
        self.assertIn("找不到类别清单", err)

    def test_empty_path_exits_non_zero(self) -> None:
        code, _, err = self._run(["classify", "   "])
        self.assertEqual(code, 2)
        self.assertIn("路径无效", err)


if __name__ == "__main__":
    unittest.main()
