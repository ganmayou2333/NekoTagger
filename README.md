# AppClassifier

把本机跑过的应用归到 **NekoTrak 那套类别**（游戏 / 开发 / 办公 / 浏览 / 通讯 / 工具 / 娱乐 / 未分类）里的一个小工具。

给定一个 exe 路径，它按「人工 override → 本地规则 → 缓存 → AI 兜底」的顺序给出类别，并把结论持久化——**同一个应用最多问一次模型**。

它只做分类，不做别的：

- 不读、不写 NekoTrak 的数据库与配置，不注入它的进程
- 不常驻、不监控、不做使用时长统计
- 结果只落一个 JSON 文件，不写进 StatusCast 的 schema

设计与边界见 [`.scratch/app-classifier/spec.md`](.scratch/app-classifier/spec.md)，实现票在 `.scratch/app-classifier/issues/`。

## 状态

脚手架阶段：spec 与 5 张票已就绪，尚未开始实现（票 01 是入口）。
