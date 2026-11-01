"""可执行文件路径的规范化。

缓存、override 和种子映射都以这个结果为键，所以规则必须稳定且可预测：
去引号、去首尾空白、统一反斜杠、折叠重复分隔符、去尾随分隔符、转小写。
"""

from __future__ import annotations

import re

_QUOTES = ("'", '"')


def normalize_exe_path(raw: object) -> str:
    """把一个 exe 路径规范成稳定的缓存键。

    Raises:
        ValueError: 输入为空、不是字符串、或规范化后为空。
    """

    if raw is None:
        raise ValueError("路径为空")
    if not isinstance(raw, str):
        raise ValueError(f"路径必须是字符串，收到 {type(raw).__name__}")

    text = raw.strip()
    # Windows 的「打开方式」和快捷方式常把整条命令带引号塞进来。
    while len(text) >= 2 and text[0] in _QUOTES and text[-1] == text[0]:
        text = text[1:-1].strip()

    if not text:
        raise ValueError("路径为空")

    text = text.replace("/", "\\")

    # UNC 前缀（\\server\share）要保留双反斜杠，其余重复分隔符折叠成一个。
    unc = text.startswith("\\\\")
    body = text[2:] if unc else text
    body = re.sub(r"\\{2,}", r"\\", body)
    text = ("\\\\" + body) if unc else body

    text = text.rstrip("\\").lower()
    if not text:
        raise ValueError("路径为空")
    return text


def exe_basename(normalized_path: str) -> str:
    """规范化路径的最后一段（文件名）。"""

    return normalized_path.rsplit("\\", 1)[-1]
