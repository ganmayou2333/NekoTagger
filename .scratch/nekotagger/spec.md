# Spec: 应用分类器（NekoTagger）

状态：草案（2026-11-01）。三个决策已定（见文末「决策记录」），类别清单已从 NekoTrak 实测抄录，下一步拆票。
日期：2026-11-01
关联：`StatusCast`（前台进程来源）、`NekoTool`（消费侧候选）、`docs/NekoTrak接入实施计划.md`（同一台机器上的 NekoTrak 集成）

## 背景

NekoTrak 自己会把应用归类（界面里的「分类 / 应用」页），但那份数据**读不出来**：

- `%APPDATA%\NekoTrak` 下所有 `.db`（含 `backups/`、`users/<id>/cloud*/`）都没有 SQLite 魔数，是加密的；
- `users/<id>/tracker-heartbeat.json` 是 `NKSC` 密文；
- 两处明文 `config.json` 里只有 `systemCategorySettings`（`idleColor` / `uncategorizedColor` / 两个 `hidden` 开关），**没有类别列表，也没有应用→类别的映射**；
- `NekoTrak.log` 里也没有分类相关记录。

所以要「按 NekoTrak 的分类体系归类」，只能自己重建这套分类，而不是去读它。

同时有三份**明文**语料可以直接用（本机实测）：

| 来源 | 内容 | 规模 |
| --- | --- | --- |
| `NekoTrak\caches\app-icons\*.json` | 每个应用一份，含 `normalized_exe_path`、图标索引 | 180 份 / 141 个不同 exe 名 |
| `NekoTrak\caches\steam-library-index.json` | Steam 库索引：`appid`、`name`、`installDir`、`manifestPath` | 3 个库 |
| StatusCast 的 NekoTrak 采集器 | 实时前台进程（`foregroundApp` = exe 名，`currentGameAppId`） | 每 30 秒 |

`caches/app-icons/` 这份是最有价值的：它等于「这台机器上跑过的应用清单」，可以直接当冷启动语料和测试夹具。

## 目标

一个**独立的小工具**（不进 StatusCast / NekoTool 两个仓库），给定一个可执行文件路径或前台进程，输出它属于哪个类别，并把这个判断持久化：

- 规则优先，AI 兜底：常见应用靠本地规则秒判；规则判不出来的才问模型，**判一次就写进缓存，同一个应用不再问第二次**。
- 冷启动可批量：拿 `caches/app-icons/` 那 180 条跑一遍，把未知项合并成**一次**模型调用，而不是 180 次。
- 运行时可单条：前台进程查缓存命中就返回，未命中才走 AI，并带超时与降级。
- 结果可复现：同一个 exe 路径任何时候都得到同一个类别，除非显式 `--reclassify`。
- 全程离线可测：测试只用合成夹具 + 假模型，不联网。

## 非目标

- **不读、不写 NekoTrak 的数据库与配置**，不注入它的进程，不读它的界面。分类体系是我们自己维护的一份，只是「名字对齐」它界面上的那一套。
- 不做进程实时监控/常驻服务（`--watch` 是后续可选项，见开放问题）。
- 不做使用时长统计/报表（那是另一个议题；本工具只回答「这个应用属于哪类」）。
- 不做模型训练/微调。
- 不引入需要 GPU 或大体积运行时的依赖。

## 设计

### 1. 分类契约（核心）

一个函数，一个返回结构——这是整个工具的**深模块边界**，其它一切都是它的输入输出：

```text
classify(exe_path | running_process) -> {
  category:     <类别 id，见 §2>,
  confidence:   "certain" | "likely" | "guess",
  source:       "override" | "rule" | "cache" | "ai" | "unknown",
  matched_by:   <一句话说明命中原因，便于调试>,
  classified_at: <epoch 秒>
}
```

语义：

- `override`：用户显式指定（人工兜底，永远最高优先级）。
- `rule`：本地规则命中（路径/进程名/Steam 库）。
- `cache`：之前算过，直接复用（**不再调模型**）。
- `ai`：模型判的，已写缓存。
- `unknown`：都没有，返回「未分类」并在缓存里标记 `pending`，等下次批量或单条再试（**不要每次都重试同一批**，防止刷接口）。

### 2. 类别（已从 NekoTrak 实测抄录）

2026-11-01 用 UI Automation 读了一次 NekoTrak 的「分类」页（只切换页签读取，未修改任何数据，读完切回仪表盘）。实际清单：

