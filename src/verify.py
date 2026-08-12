#!/usr/bin/env python3
"""
Stage 5: prove each lead is real before it costs anyone a reply.

    python3 src/verify.py                       # verifies data/contacts.json
    python3 src/verify.py --min-confidence 70
    python3 src/verify.py --no-paid             # free checks only

No single source is trusted. A lead is scored by how many independent checks agree,
which is the only way to catch the failure that actually bites: a provider returning
a confident, well-formed, completely wrong answer. This project has seen a vendor
resolve manutd.com to a company literally named "FDC Fake Company", and return
mp@xxx.com as a real address. Both would pass a syntax check and a single API call.

Six checks, weighted by how hard each is to fake:

  syntax        20   RFC-ish shape. Cheap, catches placeholders and typos.
  domain_live   20   The company's site actually resolves and responds.
  mx            25   The mail domain publishes MX records. No MX, no delivery, ever.
  provider      25   NeverBounce or ZeroBounce verdict, when linked.
  pattern       10   Address matches a pattern seen elsewhere at that company.
  corroboration 10   Independently found by more than one source.

Anything at or above --min-confidence is written to verified.json. Everything else
goes to rejected.json with the reason, because a rejected lead is diagnostic: a pile
of no-MX rejections means the domain guesser is wrong, not that the leads are bad.
"""
import json, os, sys, argparse, re, socket, ssl
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connectors as C
from schema import Evidence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN = os.path.join(ROOT, "data", "contacts.json")
OUT = os.path.join(ROOT, "data", "verified.json")
REJ = os.path.join(ROOT, "data", "rejected.json")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# Addresses that are structurally valid and commercially useless.
ROLE_PREFIX = {"info", "hello", "contact", "support", "admin", "sales", "help",
               "team", "office", "enquiries", "inquiries", "hi", "mail", "no-reply",
               "noreply", "donotreply", "webmaster", "postmaster", "abuse",
               "careers", "career", "jobs", "job", "recruiting", "recruitment",
               "hiring", "hr", "billing", "accounts", "legal", "privacy",
               "security", "press", "media", "marketing", "general", "service"}

# Placeholders that real providers genuinely return. Every one of these has been
# seen in live output at some point, which is why the list is explicit.
FAKE_TOKENS = {"xxx", "example", "test", "sample", "yourcompany", "domain",
               "email", "none", "null", "na", "n/a", "fake", "placeholder",
               "firstname", "lastname", "acme"}

DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com",
              "tempmail.com", "throwaway.email", "yopmail.com", "trashmail.com",
              "sharklasers.com", "getnada.com", "temp-mail.org"}

FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
             "icloud.com", "protonmail.com", "gmx.com", "mail.com", "live.com"}

WEIGHTS = {"syntax": 20, "domain_live": 20, "mx": 25,
           "provider": 25, "pattern": 10, "corroboration": 10}


# ----------------------------------------------------------------- free local checks

def check_syntax(email):
    """Shape, placeholders and role accounts. Free, instant, catches a lot."""
    e = (email or "").strip().lower()
    if not e or not EMAIL_RE.match(e):
        return False, "malformed"
    local, domain = e.rsplit("@", 1)

    for token in FAKE_TOKENS:
        if local == token or domain.split(".")[0] == token:
            return False, f"placeholder ({token})"
    if re.fullmatch(r"(.)\1{2,}", domain.split(".")[0]):
        return False, "placeholder domain"
    if domain in DISPOSABLE:
        return False, "disposable domain"
    if local in ROLE_PREFIX:
        return False, f"role account ({local})"
    if domain in FREE_MAIL:
        return True, "free provider, not a company address"
    return True, "ok"


def check_mx(domain):
    """
    Does this domain accept mail at all?

    The single highest-value free check. A domain with no MX record cannot receive
    email under any circumstance, so this is a hard fail rather than a score penalty.
    """
    try:
        import dns.resolver
    except ImportError:
        return None, "dnspython not installed"
    try:
        r = dns.resolver.Resolver()
        r.lifetime = r.timeout = 5.0
        answers = r.resolve(domain, "MX")
        hosts = sorted(str(a.exchange).rstrip(".").lower() for a in answers)
        if not hosts:
            return False, "no MX records"
        provider = "unknown"
        joined = " ".join(hosts)
        for needle, label in (("google", "google"), ("outlook", "microsoft"),
                              ("microsoft", "microsoft"), ("titan", "titan"),
                              ("zoho", "zoho"), ("protonmail", "proton")):
            if needle in joined:
                provider = label
                break
        return True, f"MX ok ({provider}, {len(hosts)} host(s))"
    except Exception as e:
        return False, f"MX lookup failed: {type(e).__name__}"


