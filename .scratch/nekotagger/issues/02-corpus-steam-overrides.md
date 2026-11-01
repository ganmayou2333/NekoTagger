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

**Status:** ready-for-agent

- [ ] 真实语料能加载出 181 条路径，正确报出「存在 / 不存在 / 非 exe / 安装包」四类计数（本机量级 114 / 67 / 2 / 13）
- [ ] 种子映射在真实语料上跑通，输出「精确命中 / 词边界命中 / 歧义跳过 / 无对应」四个计数，**不得出现 `Microsoft` 类通用词误伤**
- [ ] 歧义名（同名跨类别）不产生 seed 结论
- [ ] `FileDescription`/`ProductName` 全空的 exe 能退回文件名匹配（实测有 7 个）
- [ ] Steam 命中给出 `game` + appid；不在 Steam 目录下的不受影响
- [ ] `overrides.json` 命中后覆盖种子/规则/Steam
- [ ] `--paths-from` 与 NekoTrak 语料合并去重
- [ ] 语料目录缺失、JSON 损坏、`cache_format_version` 变化 → 空语料 + 警告，不抛未捕获异常
- [ ] 单测覆盖：版本信息读取（注入 reader 或临时假 exe）、精确/词边界/歧义/黑名单、Steam 前缀、override 优先级、跳过计数
