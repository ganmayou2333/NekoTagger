"""MCP 服务的真实 stdio 端到端测试。

起一个 ``python -m nekotagger mcp`` 子进程，说真正的 JSON-RPC：
``initialize`` → ``notifications/initialized`` → ``tools/list`` → ``tools/call``。
这一步能抓到「进程内调用没事、但协议层挂了」的问题。
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import mcp  # noqa: F401

    HAVE_MCP = True
except ImportError:  # pragma: no cover
    HAVE_MCP = False

REPO_ROOT = Path(__file__).resolve().parent.parent


class StdioClient:
    def __init__(self, cache: Path, overrides: Path, *, allow_write: bool = False) -> None:
        argv = [
            sys.executable, "-m", "nekotagger", "mcp",
            "--cache", str(cache),
            "--overrides", str(overrides),
        ]
        if allow_write:
            argv.append("--allow-write")
        self.process = subprocess.Popen(
            argv,
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._next_id = 0
        self._handshake()

    # ---------------------------------------------------------------- wire

    def _send(self, payload: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def _recv(self) -> dict:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read()[:400] if self.process.stderr else ""
            raise AssertionError(f"MCP 服务提前退出：{stderr}")
        return json.loads(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}})
        return self._recv()

    def _handshake(self) -> None:
        response = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "nekotagger-tests", "version": "0"},
        })
        assert "result" in response, response
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def tool_names(self) -> list[str]:
        response = self.request("tools/list")
        return [tool["name"] for tool in response["result"]["tools"]]

    def call(self, name: str, arguments: dict) -> dict:
        response = self.request("tools/call", {"name": name, "arguments": arguments})
        result = response["result"]
        if result.get("isError"):
            raise AssertionError(result)
        return json.loads(result["content"][0]["text"])

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.process.kill()
            self.process.wait(timeout=10)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


@unittest.skipUnless(HAVE_MCP, "没有安装 mcp SDK")
class McpStdioTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cache = self.root / "cache.json"
        self.overrides = self.root / "overrides.json"

    def _client(self, *, allow_write: bool = False) -> StdioClient:
        client = StdioClient(self.cache, self.overrides, allow_write=allow_write)
        self.addCleanup(client.close)
        return client

    def test_readonly_server_lists_only_read_tools(self) -> None:
        client = self._client()
        self.assertEqual(
            set(client.tool_names()),
            {"classify_path", "classify_many", "list_categories", "cache_stats", "pending_items"},
        )

    def test_classify_over_protocol(self) -> None:
        client = self._client()
        result = client.call("classify_path", {"path": r"C:\Windows\System32\openwith.exe"})
        self.assertEqual(result["category"], "tool")
        self.assertEqual(result["source"], "rule")

    def test_write_server_can_override_and_it_takes_effect(self) -> None:
        client = self._client(allow_write=True)
        self.assertIn("override_path", client.tool_names())

        exe = self.root / "apps" / "odd.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ")

        written = client.call("override_path", {"path": str(exe), "category": "dev"})
        self.assertEqual(written["category"], "dev")

        after = client.call("classify_path", {"path": str(exe)})
        self.assertEqual(after["source"], "override")
        self.assertEqual(after["category"], "dev")

    def test_unknown_category_is_an_error_not_a_crash(self) -> None:
        client = self._client(allow_write=True)
        exe = self.root / "apps" / "x.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ")
        response = client.request("tools/call", {
            "name": "override_path",
            "arguments": {"path": str(exe), "category": "no-such-category"},
        })
        result = response["result"]
        self.assertTrue(result.get("isError") or "未知类别" in json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