def check_domain_live(domain):
    """
    Confirm the company exists as a going concern.

    A HEAD request is enough and costs nothing. This is what would have caught the
    "FDC Fake Company" case: the record looked plausible, the domain did not back it up.
    """
    for scheme in ("https://", "http://"):
        try:
            req = Request(scheme + domain, method="HEAD",
                          headers={"User-Agent": "Mozilla/5.0 (compatible; LeadForge/1.0)"})
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urlopen(req, timeout=8, context=ctx) as resp:
                if resp.status < 400:
                    return True, f"{scheme.rstrip('://')} {resp.status}"
        except HTTPError as e:
            if e.code < 500:
                return True, f"http {e.code}"
        except (URLError, socket.timeout, ssl.SSLError, Exception):
            continue
    return False, "domain did not respond"


def check_pattern(email, siblings):
    """
    Does this address follow the same shape as other addresses at the company?

    An outlier at a company with a consistent first.last convention is usually a
    guess that leaked into the data.
    """
    if not siblings:
        return None, "no siblings to compare"

    def shape(addr):
        local = addr.split("@")[0]
        if "." in local:
            return "first.last"
        if "_" in local:
            return "first_last"
        return "flast" if len(local) <= 8 else "firstlast"

    mine = shape(email)
    others = [shape(s) for s in siblings if s != email]
    if not others:
        return None, "no siblings to compare"
    if mine in others:
        return True, f"matches company pattern ({mine})"
    return False, f"pattern {mine} unlike company norm ({max(set(others), key=others.count)})"


# ----------------------------------------------------------------- paid provider check

def check_provider(email):
    """
    NeverBounce first, ZeroBounce as an independent second opinion.

    NeverBounce returns status="success" even when result="invalid", so the verdict
    is read from `result`, never from `status`. "catchall" is deliberately treated as
    unproven rather than valid: an accept-all domain accepts anything, including
    addresses that bounce later and damage sender reputation.
    """
    if C.is_linked("neverbounce"):
        r = C.execute("NEVERBOUNCE_SINGLE_CHECK", {"email": email}, timeout=60)
        if r["ok"]:
            d = r["data"] or {}
            result = (d.get("result") or (d.get("data") or {}).get("result") or "").lower()
            if result == "valid":
                return True, "neverbounce: valid"
            if result in ("invalid", "disposable"):
                return False, f"neverbounce: {result}"
            return None, f"neverbounce: {result or 'inconclusive'}"

    if C.is_linked("zerobounce"):
        r = C.execute("ZEROBOUNCE_VALIDATE_EMAIL", {"email": email}, timeout=60)
        if r["ok"]:
            d = r["data"] or {}
            status = (d.get("status") or (d.get("data") or {}).get("status") or "").lower()
            if status == "valid":
                return True, "zerobounce: valid"
            if status in ("invalid", "spamtrap", "abuse", "do_not_mail"):
                return False, f"zerobounce: {status}"
            return None, f"zerobounce: {status or 'inconclusive'}"

    return None, "no verification provider linked"


# ----------------------------------------------------------------------- scoring

def verify_one(lead, siblings_by_domain, use_paid=True):
    email = (lead.get("email") or "").strip().lower()
    checks, score, possible = {}, 0, 0

    ok, why = check_syntax(email)
    checks["syntax"] = {"pass": ok, "detail": why}
    possible += WEIGHTS["syntax"]
    if ok:
        score += WEIGHTS["syntax"]
    else:
        lead.update(confidence=0, checks=checks, reject_reason=f"syntax: {why}")
        return lead

    mail_domain = email.rsplit("@", 1)[1]
    web_domain = (lead.get("domain") or mail_domain).split("/")[0]

    live, why = check_domain_live(web_domain)
    checks["domain_live"] = {"pass": live, "detail": why}
    possible += WEIGHTS["domain_live"]
    if live:
        score += WEIGHTS["domain_live"]

    mx_ok, why = check_mx(mail_domain)
    checks["mx"] = {"pass": mx_ok, "detail": why}
    if mx_ok is not None:
        possible += WEIGHTS["mx"]
        if mx_ok:
            score += WEIGHTS["mx"]
        else:
            # Undeliverable by definition. No amount of other signal rescues it.
            lead.update(confidence=0, checks=checks, reject_reason=f"mx: {why}")
            return lead

    if use_paid:
        pv, why = check_provider(email)
        checks["provider"] = {"pass": pv, "detail": why}
        if pv is not None:
            possible += WEIGHTS["provider"]
            if pv:
                score += WEIGHTS["provider"]
            else:
                lead.update(confidence=0, checks=checks, reject_reason=f"provider: {why}")
                return lead

    pat, why = check_pattern(email, siblings_by_domain.get(mail_domain, []))
    checks["pattern"] = {"pass": pat, "detail": why}
    if pat is not None:
        possible += WEIGHTS["pattern"]
        if pat:
            score += WEIGHTS["pattern"]

    corr = int(lead.get("corroboration", 1) or 1)
    checks["corroboration"] = {"pass": corr > 1, "detail": f"{corr} independent source(s)"}
    possible += WEIGHTS["corroboration"]
    if corr > 1:
        score += WEIGHTS["corroboration"]

    # Scored against what could actually be checked, so an unlinked provider lowers
    # certainty rather than silently capping every lead's ceiling.
    lead["confidence"] = round(100 * score / possible) if possible else 0
    lead["checks"] = checks
    return lead


