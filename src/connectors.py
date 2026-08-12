#!/usr/bin/env python3
"""
Every outside call in LeadForge goes through this file.

The point is that the rest of the codebase never learns whether a capability came from
Composio, from the Stagehand SDK, or from a local binary. Sourcing asks for `search()`
and gets results. If Firecrawl is down or unlinked, the same call falls through to
another backend and the pipeline keeps running with fewer sources rather than dying.

Three backends, chosen deliberately:

  Composio    managed auth for Firecrawl, Google Sheets, NeverBounce, ZeroBounce,
              HubSpot, Maps. One `composio link <app>` per app and no key ever lands
              in a file. This is the "single connector" layer.
  Stagehand   Browserbase's cloud browser, called directly. Composio's browserbase_tool
              connection exposes no usable tool slugs (checked 11 Aug 2026), and
              LinkedIn through Composio is ad-targeting only, with no people search.
              So anything JS-heavy or login-gated comes through here.
  local       agent-reach and browse, if present on PATH. Optional everywhere.

Nothing here raises on a missing tool. `available()` reports what is usable and the
pipeline degrades to whatever is connected.
"""
import json, os, subprocess, shutil, time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSIO = os.path.expanduser("~/.composio/composio")
_CACHE = {}


# --------------------------------------------------------------------------- secrets

