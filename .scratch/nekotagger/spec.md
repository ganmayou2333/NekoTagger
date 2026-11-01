# Spec: 应用分类器（NekoTagger）

状态：设计定稿（2026-11-01，经 grilling 确认）。决策见文末「决策记录」，实现见 `issues/`。
日期：2026-11-01

## 背景

NekoTrak 自己会把应用归类（界面里的「分类 / 应用」页），但那套数据**读不出来**：

- `%APPDATA%\NekoTrak` 下所有 `.db`（含 `backups/`、`users/<id>/cloud*/`）都没有 SQLite 魔数，是加密的（SQLCipher；DB 与 WAL 熵均为 8.00）；
- `users/<id>/tracker-heartbeat.json` 是 `NKSC` 密文；
- 两处明文 `config.json` 里只有 `systemCategorySettings`（颜色/隐藏开关），**没有类别列表，也没有应用→类别的映射**；
- 二进制静态分析：无 `crypt32`（不走 DPAPI）、advapi32 无 `CredReadW`（不走凭据管理器）、注册表与明文文件里都没有密钥块。**要解密只能运行时插桩或提取密钥，已决定不做。**

所以「按 NekoTrak 的分类体系归类」只能自己重建这套分类。

### 可用的明文输入（本机实测，2026-11-01）

| 来源 | 内容 | 实测规模 |
| --- | --- | --- |
| NekoTrak 安装目录 `caches\app-icons\*.json` | 每个应用一份，含 `normalized_exe_path` | 181 条 exe 路径，其中 **114 条文件仍存在（63%）** |
| 同目录 `caches\steam-library-index.json` | Steam 库索引：`appid` / `name` / `installDir` | 23 个 app / 3 个库 |
| 分类页 UI Automation 抓取（一次性） | 8 个类别 + 条目显示名 | **740 条，去重 716** |
| StatusCast 的 NekoTrak 采集器 | 实时前台进程 | 每 30 秒 |

### 抓过来的种子长什么样（这决定了匹配策略）

