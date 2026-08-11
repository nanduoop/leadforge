#!/usr/bin/env python3
"""
Deterministic source routing.

The rule this file enforces: the model decides WHAT information is needed, and this
router decides WHICH infrastructure fetches it. A model that picks its own tool on
every call picks differently on identical inputs, and the pipeline stops being
reproducible or debuggable.

So routing is a pure function of the task's properties:

    interaction needed        -> BROWSER      (Stagehand on Browserbase)
    bulk web extraction       -> FIRECRAWL
    social / specialist plat. -> AGENT_REACH
    structured B2B records    -> CLAY
    action in a business app  -> COMPOSIO     (Sheets, CRM, verification APIs)

Each capability also declares a fallback chain, so an unavailable or rate-limited
source degrades to the next best one instead of failing the run. Every call returns
a SourceResult with an explicit status, so "blocked" never masquerades as "no results".
"""
import sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connectors as C
from schema import SourceResult, Evidence

BROWSER, FIRECRAWL, AGENT_REACH, CLAY, COMPOSIO = (
    "browser", "firecrawl", "agent_reach", "clay", "composio")

# Ordered preference per capability. First available wins; the rest are fallbacks.
CHAINS = {
    "web_search":       [FIRECRAWL, AGENT_REACH],
    "page_extract":     [FIRECRAWL, BROWSER],
    "structured_pages": [FIRECRAWL, BROWSER],
    "interactive":      [BROWSER],
    "social":           [AGENT_REACH],
    "b2b_records":      [CLAY],
    "app_action":       [COMPOSIO],
}


class Task:
    """
    What is needed, described in terms of the information, never the tool.

    Callers set the properties that are true of the task and the router picks. This
    is the seam that keeps tool choice out of the callers.
    """

    def __init__(self, capability, payload, requires_interaction=False,
                 requires_login=False, javascript_heavy=False):
        self.capability = capability
        self.payload = payload
        self.requires_interaction = requires_interaction
        self.requires_login = requires_login
        self.javascript_heavy = javascript_heavy


def availability():
    caps = C.available()
    return {
        FIRECRAWL:   caps["firecrawl"],
        BROWSER:     caps["browserbase"],
        AGENT_REACH: caps["agent_reach"],
        CLAY:        caps["_connections"].get("clay") == ["ACTIVE"],
        COMPOSIO:    caps["composio"],
    }


def choose(task, avail=None):
    """
    Pick the source. Deterministic: same task, same availability, same answer.

    Interaction and login force the browser regardless of preference, because no
    amount of crawling reaches a page behind a click or a session.
    """
    avail = avail if avail is not None else availability()

    if task.requires_interaction or task.requires_login:
        return BROWSER if avail.get(BROWSER) else None

    chain = list(CHAINS.get(task.capability, [FIRECRAWL]))
    if task.javascript_heavy and BROWSER in chain:
        chain.remove(BROWSER)
        chain.insert(0, BROWSER)

    for source in chain:
        if avail.get(source):
            return source
    return None


# --------------------------------------------------------------------- execution

def _firecrawl_search(payload):
    q, limit = payload["query"], payload.get("limit", 10)
    r = C.execute("FIRECRAWL_SEARCH", {"q": q, "limit": min(limit, 100)})
    if not r["ok"]:
        err = (r["error"] or "").lower()
        status = ("rate_limited" if "rate" in err or "429" in err
                  else "blocked" if "403" in err or "forbid" in err
                  else "error")
        return SourceResult(FIRECRAWL, status, error=r["error"] or "", cost=2)
    d = r["data"] or {}
    hits = d.get("web") or d.get("results") or (d if isinstance(d, list) else [])
    items = [{"title": h.get("title", ""), "url": h.get("url", ""),
              "description": h.get("description") or h.get("snippet", "")}
             for h in hits if isinstance(h, dict) and h.get("url")]
    return SourceResult(FIRECRAWL,
                        "success" if items else "not_found",
                        items=items, cost=2)


def _agent_reach_search(payload):
    items = C.agent_reach(payload["query"], payload.get("limit", 10))
    return SourceResult(AGENT_REACH,
                        "success" if items else "not_found", items=items)


def _firecrawl_extract(payload):
    urls = payload.get("urls")
    if urls is None:
        url = payload.get("url")
        urls = [url] if url else []
    if not urls:
        return SourceResult(FIRECRAWL, "error", error="no url(s)", cost=2)
    data = C.extract(urls, payload["schema"], payload["prompt"])
    if data is None:
        return SourceResult(FIRECRAWL, "error", error="extract returned nothing", cost=2)
    return SourceResult(FIRECRAWL, "success", items=[data], cost=2)


