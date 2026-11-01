# NekoTagger

把本机跑过的应用归到 **NekoTrak 那套类别**（游戏 / 开发 / 办公 / 浏览 / 通讯 / 工具 / 娱乐 / 未分类）里的一个小工具。

给定一个 exe 路径，它按「人工 override → 本地规则 → 缓存 → AI 兜底」的顺序给出类别，并把结论持久化——**同一个应用最多问一次模型**。

它只做分类，不做别的：

- 不读、不写 NekoTrak 的数据库与配置，不注入它的进程
- 不常驻、不监控、不做使用时长统计
- 结果只落一个 JSON 文件，不写进 StatusCast 的 schema

设计与边界见 [`.scratch/nekotagger/spec.md`](.scratch/nekotagger/spec.md)，实现票在 `.scratch/nekotagger/issues/`。

## 状态

脚手架阶段：spec 与 5 张票已就绪，尚未开始实现（票 01 是入口）。

## 用法

```powershell
# 分类一条路径（人读）
python -m nekotagger classify 'C:\Windows\System32\openwith.exe'
# C:\Windows\System32\openwith.exe	tool	rule/certain	path: c:\windows\*

# 契约 JSON
python -m nekotagger classify 'C:/Program Files/Google/Chrome/Application/chrome.exe' --json
# {"category": "browse", "confidence": "likely", "source": "rule", ...}
```

`classify <path>` 的输出固定是这五个键：`category` / `confidence` / `source` / `matched_by` / `classified_at`。

规则优先级：人工 override → 系统路径与安装器 → 进程名/路径关键词 → 兜底 `uncategorized`。
类别清单在仓库根的 `categories.json`，id 稳定、`patterns` 可随时加；清单缺失或非法时**启动即报错**，不会带着半个清单去分类。

## 测试

```powershell
python -m unittest discover -s tests -t .
```

全离线：不联网、不读 NekoTrak 的真实数据。
