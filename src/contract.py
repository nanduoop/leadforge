#!/usr/bin/env python3
"""Write the v1 lead interchange contract.

LeadForge's internal `Lead` is rich — every claim carries its evidence, its
source weight, and when it was retrieved. A send engine needs almost none of
that. It needs to know who to write to, why now, and whether the reason is
trustworthy enough to put in an email.

This module is the narrowing. It is the only place that knows both shapes, so
when the send side needs a new field there is exactly one file to change.

    python3 -m src.contract            # data/scored.json -> data/leads.contract.json

The contract is versioned. If a change would break a reader, add v2 rather than
editing v1 — a send engine pinned to v1 must keep working.
"""
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN = os.path.join(ROOT, "data", "scored.json")
OUT = os.path.join(ROOT, "data", "leads.contract.json")

CONTRACT_VERSION = "leadforge.lead/v1"

# Only these prove a trigger on their own. An aggregator republishing a listing
# is not evidence the listing is live, and a search snippet proves less again.
EMPLOYER_CONTROLLED = {"company_site", "company_careers", "ats_board", "press_release"}

VALID_STATUS = ("qualified", "partial", "unverified", "rejected")


def first_name_of(full_name):
    """Split conservatively. A wrong first name is worse than none at all.

    Returns None for single tokens that are probably not a given name (an
    all-caps handle, an initial, a company suffix), because a send engine that
    receives None can fall back to a no-name greeting instead of writing "Hi LLC".
    """
    if not full_name:
        return None
    parts = [p for p in str(full_name).replace(",", " ").split() if p]
    if not parts:
        return None
    head = parts[0].strip(".")
    if len(head) < 2:
        return None
    if head.isupper() and len(head) <= 3:
        return None
    return head


def status_for(lead):
    """Map internal state onto the four values a send engine understands."""
    if getattr(lead, "status", None) == "rejected" or not (lead.contact or {}).get("email"):
        return "rejected"
    v = lead.verification or {}
    conf = v.get("confidence")
    if v.get("reject_reason"):
        return "rejected"
    if conf is None:
        return "unverified"
    if conf >= 80:
        return "qualified"
    return "partial"


def trigger_for(lead):
    """Derive the one sentence a send engine can quote, plus whether to trust it.

    Prefers the highest-confidence claim, because that is the one with the most
    corroboration behind it, and attaches the best source backing that claim.
    """
    claims = list(lead.claims or [])
    if not claims:
        return {
            "role": None,
            "summary": (lead.signals or ["No specific trigger recorded"])[0],
            "url": None,
            "source_type": "inferred",
            "observed_at": None,
            "verified": False,
        }

    best_claim = max(claims, key=lambda c: lead.confidence_for(c))
    evidence = sorted(
        lead.evidence_for(best_claim),
        key=lambda e: getattr(e, "weight", 0) or 0,
        reverse=True,
    )
    top = evidence[0] if evidence else None
    sources = {getattr(e, "source_type", None) for e in evidence}

    return {
        "role": (lead.signals or [None])[0],
        "summary": best_claim,
        "url": getattr(top, "url", None),
        "source_type": getattr(top, "source_type", "inferred"),
        "observed_at": getattr(top, "retrieved_at", None),
        "verified": bool(sources & EMPLOYER_CONTROLLED),
    }


def lead_to_contract(lead):
    c = lead.contact or {}
    s = lead.scores or {}
    v = lead.verification or {}

    return {
        "id": lead.key,
        "company": lead.company,
        "domain": lead.domain,
        "location": (lead.company_data or {}).get("location") or None,
        "contact": {
            "name": c.get("name") or None,
            "first_name": first_name_of(c.get("name")),
            "title": c.get("title") or None,
            "email": c.get("email", ""),
            "linkedin": c.get("linkedin") or None,
            "email_verified": bool(v.get("confidence") and v["confidence"] >= 80),
            "email_confidence": v.get("confidence"),
        },
        "trigger": trigger_for(lead),
        "scores": {
            "priority": s.get("priority", 0),
            "fit": s.get("fit", 0),
            "intent": s.get("intent", 0),
            "confidence": s.get("confidence", 0),
        },
        "signals": list(lead.signals or [])[:5],
        "evidence": [
            {
                "claim": getattr(e, "claim", ""),
                "url": getattr(e, "url", None),
                "source_type": getattr(e, "source_type", "inferred"),
                "excerpt": (getattr(e, "excerpt", None) or None),
                "retrieved_at": getattr(e, "retrieved_at", None),
                "weight": getattr(e, "weight", None),
            }
            for e in list(lead.evidence or [])[:8]
        ],
        "status": status_for(lead),
    }


def build(leads, brief_id=None):
    return {
        "contract": CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "brief_id": brief_id or "",
        "leads": [lead_to_contract(l) for l in leads],
    }


def write(leads, path=OUT, brief_id=None):
    doc = build(leads, brief_id=brief_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    return doc


def main():
    from . import schema

    if not os.path.exists(IN):
        raise SystemExit("no scored leads at %s — run the pipeline first" % IN)
    with open(IN, encoding="utf-8") as fh:
        raw = json.load(fh)
    leads = [schema.Lead.from_dict(r) for r in raw]
    doc = write(leads)
    sendable = sum(1 for l in doc["leads"] if l["status"] == "qualified")
    print("wrote %s — %d leads, %d qualified" % (OUT, len(doc["leads"]), sendable))


if __name__ == "__main__":
    main()
