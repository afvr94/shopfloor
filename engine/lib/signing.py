"""HMAC signatures on the records sf trusts: ticket state, the commands queue and its cursor, lessons and the
validated briefing.

Those files live where no crew's sandbox may write, but Claude Code's own Write and Edit tools run outside the
sandbox and are checked only by the guard, at hook time; a path race there could still land a crew's bytes in
one. The key sits in the secrets dir, which the guard and the sandbox both keep crews from reading, so a crew
can't forge a signature. A record that fails the check is reported as tampered and never acted on.

Each signature covers a `scope` naming the record (for example "ticket/<crew>/<KEY>"), so a signed record copied
from one file into another fails too. Replaying an older signed copy of the same file is not detected.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat

from . import paths

KEY_BYTES = 32
FIELD = "mac"


class Tampered(ValueError):
    pass


def key_path():
    return paths.shopfloor_home() / "secrets" / "sf.key"


def _key() -> bytes:
    p = key_path()
    if not os.path.lexists(p):
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # written whole under a private name, then linked into place: a second first use sees all of it or none
        tmp = p.with_name(f".sf.key.{secrets.token_hex(6)}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(os.urandom(KEY_BYTES))
            os.link(tmp, p)
        except FileExistsError:
            pass
        finally:
            os.unlink(tmp)
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        data = f.read(KEY_BYTES + 1)
    if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600 or len(data) != KEY_BYTES:
        raise OSError(f"{p} must be a {KEY_BYTES}-byte file with mode 0600")
    return data


def mac(scope: str, obj) -> str:
    body = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(_key(), f"{scope}\n{body}".encode(), hashlib.sha256).hexdigest()


def seal(scope: str, obj: dict) -> dict:
    data = {k: v for k, v in obj.items() if k != FIELD}
    return {**data, FIELD: mac(scope, data)}


def unseal(scope: str, obj: object, what: str) -> dict:
    """The record without its signature; Tampered if the signature is missing or wrong."""
    if not isinstance(obj, dict) or not isinstance(obj.get(FIELD), str):
        raise Tampered(f"record tampered: {what} is not signed")
    data = {k: v for k, v in obj.items() if k != FIELD}
    if not hmac.compare_digest(obj[FIELD], mac(scope, data)):
        raise Tampered(f"record tampered: {what} fails its signature check")
    return data
