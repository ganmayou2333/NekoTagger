"""分类主流程：把一条路径变成契约结构。

这是整个工具的对外边界。规则层是纯函数——不读文件、不联网；AI 与缓存是后续
ticket 的事，这里只保证「确定性优先」：同样的输入永远得到同样的输出。
"""

from __future__ import annotations

import time
from typing import Mapping

from .categories import CategorySet, load_categories
from .normalize import exe_basename, normalize_exe_path

#: 契约里必须恰好出现的键，顺序即 CLI --json 的输出顺序。
CONTRACT_KEYS = ("category", "confidence", "source", "matched_by", "classified_at")

#: 系统目录一律归「工具」——NekoTrak 没有单独的「系统」类别。
SYSTEM_PATH_PREFIXES = ("c:\\windows\\",)

#: 安装器 / 卸载器 / 更新器：出现在哪都是「工具」。
INSTALLER_HINTS = ("setup.exe", "install.exe", "installer.exe", "unins", "uninstall", "update.exe")


def _match_system_path(normalized_path: str) -> str | None:
    """系统路径或安装器，返回可读的命中原因。"""

    for prefix in SYSTEM_PATH_PREFIXES:
        if normalized_path.startswith(prefix):
            return f"path: {prefix}*"

    basename = exe_basename(normalized_path)
    for hint in INSTALLER_HINTS:
        if hint in basename:
            return f"name: contains {hint!r}"
    return None


def _resolve_category(categories: CategorySet, category_id: str) -> str:
    """命中一个可能已被用户从清单里删掉的 id 时，退到兜底类别。"""

    if category_id in categories:
        return category_id
    return categories.fallback.id


def classify(
    path: object,
    *,
    categories: CategorySet | None = None,
    overrides: Mapping[str, str] | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """把一个 exe 路径分到某个类别，返回契约结构。

    Args:
        path: 原始路径，可以带引号、正斜杠、大小写混乱。
        categories: 已加载的类别清单；缺省从仓库根 ``categories.json`` 读。
        overrides: 规范化路径 → 类别 id 的人工覆盖，优先级最高。
        now: 注入时间戳（测试用），缺省取当前时间。

    Returns:
        含 :data:`CONTRACT_KEYS` 五个键的 dict。

    Raises:
        ValueError: 路径为空或不是字符串。
        CategoryConfigError: 类别清单缺失或非法。
    """

    category_set = categories if categories is not None else load_categories()
    normalized = normalize_exe_path(path)
    classified_at = int(now if now is not None else time.time())

    if overrides:
        override = overrides.get(normalized)
        if override is not None:
            return {
                "category": _resolve_category(category_set, override),
                "confidence": "certain",
                "source": "override",
                "matched_by": "overrides.json",
                "classified_at": classified_at,
            }

    system_reason = _match_system_path(normalized)
    if system_reason is not None:
        return {
            "category": _resolve_category(category_set, "tool"),
            "confidence": "certain",
            "source": "rule",
            "matched_by": system_reason,
            "classified_at": classified_at,
        }

    matched = category_set.match_name(normalized)
    if matched is not None:
        category, pattern, where = matched
        return {
            "category": category.id,
            "confidence": "likely",
            "source": "rule",
            "matched_by": f"{where}: contains {pattern!r}",
            "classified_at": classified_at,
        }

    return {
        "category": category_set.fallback.id,
        "confidence": "guess",
        "source": "unknown",
        "matched_by": "no rule matched",
        "classified_at": classified_at,
    }
