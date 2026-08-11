#!/usr/bin/env python3
"""
Stage 3: score companies against the brief and drop the ones that will never buy.

    python3 src/qualify.py
    python3 src/qualify.py --min-score 45 --top 60

This stage exists because sourcing is deliberately greedy. It casts wide and returns
noise, and the cheapest place to remove noise is before contact discovery, which is
the step that costs credits and time. A previous run of this pipeline cut 334
companies to 23 here, and every one of the 311 removed would have been wasted spend.

The lesson encoded here: a company matching a keyword is not a prospect. Bike
retailers, mobile game studios and e-learning providers all post video editor roles
and have nobody who buys creative capacity. Signal beats vocabulary.

Scoring, out of 100:
  buying signal present   30   they are hiring or the client's stated trigger fired
  corroboration           20   more than one independent query found them
  industry match          20   matches an industry named in the brief
  title match             15   a target job title appears in what we found
  market match            10   located in a target market
  substance                5   enough distinct pages to look like a real company
"""
import json, os, sys, argparse, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIEF = os.path.join(ROOT, "config", "brief.json")
IN = os.path.join(ROOT, "data", "companies.json")
OUT = os.path.join(ROOT, "data", "qualified.json")


def blob(c):
    return " ".join([c.get("name", ""), c.get("domain", ""),
                     " ".join(c.get("titles", [])), " ".join(c.get("urls", []))]).lower()


def score(company, icp):
    text = blob(company)
    pts, why = 0, []

    if company.get("signals"):
        pts += 30
        why.append("buying signal")

    corr = int(company.get("corroboration", 1) or 1)
    if corr >= 3:
        pts += 20
        why.append(f"{corr} sources")
    elif corr == 2:
        pts += 12
        why.append("2 sources")

    inds = [i.lower() for i in icp.get("target_industries", [])]
    hit = next((i for i in inds if i and i in text), None)
    if hit:
        pts += 20
        why.append(f"industry:{hit[:18]}")

    titles = [t.lower() for t in icp.get("target_titles", [])]
    thit = next((t for t in titles if t and t in text), None)
    if thit:
        pts += 15
        why.append(f"title:{thit[:18]}")

    markets = [m.lower() for m in icp.get("target_markets", [])]
    mhit = next((m for m in markets if m and m in text), None)
    if mhit:
        pts += 10
        why.append(f"market:{mhit[:14]}")

    if len(company.get("urls", [])) >= 2:
        pts += 5
        why.append("multiple pages")

    return min(pts, 100), why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=int, default=40)
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--in", dest="inp", default=IN)
    a = ap.parse_args()

    if not os.path.exists(a.inp):
        sys.exit("No data/companies.json. Run:  python3 src/source.py")
    if not os.path.exists(BRIEF):
        sys.exit("No config/brief.json. Run:  python3 src/intake.py")

    companies = json.load(open(a.inp))
    icp = json.load(open(BRIEF))["icp"]
    excl = [e.lower() for e in icp.get("exclusions", []) if e.strip()]

    scored, dropped = [], 0
    for c in companies:
        text = blob(c)
        if any(e in text for e in excl):
            dropped += 1
            continue
        s, why = score(c, icp)
        c["score"], c["score_reasons"] = s, why
        scored.append(c)

    scored.sort(key=lambda x: x["score"], reverse=True)
    passed = [c for c in scored if c["score"] >= a.min_score][:a.top]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(passed, open(OUT, "w"), indent=1)

    print(f"\n{len(companies)} in, {len(passed)} qualified "
          f"(min score {a.min_score}, {dropped} excluded by rule)\n")
    for c in passed[:20]:
        print(f"  {c['score']:3}  {c['name'][:34]:36} {c['domain'][:30]:32} "
              f"{', '.join(c['score_reasons'][:3])}")
    if not passed:
        print("  Nothing passed. Either the brief is too narrow or sourcing found the")
        print("  wrong market. Check data/companies.json before lowering --min-score.")
    print(f"\nwrote {os.path.relpath(OUT, ROOT)}")
    print("next:  python3 src/contacts.py")


if __name__ == "__main__":
    main()
