"""缓存与 AI 后端的离线单测。

AI 部分用本地桩 HTTP 服务（``http.server``），不联网、不依赖真实模型。
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from nekotagger.ai import (
    AiBatchItem,
    AiConfig,
    HttpBackend,
    StubBackend,
    build_messages,
    load_ai_config,
    validate_results,
    _extract_json,
)
from nekotagger.cache import CACHE_VERSION, MAX_PENDING_ATTEMPTS, Cache
from nekotagger.categories import load_categories
from nekotagger.normalize import normalize_exe_path
from nekotagger.pipeline import run_corpus
from nekotagger.seeds import load_seeds

REPO_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = load_categories(REPO_ROOT / "categories.json")
PATH_A = normalize_exe_path(r"D:\apps\a.exe")
PATH_B = normalize_exe_path(r"D:\apps\b.exe")


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class CacheTests(TempDirCase):
    def test_roundtrip_and_atomic_write(self) -> None:
        path = self.root / "cache.json"
        cache = Cache.load(path)
        self.assertEqual(cache.entries, {})
        cache.put(PATH_A, {"category": "tool", "confidence": "certain",
                           "source": "rule", "matched_by": "x", "classified_at": 1})
        cache.save()

        self.assertFalse(path.with_name(path.name + ".tmp").exists())
        reloaded = Cache.load(path)
        self.assertEqual(reloaded.get(PATH_A)["category"], "tool")

    def test_corrupt_file_degrades_with_warning(self) -> None:
        path = self.root / "cache.json"
        path.write_text("{not json", encoding="utf-8")
        cache = Cache.load(path)
        self.assertEqual(cache.entries, {})
        self.assertTrue(cache.warnings)

    def test_unknown_version_degrades(self) -> None:
        path = self.root / "cache.json"
        path.write_text(json.dumps({"version": 99, "entries": {PATH_A: {"category": "dev"}}}),
                        encoding="utf-8")
        cache = Cache.load(path)
        self.assertEqual(cache.entries, {})
        self.assertTrue(any("版本" in warning for warning in cache.warnings))

    def test_pending_cap(self) -> None:
        cache = Cache.load(self.root / "cache.json")
        for _ in range(MAX_PENDING_ATTEMPTS):
            self.assertTrue(cache.is_pending_eligible(PATH_A))
            cache.mark_pending(PATH_A, now=1)
        self.assertFalse(cache.is_pending_eligible(PATH_A))

    def test_only_entries_with_removed_category_are_invalidated(self) -> None:
        path = self.root / "cache.json"
        path.write_text(json.dumps({
            "version": CACHE_VERSION,
            "entries": {
                PATH_A: {"category": "dev", "confidence": "certain", "source": "seed", "matched_by": "x"},
                PATH_B: {"category": "game", "confidence": "likely", "source": "steam", "matched_by": "y"},
            },
            "pending": {},
        }), encoding="utf-8")

        limited = self.root / "categories-min.json"
        limited.write_text(json.dumps({
            "version": 1,
            "categories": [
                {"id": "dev", "label": "开发", "patterns": []},
                {"id": "uncategorized", "label": "未分类", "patterns": []},
            ],
        }), encoding="utf-8")

        cache = Cache.load(path, load_categories(limited))
        self.assertIn(PATH_A, cache.entries)  # dev 还在
        self.assertNotIn(PATH_B, cache.entries)  # game 被删了
        self.assertIn(PATH_B, cache.pending)
        self.assertTrue(any("game" in warning for warning in cache.warnings))


class AiConfigTests(TempDirCase):
    def test_env_overrides_file(self) -> None:
        path = self.root / "config.local.json"
        path.write_text(json.dumps({"api_key": "from-file", "model": "m1"}), encoding="utf-8")
        import os
        os.environ["NEKOTAGGER_API_KEY"] = "from-env"
        self.addCleanup(os.environ.pop, "NEKOTAGGER_API_KEY", None)
        config = load_ai_config(path)
        self.assertEqual(config.api_key, "from-env")
        self.assertEqual(config.model, "m1")

    def test_missing_file_is_not_an_error(self) -> None:
        config = load_ai_config(self.root / "nope.json")
        self.assertFalse(config.usable)


class PromptTests(TempDirCase):
    def test_prompt_contains_paths_and_hints_but_no_window_title(self) -> None:
        seeds = load_seeds(REPO_ROOT / "data/seed-categories.json", CATEGORIES)
        items = [AiBatchItem(path=PATH_A, display="Visual Studio Code")]
        messages = build_messages(items, CATEGORIES, seeds)
        body = "\n".join(message["content"] for message in messages)
        self.assertIn(PATH_A, body)
        self.assertIn("Visual Studio Code", body)
        self.assertIn("game", body)  # 类别菜单
        hint_lines = [line for line in body.splitlines() if "=>" in line]
        self.assertLessEqual(len(hint_lines), 20)
        # 隐私回归：提示词里不允许出现这些字段名或典型窗口标题内容
        for forbidden in ("窗口标题", "window title", "用户名", "username"):
            self.assertNotIn(forbidden, body)


class ValidationTests(unittest.TestCase):
    def test_invalid_category_is_rejected(self) -> None:
        raw = {"results": [
            {"path": PATH_A, "category": "game", "confidence": "likely", "reason": "x"},
            {"path": PATH_B, "category": "invented-category", "confidence": "likely", "reason": "y"},
        ]}
        accepted, rejected = validate_results(raw, CATEGORIES, [PATH_A, PATH_B])
        self.assertIn(PATH_A, accepted)
        self.assertIn(PATH_B, rejected)

    def test_json_extraction_tolerates_fences(self) -> None:
        payload = '```json\n{"results": []}\n```'
        self.assertEqual(_extract_json(payload), {"results": []})


class _StubHandler(BaseHTTPRequestHandler):
    payload: dict = {"results": []}
    seen: list = []

    def do_POST(self) -> None:  # noqa: N802 - http.server 接口
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append(body)
        content = json.dumps(type(self).payload)
        response = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *args) -> None:  # 静音
        return


class HttpBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        _StubHandler.seen = []
        _StubHandler.payload = {"results": []}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def test_real_http_roundtrip_accepts_valid_category(self) -> None:
        _StubHandler.payload = {"results": [
            {"path": PATH_A, "category": "dev", "confidence": "likely", "reason": "looks like an IDE"}
        ]}
        backend = HttpBackend(AiConfig(base_url=self.base_url, model="test", api_key="k"), timeout=5)
        result = backend.classify([AiBatchItem(PATH_A, "code.exe")], CATEGORIES, None)
        self.assertEqual(result[PATH_A]["category"], "dev")
        self.assertEqual(len(_StubHandler.seen), 1)
        sent = json.dumps(_StubHandler.seen[0], ensure_ascii=False)
        self.assertIn("a.exe", sent)  # 路径被 JSON 转义，断言文件名更稳


class PipelineTests(TempDirCase):
    def _categories(self):
        return CATEGORIES

    def test_batches_are_counted_and_results_cached(self) -> None:
        cache = Cache.load(self.root / "cache.json")
        paths = [f"D:\\apps\\{index}.exe" for index in range(45)]
        backend = StubBackend({normalize_exe_path(p): {"category": "tool", "confidence": "likely", "reason": "r"} for p in paths})
        run = run_corpus(paths, categories=CATEGORIES, cache=cache, backend=backend,
                         batch_size=20, info_reader=lambda path: None)
        self.assertEqual(run.ai_batches, 3)
        self.assertEqual(len(backend.calls), 3)
        self.assertEqual(run.by_source["ai"], 45)
        self.assertEqual(len(cache.entries), 45)
        stored = next(iter(cache.entries.values()))
        self.assertEqual(stored["source"], "ai")
        self.assertEqual(stored["matched_by"], "ai: r")

    def test_second_run_does_not_call_the_model(self) -> None:
        cache = Cache.load(self.root / "cache.json")
        path = r"D:\apps\mystery.exe"
        normalized = normalize_exe_path(path)
        first = StubBackend({normalized: {"category": "tool", "confidence": "likely", "reason": "r"}})
        run_corpus([path], categories=CATEGORIES, cache=cache, backend=first,
                   info_reader=lambda _p: None)
        self.assertEqual(len(first.calls), 1)

        second = StubBackend({})
        run = run_corpus([path], categories=CATEGORIES, cache=cache, backend=second,
                         info_reader=lambda _p: None)
        self.assertEqual(second.calls, [])
        self.assertEqual(run.by_source["cache"], 1)

    def test_failure_marks_pending_and_does_not_crash(self) -> None:
        cache = Cache.load(self.root / "cache.json")
        backend = StubBackend(fail=True)
        run = run_corpus([r"D:\apps\x.exe"], categories=CATEGORIES, cache=cache, backend=backend,
                         info_reader=lambda _p: None)
        self.assertEqual(run.ai_failures, 1)
        self.assertEqual(run.by_source["unknown"], 1)
        self.assertEqual(len(cache.pending), 1)

    def test_max_batches_limits_requests(self) -> None:
        cache = Cache.load(self.root / "cache.json")
        paths = [f"D:\\apps\\{index}.exe" for index in range(45)]
        backend = StubBackend({normalize_exe_path(p): {"category": "tool"} for p in paths})
        run = run_corpus(paths, categories=CATEGORIES, cache=cache, backend=backend,
                         batch_size=20, max_batches=1, info_reader=lambda _p: None)
        self.assertEqual(run.ai_batches, 1)
        self.assertEqual(run.by_source["unknown"], 25)


if __name__ == "__main__":
    unittest.main()
