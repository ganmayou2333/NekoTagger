# 02: 语料接入 —— 种子映射 + NekoTrak caches + Steam 规则 + 人工 override

**What to build:** 把「真实的机器数据」接进来，让规则层不再只靠关键词硬猜，并让已抓到的种子数据发挥权威作用。

1. **种子映射（最重要）**（现状：01 的规则已能覆盖 104/181 条语料，剩下 77 条靠这一条与 Steam/AI 补）：`data/seed-categories.json` 里有 740 条**应用显示名**和它们所属的类别。把这些名字映射到可执行文件路径，映射成功的直接以 `source:"seed"` 写进缓存——**用户亲手分过的结果优先于任何模型猜测**。映射手段：
   - exe 的 `VersionInfo.FileDescription` / `ProductName`（Windows 版本信息资源，用 PowerShell 或 `ctypes` 读）
   - Steam 库索引里的 `name`（对得上就是 Steam 游戏）
   - `caches/app-icons/*.json` 提供的 `normalized_exe_path` 语料（本机实测 181 条 exe）
   - 剩下对不上的留在种子文件里，不强行匹配
2. **语料**：读 **NekoTrak 安装目录**下的 `caches\app-icons\*.json`（每份含 `normalized_exe_path`；本机实测在 `G:\SteamLibrary\steamapps\common\NekoTrak\caches`，**不在** `%APPDATA%`）。目录可用参数或环境变量覆盖；目录不存在时返回空语料并正常退出，不崩。
3. **Steam 规则**：读 `caches/steam-library-index.json`（appid / name / installDir 明文），exe 落在某个 `installDir` 下 → `game`，并附上 appid。
4. **人工 override**：`overrides.json`，`{ "<normalized exe path>": "<category id>" }`，优先级高于种子、规则与 AI。
5. 加一条命令把语料跑一遍并打印分布（不调模型）：各类命中多少、种子命中多少、多少未分类。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 种子映射跑通：把 740 条名字里能对上的映射到 exe 路径，输出「映射成功 / 对不上」两个计数，对不上的不瞎编
- [ ] 种子命中的条目以 `source:"seed"` 落缓存，且**不会被后续 AI 结果覆盖**
- [ ] 本机真实语料能跑出分类分布，各类计数加起来等于语料条数
- [ ] caches 目录不存在 / JSON 损坏 / `cache_format_version` 变化时：降级为空语料或跳过坏文件 + 警告，不抛未捕获异常
- [ ] Steam `installDir` 下的 exe 判为 `game` 且带 appid；不在任何 Steam 目录下的不受影响
- [ ] `overrides.json` 里指定的类别**覆盖**种子、规则与（后续的）AI 结果
- [ ] 单测覆盖：种子→路径映射、语料解析、Steam 路径匹配（大小写/分隔符）、override 优先级、坏文件降级
