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
    # Ensure the fd is not inherited by children; avoids leaking privileged dirfds.
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
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
        # Open atomically with O_NOFOLLOW to prevent TOCTOU symlink races, and O_CLOEXEC to avoid fd leaks.
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(name, flags, dir_fd=dirfd)
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            os.close(fd)
            raise
    except FileNotFoundError:
        return None


def write_replace_text_in_dir(dirfd: int, dst_name: str, content: str, *, mode: int = 0o600) -> None:
    """Write content to a temp file in dirfd and atomically replace dst_name.

    Refuses if dst_name is a symlink.
    """
    if is_symlink_in_dir(dirfd, dst_name):
        raise RuntimeError("Refusing to overwrite symlink: " + dst_name)
    tmp_name = f".__tmp.{os.getpid()}.{int(time.time()*1e6)}"
    # Create a unique temp file; use CLOEXEC to avoid leaks. O_NOFOLLOW guards
    # against an attacker pre-creating a symlink with our tmp_name.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    # Use caller-provided mode (default 0600) to avoid exposing sensitive content
    # during the small window before rename when the directory may be world-readable.
    fd = os.open(tmp_name, flags, mode, dir_fd=dirfd)
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
    # Best-effort: fsync the directory to make the metadata update (rename) durable
    # so the entry survives a crash/power-loss. Some filesystems may not support
    # directory fsync; ignore errors to avoid surfacing non-critical failures.
    try:
        os.fsync(dirfd)
    except Exception:
        pass


def unlink_in_dir(dirfd: int, name: str) -> None:
    if is_symlink_in_dir(dirfd, name):
        raise RuntimeError("Refusing to remove symlink: " + name)
    os.unlink(name, dir_fd=dirfd)