- 740 条里 **24 条重复**（跨类别重名），去重后 716。
- 用版本信息（`FileDescription` / `ProductName`）把 716 个显示名映射到 114 个存在的 exe：**只有 252 个名字（35%）能被用掉**，464 个没有对应 exe。
- exe 一侧反过来很乐观：存在的 114 条里 113 条能映射（99%）。
- 但**裸子串匹配会误伤**：种子 `Microsoft` 一个词命中 39 个 exe；`app` / `windows` / `x64` / `setup` / `application` / `desktop` / `download` 各命中 7–29 个纯属目录名巧合。
- 版本信息质量：`FileDescription` 空 7.9%；`ProductName` 为通用串「Microsoft® Windows® Operating System」占 22.8%；中文 Windows 的 FD 本地化良好（`openwith.exe` → 选取应用）。
- 缓存里的 13 个命中是**安装包/临时文件**（`\idm\`、`\temp\`、`\download\`、`*setup*`），不是运行中的主程序。

## 目标

一个**独立的小工具**：给定 exe 路径，输出它属于哪个类别并持久化判断。

- 规则与种子优先，AI 兜底；**同一个应用最多问一次模型**。
- 冷启动可批量，未知项合并请求；运行时可单条（可选走 AI）。
- 同一路径任何时候都得到同一类别，除非显式重判。
- 全程离线可测（合成夹具 + 假模型）。

## 非目标

- 不读、不写 NekoTrak 的数据库与配置；不注入进程；**不把「读它的界面」变成常规能力**（种子只手动重抓）。
- 不做常驻监控 / 使用时长统计 / 报表。
- 不写进 StatusCast 的 schema，不接 NekoTool（输出是 JSON，谁要谁读）。
- 不训练/微调模型；不引入 GPU 或重运行时依赖。

## 设计

### 1. 分类契约（核心）

```text
classify(exe_path) -> {
  category:      <类别 id>,
  confidence:    "certain" | "likely" | "guess",
  source:        "override" | "seed" | "rule" | "steam" | "cache" | "ai" | "unknown",
  matched_by:    <一句话命中原因，便于抽查>,
  classified_at: <epoch 秒>
}
```

判定顺序（前一条命中即返回）：

1. **override**：`overrides.json`，人工指定，**最高优先级，永不被覆盖**。
2. **seed（精确）**：种子名与 exe 的版本信息/文件名**精确或词边界**匹配，且该名字**只属于一个类别**。
3. **rule**：系统路径 / 安装器 → `tool`；`categories.json` 的 patterns。
4. **steam**：路径落在某个 Steam `installDir` 下 → `game`，附 `appid`。
5. **cache**：之前判过（含 AI 结果）。
6. **ai**：显式允许时问模型。
7. **unknown**：兜底 `uncategorized` + `confidence:"guess"`，进 `pending`。

> 顺序说明：cache 放在种子/规则之后，是因为规则与种子是**确定性**的，重算成本为零；cache 只是省一次 AI，不该挡住确定性判断。

### 2. 类别

从 NekoTrak 分类页实测抄录，落成 `categories.json`（仓库根）：`id` 稳定 slug、`label` 中文名、`emoji`、`patterns`、`notes`。

| emoji | id | 名称 | 当时条目数 |
| --- | --- | --- | --- |
| 🎮 | game | 游戏 | 14 |
| 💻 | dev | 开发 | 10 |
| 📊 | office | 办公 | 2 |
| 🌐 | browse | 浏览 | 232 |
| 💬 | comm | 通讯 | 1 |
| 🔧 | tool | 工具 | 6 |
| ✨ | entertainment | 娱乐 | — |
| 🗂️ | uncategorized | 未分类 | 549 |

类别是用户可自定义的（界面有「新建分类」），所以**不硬编码**；改清单后按 §5 处理缓存。

### 3. 种子（`data/seed-categories.json`）

**匹配策略（放弃裸子串）**

- 规范化：NFKC → 小写 → 去首尾空白 → 去尾部 1–2 层括号 → 去尾部版本号。
- **精确**：规范化后完全相等 → 可落 `source:"seed"`。
- **词边界**：按非字母数字切词后整词相等（能救 `OBS Studio` vs `obs64` 的一部分）→ 同样可落 seed。
- **歧义**：同一显示名出现在多个类别 → **视为歧义，不落 seed**，只作为提示词候选。
- **黑名单**：`microsoft` / `windows` / `x64` / `app` / `setup` / `application` / `desktop` / `download` 等通用词永不参与匹配（实测 `Microsoft` 会命中 39 个 exe）。
- 只对**存在的文件**做映射；`FileDescription` / `ProductName` 为空时退回文件名与父目录名（实测 7 个这样的 exe 全部靠文件名救回）。

**权威边界**：只有精确/词边界命中才写 `source:"seed"`；模糊相似只作为 AI 提示词上下文，**不写缓存终态**。

**新鲜度**：`_meta.harvestedAt` 超过 30 天时，`corpus` / `show` 打一次警告；重新抓取由用户手动执行（不内置 UI 自动化）。

### 4. 规则层（`categories.json` 的 patterns）

- patterns 同时参与「文件名」与「完整路径」匹配，但**长度 ≥4 才允许匹配路径**；短 pattern（`qq` / `7z` / `gh` / `code`）只匹配文件名——否则会在目录名上误伤。
- 系统路径（`c:\windows\*`）与安装器/卸载器（`setup.exe` / `*install*` / `unins*`）→ `tool`（NekoTrak 没有「系统」类）。
- 规则层是纯函数：不读写文件、不联网。

### 5. 缓存（`cache.json`）

- 位置：`%LOCALAPPDATA%\NekoTagger\cache.json`，可用 `--cache` 覆盖。**不再放仓库根**（缓存是这台机器的运行状态，不该跟着 clone 走）。
- 结构：`{version, entries:{<规范化路径>:{category,confidence,source,matched_by,classified_at}}, pending:{<path>:{attempts,lastAttemptAt}}}`。
- 键：规范化路径（小写、反斜杠、去引号与尾空格）。**不做 basename 兜底**——同名不同程序会判错，而重判一次的成本只是一次 AI 调用。
- **清单变更**：加载时发现条目里的 category id 不在当前清单 → 该条标记 `pending` 重判 + 警告；其余条目不动。
- `pending` 重试上限 3 次，超过后留 `uncategorized` 且不再重试。
- 原子写（同目录临时文件 + `File.Replace` 语义），损坏或版本不识 → 降级为空缓存 + 警告。

### 6. AI（OpenAI 兼容 HTTP）

- 配置：`%LOCALAPPDATA%\NekoTagger\config.local.json`（`base_url` / `model` / `api_key`），环境变量 `NEKOTAGGER_API_KEY` 等优先覆盖。
- 批量：默认 20 条/批；**不重试**，失败整批进 `pending`，下一轮再说。
- 上限：默认不限，`--max-batches N` 手动兜底。
- 返回要求：每项 `{path, category, confidence, reason}`；**类别必须命中清单枚举**，否则整条丢弃进 `pending`。
- `reason`（一句短理由）存进 `matched_by`，供抽查。
- 提示词：只放路径与进程名（不放窗口标题/用户名）；种子参考按 §3 规则挑 **≤20 条相似名**，挑不到就给「每类 2–3 个代表」。

### 7. 语料（`corpus`）

- 来源：NekoTrak `caches/app-icons/*.json` + 可选 `--paths-from <file>`（每行一个路径），合并去重。
- 过滤：文件不存在 → 跳过并计数（实测 67/181 已失效）；非 `.exe`（如 `.tmp`）→ 跳过；安装包/临时路径（`\temp\`、`\download\`、`*setup*`、`unins`）→ 跳过并计数。
- 输出：一个可复现的语料清单（路径 + 跳过原因统计），供后续判定。

### 8. CLI

```text
nekotagger classify <path> [--json] [--ai] [--cache P] [--categories P] [--config P]
nekotagger corpus [--paths-from F] [--max-batches N] [--cache P]
nekotagger show [--cache P]
nekotagger override <path> <category> [--overrides P]
nekotagger export --out F [--source S] [--cache P]
```

- 安装：`pyproject.toml` 暴露 `nekotagger` 命令（不发布 PyPI）。
- `export` 只存类别 id，不存 label/emoji（展示查 `categories.json`）。
- `overrides.json` **入库**（判断沉淀）；`cache.json` / `config.local.json` 不入库。

### 9. 完成判据

- 存在文件的语料里 **≥95%** 有终态类别；
- `source:"ai"` 占比 **≤30%**；
- `show` / `export` / `override` / `corpus` 均可用；
- 全离线测试覆盖：规则、种子匹配、Steam、缓存失效、批量 AI（假后端）、提示词隐私。

## 测试（全离线）

- 规则层：纯函数表驱动（系统路径、安装器、长/短 pattern、未知）。
- 种子：精确、词边界、歧义、黑名单、版本信息缺失时退回文件名。
- 语料：缺失文件/安装包/非 exe 的跳过计数；`--paths-from` 合并去重。
- 缓存：命中零 AI 调用；pending 上限；清单变更选择性失效；原子写；损坏降级。
- AI：批量次数断言、非法类别丢弃、超时降级、提示词不含窗口标题。

## 决策记录（2026-11-01 grilling）

| # | 决策 |
| --- | --- |
| 1 | 范围：NekoTagger 剩余 4 张票的设计（不含 StatusCast/NekoTool） |
| 2 | seed 权威：AI 不得覆盖；只有 overrides 能改 |
| 3 | 未映射的种子名：不丢，按相似度进 AI 提示词 |
| 4 | AI 触发：`corpus` 批量冷启动 + `classify --ai` 单条可选 |
| 5 | 输出：只有 `cache.json` 是真相，`export` 另开命令 |
| 6 | 清单变更：只失效受影响条目 |
| 7 | 提示词：相似度挑 ≤20 条 + 每类代表兜底 |
| 8 | 配置：`config.local.json` + 环境变量覆盖 |
| 9 | 文件位置：缓存与配置放 `%LOCALAPPDATA%\NekoTagger\` |
| 10 | `overrides.json` 入库 |
| 11 | 导出字段：只存类别 id |
| 12 | 打包：`pyproject.toml` + `nekotagger` 命令 |
| 13 | 路径变化：接受重判，不做 basename 兜底 |
| 14 | AI 失败：不重试，进 pending |
| 15 | 批量上限：默认不限，`--max-batches` 兜底 |
| 16 | `show` 口径：source + 类别 + pending，ai 单列 |
| 17 | 子串匹配：放弃裸子串，改精确 + 词边界 + 黑名单 |
| 18 | 权威边界：只有精确/词边界命中算 seed |
| 19 | 失效路径：跳过并计数 |
| 20 | 同名跨类别：视为歧义，不落 seed |
| 21 | 种子过期：30 天警告，不自动抓取 |
| 22 | 语料入口：支持 `--paths-from` |
| 23 | 安装包/临时路径：跳过并计数 |
| 24 | AI 条目：`show` 单列 + `export --source ai` |
| 25 | patterns：长度 ≥4 才匹配路径，短词只匹配文件名 |
| 26 | AI 返回带一句短理由，存进 `matched_by` |
| 27 | 完成判据：≥95% 终态、AI ≤30%、命令齐全、测试全离线 |

## 风险

- **类别语义漂移**：模型对「工作 / 学习」边界不稳 → 固定枚举 + 置信度 + override 兜底。
- **种子只覆盖 35%**：名字远多于 exe（716 vs 114）→ 剩下的靠规则与 AI，别指望种子包打天下。
- **版本信息不可靠**：FD 空 7.9%、通用 ProductName 22.8% → 匹配必须有黑名单与词边界，否则批量误判。
- **NekoTrak 升级**：`cache_format_version` 变化 → 读不到就当空语料，不崩。
