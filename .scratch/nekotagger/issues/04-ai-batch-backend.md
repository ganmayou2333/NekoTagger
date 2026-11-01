# 04: AI 批量分类（OpenAI 兼容 HTTP 后端）

**What to build:** 规则、种子、Steam 都判不出来的应用交给模型；**批量问，不许它发明类别，失败不重试**。

- 配置：`%LOCALAPPDATA%\NekoTagger\config.local.json` 里的 `base_url` / `model` / `api_key`；环境变量 `NEKOTAGGER_API_KEY`（及 `NEKOTAGGER_BASE_URL` / `NEKOTAGGER_MODEL`）优先覆盖。未配置 key 时跳过 AI 阶段并明确提示，不报错退出。
- 后端：OpenAI 兼容 `chat/completions`，只用 stdlib `urllib`，不引新依赖。
- 批量：默认 20 条/批；`--max-batches N` 手动兜底（默认不限）。**不重试**：超时（默认 10 秒）或 HTTP 失败 → 该批全部进 `pending`，不阻塞其它批、不抛未捕获异常。
- 返回要求：每项 `{path, category, confidence, reason}`；**category 必须命中 `categories.json` 枚举**，否则整条丢弃并进 `pending`。
- `reason`（一句短理由）存进 `matched_by`，供 `export --source ai` 抽查。
- 提示词：
  - 只放路径与进程名，**不放窗口标题、用户名**（窗口标题可能含文档名/网址）。
  - 种子参考：按名字相似度为每个待判 exe 挑 **≤20 条**相关种子名；挑不到就退化为「每类 2–3 个代表」。种子只作参考，不作结论。

**Blocked by:** 03

**Status:** done（2026-11-01）

- [x] 用本地桩 HTTP 服务端到端跑通：N 条待判 → 断言请求次数 = 批次数（不是 N 次）
- [x] 桩返回非法 category → 该条丢弃并进 `pending`，其余条目正常落缓存
- [x] 桩超时 / 500 → 进程不崩、该批进 `pending`，统计里能看到失败批数
- [x] `--max-batches 1` 时只发一次请求，剩余留 `pending`
- [x] 断言发出去的提示词字符串里**不含**窗口标题/用户名（隐私回归）
- [x] 未配置 `api_key` 时给出可读提示并跳过 AI，规则/种子/缓存流程照常
- [x] 模型返回的 `reason` 出现在该条目的 `matched_by` 里

## Comments

**2026-11-01 — 完成，但有一个未验证项必须说明。**

已实现：`nekotagger/ai.py`（OpenAI 兼容 `chat/completions`，stdlib `urllib`，默认 20 条/批、10 秒超时、不重试）、`pipeline.py`（批量编排、失败进 pending、`--max-batches`）、提示词只含路径与显示名 + ≤20 条种子参考。

测试覆盖：本地桩 HTTP 服务端到端（真实 `http.server` 起在 127.0.0.1 随机端口）、批次数断言（45 条 = 3 批）、非法类别丢弃并 pending、超时/失败不崩、`--max-batches 1` 只发一次、提示词隐私回归、`reason` 落进 `matched_by`。

**未验证：真实模型调用。** 本机没有配置 `api_key`，所以 `HttpBackend` 只对桩服务验证过——请求格式按 OpenAI 兼容约定写，但没有用真实端点跑过。配置后第一次请先 `--max-batches 1` 试水。
