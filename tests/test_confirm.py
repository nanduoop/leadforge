#!/usr/bin/env python3
"""Confirmation gates: the AI must not assume. Every required field is asked,
and nothing invented is treated as user-supplied evidence.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import intake
import confirm


REQUIRED = ("offer", "target_industries", "target_titles", "target_markets", "buying_signals")


def test_gaps_lists_every_required_field_on_a_blank_brief():
    brief = intake.blank_brief()
    gaps = confirm.required_gaps(brief)
    for field in REQUIRED:
        assert field in gaps


def test_confirm_plan_refuses_to_run_when_gaps_remain():
    brief = intake.blank_brief()
    plan = confirm.plan_run(brief, confirmed=False)
    assert plan["allowed"] is False
    # Gaps take priority over the confirm flag. An empty brief is not "unconfirmed",
    # it is incomplete.
    assert plan["reason"] == "gaps"
    assert set(REQUIRED).issubset(set(plan["gaps"]))


def test_confirm_plan_still_refuses_when_gaps_even_if_confirmed_flag_set():
    brief = intake.blank_brief()
    plan = confirm.plan_run(brief, confirmed=True)
    assert plan["allowed"] is False
    assert plan["reason"] == "gaps"


def test_confirm_plan_allows_only_after_user_fills_and_confirms():
    brief = intake.blank_brief()
    intake.fill_from_answers(brief, {
        "offer": "Video production for roofing companies",
        "outcome": "More booked jobs from content",
        "target_industries": "roofing, restoration",
        "target_titles": "Owner, GM",
        "target_markets": "Dallas Fort Worth, Texas",
        "buying_signals": "hiring sales reps, posting storm content",
    }, overwrite=True)
    blocked = confirm.plan_run(brief, confirmed=False)
    assert blocked["allowed"] is False
    allowed = confirm.plan_run(brief, confirmed=True)
    assert allowed["allowed"] is True
    assert allowed["gaps"] == []


def test_site_derived_fields_are_drafts_not_user_evidence():
    brief = intake.blank_brief()
    brief["client"]["name"] = "Acme"
    brief["client"]["offer"] = "We sell widgets"
    brief["meta"]["source"] = ["site:https://acme.com"]
    brief["meta"]["last_site_read"] = {"ok": True, "source": "firecrawl_json"}
    assert confirm.is_user_supplied(brief, "offer") is False
    assert "offer" in confirm.draft_fields(brief)


def test_user_answers_count_as_user_supplied():
    brief = intake.blank_brief()
    intake.fill_from_answers(brief, {"offer": "Storm restoration content"}, overwrite=True)
    brief["meta"].setdefault("answered_by", {})["offer"] = "user"
    assert confirm.is_user_supplied(brief, "offer") is True


def test_questions_always_include_a_confirmation_prompt():
    qs = confirm.questions_for_ui()
    fields = [q["field"] for q in qs]
    assert "confirm_run" in fields
    confirm_q = next(q for q in qs if q["field"] == "confirm_run")
    assert confirm_q["required"] is True


def test_pipeline_start_rejected_without_confirmation(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(ROOT / "ui"))
    import server

    (tmp_path / "config").mkdir()
    brief = intake.blank_brief()
    intake.save_brief(brief, str(tmp_path / "config" / "brief.json"))
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "ARTIFACT", {**server.ARTIFACT, "intake": str(tmp_path / "config" / "brief.json")})
    monkeypatch.setattr("server.preflight.run_checks", lambda: {"ready": True, "blocking": 0, "warnings": 0, "checks": []})
    client = TestClient(server.app)
    response = client.post("/api/pipeline/start", json={"limit": 12})
    assert response.status_code in (400, 409, 422)
    body = response.json()
    detail = str(body.get("detail") or body)
    assert "confirm" in detail.lower() or "gap" in detail.lower()
