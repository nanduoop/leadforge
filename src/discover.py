#!/usr/bin/env python3
"""
Stage 2: discovery across several independent paths, run in parallel.

    python3 src/discover.py
    python3 src/discover.py --paths hiring,signal,direct --limit 60
    python3 src/discover.py --dry-run          # print the plan, spend nothing

One search returns one slice of a market. Several different kinds of search return
overlapping slices, and the overlap is the useful part: a company found by a job
board, a funding story and its own careers page is real in a way that a company found
by one lucky keyword is not. That overlap becomes corroboration on the lead record.

Five paths, each finding companies the others structurally cannot:

  hiring    open roles on public ATS boards. The strongest buying signal there is,
            because somebody has budget and a deadline.
  signal    the client's own stated triggers: raised a round, launched, expanding.
  direct    plain industry and market, catching companies with no open roles at all.
  social    platform chatter via Agent Reach, for intent that never reaches a job board.
  semantic  meaning-based discovery, for companies whose vocabulary differs from ours.

Every query is dispatched through the router, so each one lands on whichever source
is actually available, and every result becomes Evidence with a typed source rather
than an untyped URL.
"""
import json, os, sys, argparse, re
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import router as R
from schema import Lead, Evidence, save, domain_of
import dedup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIEF = os.path.join(ROOT, "config", "brief.json")
OUT = os.path.join(ROOT, "data", "discovered.json")

ATS = ["boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
       "apply.workable.com", "careers.smartrecruiters.com"]

# Hosts that rank for our queries and are never themselves a prospect.
JUNK = {
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
    "simplyhired.com", "reddit.com", "quora.com", "medium.com", "youtube.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "pinterest.com",
    "wikipedia.org", "crunchbase.com", "g2.com", "capterra.com", "clutch.co",
    "upwork.com", "fiverr.com", "freelancer.com", "builtin.com", "wellfound.com",
    "angel.co", "remoteok.com", "weworkremotely.com", "jooble.org", "talent.com",
    "lensa.com", "adzuna.com", "careerjet.com", "glass.ai", "zippia.com",
}


