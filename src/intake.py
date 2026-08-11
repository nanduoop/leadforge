#!/usr/bin/env python3
"""
Stage 1: work out who the client is and who they want to reach.

Input can be anything, in any shape:

    python3 src/intake.py --site acme.com
    python3 src/intake.py --text "we do explainer videos for B2B SaaS in the US"
    python3 src/intake.py --file ~/icp.md
    python3 src/intake.py                       # asks from scratch
    python3 src/intake.py --site acme.com --yes  # unattended, no questions

Anything derivable from the site is derived. Whatever is still missing and actually
matters gets asked, one question at a time, in plain language. Nothing is invented:
a field nobody supplied and nothing could infer stays empty and is reported as a gap.

Output is config/brief.json, which every later stage reads. Nothing downstream ever
re-reads the client's website, so the brief is the single source of truth about intent.
"""
import json, os, sys, argparse, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connectors as C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIEF = os.path.join(ROOT, "config", "brief.json")

# The schema Firecrawl fills from the client's own website. Descriptions are the
# actual prompt the extractor reads, so they are written for a model, not a human.
SITE_SCHEMA = {
    "type": "object",
    "properties": {
        "company_name":  {"type": "string", "description": "The company's own name"},
        "what_they_do":  {"type": "string", "description": "One sentence, plain language, what they sell"},
        "services":      {"type": "array", "items": {"type": "string"},
                          "description": "Concrete services or products offered"},
        "who_they_serve": {"type": "string",
                           "description": "The kind of customer they say they serve, in their words"},
        "client_names":  {"type": "array", "items": {"type": "string"},
                          "description": "Named clients, logos or case studies shown on the page"},
        "industries":    {"type": "array", "items": {"type": "string"},
                          "description": "Industries or verticals they mention serving"},
        "value_claim":   {"type": "string",
                          "description": "Their headline promise: faster, cheaper, higher quality"},
    },
}

QUESTIONS = [
    ("offer",         "What do you sell, in one sentence?", True),
    ("outcome",       "What result does a client get from you? A number is better than an adjective.", True),
    ("target_industries", "Which industries are you targeting? Comma separated.", True),
    ("target_titles", "Which job titles actually sign off on buying this? Comma separated.", True),
    ("target_markets", "Which countries or regions? Comma separated.", True),
    ("company_size",  "What company size fits best? e.g. 11-50, 51-200, 200+", False),
    ("buying_signals", "What tells you a company needs you right now? e.g. hiring for a role, "
                       "just raised, posting a lot. Comma separated.", True),
    ("exclusions",    "Who should we never contact? Competitors, agencies, recruiters. Comma separated.", False),
    ("proof",         "Which client names can you point to publicly? Comma separated.", False),
]

LIST_FIELDS = {"target_industries", "target_titles", "target_markets",
               "buying_signals", "exclusions", "proof", "services", "industries"}


def blank_brief():
    return {
        "client": {"name": "", "site": "", "offer": "", "outcome": "",
                   "value_claim": "", "proof": []},
        "icp": {"target_industries": [], "target_titles": [], "target_markets": [],
                "company_size": "", "buying_signals": [], "exclusions": []},
        "meta": {"source": [], "gaps": []},
    }


def normalise(url):
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/")