| emoji | 类别 | 当时条目数 |
| --- | --- | --- |
| 🎮 | 游戏 | 14 |
| 💻 | 开发 | 10 |
| 📊 | 办公 | 2 |
| 🌐 | 浏览 | 232 |
| 💬 | 通讯 | 1 |
| 🔧 | 工具 | 6 |
| ✨ | 娱乐 | —（页面上未显示计数） |
| 🗂️ | 未分类 | 549 |

页面上还有「新建分类 / 分类工作区 / 条目 / 规则 / 高级筛选」，说明**类别是用户可自定义的**。所以：

- 类别不硬编码在代码里，落成 `categories.json`，字段：`id`（稳定 slug，如 `game`）、`label`（中文名，如 `游戏`）、`emoji`、`patterns`（进程名/路径关键词）、`notes`（判断依据）。
- 上面 8 个作为**初始默认值**（`uncategorized` 是兜底，等于「未分类」）。
- 用户改了 NekoTrak 的类别之后，我们这份需要手动同步（保持 id 稳定，只改 label/patterns）；**不做自动同步**，因为读它要点 UI，不适合放进常规流程。
- 未命中的一律落 `uncategorized`。

### 2.5 种子数据（已抓取，2026-11-01）

分类器不要求 AI 从零猜：已经通过 **UI Automation 只读抓取**拿到 NekoTrak「分类」页的 8 个类别与条目清单，落在 `data/seed-categories.json`（740 条，已做跨类目去噪）：

| 类别 | 条目数 | 备注 |
| --- | --- | --- |
| 游戏 | 13 | EVE / 米哈游启动器 / 三角洲行动 / Apex… |
| 开发 | 9 | ChatGPT / Cherry Studio / DeepSeek / GitHub / VS… |
| 办公 | 2 | iCloud / Microsoft Access |
| 浏览 | 211 | 含大量网页标题（这一类本来就是浏览器窗口） |
| 通讯 | 1 | QQ |
| 工具 | 6 | 123云盘 / 火绒系列 / IDM… |
| 娱乐 | 2 | 哔哩哔哩 / 网易云音乐 |
| 未分类 | 496 | 用户尚未分类的应用名 |

用法：这些是**应用显示名**，不是 exe 路径。ticket 02 负责把名字映射到可执行文件（exe 的 VersionInfo.FileDescription、Steam 库索引名、人工 override），映射成功的那部分直接以 `source:"seed"` 写进缓存——**权威数据优先于模型**。

已知局限：

- 「浏览」里的条目多是网页标题而不是应用名，做进程映射时应整类跳过或只保留能对上 exe 的。
- 类别清单是你自定义的，改动后需要重新抓一次（`data/seed-categories.json` 的 `_meta.harvestedAt` 记录时间）。

### 2.6 为什么不去读 NekoTrak 的数据库（已决策：不做）

DB 是 SQLCipher 加密的（`NekoTrak.db` 与 `-wal` 熵均为 8.00，无 SQLite 魔数），静态分析确认：

- 没有 `crypt32.dll` 导入 → 不走 DPAPI
- advapi32 没有 `CredReadW`/`CredWriteW` → 不走 Windows 凭据管理器（`CredEnumerate` 实测也返回零条）
- 注册表只有 Steam 的 app 注册项；`config.json` / `local.json` / `manifest.json` 里没有密钥块
- `libcrypto-4-x64.dll` 提供 PBKDF2 / AES-256-CBC，可能是 cloud-snapshot 用的

passphrase 来源未定位，继续需要运行时插桩或长时间静态反编译。**用户已决定不走这条路**：分类器用界面抓来的种子即可，不碰加密存储、不提取密钥。

### 3. 规则层（顺序即优先级）

1. **用户覆盖**：`overrides.json`，`{ "<normalized exe path>": "<category id>" }`，人工可编辑，永不被 AI 覆盖。
2. **系统路径**：`c:\windows\*`、驱动/安装器（`setup.exe`、`*install*.exe`、`unins*`）→ **`tool`（工具）**。注意 NekoTrak 的类别里**没有「系统」这一类**（实测只有 游戏/开发/办公/浏览/通讯/工具/娱乐/未分类），所以系统路径归到「工具」，不另造类别。这类占样本量不小（本机 180 条里就有 `openwith.exe`、`wdi-simple.exe`、`setup.exe`）。
3. **Steam 库命中**：能对上 `steam-library-index.json` 的 `installDir` → 先判为 `game`，同时记录 `appid`，供后续细分（比如按 appid 白名单把「游戏」细分成 单机/联机，属开放问题）。
4. **进程名/路径关键词**：浏览器（`msedge`/`chrome`/`firefox`）、IDE（`code`/`devenv`/`pycharm`）、终端（`windowsterminal`/`powershell`）、办公（`winword`/`excel`）等，来自 `categories.json` 里每个类别自带的 `patterns`。
5. **AI 兜底**：以上都没命中，且缓存没有终态结果 → 问模型。

