#!/usr/bin/env python3
"""Confirmation gates. The AI does not assume. The user supplies the brief.

Required ICP fields stay empty until the person answers them. Site-derived
copy is a draft, never user evidence. A run is allowed only after the gaps
are filled AND the user confirms.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake

REQUIRED = ("offer", "target_industries", "target_titles", "target_markets", "buying_signals")


def _current(brief, field):
    return brief.get("client", {}).get(field) or brief.get("icp", {}).get(field) or ""


def required_gaps(brief):
    gaps = []
    for field in REQUIRED:
        val = _current(brief, field)
        empty = (not val) or (isinstance(val, list) and not [x for x in val if str(x).strip()])
        if empty:
            gaps.append(field)
    return gaps


def is_user_supplied(brief, field):
    answered = (brief.get("meta") or {}).get("answered_by") or {}
    return answered.get(field) == "user"


def draft_fields(brief):
    """Fields filled from a website scrape, not from the user's own answers."""
    sources = (brief.get("meta") or {}).get("source") or []
    from_site = any(str(s).startswith("site:") for s in sources)
    if not from_site:
        return []
    out = []
    for field in REQUIRED:
        if _current(brief, field) and not is_user_supplied(brief, field):
            out.append(field)
    return out


def plan_run(brief, confirmed=False):
    gaps = required_gaps(brief)
    if gaps:
        return {"allowed": False, "reason": "gaps", "gaps": gaps}
    if not confirmed:
        return {"allowed": False, "reason": "unconfirmed", "gaps": []}
    return {"allowed": True, "reason": "ok", "gaps": []}


def questions_for_ui():
    qs = list(intake.questions_for_ui())
    qs.append({
        "field": "confirm_run",
        "prompt": "Confirm these answers are yours and that LeadForge should search with them. Type yes.",
        "required": True,
        "section": "confirm",
        "multiline": False,
        "list": False,
    })
    return qs