def from_site(url, brief):
    """Derive everything the client's own website will tell us."""
    url = normalise(url)
    print(f"  reading {url} ...")
    data = C.extract(
        [url, f"{url}/about", f"{url}/services", f"{url}/work", f"{url}/clients"],
        SITE_SCHEMA,
        "Read this company's website and describe what they sell, who they serve, "
        "and which clients they name. Use only what the pages actually say.")

    if not data:
        print("  site extraction returned nothing, falling back to a plain scrape")
        page = C.scrape(url)
        if page["ok"]:
            brief["meta"]["source"].append(f"scrape:{url}")
            text = page["text"][:4000]
            brief["client"]["offer"] = brief["client"]["offer"] or text[:300].strip()
            return brief
        print("  could not read the site at all")
        return brief

    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        return brief

    c = brief["client"]
    c["name"] = c["name"] or (data.get("company_name") or "").strip()
    c["offer"] = c["offer"] or (data.get("what_they_do") or "").strip()
    c["value_claim"] = c["value_claim"] or (data.get("value_claim") or "").strip()
    if data.get("client_names"):
        c["proof"] = sorted(set(c["proof"]) | set(data["client_names"]))
    if data.get("industries"):
        brief["icp"]["target_industries"] = sorted(
            set(brief["icp"]["target_industries"]) | set(data["industries"]))
    if data.get("who_they_serve") and not brief["icp"]["target_industries"]:
        brief["meta"].setdefault("who_they_serve", data["who_they_serve"])

    brief["client"]["site"] = url
    brief["meta"]["source"].append(f"site:{url}")
    print(f"  got: {c['name'] or 'unnamed'} | {(c['offer'] or '')[:70]}")
    return brief


def from_text(text, brief):
    """
    Pull what can be pulled out of freeform text without guessing.

    This stays deliberately conservative. It captures the raw text so nothing is lost
    and picks up only patterns that are unambiguous. Anything requiring interpretation
    is left for the questions, because a wrong inference here silently poisons every
    downstream search.
    """
    brief["meta"]["source"].append("text")
    brief["meta"]["raw_input"] = text.strip()

    if not brief["client"]["offer"]:
        first = re.split(r"[.\n]", text.strip())[0].strip()
        if first:
            brief["client"]["offer"] = first[:300]

    known_markets = ["united states", "usa", "us", "uk", "united kingdom", "canada",
                     "australia", "germany", "france", "india", "singapore", "uae",
                     "netherlands", "spain", "japan", "new zealand", "ireland"]
    low = text.lower()
    hits = [m for m in known_markets if re.search(rf"\b{re.escape(m)}\b", low)]
    if hits and not brief["icp"]["target_markets"]:
        canon = {"usa": "United States", "us": "United States",
                 "uk": "United Kingdom", "uae": "UAE"}
        brief["icp"]["target_markets"] = sorted(
            {canon.get(h, h.title()) for h in hits})
    return brief


