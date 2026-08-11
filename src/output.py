#!/usr/bin/env python3
"""
Stage 6: deliver the leads somewhere a human can act on them.

    python3 src/output.py                        # picks the best available target
    python3 src/output.py --to sheets --title "Acme leads"
    python3 src/output.py --to csv
    python3 src/output.py --to hubspot --min-priority 70

The output is not a contact list. A commodity lead list is company, email, phone, and
it tells a salesperson nothing about why they are calling. Every row here carries the
reason, the evidence behind it, and the separate scores, so the person working the
list can sort by intent, sanity-check a claim by opening its URL, and skip anything
whose confidence is thin.

Target selection degrades rather than fails: Sheets if linked, otherwise CSV, always.
Nobody ever loses a completed run because an integration was not connected.
"""
import json, os, sys, argparse, csv
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connectors as C
from schema import Lead, load, domain_of
from score import why_now

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN = os.path.join(ROOT, "data", "scored.json")

COLUMNS = [
    "Company", "Domain", "Contact", "Title", "Email", "LinkedIn",
    "Priority", "Fit", "Intent", "Confidence", "Why Now",
    "Buying Signal", "Evidence", "Sources", "Verified", "Status",
]


def row_for(lead):
    c = lead.contact or {}
    s = lead.scores or {}
    v = lead.verification or {}

    top = sorted(lead.claims, key=lambda x: lead.confidence_for(x), reverse=True)[:2]
    evidence = " | ".join(
        f"{e.url}" for claim in top for e in lead.evidence_for(claim)[:2] if e.url)[:900]

    verified = ""
    if v.get("confidence") is not None:
        verified = f"{v['confidence']}%"
        if v.get("reject_reason"):
            verified += f" ({v['reject_reason'][:40]})"

    return [
        lead.company,
        lead.domain,
        c.get("name", ""),
        c.get("title", ""),
        c.get("email", ""),
        c.get("linkedin", ""),
        s.get("priority", 0),
        s.get("fit", 0),
        s.get("intent", 0),
        s.get("confidence", 0),
        why_now(lead),
        ", ".join(lead.signals[:3]),
        evidence,
        ", ".join(sorted({e.source_type for e in lead.evidence}))[:120],
        verified,
        lead.status,
    ]


def to_csv(leads, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for l in leads:
            w.writerow(row_for(l))
    return path


def to_sheets(leads, title):
    """
    Create a spreadsheet and write every row in one call.

    Values must be a strictly rectangular 2D array. Ragged rows fail schema validation,
    and NaN or Infinity anywhere in the payload produces a Bad Request, so everything
    is coerced to str or int on the way in.
    """
    if not C.is_linked("googlesheets"):
        return None, "googlesheets not linked (composio link googlesheets)"

    r = C.execute("GOOGLESHEETS_CREATE_GOOGLE_SHEET1", {"title": title}, timeout=120)
    if not r["ok"]:
        return None, f"could not create sheet: {r['error']}"

    d = r["data"] or {}
    sid = (d.get("spreadsheetId") or d.get("spreadsheet_id")
           or (d.get("spreadsheet") or {}).get("spreadsheetId"))
    url = d.get("spreadsheetUrl") or (
        f"https://docs.google.com/spreadsheets/d/{sid}" if sid else "")
    if not sid:
        return None, f"sheet created but no id returned: {str(d)[:200]}"

    values = [COLUMNS]
    for l in leads:
        values.append([v if isinstance(v, (int, float)) else str(v or "")
                       for v in row_for(l)])

    end = chr(ord("A") + len(COLUMNS) - 1)
    w = C.execute("GOOGLESHEETS_VALUES_UPDATE", {
        "spreadsheet_id": sid,
        "range": f"A1:{end}{len(values)}",
        "values": values,
        "value_input_option": "RAW",       # RAW, so a lead named "=X" is never a formula
    }, timeout=180)

    if not w["ok"]:
        return url, f"sheet created but write failed: {w['error']}"
    return url, None


def to_hubspot(leads):
    """Push contacts into a CRM. Only leads with an email are eligible."""
    if not C.is_linked("hubspot"):
        return 0, "hubspot not linked"
    calls, eligible = [], []
    for i, l in enumerate(leads):
        email = (l.contact or {}).get("email")
        if not email:
            continue
        eligible.append(l)
        name = (l.contact.get("name") or "").split()
        calls.append((f"c{i}", "HUBSPOT_CREATE_CONTACT", {
            "properties": {
                "email": email,
                "firstname": name[0] if name else "",
                "lastname": " ".join(name[1:]) if len(name) > 1 else "",
                "company": l.company,
                "jobtitle": l.contact.get("title", ""),
                "website": f"https://{l.domain}" if l.domain else "",
            }}))
    if not calls:
        return 0, "no leads had an email address"
    results = C.execute_many(calls, workers=5)
    ok = sum(1 for r in results.values() if r["ok"])
    return ok, None if ok == len(calls) else f"{len(calls) - ok} failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=IN)
    ap.add_argument("--to", choices=["auto", "sheets", "csv", "hubspot"], default="auto")
    ap.add_argument("--title", default=None)
    ap.add_argument("--min-priority", type=int, default=0)
    ap.add_argument("--top", type=int, default=0, help="0 means all")
    a = ap.parse_args()

    if not os.path.exists(a.inp):
        sys.exit(f"No {os.path.relpath(a.inp, ROOT)}. Run the pipeline first: ./run.sh")

    leads = load(a.inp)
    leads = [l for l in leads if (l.scores or {}).get("priority", 0) >= a.min_priority]
    leads.sort(key=lambda l: (l.scores or {}).get("priority", 0), reverse=True)
    if a.top:
        leads = leads[:a.top]

    if not leads:
        sys.exit(f"No leads at or above priority {a.min_priority}.")

    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    title = a.title or f"LeadForge {stamp}"

    target = a.to
    if target == "auto":
        target = "sheets" if C.is_linked("googlesheets") else "csv"
        print(f"target: {target} (auto-selected)")

    print(f"\nexporting {len(leads)} leads")

    # A CSV is always written, even when the primary target is something else. The
    # run is expensive and the local copy is the thing that survives an API failure.
    csv_path = os.path.join(ROOT, "data", f"leads-{datetime.now():%Y-%m-%d-%H%M}.csv")
    to_csv(leads, csv_path)
    print(f"  csv     {os.path.relpath(csv_path, ROOT)}")

    if target == "sheets":
        url, err = to_sheets(leads, title)
        if url:
            print(f"  sheet   {url}")
        if err:
            print(f"  note    {err}")
            if not url:
                print("  the CSV above has everything, nothing was lost.")
    elif target == "hubspot":
        n, err = to_hubspot(leads)
        print(f"  hubspot {n} contacts created" + (f" ({err})" if err else ""))

    print(f"\n{'priority':>8}  {'company':26} {'contact':20} why now")
    print("-" * 100)
    for l in leads[:15]:
        p = (l.scores or {}).get("priority", 0)
        print(f"{p:>8}  {l.company[:24]:26} "
              f"{(l.contact or {}).get('name', '')[:18]:20} {why_now(l)[:44]}")
    print()


if __name__ == "__main__":
    main()
