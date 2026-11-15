from __future__ import annotations

import re

TOKEN_KIND = "a secret or token"

_TOKEN = re.compile(
    r"\b(?:sk|pk|rk)_(?:live|test)_\w+"
    r"|\blin_api_\w+"
    r"|\bghp_\w+"
    r"|\bgithub_pat_\w+"
    r"|\bsk-ant-[\w-]+"
    r"|\bAKIA[0-9A-Z]{16}"
    r"|\bxox[abposr]-[\w-]+"
    r"|eyJ[\w-]{20,}"
)

# Token patterns come first: find_sensitive returns the first match.
_PATTERNS = [
    (_TOKEN, TOKEN_KIND),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "an email address"),
    (re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"), "a phone number"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "an SSN-like number"),
    (re.compile(r"\b(dob|date of birth|mrn|medical record|patient name)\b", re.I), "patient details"),
]


def find_sensitive(text: str) -> str | None:
    for pattern, kind in _PATTERNS:
        if pattern.search(text):
            return kind
    return None


def redact(text: str) -> str:
    return _TOKEN.sub("[redacted]", text)
