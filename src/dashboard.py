#!/usr/bin/env python3
"""HTML dashboard. The output a human actually reads.

    python3 src/dashboard.py data/scored.json -o data/dashboard.html
    python3 src/dashboard.py --title "Client leads"

CSV is a machine format. ChatGPT, Claude, and client-facing agents skip
browser automation and hand this file over instead.

Rules:
  Unverified fields are shown, never hidden.
  Every claim links to its source URL.
  Self-contained: no CDN, no fonts, no scripts.
"""
import json, sys, os, argparse, html
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCE_LABEL = {
    "irs_990":         ("IRS Form 990", "filed under penalty of perjury", 0.98),
    "sec_filing":      ("SEC filing", "regulated disclosure", 0.98),
    "company_site":    ("Company site", "their own words", 0.95),
    "company_careers": ("Careers page", "employer controlled", 0.95),
    "ats_board":       ("ATS board", "employer controlled", 0.90),
    "press_release":   ("Press release", "company issued", 0.85),
    "news":            ("News", "third party", 0.75),
    "linkedin":        ("LinkedIn", "self reported", 0.75),
    "structured_db":   ("B2B database", "vendor data", 0.70),
    "social":          ("Social", "self reported", 0.50),
    "search_result":   ("Search result", "snippet only", 0.45),
    "aggregator":      ("Aggregator", "republished, may be stale", 0.30),
    "inferred":        ("Inferred", "derived by us, unconfirmed", 0.20),
}

CSS = """
:root{
  --bg:#faf9f7; --panel:#fff; --ink:#1a1a1a; --muted:#6b6b6b; --line:#e6e3de;
  --accent:#c05621; --accent-soft:#fdf0e8;
  --ok:#1a7f4b; --ok-soft:#e8f5ee; --warn:#8a6d1f; --warn-soft:#fdf6e3;
  --bad:#a33; --bad-soft:#fbeaea;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#17181a; --panel:#1f2124; --ink:#eceae7; --muted:#9a9793; --line:#2e3135;
  --accent:#e8834a; --accent-soft:#2a1d15;
  --ok:#5cc98d; --ok-soft:#14291d; --warn:#d9b95c; --warn-soft:#2a2413;
  --bad:#e88; --bad-soft:#2b1618;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:1140px;margin:0 auto;padding:32px 20px 72px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:14px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0 32px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.stat .n{font-size:22px;font-weight:600}
.stat .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-top:2px}
.lead{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:18px}
.lead-top{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:baseline}
.co{font-size:19px;font-weight:600}
.loc{color:var(--muted);font-size:13px;margin-left:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:16px 0}
.f{border-left:2px solid var(--line);padding-left:12px}
.f .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.f .v{margin-top:3px;word-break:break-word}
.f .v a{color:var(--accent);text-decoration:none}
.tag{display:inline-block;font-size:11px;padding:2px 7px;border-radius:4px;font-weight:500}
.t-ok{background:var(--ok-soft);color:var(--ok)}
.t-warn{background:var(--warn-soft);color:var(--warn)}
.t-bad{background:var(--bad-soft);color:var(--bad)}
.why{background:var(--accent-soft);border-radius:8px;padding:12px 14px;margin:14px 0;font-size:14px}
.why .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--accent);font-weight:600;margin-bottom:4px}
.ev{margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
.ev h4{margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.ev-row{display:flex;gap:10px;padding:8px 0;border-bottom:1px dashed var(--line);font-size:13px}
.ev-w{flex:0 0 108px}
.ev-b{flex:1;min-width:0}
.ev-claim{font-weight:500}
.ev-x{color:var(--muted);font-size:12px;margin-top:2px}
.ev-b a{color:var(--accent);font-size:12px;text-decoration:none;word-break:break-all}
.scores{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px;font-size:12px;color:var(--muted)}
footer{color:var(--muted);font-size:12px;margin-top:36px;text-align:center}
"""


def esc(s):
    return html.escape(str(s or ""))


def tag(text, kind):
    return f'<span class="tag t-{kind}">{esc(text)}</span>'


def field(key, value_html):
    return f'<div class="f"><div class="k">{esc(key)}</div><div class="v">{value_html}</div></div>'


def _as_dict(lead):
    if isinstance(lead, dict):
        return lead
    c = lead.contact or {}
    scores = lead.scores or {}
    loc = ""
    if isinstance(lead.company_data, dict):
        loc = lead.company_data.get("location") or lead.company_data.get("city") or ""
    evidence = []
    for e in getattr(lead, "evidence", []) or []:
        evidence.append({
            "claim": getattr(e, "claim", "") or (e.get("claim") if isinstance(e, dict) else ""),
            "url": getattr(e, "url", "") or (e.get("url") if isinstance(e, dict) else ""),
            "excerpt": getattr(e, "excerpt", "") or (e.get("excerpt") if isinstance(e, dict) else ""),
            "type": getattr(e, "source_type", "") or (e.get("source_type") or e.get("type") if isinstance(e, dict) else ""),
        })
    why = ""
    try:
        from score import why_now
        why = why_now(lead)
    except Exception:
        why = ", ".join(getattr(lead, "signals", []) or [])
    v = lead.verification or {}
    return {
        "company": lead.company,
        "domain": lead.domain,
        "city": loc,
        "contact": c,
        "scores": scores,
        "why_now": why,
        "evidence": evidence,
        "mx_ok": not bool(v.get("reject_reason")),
        "mx": (v.get("mx") or ""),
        "unverified": [v["reject_reason"]] if v.get("reject_reason") else [],
    }