规则层必须**纯函数、可单测**，不碰网络、不碰文件（由调用方注入语料）。

### 4. 缓存

`cache.json`（版本化，原子写，同 `write_to_obs.ps1` 的做法）：

```json
{
  "version": 1,
  "entries": {
    "e:\\doubao\\app\\doubao.exe": {
      "category": "ai-assistant",
      "confidence": "likely",
      "source": "ai",
      "matched_by": "model: 豆包桌面客户端",
      "classified_at": 1793500000
    },
    "c:\\windows\\system32\\openwith.exe": {
      "category": "system",
      "confidence": "certain",
      "source": "rule",
      "matched_by": "path: c:\\windows\\*",
      "classified_at": 1793500001
    }
  },
  "pending": ["d:\\some\\unknown.exe"]
}
```

- 路径键统一规范化（小写 + 反斜杠 + 去引号 + 去尾随空格），与 NekoTrak `normalized_exe_path` 一致。
- `pending` 记录「问过 AI 但没给出可用结果」的项，带重试上限（默认 3 次），超过就留 `uncategorized` 不再重试。
- 缓存里不存模型原始回复（避免体积和隐私问题），只存结论与一句理由。

### 5. AI 调用

- **批量优先**：冷启动把待判项按每批 N 个（默认 20）打包成一次请求，要求模型对每一项只回 `{path, category, confidence}`。
- 输出必须校验：类别必须在 §2 的枚举里，不在枚举里的整条丢弃并计入 `pending`，**不允许模型发明类别**。
- 超时/失败：该批全部进 `pending`，本轮不阻塞其它批次。
- 提示词里只放**路径和进程名**，不放窗口标题（窗口标题可能含文档名、网址，属隐私），除非后续明确要。
- 后端：**OpenAI 兼容的 HTTP 接口**（`base_url` / `model` / `api_key` 走本地配置文件，不进仓库）。只用 stdlib `urllib`，不引新依赖。

### 6. CLI 形态（最小集）

```text
nekotagger classify <exe-path>          # 单条，命中缓存则 0 次网络
nekotagger corpus                       # 用 NekoTrak app-icons 语料批量冷启动
nekotagger show                         # 打印缓存统计（各类多少、多少 pending）
nekotagger override <exe-path> <cat>    # 写 overrides.json
nekotagger --json                       # 机器可读输出
```

## 测试（全离线）

- 规则层：纯函数，用合成路径表驱动（`c:\windows\...`、Steam 装目录、浏览器、IDE、未知）。
- 类别校验：模型返回非法类别 → 丢弃 + 进 pending；返回合法但低置信 → 接受并标 `guess`。
- 缓存：命中不调模型（用假模型计数断言 **0 次调用**）；`pending` 重试上限生效；原子写不产生半截文件。
- 冷启动：180 条合成语料，只对未知项发 **1 次**批量请求（断言调用次数，不是断言内容）。
- 隐私：提示词构造里不含窗口标题/用户名（断言字符串）。

## C0 / 真机边界

- 真实 AI 后端的分类质量无法离线验证，只能抽样人工核对；spec 只保证**契约与降级**正确，不保证分得准。
- 本机 180 条语料可以作为验收样本，但它是「这台机器」的分布，不代表通用。

## 决策记录（2026-11-01）

| 问题 | 决定 |
| --- | --- |
| 类别清单来源 | **UI Automation 读一次 NekoTrak 的「分类」页**（已执行，见 §2）；之后手动维护 `categories.json` |
| AI 后端 | **OpenAI 兼容 HTTP 接口**，stdlib `urllib`，key 放本地配置 |
| 输出去向 | **只落一个 JSON 文件**，不写进 StatusCast schema，不接 NekoTool |
| 工具形态 | 独立小工具 / 新仓库，不进现有两仓 |

## 风险

- **类别语义漂移**：模型对「工作 / 学习」这类边界模糊的类别不稳定 → 靠固定枚举 + 置信度 + 人工 override 兜底。
- **路径不可比**：同一应用不同安装位置 / 版本号在路径里 → 规范化要处理，且规则按「进程名」而非全路径优先匹配。
- **冷启动耗时**：180 条 × 批量 = 若干次调用；用 `pending` + 增量避免每次全量重跑。
- **NekoTrak 升级**：`caches/app-icons/` 的 `cache_format_version` 目前是 1，格式变了要能识别并降级（读不到就当空语料，不崩）。
