# 05: 冷启动命令 + 统计 + 文档

**What to build:** 把前面四张票串成可用的工具，并让它能在一台真机上从零跑起来。

- `corpus`：一键读 NekoTrak 语料 → 规则 → 缓存 → 批量 AI（未配置 key 就跳过），产出 `classifications.json`。
- `show`：打印统计——各类数量、`pending` 数量、缓存条目数、上次更新时间。
- README：这个工具解决什么问题、不做什么（不读写 NekoTrak 数据库/配置、不注入、不改它的数据）、配置怎么写、三条命令怎么用。
- `.gitignore`：`cache.json`、`classifications.json`、本地配置与 key 一律不入库。

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] 本机 `corpus` 一次跑完全部真实语料（本机实测 180 条），产出 `classifications.json` 与缓存
- [ ] `show` 输出的各类计数之和等于已分类条数，且 `pending` 数可读
- [ ] 未配置 AI key 时 `corpus` 只跑规则与缓存，不报错、不卡住，统计里明确写出「AI 跳过」
- [ ] README 里能照做复现：从空目录到跑出 `classifications.json`，不需要读代码
- [ ] `git status` 干净；缓存与配置确认没被提交
