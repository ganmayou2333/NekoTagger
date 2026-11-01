# NekoTagger

把本机跑过的应用归到 **NekoTrak 那套类别**（游戏 / 开发 / 办公 / 浏览 / 通讯 / 工具 / 娱乐 / 未分类）里的小工具。

给定一个 exe 路径，它按「人工 override → 种子 → 本地规则 → Steam → 缓存 → AI」的顺序给出类别，并把结论持久化——**同一个应用最多问一次模型**。

它只做分类，不做别的：

- 不读、不写 NekoTrak 的数据库与配置，不注入它的进程（DB 是 SQLCipher 加密的，密钥来源未定位，已决定不碰）
- 不常驻、不监控、不做使用时长统计
- 结果只落一个 JSON 文件，不写进 StatusCast 的 schema

设计与决策见 [`.scratch/nekotagger/spec.md`](.scratch/nekotagger/spec.md)。

## 安装

```powershell
pip install -e .        # 需要环境里有 setuptools；提供 nekotagger 命令
python -m nekotagger    # 不需要安装，直接这样跑
```

> 本机 Python 3.14 环境没有 setuptools，`pip install -e .` 还没实测通过；日常用 `python -m nekotagger` 即可。

## 用法

```powershell
# 单条（人读 / JSON）
nekotagger classify 'C:\Windows\System32\openwith.exe'
nekotagger classify 'C:\Program Files\Google\Chrome\Application\chrome.exe' --json

# 批量冷启动：读 NekoTrak 的 app-icons 语料 + 规则 + 种子 + 缓存 + AI
nekotagger corpus --nekotrak-root 'G:\SteamLibrary\steamapps\common\NekoTrak'
nekotagger corpus --paths-from my-apps.txt --no-ai      # 不联网，只跑确定性部分

# 看统计 / 抽查 / 导出 / 人工修正
nekotagger show
nekotagger export --out classifications.json
nekotagger export --out ai-only.json --source ai
nekotagger override 'D:\apps\weird.exe' dev
```

契约固定五个键：`category` / `confidence` / `source` / `matched_by` / `classified_at`。
`export` 只写类别 id，中文名与 emoji 由消费方查 `categories.json`。

## 配置

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `categories.json` | 仓库根（入库） | 类别定义与关键词 patterns；id 稳定，可自行增改 |
| `data/seed-categories.json` | 仓库根（入库） | 从 NekoTrak 分类页抓的种子名，超 30 天会提示过期 |
| `overrides.json` | 仓库根（入库） | 人工指定，优先级最高 |
| `cache.json` | `%LOCALAPPDATA%\NekoTagger\`（不入库） | 分类结果与 pending |
| `config.local.json` | `%LOCALAPPDATA%\NekoTagger\`（不入库） | AI 的 `base_url` / `model` / `api_key` |

环境变量优先于配置文件：`NEKOTAGGER_API_KEY` / `NEKOTAGGER_BASE_URL` / `NEKOTAGGER_MODEL` / `NEKOTAGGER_NEKOTRAK_ROOT`。

没配 `api_key` 时 AI 阶段自动跳过，规则与种子照常工作。

## 测试

```powershell
python -m unittest discover -s tests -t .
```

全离线：不联网（AI 用本地桩 HTTP 服务），不读本机真实应用。