def secret(name, default=None):
    """
    Read a credential without ever committing one.

    Order: real environment, then .env, then config/secrets.json, then ~/.zshrc.
    Everything after the first is gitignored.

    `.env` is the portable layer — it is the one a container, a CI job or a cloud
    agent can be handed. The zshrc fallback below is the opposite: a local macOS
    patch. BROWSERBASE_API_KEY is exported there, but a non-interactive shell does
    not source zshrc, so the key goes invisible exactly when automation runs
    unattended. Keep it for existing machines; do not rely on it anywhere else.
    """
    if os.environ.get(name):
        return os.environ[name]

    if "dotenv" not in _CACHE:
        found = {}
        try:
            with open(os.path.join(ROOT, ".env")) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if v:
                        found[k.strip()] = v
        except Exception:
            pass
        _CACHE["dotenv"] = found
    if _CACHE["dotenv"].get(name):
        return _CACHE["dotenv"][name]

    if "secrets" not in _CACHE:
        path = os.path.join(ROOT, "config", "secrets.json")
        try:
            _CACHE["secrets"] = json.load(open(path))
        except Exception:
            _CACHE["secrets"] = {}
    if _CACHE["secrets"].get(name):
        return _CACHE["secrets"][name]

    if "zshrc" not in _CACHE:
        found = {}
        try:
            with open(os.path.expanduser("~/.zshrc")) as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("export "):
                        continue
                    body = line[7:]
                    if "=" not in body:
                        continue
                    k, v = body.split("=", 1)
                    found[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
        _CACHE["zshrc"] = found
    return _CACHE["zshrc"].get(name, default)


# ------------------------------------------------------------------------- composio

def composio_ready():
    return os.path.exists(COMPOSIO) or shutil.which("composio") is not None


def _composio_bin():
    return COMPOSIO if os.path.exists(COMPOSIO) else "composio"


def connections():
    """{app: [status, ...]}. Cached, since this shells out and is slow."""
    if "conns" in _CACHE:
        return _CACHE["conns"]
    out = {}
    if composio_ready():
        try:
            r = subprocess.run([_composio_bin(), "connections", "list"],
                               capture_output=True, text=True, timeout=90)
            data = json.loads(r.stdout)
            out = {app: [c.get("status", "?") for c in conns]
                   for app, conns in data.items()}
        except Exception:
            out = {}
    _CACHE["conns"] = out
    return out


def is_linked(app):
    return "ACTIVE" in connections().get(app, [])


def execute(slug, data, timeout=180, retries=2):
    """
    Run one Composio tool. Returns {ok, data, error}.

    Composio's own envelope has `successful` plus a nested `data`, and different
    toolkits nest their payload differently. This unwraps one level of the common
    {data: {data: ...}} shape so callers see something predictable, and never raises.
    """
    if not composio_ready():
        return {"ok": False, "data": None, "error": "composio CLI not installed"}

    payload = json.dumps(data)
    last = "unknown"
    for attempt in range(retries + 1):
        try:
            r = subprocess.run(
                [_composio_bin(), "execute", slug, "-d", payload],
                capture_output=True, text=True, timeout=timeout)
            body = json.loads(r.stdout)
            if body.get("successful"):
                d = body.get("data")
                if isinstance(d, dict) and isinstance(d.get("data"), (dict, list)):
                    d = d["data"]
                return {"ok": True, "data": d, "error": None}
            last = body.get("error") or "tool reported failure"
        except subprocess.TimeoutExpired:
            last = f"timed out after {timeout}s"
        except json.JSONDecodeError:
            last = (r.stdout or r.stderr or "")[:300] or "unparseable response"
        except Exception as e:
            last = str(e)[:300]
        if attempt < retries:
            time.sleep(2 ** attempt)          # 1s, 2s. Rate limits recover fast.
    return {"ok": False, "data": None, "error": last}


def execute_many(calls, workers=8):
    """
    Run many Composio tools at once. `calls` is [(key, slug, data), ...].
    Returns {key: result}.

    Parallelism is the whole reason sourcing finishes in minutes instead of an hour.
    Threads are correct here rather than processes: every call is a subprocess waiting
    on network, so this is IO-bound and the GIL is never the constraint.
    """
    out = {}
    if not calls:
        return out
    with ThreadPoolExecutor(max_workers=min(workers, len(calls))) as pool:
        futs = {pool.submit(execute, slug, data): key for key, slug, data in calls}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                out[key] = fut.result()
            except Exception as e:
                out[key] = {"ok": False, "data": None, "error": str(e)[:200]}
    return out


# ------------------------------------------------------------------- search / scrape

def search(query, limit=10):
    """
    Web search. Returns [{title, url, description, source}].

    Firecrawl bills ~2 credits per call, so callers should batch through
    execute_many rather than looping one query at a time.
    """
    r = execute("FIRECRAWL_SEARCH", {"q": query, "limit": min(limit, 100)})
    if not r["ok"]:
        return []
    d = r["data"] or {}
    hits = d.get("web") or d.get("results") or (d if isinstance(d, list) else [])
    out = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        url = h.get("url") or h.get("link")
        if not url:
            continue
        out.append({
            "title": (h.get("title") or "").strip(),
            "url": url,
            "description": (h.get("description") or h.get("snippet") or "").strip(),
            "source": "firecrawl_search",
        })
    return out


def scrape(url, formats=("markdown",)):
    """
    Fetch one page as text with multi-layer fallback.
    Order: Firecrawl -> Browserbase -> Local HTTP Fetch -> Agent Reach
    """
    # 1. Firecrawl (if linked)
    if is_linked("firecrawl"):
        r = execute("FIRECRAWL_SCRAPE", {"url": url, "formats": list(formats)})
        if r["ok"]:
            d = r["data"] or {}
            text = d.get("markdown") or d.get("html") or d.get("content") or ""
            if not text and isinstance(d.get("data"), dict):
                inner = d["data"]
                text = inner.get("markdown") or inner.get("content") or ""
            if text.strip():
                return {"ok": True, "text": text, "url": url, "source": "firecrawl", "error": None}

    # 2. Browserbase / Stagehand (if key present)
    if browser_available():
        res = browser_extract(url, "Extract all main visible text content from the page")
        if res["ok"] and res.get("data"):
            val = str(res["data"])
            if len(val) > 50:
                return {"ok": True, "text": val, "url": url, "source": "browserbase", "error": None}

    # 3. Local HTTP Fetch fallback
    import urllib.request, re, ssl, gzip, html as html_lib
    for scheme in ("https://", "http://"):
        try:
            target = url if url.startswith(("http://", "https://")) else scheme + url
            req = urllib.request.Request(
                target,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept-Encoding": "identity",
                }
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                raw_bytes = resp.read()
                if resp.info().get("Content-Encoding") == "gzip" or raw_bytes[:2] == b"\x1f\x8b":
                    try:
                        raw_bytes = gzip.decompress(raw_bytes)
                    except Exception:
                        pass
                raw_text = raw_bytes.decode("utf-8", errors="ignore")
                text = html_lib.unescape(raw_text)
                text = re.sub(r"<script.*?>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<style.*?>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<.*?>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 40:
                    return {"ok": True, "text": text, "url": target, "source": "local_http", "error": None}
        except Exception:
            continue

    # 4. Agent Reach fallback (semantic search for domain)
    if has_local("agent-reach"):
        hits = agent_reach(url, limit=3)
        if hits:
            text = " ".join([f"{h.get('title', '')}: {h.get('description', '')}" for h in hits])
            if text.strip():
                return {"ok": True, "text": text, "url": url, "source": "agent_reach", "error": None}

    return {"ok": False, "text": "", "url": url, "error": "all fetch methods failed"}


def _is_rate_limited(error):
    err = (error or "").lower()
    return "rate limit" in err or "rate_limit" in err


def _unwrap_json(d):
    """Pull structured json from a FIRECRAWL_SCRAPE response."""
    if not isinstance(d, dict):
        return None
    if isinstance(d.get("json"), dict):
        return d["json"]
    inner = d.get("data")
    if isinstance(inner, dict) and isinstance(inner.get("json"), dict):
        return inner["json"]
    return None


def extract(urls, schema, prompt):
    """
    Pull structured fields off pages via FIRECRAWL_SCRAPE + formats:json.

    SCRAPE takes a single url, so this loops and merges people arrays. Replaces
    the deprecated FIRECRAWL_EXTRACT path that cost 21 credits and returned empty.
    """
    merged_people = []
    other = {}
    for url in urls:
        r = execute("FIRECRAWL_SCRAPE", {
            "url": url,
            "formats": ["json"],
            "jsonOptions": {"prompt": prompt, "schema": schema},
        }, timeout=90)
        if not r["ok"]:
            if _is_rate_limited(r.get("error")):
                break
            continue
        data = _unwrap_json(r["data"] or {})
        if not data:
            continue
        block = data.get("people") or []
        if isinstance(block, list):
            merged_people.extend(block)
        for k, v in data.items():
            if k != "people":
                other[k] = v
        if other and not merged_people:
            break
    if merged_people:
        return {"people": merged_people, **other}
    if other:
        return other

    # Fallback: scrape page text via multi-layer fallback
    import re
    for url in urls:
        sc = scrape(url)
        if sc.get("ok") and sc.get("text"):
            text = sc["text"]
            # Extract emails and names from scraped text if present
            emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
            clean_emails = [e for e in emails if not any(e.lower().startswith(p + "@") for p in ("info", "careers", "jobs", "support", "contact"))]
            if clean_emails:
                return {
                    "people": [{"name": clean_emails[0].split("@")[0].replace(".", " ").title(), "title": "Contact", "email": clean_emails[0]}],
                    "text": text[:500]
                }
            return {"text": text[:500]}
    return None


# ------------------------------------------------------------ browserbase / stagehand

def browser_available():
    return bool(secret("BROWSERBASE_API_KEY"))


def browser_extract(url, instruction, schema=None):
    """
    Read a page that Firecrawl cannot: heavy JS, infinite scroll, or a real login.

    Stagehand 4.0.0 uses async await Stagehand.create().
    """
    key = secret("BROWSERBASE_API_KEY")
    if not key:
        return {"ok": False, "data": None, "error": "BROWSERBASE_API_KEY not set"}
    try:
        from stagehand import Stagehand
    except ImportError:
        return {"ok": False, "data": None, "error": "stagehand not installed"}

    os.environ["BROWSERBASE_API_KEY"] = key

    async def _async_extract():
        client = None
        try:
            client = await Stagehand.create()
            await client.act(f"navigate to {url}")
            kw = {"instruction": instruction}
            if schema:
                kw["schema"] = schema
            data = await client.extract(**kw)
            return {"ok": True, "data": data, "error": None}
        except Exception as e:
            return {"ok": False, "data": None, "error": f"{type(e).__name__}: {str(e)[:250]}"}
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass

    import asyncio
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(lambda: asyncio.run(_async_extract())).result(timeout=60)
        else:
            return asyncio.run(_async_extract())
    except Exception as e:
        # Fallback to local HTTP text extraction via scrape()
        h = scrape(url)
        if h.get("ok"):
            return {"ok": True, "data": {"text": h.get("markdown", "")}, "error": None}
        return {"ok": False, "data": None, "error": f"{type(e).__name__}: {str(e)[:250]}"}


# ----------------------------------------------------------------------- local tools

def has_local(binary):
    return shutil.which(binary) is not None or os.path.exists(
        os.path.expanduser(f"~/.local/bin/{binary}"))


def local_search(query, limit=10):
    """Zero-credential HTTP web search fallback (Bing HTML with base64 link decoding)."""
    import urllib.request, urllib.parse, re, html as html_lib, base64, ssl
    try:
        url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        results = []
        matches = re.findall(r'u=a1([A-Za-z0-9+/=_-]+)', html)
        seen_urls = set()
        for m in matches:
            raw_b64 = m.replace("-", "+").replace("_", "/")
            rem = len(raw_b64) % 4
            if rem:
                raw_b64 += "=" * (4 - rem)
            try:
                target_url = base64.b64decode(raw_b64).decode("utf-8", errors="ignore")
                if not target_url.startswith("http"):
                    continue
                d_host = target_url.split("/")[2].lower() if "/" in target_url else ""
                if any(x in d_host for x in ("bing.com", "microsoft.com", "google.com", "youtube.com", "facebook.com", "twitter.com")):
                    continue
                if target_url in seen_urls:
                    continue
                seen_urls.add(target_url)

                title = d_host.replace("www.", "").title()
                results.append({
                    "title": title,
                    "url": target_url,
                    "description": f"SearchResult: {query}",
                    "source": "local_search"
                })
                if len(results) >= limit:
                    break
            except Exception:
                continue
        return results
    except Exception:
        return []


def agent_reach(query, limit=10):
    """Semantic search via agent-reach. Finds by meaning where keywords miss."""
    binary = shutil.which("agent-reach") or os.path.expanduser("~/.local/bin/agent-reach")
    if os.path.exists(binary):
        try:
            r = subprocess.run([binary, "search", query, "--limit", str(limit), "--json"],
                               capture_output=True, text=True, timeout=180)
            data = json.loads(r.stdout)
            rows = data if isinstance(data, list) else data.get("results", [])
            items = [{"title": x.get("title", ""), "url": x.get("url", ""),
                     "description": x.get("text", x.get("snippet", ""))[:400],
                     "source": "agent_reach"}
                    for x in rows if isinstance(x, dict) and x.get("url")]
            if items:
                return items
        except Exception:
            pass
    return local_search(query, limit=limit)



# ---------------------------------------------------------------------- capabilities

def available():
    """What can actually run right now. Printed by preflight and the run header."""
    conns = connections()
    return {
        "composio":     composio_ready(),
        "firecrawl":    is_linked("firecrawl"),
        "googlesheets": is_linked("googlesheets"),
        "neverbounce":  is_linked("neverbounce"),
        "zerobounce":   is_linked("zerobounce"),
        "hubspot":      is_linked("hubspot"),
        "google_maps":  is_linked("google_maps"),
        "browserbase":  browser_available(),
        "agent_reach":  has_local("agent-reach"),
        "_connections": conns,
    }


if __name__ == "__main__":
    caps = available()
    print("\nLeadForge connectors\n" + "-" * 46)
    for k, v in caps.items():
        if k.startswith("_"):
            continue
        print(f"  {'ok ' if v else '-- '} {k}")
    print("\nLinked Composio apps:")
    for app, st in sorted(caps["_connections"].items()):
        print(f"  {app:24} {','.join(st)}")
    print()
