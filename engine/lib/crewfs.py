"""The one way sf, the board and the guard touch files inside a crew's write roots.

Those processes run outside the sandbox, and sandboxed code can plant a symlink, a FIFO or a hard link anywhere
under a write root. So every access here starts from an fd on the root itself and walks each component with
O_NOFOLLOW: a symlink at any level is refused, never followed. Files must be regular, single-linked and under a
size cap; opens never block on a FIFO. Writes create with O_EXCL and land by rename relative to a walked dir fd.
"""
from __future__ import annotations

import contextlib
import os
import secrets
import stat
from pathlib import Path

from . import state

MAX_TEXT = 1 << 20
_CHUNK = 1 << 16


class CrewFsError(OSError):
    pass


def _parts(rel) -> list[str]:
    parts = Path(rel).parts
    if not parts or Path(rel).is_absolute() or any(p in ("", ".", "..") for p in parts):
        raise CrewFsError(f"not a plain relative path: {rel}")
    return list(parts)


def _root_fd(root, create: bool) -> int:
    root = Path(root)
    if create:
        state.make_dir(root)
    real = os.path.realpath(root)
    fd = os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    a, b = os.fstat(fd), os.stat(root)
    if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
        os.close(fd)
        raise CrewFsError(f"{root} moved while being opened")
    return fd


def _down(fd: int, name: str, what: str) -> int:
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    except FileNotFoundError:
        raise
    except OSError as e:
        raise CrewFsError(f"{name} in {what} is a link or not a folder ({e.strerror})") from None


@contextlib.contextmanager
def folder(root, rel=None, create: bool = False):
    """An fd on root/rel, every component walked without following links; `create` makes missing folders."""
    fd = _root_fd(root, create)
    try:
        for name in _parts(rel) if rel not in (None, "", ".") else []:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(name, 0o755, dir_fd=fd)
            nxt = _down(fd, name, str(rel))
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def _regular(root, rel, limit: int):
    parts = _parts(rel)
    with folder(root, Path(*parts[:-1]) if len(parts) > 1 else None) as d:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=d)
        except FileNotFoundError:
            raise
        except OSError as e:
            raise CrewFsError(f"{rel} is a link or can't be opened ({e.strerror})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise CrewFsError(f"{rel} is not a regular file")
        if st.st_nlink != 1:
            raise CrewFsError(f"{rel} has other hard links")
        if st.st_size > limit:
            raise CrewFsError(f"{rel} is larger than {limit} bytes")
        yield fd
    finally:
        os.close(fd)


def read_bytes(root, rel, limit: int = MAX_TEXT) -> bytes:
    with _regular(root, rel, limit) as fd:
        data = b""
        while len(data) <= limit:
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                return data
            data += chunk
    raise CrewFsError(f"{rel} grew past {limit} bytes")


def copy_out(root, rel, dst, limit: int) -> Path:
    """Stream root/rel into `dst`, a new file somewhere sf alone writes."""
    with _regular(root, rel, limit) as fd, open(dst, "xb") as out:
        n = 0
        while chunk := os.read(fd, _CHUNK):
            n += len(chunk)
            if n > limit:
                raise CrewFsError(f"{rel} grew past {limit} bytes")
            out.write(chunk)
    return Path(dst)


def exists(root, rel) -> bool:
    """Whether root/rel names anything, a link included; a link above it is refused."""
    parts = _parts(rel)
    try:
        with folder(root, Path(*parts[:-1]) if len(parts) > 1 else None) as d:
            os.stat(parts[-1], dir_fd=d, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def write_bytes(root, rel, data: bytes) -> None:
    """Create root/rel with `data`: written to a fresh O_EXCL file, then renamed over whatever was there."""
    parts = _parts(rel)
    with folder(root, Path(*parts[:-1]) if len(parts) > 1 else None, create=True) as d:
        tmp = f".{parts[-1]}.{secrets.token_hex(6)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=d)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.rename(tmp, parts[-1], src_dir_fd=d, dst_dir_fd=d)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=d)
            raise


def move_in(src, root, rel, replace: bool = True) -> None:
    """Rename `src` (sf's own file or folder) to root/rel. A link already at rel is replaced, never followed;
    with replace=False anything already there is refused."""
    parts = _parts(rel)
    with folder(root, Path(*parts[:-1]) if len(parts) > 1 else None, create=True) as d:
        if not replace:
            with contextlib.suppress(FileNotFoundError):
                os.stat(parts[-1], dir_fd=d, follow_symlinks=False)
                raise FileExistsError(f"{rel} already exists")
        os.rename(src, parts[-1], dst_dir_fd=d)


def remove_tree(root, rel) -> None:
    """Delete root/rel and everything under it. Each folder is opened no-follow through its parent's fd, so a link
    anywhere in the tree is removed itself, never followed; a folder swapped for a link mid-walk is refused."""
    parts = _parts(rel)
    try:
        with folder(root, Path(*parts[:-1]) if len(parts) > 1 else None) as d:
            _remove(d, parts[-1], str(rel))
    except FileNotFoundError:
        return


def _remove(d: int, name: str, what: str) -> None:
    if not stat.S_ISDIR(os.stat(name, dir_fd=d, follow_symlinks=False).st_mode):
        os.unlink(name, dir_fd=d)
        return
    fd = _down(d, name, what)
    try:
        for child in os.listdir(fd):
            _remove(fd, child, what)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=d)


def locate(roots, path: str, base=None) -> tuple:
    """(root, rel) for a path a crew named inside one of `roots`, matched as written or through the root's real
    path, ignoring case as macOS does. A relative path is taken relative to `base` (the ticket folder)."""
    if base is not None and not os.path.isabs(path):
        path = os.path.join(str(base), path)
    if not os.path.isabs(path) or ".." in path.split("/"):
        raise CrewFsError(f"not a plain absolute path: {path}")
    p = os.path.normpath(path)
    for r in roots:
        for form in dict.fromkeys((str(r).rstrip("/"), os.path.realpath(r))):
            if p.casefold().startswith(form.casefold() + "/"):
                return r, p[len(form) + 1:]
    raise CrewFsError(f"{path} is not inside the crew's write roots")