def render_lead(d):
    if not isinstance(d, dict):
        d = _as_dict(d)
    c = d.get("contact", {}) or {}
    scores = d.get("scores") or {}
    parts = [f'<div class="lead"><div class="lead-top"><div><span class="co">{esc(d.get("company"))}</span>'
             f'<span class="loc">{esc(d.get("city"))}</span></div>']
    if scores.get("priority") is not None:
        parts.append(f'<div class="rev"><div class="amt">{esc(scores.get("priority"))}</div>'
                     f'<div class="src">priority</div></div>')
    parts.append("</div>")

    email = c.get("email")
    origin = c.get("email_origin") or c.get("origin") or ""
    if email:
        badge = tag("published", "ok") if origin in ("published", "team_page", "browser") else tag("pattern guess", "warn")
        email_html = f'<a href="mailto:{esc(email)}">{esc(email)}</a> {badge}'
    else:
        email_html = f'<span style="color:var(--muted)">none published</span> {tag("not found", "bad")}'

    parts.append('<div class="grid">')
    parts.append(field("Decision maker", f'<strong>{esc(c.get("name") or "unknown")}</strong><br>'
                                         f'<span style="color:var(--muted);font-size:13px">{esc(c.get("title") or "")}</span>'))
    parts.append(field("Email", email_html))
    parts.append(field("Domain", f'<a href="https://{esc(d.get("domain"))}" target="_blank" rel="noopener">{esc(d.get("domain"))}</a>'))
    if scores:
        parts.append(field("Scores", f'fit {esc(scores.get("fit", "—"))} · intent {esc(scores.get("intent", "—"))} · confidence {esc(scores.get("confidence", "—"))}'))
    parts.append("</div>")

    if d.get("why_now"):
        parts.append(f'<div class="why"><div class="k">Why now</div>{esc(d["why_now"])}</div>')

    ev = sorted(d.get("evidence", []) or [],
                key=lambda e: SOURCE_LABEL.get(e.get("type", ""), ("", "", 0))[2], reverse=True)
    if ev:
        parts.append('<div class="ev"><h4>Evidence — every claim, with its source</h4>')
        for e in ev:
            label, note, w = SOURCE_LABEL.get(e.get("type", ""), ("Unknown", "unclassified", 0.2))
            kind = "ok" if w >= 0.85 else ("warn" if w >= 0.45 else "bad")
            url = e.get("url") or ""
            parts.append(
                f'<div class="ev-row"><div class="ev-w">{tag(label, kind)}</div>'
                f'<div class="ev-b"><div class="ev-claim">{esc(e.get("claim"))}</div>'
                f'<div class="ev-x">{esc(e.get("excerpt"))[:210]}</div>'
                f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(url)}</a>'
                f'</div></div>')
        parts.append("</div>")

    if d.get("unverified"):
        parts.append('<div class="unv"><div class="k">Not verified — do not present as fact</div><ul>')
        for u in d["unverified"]:
            parts.append(f"<li>{esc(u)}</li>")
        parts.append("</ul></div>")

    parts.append("</div>")
    return "".join(parts)


def _normalise(leads):
    out = []
    for l in leads:
        if hasattr(l, "company") and not isinstance(l, dict):
            out.append(_as_dict(l))
        else:
            out.append(l)
    return out


def build(leads, title, note=None):
    leads = _normalise(leads)
    with_email = sum(1 for l in leads if (l.get("contact") or {}).get("email"))
    ev_count = sum(len(l.get("evidence") or []) for l in leads)
    stats = [
        (len(leads), "leads"),
        (f"{with_email}/{len(leads)}", "email found"),
        (ev_count, "evidence items"),
    ]
    h = [f"<title>{esc(title)}</title>", f"<style>{CSS}</style>", '<div class="wrap">',
         f'<header><h1>{esc(title)}</h1><div class="sub">Generated '
         f'{datetime.now():%d %B %Y, %H:%M} · every claim links to its source</div></header>',
         '<div class="stats">']
    for n, l in stats:
        h.append(f'<div class="stat"><div class="n">{esc(n)}</div><div class="l">{esc(l)}</div></div>')
    h.append("</div>")
    if note:
        h.append(f'<div class="note">{note}</div>')
    for l in leads:
        h.append(render_lead(l))
    h.append('<footer>LeadForge · nothing here is asserted without a source · '
             'unverified fields are listed, never hidden</footer></div>')
    return "\n".join(h)


def render_leads(leads, title="Verified leads", note=None):
    return build(leads, title, note)


def to_html(leads, path, title="Verified leads", note=None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w").write(render_leads(leads, title, note))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "data", "dashboard.html"))
    ap.add_argument("--title", default="Verified leads")
    ap.add_argument("--note", default=None)
    a = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from schema import load as load_leads
    try:
        leads = load_leads(a.infile)
    except Exception:
        raw = json.load(open(a.infile))
        leads = raw.get("leads", raw) if isinstance(raw, dict) else raw

    to_html(leads, a.out, a.title, a.note)
    print(f"wrote {a.out}  ({len(leads)} leads, {os.path.getsize(a.out):,} bytes)")


if __name__ == "__main__":
    main()