def plan(brief, cap=60, paths=None, allow_no_market=False):
    """Expand the ICP into concrete queries, tagged by which path produced them."""
    icp = brief["icp"]
    industries = [i for i in icp.get("target_industries", []) if i.strip()]
    titles = [t for t in icp.get("target_titles", []) if t.strip()]
    markets = [m for m in icp.get("target_markets", []) if m.strip()] or [""]
    signals = [s for s in icp.get("buying_signals", []) if s.strip()]
    want = set(paths or ["hiring", "signal", "direct", "social", "semantic"])

    if not industries and not titles:
        sys.exit("The brief has no industries and no titles.\n"
                 "Fix config/brief.json or rerun: python3 src/intake.py")

    # A brief with no geography used to run anyway, unscoped. That is worse than
    # failing: the queries look reasonable, cost real credits, and return the wrong
    # market entirely. Anything sold on local authority is meaningless without a place.
    if not [m for m in icp.get("target_markets", []) if str(m).strip()]:
        if not allow_no_market:
            sys.exit(
                "\nSTOPPED: the brief has no target_markets.\n\n"
                "Discovery would run unscoped and return the wrong market at full cost.\n"
                "Set target_markets in config/brief.json, for example:\n"
                '    "target_markets": ["Dallas Fort Worth", "Texas"]\n\n'
                "If a deliberately global search is what you want:\n"
                "    python3 src/discover.py --allow-no-market\n")
        print("  WARNING: no target_markets. Running unscoped at your request.\n")

    # The roles a prospect hires when they need what the client sells. These come from
    # the buying signals, NOT from target_titles. Those are two different people: a
    # company hiring a video editor is the signal, and the Head of Content is who we
    # then contact about it. Searching ATS boards for buyer titles finds companies
    # recruiting marketing leaders, which is a different market entirely.
    hire_roles = []
    for s in signals:
        m = re.match(r"^\s*hiring\s+(.*)$", s.strip(), re.I)
        if m:
            role = m.group(1).strip()
            if role.endswith("s") and not role.endswith("ss"):
                role = role[:-1]                   # "video editors" -> "video editor"
            hire_roles.append(role)
    other_signals = [s for s in signals
                     if not re.match(r"^\s*hiring\s+", s.strip(), re.I)]

    # Each path builds its full candidate list independently. The cap is applied
    # afterwards by round-robin, so a small --limit narrows every path evenly
    # instead of spending the entire budget on whichever path was built first.
    buckets = {}

    if "hiring" in want and hire_roles:
        b = []
        for r in hire_roles[:3]:
            for m in markets[:2]:
                where = f" {m}" if m else ""
                # The industry MUST stay in the query. Whether an open role is a buying
                # signal depends entirely on who posted it. "Hiring a video editor" is a
                # signal at any company, so an industry-free query worked for that ICP.
                # "Hiring a marketing coordinator" is a signal only at, say, a private
                # school; unscoped it returns every company on earth with a marketing
                # opening. Dropping the industry here silently changed which market was
                # being searched.
                for ind in (industries[:3] or [""]):
                    scope = f' "{ind}"' if ind else ""
                    for board in ATS[:2]:
                        b.append((f'"{r}"{scope}{where} site:{board}',
                                  f"Company is hiring: {r}"))
                    # Their own careers page outranks an ATS board as evidence.
                    b.append((f'"{r}"{scope}{where} careers -site:indeed.com '
                              f'-site:linkedin.com -site:glassdoor.com',
                              f"Company is hiring: {r}"))
        buckets["hiring"] = b

    if "signal" in want:
        b = []
        for s in (other_signals or signals)[:3]:
            for ind in (industries[:2] or [""]):
                m = markets[0] if markets[0] else ""
                b.append((f"{ind} company {s} {m}".strip(), f"Buying signal: {s}"))
        buckets["signal"] = b

    if "direct" in want:
        b = []
        for ind in industries[:4]:
            for m in markets[:2]:
                where = f" in {m}" if m else ""
                b.append((f"{ind} companies{where}", f"Matches ICP: {ind}"))
        buckets["direct"] = b

    if "social" in want and signals:
        buckets["social"] = [(f"companies discussing {s}", f"Discussing: {s}")
                             for s in signals[:2]]

    if "semantic" in want and industries:
        seed = f"{industries[0]} {signals[0] if signals else ''} {markets[0]}".strip()
        buckets["semantic"] = [(seed, f"Semantic match: {industries[0]}")]

    buckets = {k: v for k, v in buckets.items() if v}
    if not buckets:
        sys.exit("No queries could be built from this brief. Check buying_signals "
                 "and target_industries in config/brief.json.")

    # Round-robin so every path gets a fair share of the budget. Hiring goes first
    # in each cycle because it carries the strongest intent.
    order = [p for p in ["hiring", "signal", "direct", "social", "semantic"]
             if p in buckets]
    seen, out, i = set(), [], 0
    while len(out) < cap and any(i < len(buckets[p]) for p in order):
        for path in order:
            if len(out) >= cap:
                break
            if i < len(buckets[path]):
                query, claim = buckets[path][i]
                k = query.lower()
                if k not in seen:
                    seen.add(k)
                    out.append((path, query, claim))
        i += 1
    return out


