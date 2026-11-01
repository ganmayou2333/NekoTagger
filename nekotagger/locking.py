"""跨进程文件锁。

MCP 服务是常驻进程，CLI 也会随时写同一个 ``cache.json``；没有锁的话，后写的
会把先写的结果覆盖掉（实测场景：一边 `corpus` 一边查 MCP）。

Windows 用 ``msvcrt.locking``，其他平台用 ``fcntl.flock``；拿不到锁时等待重试，
超时就抛 ``TimeoutError``——宁可让调用方知道失败，也不静默丢数据。
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_IS_WINDOWS = sys.platform.startswith("win")
DEFAULT_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL = 0.05


class LockTimeout(TimeoutError):
    """在超时时间内没有拿到锁。"""


@contextmanager
def file_lock(
    path: str | os.PathLike[str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """对 ``<path>.lock`` 加排它锁，退出时释放。"""

    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")  # noqa: SIM115 - 需要跨 with 持有
    try:
        _acquire(handle, timeout, lock_path)
        try:
            yield
        finally:
            _release(handle)
    finally:
        handle.close()


def _acquire(handle, timeout: float, lock_path: Path) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if _IS_WINDOWS:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - 开发环境可能是 Linux
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise LockTimeout(f"等待缓存锁超时：{lock_path}") from None
            time.sleep(_POLL_INTERVAL)


def _release(handle) -> None:
    try:
        if _IS_WINDOWS:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        # 释放失败不应该掩盖业务异常；进程退出时系统也会释放。
        pass
