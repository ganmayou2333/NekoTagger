"""CLI 端到端测试：全部指向临时目录，不碰真实缓存与配置。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from nekotagger.__main__ import main

REPO_ROOT = Path(__file__).resolve().parent.parent


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cache = self.root / "cache.json"
        self.overrides = self.root / "overrides.json"

    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def common(self) -> list[str]:
        return [
            "--cache", str(self.cache),
            "--overrides", str(self.overrides),
            "--nekotrak-root", str(self.root / "no-nekotrak"),
        ]

    def touch_exe(self, name: str) -> Path:
        target = self.root / "apps" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"MZ")
        return target


class ClassifyCliTests(CliCase):
    def test_override_wins_and_is_reported(self) -> None:
        exe = self.touch_exe("thing.exe")
        self.overrides.write_text(json.dumps({str(exe): "dev"}), encoding="utf-8")
        code, out, err = self.run_cli(["classify", str(exe), "--json", *self.common()])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["category"], "dev")
        self.assertEqual(payload["source"], "override")

    def test_unknown_path_goes_to_pending(self) -> None:
        exe = self.touch_exe("mystery-application.exe")
        code, out, err = self.run_cli(["classify", str(exe), *self.common()])
        self.assertEqual(code, 0, err)
        self.assertIn("uncategorized", out)
        cache = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(len(cache["pending"]), 1)

    def test_bad_categories_file_exits_two(self) -> None:
        code, _, err = self.run_cli([
            "classify", r"C:\x.exe",
            "--cache", str(self.cache),
            "--categories", str(REPO_ROOT / "no-such.json"),
        ])
        self.assertEqual(code, 2)
        self.assertIn("找不到类别清单", err)


class CorpusCliTests(CliCase):
    def _paths_file(self, paths: list[Path]) -> Path:
        listing = self.root / "paths.txt"
        listing.write_text("\n".join(str(path) for path in paths), encoding="utf-8")
        return listing

    def test_corpus_without_ai_reports_counts(self) -> None:
        first = self.touch_exe("edge-case.exe")
        second = self.touch_exe("setup_helper.exe")  # 安装器，应被跳过
        listing = self._paths_file([first, second, self.root / "missing.exe"])
        code, out, err = self.run_cli([
            "corpus", "--paths-from", str(listing), "--no-ai", *self.common(),
        ])
        self.assertEqual(code, 0, err)
        self.assertIn("语料：", out)
        self.assertIn("skipped_installer=1", out)
        self.assertIn("AI：", out)

    def test_show_and_export_after_corpus(self) -> None:
        exe = self.touch_exe("some-app.exe")
        listing = self._paths_file([exe])
        self.run_cli(["corpus", "--paths-from", str(listing), "--no-ai", *self.common()])

        code, out, err = self.run_cli(["show", "--cache", str(self.cache),
                                       "--nekotrak-root", str(self.root / "no-nekotrak")])
        self.assertEqual(code, 0, err)
        self.assertIn("条目：", out)

        target = self.root / "export.json"
        code, out, err = self.run_cli(["export", "--out", str(target), *self.common()])
        self.assertEqual(code, 0, err)
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("entries", document)
        self.assertEqual(document["count"], len(document["entries"]))

    def test_export_source_filter(self) -> None:
        exe = self.touch_exe("another-app.exe")
        listing = self._paths_file([exe])
        self.run_cli(["corpus", "--paths-from", str(listing), "--no-ai", *self.common()])
        target = self.root / "ai-only.json"
        code, _, err = self.run_cli(["export", "--out", str(target), "--source", "ai", *self.common()])
        self.assertEqual(code, 0, err)
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["count"], 0)


class OverrideCliTests(CliCase):
    def test_override_command_writes_normalized_key(self) -> None:
        exe = self.touch_exe("to-override.exe")
        code, out, err = self.run_cli(["override", str(exe).upper(), "dev", *self.common()])
        self.assertEqual(code, 0, err)
        document = json.loads(self.overrides.read_text(encoding="utf-8"))
        self.assertEqual(len(document), 1)
        key = next(iter(document))
        self.assertEqual(key, key.lower())
        self.assertEqual(document[key], "dev")

    def test_unknown_category_is_rejected(self) -> None:
        exe = self.touch_exe("x.exe")
        code, _, err = self.run_cli(["override", str(exe), "no-such-category", *self.common()])
        self.assertEqual(code, 2)
        self.assertIn("未知类别", err)


if __name__ == "__main__":
    unittest.main()