def _browser_extract(payload):
    urls = payload.get("urls") or [payload.get("url")]
    r = C.browser_extract(urls[0], payload.get("prompt", "Extract the main content."),
                          payload.get("schema"))
    if not r["ok"]:
        return SourceResult(BROWSER, "error", error=r["error"] or "")
    return SourceResult(BROWSER, "success", items=[r["data"]], cost=10)


HANDLERS = {
    (FIRECRAWL, "web_search"):       _firecrawl_search,
    (AGENT_REACH, "web_search"):     _agent_reach_search,
    (AGENT_REACH, "social"):         _agent_reach_search,
    (FIRECRAWL, "page_extract"):     _firecrawl_extract,
    (FIRECRAWL, "structured_pages"): _firecrawl_extract,
    (BROWSER, "page_extract"):       _browser_extract,
    (BROWSER, "structured_pages"):   _browser_extract,
    (BROWSER, "interactive"):        _browser_extract,
}


def run(task, avail=None, retries=1):
    """
    Execute one task through the chosen source, falling back down the chain.

    A retryable failure (rate limit, transient error) backs off once, then moves to
    the next source rather than burning the run on one flaky provider.
    """
    avail = avail if avail is not None else availability()
    chain = list(CHAINS.get(task.capability, [FIRECRAWL]))

    if task.requires_interaction or task.requires_login:
        chain = [BROWSER]
    elif task.javascript_heavy and BROWSER in chain:
        chain.remove(BROWSER)
        chain.insert(0, BROWSER)

    tried, last = [], None
    for source in chain:
        if not avail.get(source):
            tried.append(f"{source}:unavailable")
            continue
        handler = HANDLERS.get((source, task.capability))
        if not handler:
            tried.append(f"{source}:no-handler")
            continue

        for attempt in range(retries + 1):
            try:
                result = handler(task.payload)
            except Exception as e:
                result = SourceResult(source, "error", error=f"{type(e).__name__}: {e}")
            last = result
            if result.ok:
                result.error = result.error or ""
                return result
            if result.retryable and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        tried.append(f"{source}:{last.status if last else '?'}")

    return last or SourceResult("none", "error",
                                error=f"no source available (tried {', '.join(tried) or 'nothing'})")


def evidence_from(result, claim, source_type=None):
    """Turn a SourceResult into Evidence objects, typed by where it actually came from."""
    default_type = {FIRECRAWL: "search_result", AGENT_REACH: "search_result",
                    BROWSER: "company_site", CLAY: "structured_db"}
    out = []
    for item in result.items:
        if not isinstance(item, dict):
            continue
        url = item.get("url", "")
        st = source_type or classify_url(url) or default_type.get(result.source, "search_result")
        out.append(Evidence(claim=claim, url=url, source_type=st,
                            excerpt=(item.get("description") or item.get("title") or "")[:300]))
    return out


def classify_url(url):
    """Type a URL by host, so evidence weight reflects the real source."""
    u = (url or "").lower()
    if not u:
        return None
    ats = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
           "smartrecruiters.com", "bamboohr.com", "workablehq.com")
    if any(a in u for a in ats):
        return "ats_board"
    if "linkedin.com" in u:
        return "linkedin"
    if any(b in u for b in ("indeed.com", "glassdoor", "ziprecruiter", "monster.com",
                            "simplyhired", "talent.com", "adzuna", "jooble")):
        return "aggregator"
    if any(s in u for s in ("twitter.com", "x.com", "instagram.com", "reddit.com",
                            "facebook.com", "tiktok.com", "youtube.com")):
        return "social"
    if any(n in u for n in ("techcrunch", "reuters", "bloomberg", "forbes",
                            "businesswire", "prnewswire")):
        return "news"
    if "/careers" in u or "/jobs" in u:
        return "company_careers"
    return None


if __name__ == "__main__":
    avail = availability()
    print("\nSource availability\n" + "-" * 40)
    for src, ok in avail.items():
        print(f"  {'ok ' if ok else '-- '} {src}")

    print("\nRouting decisions\n" + "-" * 40)
    cases = [
        ("web_search",       Task("web_search", {})),
        ("page_extract",     Task("page_extract", {})),
        ("js-heavy extract", Task("page_extract", {}, javascript_heavy=True)),
        ("needs login",      Task("interactive", {}, requires_login=True)),
        ("social",           Task("social", {})),
        ("b2b records",      Task("b2b_records", {})),
    ]
    for label, t in cases:
        print(f"  {label:20} -> {choose(t, avail) or 'NONE AVAILABLE'}")
    print()
