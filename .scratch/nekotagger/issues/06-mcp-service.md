# 06: MCP 服务

**What to build:** 把分类器包成 MCP 服务，让 agent / 客户端直接调用，而不是 shell 出去。

- 实现：官方 Python SDK（`mcp` 2.x）。注意 2.x 把 `FastMCP` 改名成 `mcp.server.mcpserver.MCPServer`，代码里做了一代兼容。
- 传输：stdio。stdout 归协议所有，提示/警告只走 stderr。
- 工具：
  - 只读：`classify_path` / `classify_many` / `list_categories` / `cache_stats`
  - 需 `--allow-write`：`override_path` / `export_results`
- 业务实现在 `Toolset` 里，MCP 只是注册壳——不装 SDK 也能测业务。
- 缓存并发：`cache.save()` / `Cache.load()` 走 `locking.file_lock`（Windows `msvcrt.locking`，其他平台 `fcntl.flock`），超时抛 `LockTimeout` 而不是静默丢数据。

**Blocked by:** 05

**Status:** done（2026-11-01）

- [x] `python -m nekotagger mcp` 起得来，`initialize` 握手成功（协议版本 2025-06-18）
- [x] `tools/list` 只读模式只返回 4 个只读工具；加 `--allow-write` 后 6 个
- [x] `tools/call classify_path` 返回契约结构（走真实 stdio）
- [x] `override_path` 写入后，同一进程内 `classify_path` 立刻返回 `source:"override"`
- [x] 未知类别返回工具错误而不是让进程崩
- [x] 缓存加跨进程锁；MCP 与 CLI 同时写不互相覆盖
- [x] `pyproject.toml` 里有 `[project.optional-dependencies] mcp` 与 `nekotagger-mcp` 入口
- [x] 全离线测试：82 项，其中 4 项是真实 stdio 端到端

## Comments

**2026-11-01 — 完成。**

踩到两个坑，记录一下：

1. **`pip install mcp` 超时**：第一次在前台跑，5 分钟没装完（pywin32 7 MB 下载只有 35 kB/s）。改成后台任务后装完——依赖树不小（pydantic / starlette / uvicorn / cryptography / pywin32）。
2. **SDK 2.x 的 API 变了**：`from mcp.server.fastmcp import FastMCP` 直接报错并提示「FastMCP renamed to MCPServer」。改成优先 `mcp.server.mcpserver.MCPServer`、失败再回退 `FastMCP`，`run()` 也按 `transport="stdio"` 优先。

实测（stdio JSON-RPC）：握手 OK、只读模式 4 个工具、写模式 6 个工具、`override_path` 后 `classify_path` 立刻变成 `source:"override"`、未知类别走工具错误。
