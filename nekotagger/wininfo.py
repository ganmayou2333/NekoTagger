"""读取 Windows 可执行文件的版本信息资源。

种子的显示名要和 exe 对上，靠的就是这里的 ``FileDescription`` / ``ProductName``。
纯 stdlib（``ctypes`` 调 ``version.dll``），读不到就返回 ``None``——绝不抛异常，
因为分类器不该因为有文件没有版本资源就崩掉。

实测（本机 114 个存在的 exe）：``FileDescription`` 为空 7.9%，``ProductName``
是通用串「Microsoft® Windows® Operating System」占 22.8%。所以调用方必须
准备好「版本信息不可靠」时的退化路径（文件名 / 父目录名）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VersionInfo:
    file_description: str | None = None
    product_name: str | None = None
    file_version: str | None = None

    def candidates(self) -> tuple[str, ...]:
        """非空的候选显示名，按可信度排序。"""

        return tuple(
            value
            for value in (self.file_description, self.product_name)
            if value and value.strip()
        )


_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:  # pragma: no branch - platform split
    _version = ctypes.WinDLL("version", use_last_error=True)

    _version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    _version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    _version.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _version.GetFileVersionInfoW.restype = wintypes.BOOL
    _version.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    _version.VerQueryValueW.restype = wintypes.BOOL

    # 找不到 Translation 表时的兜底：英语(美国)/Unicode。
    _FALLBACK_LANG_CODEPAGES = ("040904B0", "080404B0")
else:  # pragma: no cover - 开发环境可能是 Linux
    _version = None
    _FALLBACK_LANG_CODEPAGES = ()


def _query_string(block: ctypes.c_void_p, field: str) -> str | None:
    buffer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not _version.VerQueryValueW(
        block, f"\\StringFileInfo\\{field}", ctypes.byref(buffer), ctypes.byref(length)
    ):
        return None
    if not buffer.value or not length.value:
        return None
    return ctypes.wstring_at(buffer.value, length.value).strip() or None


def _translations(block: ctypes.c_void_p) -> tuple[str, ...]:
    buffer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not _version.VerQueryValueW(
        block, "\\VarFileInfo\\Translation", ctypes.byref(buffer), ctypes.byref(length)
    ):
        return ()
    if not buffer.value:
        return ()
    # 每项 4 字节：低 16 位语言 id，高 16 位代码页。
    count = length.value // 4
    raw = ctypes.string_at(buffer.value, length.value)
    codes = []
    for index in range(count):
        language = int.from_bytes(raw[index * 4 : index * 4 + 2], "little")
        codepage = int.from_bytes(raw[index * 4 + 2 : index * 4 + 4], "little")
        codes.append(f"{language:04X}{codepage:04X}")
    return tuple(codes)


def read_version_info(path: str | os.PathLike[str]) -> VersionInfo | None:
    """读取一个 exe 的版本信息，读不到返回 ``None``。

    只读、不执行目标文件。非 Windows 平台直接返回 ``None``。
    """

    if not _IS_WINDOWS or _version is None:  # pragma: no cover - platform guard
        return None
    try:
        target = Path(path)
        if not target.is_file():
            return None
    except OSError:
        return None

    size = _version.GetFileVersionInfoSizeW(str(target), None)
    if not size:
        return None

    buffer = ctypes.create_string_buffer(size)
    if not _version.GetFileVersionInfoW(str(target), 0, size, buffer):
        return None

    block = ctypes.cast(buffer, ctypes.c_void_p)
    fields = []
    for lang_codepage in (*_translations(block), *_FALLBACK_LANG_CODEPAGES):
        description = _query_string(block, f"{lang_codepage}\\FileDescription")
        product = _query_string(block, f"{lang_codepage}\\ProductName")
        version = _query_string(block, f"{lang_codepage}\\FileVersion")
        if description or product or version:
            fields.append(
                VersionInfo(
                    file_description=description,
                    product_name=product,
                    file_version=version,
                )
            )
            break
    if not fields:
        return None
    return fields[0]
