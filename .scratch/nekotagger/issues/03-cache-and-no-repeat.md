# 03: 缓存 —— 判过一次就不再问模型

**What to build:** 让分类结果可持久化、可复用，并且**同一应用永远只问一次模型**。

- `cache.json`（版本化，原子写：先写临时文件再替换），结构见 spec §4：`entries` 按规范化路径索引 + `pending` 列表。
- 路径规范化：小写、反斜杠归一、去引号与尾随空格，与 NekoTrak 的 `normalized_exe_path` 对齐。
- 命中缓存直接返回 `source:"cache"`，**不发起任何模型调用**。
- `pending` 队列带重试上限（默认 3 次）；超过上限的留 `uncategorized` 且不再重试，避免每轮刷接口。
- AI 调用抽象成可注入接口：本轮只定义接口 + 一个记录调用的假后端，真后端留给 04。

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] 连跑两次：第二次模型调用次数为 **0**（用假后端计数断言，不是断言输出文本）
- [ ] `pending` 项重试到上限后不再被重试，且最终类别是 `uncategorized`
- [ ] 原子写：写入中途异常不会留下半截 `cache.json`（旧文件保持可读）
- [ ] `cache.json` 损坏或版本不认识时降级为空缓存 + 警告，不抛未捕获异常
- [ ] 规范化：同一 exe 的 `"C:\Foo\Bar.EXE"`、`c:/foo/bar.exe `、`"c:\\foo\\bar.exe"` 命中同一条缓存
