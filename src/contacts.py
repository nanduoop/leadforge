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

# Shared inboxes. A message to one of these reaches a queue, not a person, so it
# cannot back a claim that we found a decision maker. An earlier run exported
# three rows for one company all sharing careers@ — the same jobs inbox, three times.
GENERIC_LOCALPARTS = {
    "careers", "career", "jobs", "job", "recruiting", "recruitment", "hiring",
    "hr", "info", "hello", "hey", "contact", "enquiries", "inquiries", "support",
    "help", "admin", "office", "team", "sales", "press", "media", "marketing",
    "billing", "accounts", "legal", "privacy", "security", "noreply", "no-reply",
    "donotreply", "mail", "email", "general", "ask", "welcome", "service",
}

# How many contacts we keep per company. More than two fills the list with the same
# brand and crowds out other companies — the exact failure seen in the 11 Aug run.
MAX_PER_COMPANY = 2

# Words that mean the string is a role, not a human. The old check was
# `len(name.split()) >= 2`, which happily accepted "Associate Director, Project
# Management" as a person's name because it has three words.
TITLE_WORDS = {
    "director", "manager", "engineer", "developer", "designer", "analyst",
    "specialist", "coordinator", "associate", "assistant", "intern", "internship",
    "lead", "head", "chief", "officer", "president", "vp", "svp", "evp", "founder",
    "partner", "consultant", "executive", "supervisor", "administrator",
    "representative", "strategist", "producer", "editor", "writer", "recruiter",
    "architect", "scientist", "technician", "apprentice", "trainee", "generalist",
    "jr", "jr.", "sr", "sr.", "junior", "senior", "staff", "principal",
}


def is_generic_inbox(email):
    """True if this address reaches a queue rather than a named person."""
    local = (email or "").split("@")[0].strip().lower()
    if not local:
        return True
    if local in GENERIC_LOCALPARTS:
        return True
    # careers-uk, jobs.us, hr_india and friends.
    head = re.split(r"[.\-_+]", local)[0]
    return head in GENERIC_LOCALPARTS


def looks_like_person(name):
    """Reject job listings, department names and page furniture posing as people.

    Scraping a careers page returns rows that look structurally identical to a
    team page: a string and a title. The only thing separating them is whether
    the string is plausibly somebody's name.
    """
    n = (name or "").strip()
    if not n or "," in n or "/" in n or "|" in n or " - " in n or " – " in n:
        return False
    if any(ch.isdigit() for ch in n):
        return False

    parts = n.split()
    if not 2 <= len(parts) <= 4:
        return False
    # Any word that is a job title disqualifies the whole string.
    if any(p.lower().strip(".,") in TITLE_WORDS for p in parts):
        return False
    # Real names are capitalised; "open roles" and "SENIOR EDITOR" are not.
    if not all(p[:1].isupper() and not p.isupper() for p in parts if p[:1].isalpha()):
        return False
    # Names shorter than 2 characters per part are likely initials or noise
    if any(len(p.strip(".")) < 2 for p in parts):
        return False
    return True


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
    """Copy the company's own convention. Falls back to first.last@domain if empty."""
    if not (first and last and domain):
        return None
    f, l = first.lower(), last.lower()
    if not known:
        return f"{f}.{l}@{domain}"
    local = known.split("@")[0].lower()
    for shape, out in (
        (f"{f}.{l}", f"{f}.{l}"), (f"{f}_{l}", f"{f}_{l}"),
        (f"{f[0]}{l}", f"{f[0]}{l}"), (f"{f}{l}", f"{f}{l}"), (f, f),
    ):
        if local == shape:
            return f"{out}@{domain}"
    return f"{f}.{l}@{domain}"


