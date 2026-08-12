#!/usr/bin/env python3
"""
Check everything before a run, so a job fails in ten seconds instead of ten minutes.

    python3 src/preflight.py
    python3 src/preflight.py --strict     # exit non-zero on any warning

Ordered by how much a failure costs: things that stop the run entirely come first,
then things that quietly degrade quality, then optional extras.
"""
import os, sys, json, argparse, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connectors as C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL, WARN, OK = "FAIL", "warn", "ok"
results = []


def check(name, status, detail="", fix=""):
    results.append((name, status, detail, fix))


def c_python():
    v = sys.version_info
    if v < (3, 10):
        check("python", FAIL, f"{v.major}.{v.minor}, need 3.10+",
              "use .venv/bin/python, or ./setup.sh --install")
    else:
        check("python", OK, f"{v.major}.{v.minor}.{v.micro}")


def c_deps():
    for mod, why in (("dns.resolver", "MX verification"),
                     ("stagehand", "Browserbase browser layer")):
        try:
            __import__(mod)
            check(f"dep {mod.split('.')[0]}", OK, why)
        except ImportError:
            level = FAIL if "dns" in mod else WARN
            check(f"dep {mod.split('.')[0]}", level, f"missing, needed for {why}",
                  "./setup.sh --install")


def c_composio():
    if not C.composio_ready():
        check("composio", FAIL, "CLI not found",
              "curl -fsSL https://composio.dev/install.sh | bash")
        return
    check("composio", OK, "CLI present")

    conns = C.connections()
    if not conns:
        check("composio auth", WARN, "no connections returned", "composio login")
        return

    if C.is_linked("firecrawl"):
        check("firecrawl", OK, "connected. discovery and extraction will work")
    else:
        check("firecrawl", FAIL, "not connected. nothing can be discovered",
              "composio link firecrawl")

    if C.is_linked("googlesheets"):
        check("googlesheets", OK, "connected")
    else:
        check("googlesheets", WARN, "not connected, output falls back to CSV",
              "composio link googlesheets")

    if C.is_linked("neverbounce") or C.is_linked("zerobounce"):
        check("email verification", OK, "provider connected")
    else:
        check("email verification", WARN,
              "no provider. free checks only, so confidence is capped lower",
              "composio link neverbounce")

    expired = [a for a, st in conns.items() if "ACTIVE" not in st]
    if expired:
        check("expired connections", WARN, ", ".join(sorted(expired)[:6]),
              "composio link <app> to refresh any you need")


def c_browser():
    if C.browser_available():
        src = "environment" if os.environ.get("BROWSERBASE_API_KEY") else "~/.zshrc fallback"
        check("browserbase", OK, f"key found via {src}")
    else:
        check("browserbase", WARN, "no API key. JS-heavy pages will be skipped",
              'export BROWSERBASE_API_KEY="bb_live_..." in ~/.zshrc')


def c_agent_reach():
    if C.has_local("agent-reach"):
        check("agent-reach", OK, "semantic and social discovery available")
    else:
        check("agent-reach", WARN, "not installed, those paths will be skipped",
              "npm install -g agent-reach")


def c_brief():
    path = os.path.join(ROOT, "config", "brief.json")
    if not os.path.exists(path):
        check("brief", WARN, "no brief yet",
              "python3 src/intake.py --site <client-site>")
        return
    try:
        brief = json.load(open(path))
    except Exception as e:
        check("brief", FAIL, f"unreadable: {e}", "delete it and rerun intake")
        return

    icp = brief.get("icp", {})
    gaps = brief.get("meta", {}).get("gaps", [])
    if not icp.get("target_industries") and not icp.get("target_titles"):
        check("brief", FAIL, "no industries and no titles, discovery cannot plan",
              "edit config/brief.json")
    elif gaps:
        check("brief", WARN, f"{len(gaps)} unanswered: {', '.join(gaps[:4])}",
              "rerun intake without --yes")
    else:
        check("brief", OK, f"{brief.get('client', {}).get('name') or 'client'}, "
                           f"{len(icp.get('buying_signals', []))} buying signals")


def c_secrets():
    """A credential must never reach the repo. Mirrors the .gitignore rules."""
    bad = []
    for dirpath, dirs, files in os.walk(ROOT):
        if ".git" in dirpath or ".venv" in dirpath:
            continue
        for f in files:
            if f == ".env" or f.endswith(".env") or f == "secrets.json":
                rel = os.path.relpath(os.path.join(dirpath, f), ROOT)
                if rel != "config/secrets.example.json":
                    bad.append(rel)
    if bad:
        check("secrets", WARN, f"credential files present: {', '.join(bad[:3])}",
              "confirm these are gitignored before pushing")
    else:
        check("secrets", OK, "no credential files in the repo")


def c_writable():
    for d in ("data", "logs"):
        p = os.path.join(ROOT, d)
        os.makedirs(p, exist_ok=True)
        if os.access(p, os.W_OK):
            check(f"{d}/ writable", OK)
        else:
            check(f"{d}/ writable", FAIL, "not writable", f"chmod u+w {p}")


def run_checks():
    """Run all preflight checks and return structured results for the UI."""
    results.clear()
    for fn in (c_python, c_deps, c_composio, c_browser, c_agent_reach,
               c_brief, c_secrets, c_writable):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, WARN, f"check crashed: {str(e)[:60]}")

    checks = []
    for name, status, detail, fix in results:
        checks.append({
            "name": name,
            "status": status,
            "detail": detail,
            "fix": fix,
        })
    fails = sum(1 for c in checks if c["status"] == FAIL)
    warns = sum(1 for c in checks if c["status"] == WARN)
    return {
        "checks": checks,
        "ready": fails == 0,
        "blocking": fails,
        "warnings": warns,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit non-zero on warnings")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output, for agents driving this repo")
    a = ap.parse_args()

    report = run_checks()

    # An agent should never have to parse the human table to find out whether it
    # can run. Same data, one shape it can branch on.
    if a.json:
        print(json.dumps(report, indent=2))
        sys.exit(1 if report["blocking"] or (a.strict and report["warnings"]) else 0)

    print("\nLeadForge preflight\n" + "=" * 66)
    for c in report["checks"]:
        mark = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}[c["status"]]
        print(f"  {mark}  {c['name']:22} {c['detail'][:44]}")
        if c["fix"] and c["status"] != OK:
            print(f"        -> {c['fix']}")

    print("=" * 66)
    if report["blocking"]:
        print(f"{report['blocking']} blocking problem(s). The pipeline cannot run.\n")
        sys.exit(1)
    if report["warnings"]:
        print(f"Ready, with {report['warnings']} warning(s). Quality will be lower than it could be.\n")
        sys.exit(1 if a.strict else 0)
    print("All good.\n")


if __name__ == "__main__":
    main()
