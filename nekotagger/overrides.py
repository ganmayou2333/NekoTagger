"""人工覆盖：``overrides.json``。

形如 ``{"<规范化路径>": "<类别 id>"}``。它的优先级高于种子、规则、Steam 与 AI——
这是人最后说话的地方，也是改正 AI 误判的入口。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .normalize import normalize_exe_path


class OverrideDataError(ValueError):
    """覆盖文件非法。"""


def load_overrides(path: str | os.PathLike[str]) -> dict[str, str]:
    """读取覆盖文件；文件不存在返回空字典。键会被规范化。"""

    target = Path(path)
    if not target.exists():
        return {}

    try:
        document = json.loads(target.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise OverrideDataError(
            f"覆盖文件不是合法 JSON：{target}：第 {exc.lineno} 行 {exc.msg}"
        ) from exc
    except OSError as exc:
        raise OverrideDataError(f"覆盖文件读取失败：{target}：{exc}") from exc

    if not isinstance(document, dict):
        raise OverrideDataError(f"覆盖文件根部必须是对象：{target}")

    overrides: dict[str, str] = {}
    for raw_path, raw_category in document.items():
        if not isinstance(raw_path, str) or not isinstance(raw_category, str):
            continue
        if not raw_category.strip():
            continue
        try:
            overrides[normalize_exe_path(raw_path)] = raw_category.strip()
        except ValueError:
            continue
    return overrides
