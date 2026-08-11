#!/usr/bin/env python3
"""
Multi-dimensional lead scoring.

One number hides why a lead is good. A lead scoring 82 because it perfectly matches
the ICP but shows no buying intent needs a completely different approach from one
scoring 82 on raw intent with mediocre fit. So six dimensions are computed and kept
separate, and the weighted priority score is derived from them rather than replacing
them.

    fit         30%   does this company match the ICP at all
    intent      25%   is there evidence they need this right now
    quality     15%   is this a real, substantial company
    persona     10%   is the contact someone who can say yes
    confidence  10%   how well evidenced is everything above
    recency     10%   how fresh is the signal

Two rules hold. Nothing scores on vocabulary alone, because a keyword match is not a
buying signal. And every dimension is derived from evidence already in the record, so
a score can always be explained by pointing at a URL.
"""
import sys, os, re
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import Lead, domain_of

WEIGHTS = {"fit": 0.30, "intent": 0.25, "quality": 0.15,
           "persona": 0.10, "confidence": 0.10, "recency": 0.10}

SENIORITY = [
    (["founder", "co-founder", "ceo", "owner", "president"], 100),
    (["chief", "cxo", "cmo", "cto", "coo"], 95),
    (["vp", "vice president", "svp", "evp"], 90),
    (["head of", "director"], 85),
    (["lead", "principal", "manager"], 65),
    (["senior", "sr."], 45),
    (["coordinator", "associate", "assistant", "intern", "junior"], 20),
]

# Words that indicate a real buying trigger rather than a topic match.
INTENT_CLAIMS = ("hiring", "raised", "funding", "expanding", "launch",
                 "growing", "scaling", "opened", "acquired")


def _text(lead):
    parts = [lead.company, lead.domain, " ".join(lead.signals)]
    parts += [e.claim + " " + e.excerpt for e in lead.evidence]
    parts += [str(v) for v in lead.company_data.values()]
    return " ".join(parts).lower()


def score_fit(lead, icp):
    """Does this company match what the client asked for?"""
    text = _text(lead)
    pts, why = 0, []

    inds = [i.lower().strip() for i in icp.get("target_industries", []) if i.strip()]
    if inds:
        hits = [i for i in inds if i and i in text]
        if hits:
            pts += 45
            why.append(f"industry: {hits[0][:24]}")
    else:
        pts += 20                                  # no filter stated, do not punish

    markets = [m.lower().strip() for m in icp.get("target_markets", []) if m.strip()]
    if markets:
        hits = [m for m in markets if m in text]
        if hits:
            pts += 30
            why.append(f"market: {hits[0][:18]}")
    else:
        pts += 15

    size = (icp.get("company_size") or "").strip()
    got = str(lead.company_data.get("size", "") or "")
    if size and got:
        nums = re.findall(r"\d+", got)
        want = re.findall(r"\d+", size)
        if nums and want and int(want[0]) <= int(nums[0]) <= (int(want[-1]) if len(want) > 1 else 10 ** 9):
            pts += 25
            why.append(f"size {got}")
    elif not size:
        pts += 12

    # A negative-ICP hit is disqualifying, not a deduction.
    for bad in icp.get("exclusions", []):
        if bad.strip() and bad.lower().strip() in text:
            return 0, [f"excluded: {bad[:24]}"]

    return min(pts, 100), why


def score_intent(lead, icp):
    """
    Is there evidence they need this now?

    Driven by evidenced claims rather than keywords. A claim only counts at the
    confidence its evidence supports, so a hiring claim backed by the company's own
    careers page outscores the same claim seen once on an aggregator.
    """
    pts, why = 0, []
    signals = [s.lower().strip() for s in icp.get("buying_signals", []) if s.strip()]

    for claim in lead.claims:
        conf = lead.confidence_for(claim)
        cl = claim.lower()
        if any(k in cl for k in INTENT_CLAIMS):
            pts += 55 * conf
            why.append(f"{claim[:40]} ({conf:.0%})")
        elif any(s in cl for s in signals):
            pts += 40 * conf
            why.append(f"{claim[:40]} ({conf:.0%})")

    text = _text(lead)
    unevidenced = [s for s in signals if s in text and
                   not any(s in c.lower() for c in lead.claims)]
    if unevidenced:
        pts += 10                                  # mentioned but unproven
        why.append(f"mentions {unevidenced[0][:22]}, unevidenced")

    if len(lead.signals) > 1:
        pts += 10
        why.append(f"{len(lead.signals)} signal types")

    return min(int(pts), 100), why