def host_of(url):
    try:
        h = urlparse(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


# A page that lists companies is not a company. These pages rank extremely well for
# exactly the queries that find real prospects ("top consumer brands in the US"), so
# they are matched by shape rather than by host. A blocklist of hosts never keeps up;
# there is always another listicle farm.
LISTICLE = re.compile(
    r"^\s*(the\s+)?(\d+\s+)?(top|best|leading|greatest|biggest|largest|"
    r"list\s+of|directory\s+of|guide\s+to|examples?\s+of|companies\s+like)\b"
    r"|^\s*\d+\s+(top|best|companies|startups|brands|agencies)\b"
    r"|\b(companies|startups|brands|agencies|firms)\s+(in|to\s+watch|hiring|list)\b"
    r"|\bvs\.?\s|\balternatives?\b|\bcomparison\b",
    re.I)


def is_listicle(title):
    t = (title or "").strip()
    if not t:
        return False
    if LISTICLE.search(t):
        return True
    # "Acme, Betches, Chorus and 40 more" style roundups
    return t.count(",") >= 3


def company_from(url, title):
    """
    Work out which company a result belongs to.

    ATS URLs are special: boards.greenhouse.io/acme is Acme, not Greenhouse. Getting
    this wrong collapses every company on a board into one record.
    """
    host = host_of(url)
    if not host:
        return None, None

    if any(host.endswith(a) for a in ATS):
        parts = [p for p in urlparse(url).path.split("/") if p]
        if not parts:
            return None, None
        slug = parts[0]
        return slug.replace("-", " ").title(), f"{slug}.com"

    root = domain_of(host)
    if root in JUNK or host in JUNK:
        return None, None

    # Only applied off the ATS boards. An ATS slug is an employer by construction,
    # and a job title there can legitimately read like a listicle.
    if is_listicle(title):
        return None, None

    name = (title or "").split("|")[0].split(" - ")[0].strip() or root.split(".")[0]
    if len(name) > 60:                             # headline, not a company name
        name = root.split(".")[0].title()
    return name[:80], root


def execute_path(path, query, claim, limit):
    """Run one query through the router and turn hits into leads with evidence."""
    capability = "social" if path == "social" else "web_search"
    task = R.Task(capability, {"query": query, "limit": limit})
    result = R.run(task)

    leads = []
    if not result.ok:
        return leads, result

    for item in result.items:
        url = item.get("url", "")
        name, dom = company_from(url, item.get("title", ""))
        if not name or not dom:
            continue
        lead = Lead(company=name, domain=dom)
        lead.sources = [f"{path}:{result.source}"]
        lead.signals = [path] if path in ("hiring", "signal", "social") else []
        lead.add_evidence(Evidence(
            claim=claim, url=url,
            source_type=R.classify_url(url) or "search_result",
            excerpt=(item.get("description") or item.get("title") or "")[:300]))
        leads.append(lead)
    return leads, result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60, help="max queries")
    ap.add_argument("--per-query", type=int, default=10)
    ap.add_argument("--paths", default="", help="comma separated subset of paths")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-no-market", action="store_true",
                    help="run unscoped when the brief has no target_markets")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    if not os.path.exists(BRIEF):
        sys.exit("No config/brief.json. Run:  python3 src/intake.py --site <client-site>")
    brief = json.load(open(BRIEF))

    paths = [p.strip() for p in a.paths.split(",") if p.strip()] or None
    queries = plan(brief, cap=a.limit, paths=paths,
                   allow_no_market=a.allow_no_market)

    counts = {}
    for p, _, _ in queries:
        counts[p] = counts.get(p, 0) + 1
    print(f"\n{len(queries)} queries across {len(counts)} paths")
    for p, n in sorted(counts.items()):
        print(f"  {p:10} {n}")
    print(f"\nestimated cost ~{len(queries) * 2} firecrawl credits")

    if a.dry_run:
        print("\ndry run. queries:")
        for p, q, _ in queries[:20]:
            print(f"  [{p:8}] {q}")
        return

    avail = R.availability()
    if not any(avail.values()):
        sys.exit("\nNo discovery source available. Run:  composio link firecrawl")

    print(f"\nrunning in parallel ...")
    all_leads, statuses = [], {}
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(execute_path, p, q, c, a.per_query): (p, q)
                for p, q, c in queries}
        for i, fut in enumerate(as_completed(futs), 1):
            p, q = futs[fut]
            try:
                leads, res = fut.result()
            except Exception as e:
                statuses.setdefault("error", 0)
                statuses["error"] += 1
                continue
            all_leads.extend(leads)
            statuses[res.status] = statuses.get(res.status, 0) + 1
            if i % 10 == 0 or i == len(queries):
                print(f"  {i}/{len(queries)} queries, {len(all_leads)} raw hits")

    print("\nsource outcomes:")
    for st, n in sorted(statuses.items(), key=lambda x: -x[1]):
        print(f"  {st:14} {n}")
    if statuses.get("rate_limited"):
        print("  note: rate limits hit. Rerun later to pick up the missed queries.")

    leads = dedup.run(all_leads)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    save(leads, a.out)

    multi = sum(1 for l in leads if l.corroboration > 1)
    print(f"\n{len(leads)} unique companies, {multi} corroborated by 2+ sources\n")
    for l in sorted(leads, key=lambda x: x.corroboration, reverse=True)[:15]:
        print(f"  {l.corroboration}x  {l.company[:32]:34} {l.domain[:26]:28} "
              f"{','.join(sorted(set(s.split(':')[0] for s in l.sources)))}")
    print(f"\nwrote {os.path.relpath(a.out, ROOT)}")
    print("next:  python3 src/contacts.py")


if __name__ == "__main__":
    main()