def people_from_item(item, origin="team_page"):
    """Pull the people list out of one extraction result, whatever shape it came in.

    Extractors wrap their JSON differently — some return `{"people": [...]}`,
    some nest it under `data`. Anyone without a full name is dropped: a first
    name alone cannot be matched to a company or used to personalise outreach.
    """
    if not isinstance(item, dict):
        return []
    block = item.get("people") or (item.get("data") or {}).get("people") or []
    if not isinstance(block, list):
        return []

    people = []
    for p in block:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not looks_like_person(name):
            continue                       # a role or a heading, not somebody to email
        email = (p.get("email") or "").strip().lower()
        if email and is_generic_inbox(email):
            email = ""                     # shared inbox proves nothing about this person
        people.append({
            "name": name,
            "title": (p.get("title") or "").strip(),
            "email": email,
            "linkedin": (p.get("linkedin") or "").strip(),
            "origin": origin,
        })

    # Same person listed on two pages, or twice on one.
    seen, unique = set(), []
    for p in people:
        key = p["name"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def find_for(lead, wanted, use_browser=False):
    """Return one Lead per person found at this company. Never mutates the input."""
    domain = (lead.domain or "").split("/")[0]
    if not domain or "." not in domain:
        return []

    base = f"https://{domain}"
    prompt = ("List the named individual people on this page with their job titles. "
              "A person is a human being with a personal name. Do NOT list open job "
              "postings, vacancies, departments, teams or office locations, even if "
              "they appear in the same format. If the page is a careers or jobs "
              "listing rather than a team page, return an empty list. "
              "Include an email or LinkedIn URL only if actually shown on the page, "
              "and never a shared inbox like careers@ or info@. "
              "Never invent or guess contact details.")

    people = []
    origin = "team_page"
    for page in PAGES:
        task = R.Task("structured_pages", {
            "url": base + page,
            "schema": PEOPLE_SCHEMA,
            "prompt": prompt,
        }, javascript_heavy=use_browser)
        result = R.run(task)
        if not result.ok:
            continue
        origin = "team_page" if result.source == "firecrawl" else "browser"
        for item in result.items:
            people.extend(people_from_item(item, origin))

    if not people:
        return []

    # Prefer people whose title the client actually asked for. The old fallback was
    # `ranked or people`, so a company with no matching title contributed whoever
    # happened to be on the page — that is how a junior social media manager landed
    # in a list targeting Head of Content. Fall back to at most one person, so an
    # off-target company can still be a foot in the door without crowding the list.
    ranked = [p for p in people if title_matches(p["title"], wanted)]
    chosen = ranked[:MAX_PER_COMPANY] if ranked else people[:1]

    # Only a personal address reveals the company's convention. Guessing from
    # careers@ produces careers-shaped nonsense for everyone else on the page.
    known = next((p["email"] for p in people
                  if "@" in p.get("email", "") and not is_generic_inbox(p["email"])), None)

    out = []
    for p in chosen:
        origin = p["origin"]
        if not p["email"]:
            parts = p["name"].split()
            if len(parts) >= 2:
                guess = pattern_from(known or "", parts[0], parts[-1], domain)
                if guess:
                    p["email"], origin = guess, "pattern"
        if not p["email"] or is_generic_inbox(p["email"]):
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


def enrich(leads, icp, use_browser=False, workers=6, on_progress=None):
    """Called by the orchestrator. Returns one Lead per person found.

    Domain-level dedup ensures each company is scraped exactly once, even if
    multiple discovery paths found the same domain. This prevents the output
    from being dominated by a single brand.
    """
    wanted = icp.get("target_titles", [])

    # Deduplicate by domain: keep the lead with the most evidence per domain.
    by_domain = {}
    for l in leads:
        d = (l.domain or "").split("/")[0].lower()
        if not d:
            continue
        if d not in by_domain or len(l.evidence) > len(by_domain[d].evidence):
            by_domain[d] = l
    unique_leads = list(by_domain.values())

    found = []
    total = len(unique_leads)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(find_for, l, wanted, use_browser): l for l in unique_leads}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                found.extend(fut.result())
            except Exception:
                continue
            if on_progress:
                on_progress(i, total, len(found))
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