def fill_from_answers(brief, answers, overwrite=False):
    """Apply wizard answers to a brief. answers maps field names to values."""
    def current(field):
        return brief["client"].get(field) or brief["icp"].get(field) or ""

    brief["meta"].setdefault("gaps", [])
    for field, _prompt, required in QUESTIONS:
        if not overwrite and current(field):
            continue
        raw = answers.get(field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if required and not current(field):
                if field not in brief["meta"]["gaps"]:
                    brief["meta"]["gaps"].append(field)
            continue
        if field in LIST_FIELDS:
            if isinstance(raw, list):
                value = [str(x).strip() for x in raw if str(x).strip()]
            else:
                value = [p.strip() for p in str(raw).split(",") if p.strip()]
        else:
            value = str(raw).strip()
        if field in brief["client"]:
            brief["client"][field] = value
        else:
            brief["icp"][field] = value
        if field in brief["meta"]["gaps"]:
            brief["meta"]["gaps"].remove(field)
    return brief


def brief_summary(brief):
    """Structured summary for the review step."""
    c, i = brief["client"], brief["icp"]
    return {
        "client_name": c.get("name") or "",
        "site": c.get("site") or "",
        "offer": c.get("offer") or "",
        "outcome": c.get("outcome") or "",
        "industries": i.get("target_industries") or [],
        "titles": i.get("target_titles") or [],
        "markets": i.get("target_markets") or [],
        "company_size": i.get("company_size") or "",
        "signals": i.get("buying_signals") or [],
        "exclusions": i.get("exclusions") or [],
        "proof": c.get("proof") or [],
        "gaps": brief.get("meta", {}).get("gaps") or [],
        "sources": brief.get("meta", {}).get("source") or [],
    }


def questions_for_ui():
    """Return intake questions formatted for the web wizard."""
    client_fields = {"offer", "outcome", "proof"}
    out = []
    for field, prompt, required in QUESTIONS:
        out.append({
            "field": field,
            "prompt": prompt,
            "required": required,
            "section": "client" if field in client_fields else "icp",
            "multiline": field in ("offer", "outcome"),
            "list": field in LIST_FIELDS,
        })
    return out


def save_brief(brief, path=BRIEF):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(brief, open(path, "w"), indent=2)
    return path


def load_brief(path=BRIEF):
    if not os.path.exists(path):
        return blank_brief()
    try:
        return json.load(open(path))
    except Exception:
        return blank_brief()


def ask(brief, auto=False):
    """Fill what is still missing. Skipped entirely under --yes."""
    def current(field):
        return brief["client"].get(field) or brief["icp"].get(field) or ""

    for field, prompt, required in QUESTIONS:
        if current(field):
            continue
        if auto:
            if required:
                brief["meta"]["gaps"].append(field)
            continue

        print(f"\n{prompt}")
        if field == "target_titles" and brief["icp"]["target_industries"]:
            print("  (the person who signs off, not the person who uses it)")
        try:
            answer = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nstopped")
            sys.exit(1)

        if not answer:
            if required:
                brief["meta"]["gaps"].append(field)
            continue

        value = ([p.strip() for p in answer.split(",") if p.strip()]
                 if field in LIST_FIELDS else answer)
        if field in brief["client"]:
            brief["client"][field] = value
        else:
            brief["icp"][field] = value
    return brief


def report(brief):
    c, i = brief["client"], brief["icp"]
    print("\n" + "=" * 62)
    print(f"  {c['name'] or 'client'}  {c['site']}")
    print("=" * 62)
    print(f"  offer      {c['offer'][:80] or '(empty)'}")
    print(f"  outcome    {c['outcome'][:80] or '(empty)'}")
    print(f"  industries {', '.join(i['target_industries'][:6]) or '(empty)'}")
    print(f"  titles     {', '.join(i['target_titles'][:6]) or '(empty)'}")
    print(f"  markets    {', '.join(i['target_markets']) or '(empty)'}")
    print(f"  signals    {', '.join(i['buying_signals'][:5]) or '(empty)'}")
    print(f"  exclude    {', '.join(i['exclusions'][:5]) or '(none)'}")

    gaps = brief["meta"]["gaps"]
    if gaps:
        print(f"\n  {len(gaps)} required field(s) still empty: {', '.join(gaps)}")
        print("  Sourcing will run, but a brief this thin produces weak matches.")
        print("  Fill them in config/brief.json or rerun without --yes.")
    print()


def main():
    ap = argparse.ArgumentParser(description="Build an ICP brief from anything.")
    ap.add_argument("--site", help="client website, the richest single input")
    ap.add_argument("--text", help="freeform description of the client and who they want")
    ap.add_argument("--file", help="path to an ICP doc, brief or notes")
    ap.add_argument("--yes", action="store_true", help="never prompt; record gaps instead")
    ap.add_argument("--out", default=BRIEF)
    a = ap.parse_args()

    brief = blank_brief()
    if os.path.exists(a.out):
        try:
            existing = json.load(open(a.out))
            print(f"note: {os.path.relpath(a.out, ROOT)} exists; filling only empty fields.")
            brief.update({k: v for k, v in existing.items() if k in brief})
        except Exception:
            pass

    if a.file:
        try:
            brief = from_text(open(os.path.expanduser(a.file)).read(), brief)
            print(f"  read {a.file}")
        except Exception as e:
            print(f"  could not read {a.file}: {e}")
    if a.text:
        brief = from_text(a.text, brief)
    if a.site:
        if not C.is_linked("firecrawl"):
            print("  firecrawl is not linked, so the site cannot be read.")
            print("  run:  composio link firecrawl")
        else:
            brief = from_site(a.site, brief)

    if not any([a.site, a.text, a.file]) and a.yes:
        sys.exit("Nothing to work from. Pass --site, --text or --file, or drop --yes.")

    brief = ask(brief, auto=a.yes)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(brief, open(a.out, "w"), indent=2)
    report(brief)
    print(f"wrote {os.path.relpath(a.out, ROOT)}")
    print("next:  python3 src/source.py")


if __name__ == "__main__":
    main()
