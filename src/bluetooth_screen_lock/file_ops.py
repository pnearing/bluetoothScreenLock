"""Dirfd-anchored, symlink-safe filesystem helpers.

These helpers are used to harden XDG autostart operations against TOCTOU and
symlink attacks by operating relative to a directory file descriptor (dirfd).

All functions refuse to follow symlinks when opening or replacing files.
Callers are responsible for closing any returned dirfd via os.close().
"""
from __future__ import annotations

import os
import stat
import time
from typing import Optional


def open_dir_nofollow(path: str) -> int:
    """Open a directory without following symlinks; verify ownership and type.

    Returns a dirfd that callers MUST close with os.close().
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    dirfd = os.open(path, flags)
    st = os.fstat(dirfd)
    if not stat.S_ISDIR(st.st_mode):
        os.close(dirfd)
        raise RuntimeError(f"Not a directory: {path}")
    if st.st_uid != os.getuid():
        os.close(dirfd)
        raise RuntimeError(f"Refusing to use non-owned directory: {path}")
    return dirfd


def open_autostart_dirfd() -> int:
    autostart_dir = os.path.join(os.path.expanduser("~"), ".config", "autostart")
    os.makedirs(autostart_dir, exist_ok=True)
    # Refuse if the path itself is a symlink
    st = os.lstat(autostart_dir)
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"Refusing to use symlinked autostart dir: {autostart_dir}")
    return open_dir_nofollow(autostart_dir)


def is_symlink_in_dir(dirfd: int, name: str) -> bool:
    try:
        st = os.lstat(name, dir_fd=dirfd)
        return stat.S_ISLNK(st.st_mode)
    except FileNotFoundError:
        return False


def exists_in_dir(dirfd: int, name: str) -> bool:
    try:
        os.lstat(name, dir_fd=dirfd)
        return True
    except FileNotFoundError:
        return False


def read_text_in_dir(dirfd: int, name: str) -> Optional[str]:
    try:
        if is_symlink_in_dir(dirfd, name):
            raise RuntimeError("Refusing to read symlink: " + name)
        fd = os.open(name, os.O_RDONLY, dir_fd=dirfd)
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            os.close(fd)
            raise
    except FileNotFoundError:
        return None


def write_replace_text_in_dir(dirfd: int, dst_name: str, content: str) -> None:
    """Write content to a temp file in dirfd and atomically replace dst_name.

    Refuses if dst_name is a symlink.
    """
    if is_symlink_in_dir(dirfd, dst_name):
        raise RuntimeError("Refusing to overwrite symlink: " + dst_name)
    tmp_name = f".__tmp.{os.getpid()}.{int(time.time()*1e6)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp_name, flags, 0o644, dir_fd=dirfd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            os.unlink(tmp_name, dir_fd=dirfd)
        except Exception:
            pass
        raise
    os.replace(tmp_name, dst_name, src_dir_fd=dirfd, dst_dir_fd=dirfd)


def unlink_in_dir(dirfd: int, name: str) -> None:
    if is_symlink_in_dir(dirfd, name):
        raise RuntimeError("Refusing to remove symlink: " + name)
    os.unlink(name, dir_fd=dirfd)