def verify_leads(leads, use_paid=True, workers=8, on_progress=None):
    """
    Schema-native entry point, used by the orchestrator.

    Verification is deliberately kept independent of discovery. The component that
    found a lead never gets to rule on whether it is real, because a model asked to
    check its own claim will confirm it. This function only reads the record.

    Results land on lead.verification and the lead is also given evidence for the
    verification outcome itself, so the audit trail covers the check as well as
    the claim.
    """
    siblings = {}
    for l in leads:
        e = ((l.contact or {}).get("email") or "").strip().lower()
        if "@" in e:
            siblings.setdefault(e.rsplit("@", 1)[1], []).append(e)

    def one(lead):
        payload = {
            "email": (lead.contact or {}).get("email", ""),
            "domain": lead.domain,
            "corroboration": lead.corroboration or 1,
        }
        res = verify_one(payload, siblings, use_paid)
        lead.verification = {
            "confidence": res.get("confidence", 0),
            "checks": res.get("checks", {}),
            "reject_reason": res.get("reject_reason", ""),
        }
        conf = lead.verification["confidence"]
        lead.status = "verified" if conf >= 70 else "unverified"
        email = payload["email"]
        if email:
            passed = [k for k, v in lead.verification["checks"].items() if v.get("pass")]
            lead.add_evidence(Evidence(
                claim=f"Email {email} is deliverable",
                url=f"https://{lead.domain}" if lead.domain else "",
                source_type="company_site" if conf >= 70 else "inferred",
                excerpt=f"{conf}% confidence; passed: {', '.join(passed) or 'none'}"))
        return lead

    out = []
    total = len(leads)
    passed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one, l) for l in leads]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                lead = fut.result()
                out.append(lead)
                if lead.verification.get("confidence", 0) >= 70:
                    passed += 1
            except Exception:
                continue
            if on_progress:
                on_progress(i, total, passed)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=IN)
    ap.add_argument("--min-confidence", type=int, default=70)
    ap.add_argument("--no-paid", action="store_true", help="skip provider APIs")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    if not os.path.exists(a.inp):
        sys.exit(f"No {os.path.relpath(a.inp, ROOT)}. Run src/contacts.py first.")

    from schema import load as load_leads, save as save_leads
    leads = load_leads(a.inp)
    if not leads:
        sys.exit("No contacts to verify.")

    provider = ("neverbounce" if C.is_linked("neverbounce")
                else "zerobounce" if C.is_linked("zerobounce") else None)
    print(f"\nverifying {len(leads)} leads")
    print(f"  provider: {provider or 'none linked (free checks only)'}")
    if not provider:
        print("  to add one:  composio link neverbounce")
    print()

    done = verify_leads(leads, use_paid=not a.no_paid, workers=a.workers)
    passed = sorted([l for l in done
                     if l.verification.get("confidence", 0) >= a.min_confidence],
                    key=lambda x: x.verification["confidence"], reverse=True)
    failed = [l for l in done if l not in passed]

    save_leads(passed, OUT)
    save_leads(failed, REJ)

    print(f"{'=' * 58}")
    print(f"  verified   {len(passed)}   (confidence >= {a.min_confidence})")
    print(f"  rejected   {len(failed)}")
    print(f"{'=' * 58}\n")
    for l in passed[:12]:
        print(f"  {l.verification['confidence']:3}  "
              f"{(l.contact.get('name') or '?')[:22]:24} "
              f"{l.contact.get('email', '')[:36]:38} {l.company[:20]}")

    if failed:
        reasons = {}
        for l in failed:
            key = (l.verification.get("reject_reason") or "below threshold").split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        print("\n  rejected because:")
        for reason, n in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {n:4}  {reason}")

    print(f"\nwrote {os.path.relpath(OUT, ROOT)} and {os.path.relpath(REJ, ROOT)}")
    print("next:  python3 src/run.py --from score")


if __name__ == "__main__":
    main()
