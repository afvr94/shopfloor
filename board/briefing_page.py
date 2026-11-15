from __future__ import annotations

import re

from engine.lib.briefing import MAX_ITEMS, MAX_LEN, PR_URL

# Callers must use BRIEF_PATH.fullmatch: "$" would accept a trailing newline.
BRIEF_PATH = re.compile(r"^/briefing/([a-z0-9][a-z0-9-]{0,30})/([A-Za-z0-9][A-Za-z0-9-]{0,19})$")


def esc(t: object) -> str:
    return (str("" if t is None else t).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;"))


def clip(t: object, n: int) -> str:
    t = str("" if t is None else t)
    return t if len(t) <= n else t[: n - 1] + "…"


def _list(v: object) -> list:
    return v if isinstance(v, list) else []


def _section(title: str, body: str, cls: str = "") -> str:
    return f'<section class="{cls}"><h2>{title}</h2>{body}</section>'


def _ul(tag: str, items: list, n: int = 400) -> str:
    return f"<{tag}>" + "".join(f"<li>{esc(clip(i, n))}</li>" for i in items) + f"</{tag}>"


_CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1c1e;--muted:#6b6b70;--card:#f5f5f7;--line:#d9d9de;--accent:#0b5fff;--risk:#b42318}
@media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececee;--muted:#9a9aa1;--card:#202024;--line:#34343a;--accent:#7aa7ff;--risk:#ff8a7a}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0 0 4px;line-height:1.3}h2{font-size:15px;color:var(--muted);margin:0 0 6px}
.meta{color:var(--muted);font-size:13px}.prs a{color:var(--accent);margin-right:12px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-top:12px}
section.risk{border-color:var(--risk)}p{margin:0}ul,ol{margin:0;padding-left:20px}
pre.mermaid{background:transparent;margin:0;overflow-x:auto;text-align:center}
"""

_MERMAID = ('<script src="/static/mermaid.min.js"></script>'
            '<script>mermaid.initialize({ startOnLoad: true, securityLevel: "strict" });</script>')


def render_briefing(data: dict, crew_id: str, key: str) -> str:
    prs = []
    for p in _list(data.get("prs"))[:MAX_ITEMS["prs"]]:
        if not isinstance(p, dict):
            continue
        label = esc(f"{p.get('repo', '')} #{p.get('number', '')}".strip())
        url = str(p.get("url") or "")
        prs.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{label}</a>'
                   if PR_URL.fullmatch(url) else label)
    rows = []
    if data.get("whatWhy"):
        rows.append(_section("What changed and why", f"<p>{esc(clip(data['whatWhy'], MAX_LEN['whatWhy']))}</p>"))
    if _list(data.get("how")):
        rows.append(_section("How it works now", _ul("ol", _list(data["how"])[:MAX_ITEMS["how"]], MAX_LEN["how"])))
    diagram = str(data.get("diagram") or "").strip()
    if diagram and len(diagram) <= MAX_LEN["diagram"]:
        rows.append(_section("The changed path", f'<pre class="mermaid">{esc(diagram)}</pre>'))
    if data.get("risk"):
        rows.append(_section("Risk", f"<p>{esc(clip(data['risk'], MAX_LEN['risk']))}</p>", "risk"))
    if _list(data.get("verify")):
        rows.append(_section("How to verify", _ul("ul", _list(data["verify"])[:MAX_ITEMS["verify"]], MAX_LEN["verify"])))
    if _list(data.get("files")):
        rows.append(_section("Files", _ul("ul", _list(data["files"])[:MAX_ITEMS["files"]], MAX_LEN["files"])))
    meta = " · ".join(x for x in (esc(crew_id), esc(key), esc(data.get("size") or "")) if x)
    title = esc(clip(data.get("title") or key, MAX_LEN["title"]))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Briefing: {esc(key)}</title><style>{_CSS}</style></head><body><main>
<h1>{title}</h1>
<div class="meta">{meta}</div><div class="prs">{"".join(prs)}</div>
{"".join(rows) or "<section><p>This briefing is empty.</p></section>"}
</main>{_MERMAID if diagram and len(diagram) <= MAX_LEN["diagram"] else ""}</body></html>
"""