def score_quality(lead):
    """Is this a real company, or a directory page that looked like one?"""
    pts, why = 0, []
    if lead.domain and "." in lead.domain:
        pts += 25
    hosts = lead.corroboration
    if hosts >= 3:
        pts += 40
        why.append(f"{hosts} distinct sources")
    elif hosts == 2:
        pts += 25
        why.append("2 sources")
    elif hosts == 1:
        pts += 10
    if lead.company_data:
        pts += 20
        why.append("has firmographics")
    if len(lead.evidence) >= 3:
        pts += 15
    return min(pts, 100), why


def score_persona(lead, icp):
    """Can this person actually say yes?"""
    title = (lead.contact.get("title") or "").lower().strip()
    if not lead.contact.get("name"):
        return 0, ["no contact"]
    if not title:
        return 30, ["contact found, title unknown"]

    wanted = [t.lower().strip() for t in icp.get("target_titles", []) if t.strip()]
    pts, why = 0, []
    if any(w in title for w in wanted):
        pts += 60
        why.append("target title")
    else:
        for w in wanted:
            words = [x for x in re.split(r"\W+", w) if len(x) > 3]
            if words and all(x in title for x in words):
                pts += 45
                why.append("title close match")
                break

    for keys, val in SENIORITY:
        if any(k in title for k in keys):
            pts += val * 0.4
            why.append(f"seniority {val}")
            break

    return min(int(pts), 100), why


def score_confidence(lead):
    """How well evidenced is this record overall?"""
    if not lead.evidence:
        return 0, ["no evidence"]
    confs = [lead.confidence_for(c) for c in lead.claims] or [0]
    avg = sum(confs) / len(confs)
    pts = int(avg * 70)
    why = [f"avg claim confidence {avg:.0%}"]

    email = (lead.contact.get("email") or "")
    origin = lead.contact.get("origin", "")
    if email and origin in ("team_page", "browser"):
        pts += 30
        why.append("email found, not guessed")
    elif email and origin == "pattern":
        pts += 5
        why.append("email is a pattern guess")

    v = lead.verification.get("confidence")
    if isinstance(v, (int, float)):
        pts = int(0.5 * pts + 0.5 * v)
        why.append(f"verified {v}")
    return min(pts, 100), why


def score_recency(lead):
    """Fresh signal beats stale signal. Hiring posts go cold fast."""
    if not lead.evidence:
        return 50, ["no timestamps"]
    newest = None
    for e in lead.evidence:
        try:
            t = datetime.fromisoformat(e.retrieved_at.replace("Z", "+00:00"))
            if newest is None or t > newest:
                newest = t
        except Exception:
            continue
    if newest is None:
        return 50, ["unparseable timestamps"]
    days = (datetime.now(timezone.utc) - newest).days
    if days <= 7:
        return 100, ["within a week"]
    if days <= 30:
        return 80, [f"{days}d old"]
    if days <= 90:
        return 55, [f"{days}d old"]
    return 25, [f"{days}d old, likely stale"]


def score_lead(lead, icp):
    """Compute all six, then the weighted priority. Stored on the lead."""
    fit, fw = score_fit(lead, icp)
    intent, iw = score_intent(lead, icp)
    quality, qw = score_quality(lead)
    persona, pw = score_persona(lead, icp)
    conf, cw = score_confidence(lead)
    rec, rw = score_recency(lead)

    dims = {"fit": fit, "intent": intent, "quality": quality,
            "persona": persona, "confidence": conf, "recency": rec}
    priority = sum(dims[k] * WEIGHTS[k] for k in WEIGHTS)

    # An excluded company cannot be rescued by a strong score elsewhere.
    if fit == 0 and fw and fw[0].startswith("excluded"):
        priority = 0

    lead.scores = {**dims, "priority": round(priority),
                   "why": {"fit": fw, "intent": iw, "quality": qw,
                           "persona": pw, "confidence": cw, "recency": rw}}
    return lead


def why_now(lead):
    """
    One human sentence explaining why this lead is worth contacting today.

    This is the column a salesperson actually reads, so it names the strongest
    evidenced claim rather than restating the score.
    """
    ranked = sorted(lead.claims, key=lambda c: lead.confidence_for(c), reverse=True)
    for claim in ranked:
        conf = lead.confidence_for(claim)
        if conf >= 0.5:
            n = len({domain_of(e.url) for e in lead.evidence_for(claim) if e.url})
            src = f"{n} sources" if n > 1 else "1 source"
            return f"{claim} ({src}, {conf:.0%} confidence)"
    if lead.signals:
        return f"Signal: {', '.join(lead.signals[:2])} (unconfirmed)"
    return "Matches ICP profile, no timing signal found"


def rank(leads, icp):
    for l in leads:
        score_lead(l, icp)
    return sorted(leads, key=lambda l: l.scores["priority"], reverse=True)
