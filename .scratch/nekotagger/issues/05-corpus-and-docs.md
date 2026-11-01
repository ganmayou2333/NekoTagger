# 05: 命令编排 + 统计 + 打包 + 文档

**What to build:** 把前四张票串成能用的工具，并在一台真机上从零跑通。

**命令**
- `corpus`：读语料 → 种子 → 规则 → Steam → 缓存 → 批量 AI（未配置 key 则跳过），产出缓存；支持 `--paths-from`、`--max-batches`。
- `show`：打印统计——按 `source`（override/seed/rule/steam/cache/ai/unknown）+ 按类别 + `pending` 数量；`ai` 单列。
- `override <path> <category>`：写 `overrides.json`（保持 JSON 可读、幂等）。
- `export --out <file> [--source S]`：导出**只含类别 id** 的结果；`--source ai` 用于抽查模型判过的条目。
- 全局：`--cache` / `--config` / `--categories` 覆盖默认路径。

**工程**
- `pyproject.toml`：`[project.scripts] nekotagger = "nekotagger.__main__:main"`，不发布 PyPI。
- `.gitignore`：`cache.json` / `config.local.json` 不入库；`overrides.json` **入库**（判断沉淀）。
- README：用途、不做什么（不读 NekoTrak DB / 不注入 / 不常驻）、配置写法、五条命令、如何跑测试。

**完成判据（spec §9）**
- 存在文件的语料里 ≥95% 有终态类别；
- `source:"ai"` 占比 ≤30%；
- 上述命令均可用；
- 全离线测试覆盖规则/种子/Steam/缓存/批量 AI/提示词隐私。

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] 本机 `corpus` 一次跑完全部真实语料，产出缓存；跳过的失效/安装包路径有计数
- [ ] `show` 的 source 与类别计数之和等于已分类条数，`pending` 与 `ai` 可读
- [ ] `export --source ai` 只导出 AI 判过的条目，且每条带 `matched_by` 里的理由
- [ ] `override` 写入后，同一路径再 `classify` 立刻以 `source:"override"` 返回
- [ ] 未配置 AI key 时 `corpus` 只跑确定性部分，统计里明确写出「AI 跳过」
- [ ] `pyproject.toml` 安装后 `nekotagger classify ...` 可直接运行
- [ ] README 能照做复现：从空目录到跑出结果，不需要读代码
- [ ] 达成 spec §9 的四条判据，并在票里贴出实测数字
- [ ] `git status` 干净；缓存与本地配置确认没被提交
