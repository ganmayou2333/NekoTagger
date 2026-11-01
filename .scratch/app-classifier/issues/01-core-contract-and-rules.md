# 01: 基座 —— 类别枚举 + classify 契约 + CLI 单条

**What to build:** 一个能跑的最小工具：给定一个 exe 路径，输出它属于 NekoTrak 那套类别里的哪一类。

- `categories.json`：8 个类别（`game` 游戏 / `dev` 开发 / `office` 办公 / `browse` 浏览 / `comm` 通讯 / `tool` 工具 / `entertainment` 娱乐 / `uncategorized` 未分类），每类带 `id` / `label` / `emoji` / `patterns`（进程名或路径关键词）/ `notes`。清单来自 spec §2 的实测抄录。
- `classify(path)` 返回契约结构：`{category, confidence, source, matched_by, classified_at}`，`source` 取值 `override | rule | cache | ai | unknown`。
- CLI：`classify <exe-path>`（人读）+ `--json`（机器读）。
- 最小规则：系统路径（`c:\windows\*`、安装器/卸载器）→ `tool`；进程名关键词 → 对应类别；都不中 → `uncategorized` + `source:"unknown"`。

**Blocked by:** None（可立即开始）

**Status:** ready-for-agent

- [ ] `classify 'C:\Windows\System32\openwith.exe'` → `tool` / `rule`；`msedge.exe` → `browse` / `rule`；`D:\some\strange.exe` → `uncategorized` / `unknown`
- [ ] `--json` 输出字段与契约完全一致，不丢字段、不多字段
- [ ] `categories.json` 缺失或字段非法时给出可读错误并退出非零，不静默降级
- [ ] 规则层是纯函数（不碰网络、不写文件），有离线单测覆盖上述三类样例
- [ ] 目录已 `git init`，含 README（一句话说明这个工具做什么）与 `.gitignore`（缓存/本地配置不入库），首次提交完成
