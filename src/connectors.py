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

    Order: real environment, then config/secrets.json (gitignored), then ~/.zshrc.

    The zshrc fallback exists for a specific reason. BROWSERBASE_API_KEY is exported
    there, but a non-interactive shell (cron, subprocess, CI) does not source zshrc,
    so the key is invisible exactly when the automation runs unattended. Verified on
    11 Aug 2026: the key is in zshrc and absent from a fresh non-interactive shell.
    """
    if os.environ.get(name):
        return os.environ[name]

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
    """Fetch one page as text. Returns {ok, text, url}."""
    r = execute("FIRECRAWL_SCRAPE", {"url": url, "formats": list(formats)})
    if not r["ok"]:
        return {"ok": False, "text": "", "url": url, "error": r["error"]}
    d = r["data"] or {}
    text = d.get("markdown") or d.get("html") or d.get("content") or ""
    if not text and isinstance(d.get("data"), dict):
        inner = d["data"]
        text = inner.get("markdown") or inner.get("content") or ""
    return {"ok": bool(text), "text": text, "url": url, "error": None}


def extract(urls, schema, prompt):
    """
    Pull structured fields off pages. This is the one that finds decision makers,
    because it reads a team page and returns names and titles rather than raw text.
    """
    r = execute("FIRECRAWL_EXTRACT",
                {"urls": list(urls), "schema": schema, "prompt": prompt},
                timeout=120)
    return r["data"] if r["ok"] else None


# ------------------------------------------------------------ browserbase / stagehand

def browser_available():
    return bool(secret("BROWSERBASE_API_KEY"))


def browser_extract(url, instruction, schema=None):
    """
    Read a page that Firecrawl cannot: heavy JS, infinite scroll, or a real login.

    Stagehand 4.0.0 has a flat client API (act/extract/observe/create/close). Both the
    published README (which shows client.sessions.*) and the Browserbase onboarding doc
    (which shows env="BROWSERBASE") describe older versions. Verified against the
    installed package on 11 Aug 2026.

    Slower and more expensive than scrape(), so callers should try scrape() first.
    """
    key = secret("BROWSERBASE_API_KEY")
    if not key:
        return {"ok": False, "data": None, "error": "BROWSERBASE_API_KEY not set"}
    try:
        from stagehand import Stagehand
    except ImportError:
        return {"ok": False, "data": None, "error": "stagehand not installed"}

    os.environ.setdefault("BROWSERBASE_API_KEY", key)
    client = None
    try:
        client = Stagehand()
        client.create()
        client.act(f"navigate to {url}")
        kw = {"instruction": instruction}
        if schema:
            kw["schema"] = schema
        return {"ok": True, "data": client.extract(**kw), "error": None}
    except Exception as e:
        return {"ok": False, "data": None, "error": f"{type(e).__name__}: {str(e)[:250]}"}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass                          # a leaked session expires on its own


# ----------------------------------------------------------------------- local tools

def has_local(binary):
    return shutil.which(binary) is not None or os.path.exists(
        os.path.expanduser(f"~/.local/bin/{binary}"))


def agent_reach(query, limit=10):
    """Semantic search via agent-reach. Finds by meaning where keywords miss."""
    binary = shutil.which("agent-reach") or os.path.expanduser("~/.local/bin/agent-reach")
    if not os.path.exists(binary):
        return []
    try:
        r = subprocess.run([binary, "search", query, "--limit", str(limit), "--json"],
                           capture_output=True, text=True, timeout=180)
        data = json.loads(r.stdout)
        rows = data if isinstance(data, list) else data.get("results", [])
        return [{"title": x.get("title", ""), "url": x.get("url", ""),
                 "description": x.get("text", x.get("snippet", ""))[:400],
                 "source": "agent_reach"}
                for x in rows if isinstance(x, dict) and x.get("url")]
    except Exception:
        return []


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
