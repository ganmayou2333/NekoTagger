# 03: 缓存 —— 判过一次就不再问模型

**What to build:** 分类结果持久化、可复用，并且**同一路径只问一次模型**。

- 位置：`%LOCALAPPDATA%\NekoTagger\cache.json`，`--cache` 可覆盖。**不放仓库根**（缓存是这台机器的状态，不该跟着 clone 走）。
- 结构：

```json
{
  "version": 1,
  "entries": {"<规范化路径>": {"category": "tool", "confidence": "certain",
                              "source": "rule", "matched_by": "...", "classified_at": 1793500000}},
  "pending": {"<规范化路径>": {"attempts": 1, "lastAttemptAt": 1793500000}}
}
```

- 键 = 规范化路径（小写、反斜杠、去引号/尾空格）。**不做 basename 兜底**：同名不同程序会判错，重判一次的成本只是一次 AI 调用。
- 判定顺序里 cache 排在确定性的种子/规则/Steam **之后**（见 spec §1）：确定性结论重算零成本，不该被缓存挡住；cache 只用来避免重复问 AI。
- **清单变更（关键）**：加载时发现条目里的 `category` 不在当前 `categories.json` → 该条打回 `pending` 重判 + 警告；其它条目原样保留，不整份作废。
- `pending` 重试上限 3 次（`attempts` 递增），超过后留 `uncategorized` 且不再重试。
- 原子写：同目录临时文件 + 替换；中途失败不产生半截文件。
- 缓存损坏或 `version` 不认识 → 降级为空缓存 + 警告，不抛未捕获异常。

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] 命中缓存时**不发起任何 AI 调用**（假后端计数为 0）
- [ ] 未命中但规则/种子命中时，同样不调用 AI
- [ ] `pending` 到 3 次后不再重试，最终类别是 `uncategorized`
- [ ] 清单里删掉一个 category id 后：只有引用该 id 的条目变成 `pending`，其余条目保留（两份清单对比断言）
- [ ] 原子写：写入中途异常不会留下半截 `cache.json`，旧文件仍可读
- [ ] `cache.json` 损坏 / version 未知 → 空缓存 + 警告
- [ ] 规范化：`"C:\Foo\Bar.EXE"`、`c:/foo/bar.exe `、`"c:\\foo\\bar.exe"` 命中同一条
- [ ] 默认位置在 `%LOCALAPPDATA%\NekoTagger\`，`--cache` 可覆盖
