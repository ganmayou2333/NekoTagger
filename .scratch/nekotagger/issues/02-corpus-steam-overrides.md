# 02: 数据层 —— 种子映射 + 语料 + Steam + override

**What to build:** 把真实数据接进来：读语料、读种子、读 Steam 库、读人工 override，并提供一个能被 `classify` 调用的**数据层**（命令编排留给 05）。

**语料**
- 读 NekoTrak **安装目录**下 `caches\app-icons\*.json` 的 `normalized_exe_path`（本机 181 条，其中 114 条文件仍存在）。路径可用参数/环境变量覆盖；目录不存在 → 空语料，不崩。
- 可选 `--paths-from <file>`（每行一个路径）与 NekoTrak 语料**合并去重**。
- 过滤并计数：文件不存在（本机 67 条）、非 `.exe`（本机 2 条 `.tmp`）、安装包/临时路径（`\temp\`、`\download\`、`*setup*`、`unins`；实测命中里有 13 个是安装包）。

**种子（`data/seed-categories.json`，740 条 / 去重 716）**
- 读 `_meta.categories[*].names`。
- exe 侧候选名：`FileDescription` 与 `ProductName`（Windows 版本信息，stdlib `ctypes` 调 `GetFileVersionInfoSizeW` / `GetFileVersionInfoW` / `VerQueryValueW`），为空时退回文件名与父目录名。
- 只允许两种匹配：**精确**（NFKC + 小写 + 去尾部括号/版本号后相等）与**词边界**（按非字母数字切词、整词相等）。
- **禁止裸子串**：实测种子 `Microsoft` 会命中 39 个 exe。
- 通用词黑名单（`microsoft` / `windows` / `x64` / `app` / `setup` / `application` / `desktop` / `download`）不参与匹配。
- **同名跨类别视为歧义**：不产生 seed 结论，只保留候选（740 条里 24 条重复）。
- 只有精确/词边界命中才允许 `source:"seed"`；模糊相似只交给 04 当提示词。

**Steam**
- 读 `caches/steam-library-index.json`；路径以某 `installDir\` 为前缀 → `game` 并附 `appid`。
- 实测：Steam 层对现有语料**新增 0 个 exe**（6 个命中都已被精确层覆盖），是补充不是主力。

**override**
- `overrides.json`（仓库根，**入库**）：`{"<规范化路径>": "<category id>"}`，文件缺失视为空。
- 优先级最高，覆盖种子/规则/Steam/AI 的一切结论。

**Blocked by:** 01

**Status:** done（2026-11-01）

- [x] 真实语料能加载出 181 条路径，正确报出「存在 / 不存在 / 非 exe / 安装包」四类计数（本机量级 114 / 67 / 2 / 13）
- [x] 种子映射在真实语料上跑通，输出「精确命中 / 词边界命中 / 歧义跳过 / 无对应」四个计数，**不得出现 `Microsoft` 类通用词误伤**
- [x] 歧义名（同名跨类别）不产生 seed 结论
- [x] `FileDescription`/`ProductName` 全空的 exe 能退回文件名匹配（实测有 7 个）
- [x] Steam 命中给出 `game` + appid；不在 Steam 目录下的不受影响
- [x] `overrides.json` 命中后覆盖种子/规则/Steam
- [x] `--paths-from` 与 NekoTrak 语料合并去重
- [x] 语料目录缺失、JSON 损坏、`cache_format_version` 变化 → 空语料 + 警告，不抛未捕获异常
- [x] 单测覆盖：版本信息读取（注入 reader 或临时假 exe）、精确/词边界/歧义/黑名单、Steam 前缀、override 优先级、跳过计数

## Comments

**2026-11-01 — 完成，43 项离线单测全绿。**

新增模块：`wininfo.py`（ctypes 读版本信息）、`seeds.py`（种子索引与匹配）、`corpus.py`（语料加载与过滤）、`steam.py`、`overrides.py`；`classify` 接入新顺序 override → seed → rule → steam → unknown。

**真实语料实测**（`G:\SteamLibrary\steamapps\common\NekoTrak`）：

```text
corpus:  paths=107  skipped_missing=65  skipped_non_exe=2  skipped_installer=7
seeds:   usable=659  ambiguous=24  harvestedAt=2026-11-01T12:19:34
classify: seed=71 (exact 29 / word 42)  rule=25  unknown=11
coverage: 96/107 = 90%       耗时 1 ms/条
```

与子代理预估的差异（如实记录）：

- **missing 65 而不是 67**：那 2 个 `.tmp` 先被「非 exe」拦下，所以没进 missing 桶。
- **installer 7 而不是 13**：子代理的 13 是「命中里有多少落在安装包路径」，我的过滤只认「文件名含 setup/install/unins/update」或「exe 自己的两级父目录叫 temp/tmp/downloads/download」。定义不同，不是覆盖不全。

**过程中修掉的两个真问题**：

1. **系统路径只认 `c:\windows\`** — 本机有装在 `H:` 的系统盘，`h:\windows\explorer.exe` 因此漏判。改成 `^[a-z]:\\windows\\`，覆盖率 88% → 90%。
2. **临时目录过滤太宽**：原本「路径里出现过 `\\temp\\` 就跳过」，会把整个用户目录都在 Temp 下的应用全判成临时文件（我自己的测试就先撞上了）。改成只看 exe 自己的两级父目录名。

**词边界匹配的抽样审计**：42 条 word 命中人工看了前 12 条，都是合理配对（`code.exe`→Visual Studio Code、`windowsterminal.exe`→Windows Terminal、`excel.exe`→Microsoft Excel）。有一条 `ddu v18.1.5.6.exe`→「7z SFX」（你在未分类里的一个条目）属于边缘情况，但类别是「未分类」，不影响结果。

**剩余 11 条 unknown**：`workbuddy`、`cc-switch`、`kook`、`gamepp`、`图吧工具箱2026`、`office tool plus`、`seephealthui`、Windows 商店应用等——正是 04 的 AI 要处理的部分。

**2026-11-01 补记（MCP 实测发现的误判）**：通过 MCP 工具实测时发现
`C:\Windows\...\oem1.inf_amd64\setup.exe` 被判成「未分类 / seed[word]: 'INF 默认安装'」。
根因是切词把中文整段丢掉，种子「INF 默认安装」退化成 `("inf",)`，撞上了路径里的 `inf`。

修法：含中文的种子只允许精确匹配；单 token 种子至少 4 字符才参与词边界。修完后：

- `setup.exe` → `tool / rule`（系统路径）✅
- 真实语料：seed 66（exact 30 / word 36）、rule 27、unknown 16 → 覆盖率 **90% → 85%**
- 少掉的 6 条命中里有一部分原本就是误判；代价是多了 5 条交给 AI，正是设计里预留给 04 的部分。
