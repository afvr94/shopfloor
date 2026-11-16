"""HMAC signatures on the records sf trusts: ticket state, the commands queue and its cursor, lessons and the
validated briefing.

Those files live in folders sf keeps read-only while idle, out of every crew's sandbox write roots. The
signature is the second line: a record that fails the check is reported as tampered and never acted on. It
proves sf wrote the record, not that the owner chose what it says; sf signs whatever text a crew passes it.

Each signature covers a `scope` naming the record (for example "ticket/<crew>/<KEY>"), so a signed record copied
from one file into another fails too.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat

from . import paths

KEY_BYTES = 32
FIELD = "mac"
_MAC = re.compile("[0-9a-f]{64}")
KEY_DIR_MODE, KEY_MODE = 0o500, 0o400


class Tampered(ValueError):
    pass


def key_path():
    return paths.shopfloor_home() / "secrets" / "signing" / "sf.key"


def _create(p) -> None:
    d = p.parent
    d.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(FileExistsError):
        os.mkdir(d, 0o700)
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # a second first use waits, then finds the key
        if os.path.lexists(p):
            return
        os.fchmod(fd, 0o700)
        try:
            tmp = d / f".sf.key.{secrets.token_hex(6)}"
            kfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, KEY_MODE)
            with os.fdopen(kfd, "wb") as f:
                f.write(os.urandom(KEY_BYTES))
            os.rename(tmp, p)
        finally:
            os.fchmod(fd, KEY_DIR_MODE)
    finally:
        os.close(fd)


def key_problem() -> str | None:
    """Why the key can't be used, or None. A key that doesn't exist yet is made on first use."""
    p = key_path()
    fix = f"fix: chmod 500 {p.parent} && chmod 400 {p}, or delete {p.parent} to make a new key"
    try:
        dst = os.lstat(p.parent)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(dst.st_mode) or stat.S_IMODE(dst.st_mode) != KEY_DIR_MODE or dst.st_uid != os.getuid():
        return f"{p.parent} must be a folder you own with mode 0500 ({fix})"
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"{p} can't be opened ({e.strerror}; {fix})"
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != KEY_MODE or st.st_uid != os.getuid() \
            or st.st_size != KEY_BYTES:
        return f"{p} must be a {KEY_BYTES}-byte regular file you own with mode 0400 ({fix})"
    return None


def _key() -> bytes:
    p = key_path()
    if not os.path.lexists(p):
        _create(p)
    dfd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(dfd, fcntl.LOCK_SH)  # waits out a first use that is still making the key
        problem = key_problem()
        if problem:
            raise OSError(f"signing key refused: {problem}")
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    finally:
        os.close(dfd)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        data = f.read(KEY_BYTES + 1)
    if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != KEY_MODE or len(data) != KEY_BYTES:
        raise OSError(f"signing key refused: {p} changed while being read")
    return data


def mac(scope: str, obj) -> str:
    body = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hmac.new(_key(), f"{scope}\n{body}".encode("ascii"), hashlib.sha256).hexdigest()


def seal(scope: str, obj: dict) -> dict:
    data = {k: v for k, v in obj.items() if k != FIELD}
    return {**data, FIELD: mac(scope, data)}


def unseal(scope: str, obj: object, what: str) -> dict:
    """The record without its signature; Tampered if the signature is missing, malformed or wrong."""
    if not isinstance(obj, dict) or not isinstance(obj.get(FIELD), str) or not _MAC.fullmatch(obj[FIELD]):
        raise Tampered(f"record tampered: {what} is not signed")
    data = {k: v for k, v in obj.items() if k != FIELD}
    try:
        want = mac(scope, data)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise Tampered(f"record tampered: {what} can't be checked") from None
    if not hmac.compare_digest(obj[FIELD].encode("ascii"), want.encode("ascii")):
        raise Tampered(f"record tampered: {what} fails its signature check")
    return data


def read(scope: str, text: str, what: str) -> dict:
    """Parse and unseal one record's text; anything that isn't a well-formed signed record is Tampered."""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        raise Tampered(f"record tampered: {what} is not valid JSON") from None
    return unseal(scope, obj, what)
