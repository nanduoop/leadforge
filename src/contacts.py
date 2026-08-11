#!/usr/bin/env python3
"""
Stage 4: find the person who can say yes, with evidence for how we found them.

    python3 src/contacts.py
    python3 src/contacts.py --browser --max-companies 40

Two rules hold throughout, both learned from watching this fail:

  A guessed address is labelled a guess. It carries origin="pattern", gets evidence
  typed "inferred" (weight 0.20), and stage 5 decides whether it survives. It is
  never presented as found.

  A person without a full name is not a contact. A blank name produces "Hi ," in an
  email, which is worse than sending nothing.

Companies publish their own leadership on their own site, so team pages are the first
and cheapest source and need no paid database at all. Apollo and Clay are deliberately
not used here: Apollo's bulk export is paywalled and its connection is EXPIRED, and
Clay's title filter silently returns unfiltered results through MCP, which once
produced a contact list containing four VCs, a professor and a celebrity.
"""
import sys, os, re, argparse, json
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import router as R
from schema import Lead, Evidence, load, save

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIEF = os.path.join(ROOT, "config", "brief.json")
IN = os.path.join(ROOT, "data", "qualified.json")
OUT = os.path.join(ROOT, "data", "contacts.json")

PEOPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string", "description": "Full name of the person"},
                    "title": {"type": "string", "description": "Their job title"},
                    "email": {"type": "string", "description": "Email, only if shown on the page"},
                    "linkedin": {"type": "string", "description": "LinkedIn URL, only if shown"},
                },
            },
        }
    },
}

# Only the three paths that actually pay. Every extra path is mostly 404s, and the
# extractor spends real time on them. Measured: 6 paths took >25 min for 8 companies.
PAGES = ["", "/about", "/team"]


def title_matches(title, wanted):
    t = (title or "").lower().strip()
    if not t:
        return False
    for w in wanted:
        w = (w or "").lower().strip()
        if not w:
            continue
        if w in t:
            return True
        words = [x for x in re.split(r"\W+", w) if len(x) > 3]
        if words and all(x in t for x in words):
            return True
    return False


def pattern_from(known, first, last, domain):
    """Copy the company's own convention. Only ever called with a real address."""
    if not (known and first and last):
        return None
    local = known.split("@")[0].lower()
    f, l = first.lower(), last.lower()
    for shape, out in (
        (f"{f}.{l}", f"{f}.{l}"), (f"{f}_{l}", f"{f}_{l}"),
        (f"{f[0]}{l}", f"{f[0]}{l}"), (f"{f}{l}", f"{f}{l}"), (f, f),
    ):
        if local == shape:
            return f"{out}@{domain}"
    return None


def find_for(lead, wanted, use_browser=False):
    """Return one Lead per person found at this company. Never mutates the input."""
    domain = (lead.domain or "").split("/")[0]
    if not domain or "." not in domain:
        return []

    base = f"https://{domain}"
    urls = [base + p for p in PAGES]

    task = R.Task("structured_pages", {
        "urls": urls, "schema": PEOPLE_SCHEMA,
        "prompt": ("List the people named on these pages with their job titles. "
                   "Include an email or LinkedIn URL only if actually shown on the page. "
                   "Never invent or guess contact details."),
    }, javascript_heavy=use_browser)

    result = R.run(task)
    people = []
    if result.ok:
        for item in result.items:
            if not isinstance(item, dict):
                continue
            block = item.get("people") or (item.get("data") or {}).get("people") or []
            for p in block:
                if not isinstance(p, dict):
                    continue
                name = (p.get("name") or "").strip()
                if not name or len(name.split()) < 2:
                    continue                       # no full name, cannot personalise
                people.append({
                    "name": name,
                    "title": (p.get("title") or "").strip(),
                    "email": (p.get("email") or "").strip().lower(),
                    "linkedin": (p.get("linkedin") or "").strip(),
                    "origin": "team_page" if result.source == "firecrawl" else "browser",
                })

    if not people:
        return []

    ranked = [p for p in people if title_matches(p["title"], wanted)]
    chosen = (ranked or people)[:3]
    known = next((p["email"] for p in people if "@" in p.get("email", "")), None)

    out = []
    for p in chosen:
        origin = p["origin"]
        if not p["email"] and known:
            parts = p["name"].split()
            guess = pattern_from(known, parts[0], parts[-1], domain)
            if guess:
                p["email"], origin = guess, "pattern"
        if not p["email"]:
            continue                               # nothing to verify, nothing to send

        person = Lead(company=lead.company, domain=lead.domain)
        person.company_data = dict(lead.company_data)
        person.signals = list(lead.signals)
        person.sources = list(lead.sources) + [f"contacts:{result.source}"]
        person.contact = {**p, "origin": origin}
        for e in lead.evidence:
            person.add_evidence(e)

        claim = f"{p['name']} is {p['title'] or 'a contact'} at {lead.company}"
        if origin == "pattern":
            person.add_evidence(Evidence(
                claim=f"Email for {p['name']} is {p['email']}",
                url=base, source_type="inferred",
                excerpt=f"Derived from the company pattern seen in {known}"))
            person.add_evidence(Evidence(claim=claim, url=base,
                                         source_type="company_site",
                                         excerpt=f"{p['name']}, {p['title']}"))
        else:
            person.add_evidence(Evidence(claim=claim, url=base,
                                         source_type="company_site",
                                         excerpt=f"{p['name']}, {p['title']}"))
        person.contact["title_match"] = title_matches(p["title"], wanted)
        out.append(person)
    return out


def enrich(leads, icp, use_browser=False, workers=6):
    """Called by the orchestrator. Returns one Lead per person found."""
    wanted = icp.get("target_titles", [])
    found = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(find_for, l, wanted, use_browser): l for l in leads}
        for fut in as_completed(futs):
            try:
                found.extend(fut.result())
            except Exception:
                continue
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=IN)
    ap.add_argument("--max-companies", type=int, default=50)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--browser", action="store_true",
                    help="prefer Browserbase for JS-rendered team pages")
    a = ap.parse_args()

    if not os.path.exists(a.inp):
        sys.exit("No data/qualified.json. Run:  python3 src/run.py --from qualify")
    leads = load(a.inp)[:a.max_companies]
    icp = json.load(open(BRIEF))["icp"]

    print(f"\nsearching {len(leads)} companies for decision makers")
    print(f"  target titles: {', '.join(icp.get('target_titles', [])[:5]) or '(none)'}\n")

    out = enrich(leads, icp, a.browser, a.workers)
    save(out, OUT)

    by_origin = {}
    for l in out:
        o = l.contact.get("origin", "?")
        by_origin[o] = by_origin.get(o, 0) + 1
    matched = sum(1 for l in out if l.contact.get("title_match"))

    for l in out[:20]:
        flag = "*" if l.contact.get("title_match") else " "
        print(f" {flag} {l.contact['name'][:22]:24} {l.contact['title'][:26]:28} "
              f"{l.contact['email'][:34]:36} ({l.contact['origin']})")

    print(f"\n{len(out)} contacts, {matched} match a target title")
    for o, n in sorted(by_origin.items()):
        note = "   <- guessed, verification will test these" if o == "pattern" else ""
        print(f"  {n:4} {o}{note}")
    print(f"\nwrote {os.path.relpath(OUT, ROOT)}")
    print("next:  python3 src/verify.py")


if __name__ == "__main__":
    main()
