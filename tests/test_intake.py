#!/usr/bin/env python3
"""
Unit tests for src/intake.py (Brief creation, normalizing, answering).
"""
import sys
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import intake


def test_blank_brief():
    brief = intake.blank_brief()
    assert "client" in brief
    assert "icp" in brief
    assert "meta" in brief
    assert isinstance(brief["icp"]["target_industries"], list)


def test_normalise():
    assert intake.normalise("acme.com") == "https://acme.com"
    assert intake.normalise("https://www.acme.com/about") == "https://www.acme.com/about"
    assert intake.normalise(" HTTP://acme.co.uk/ ") == "HTTP://acme.co.uk"


def test_from_text():
    brief = intake.blank_brief()
    text = "We provide B2B video editing for SaaS companies in the US hiring growth roles"
    updated = intake.from_text(text, brief)
    assert updated["client"]["offer"] == text
    assert "United States" in updated["icp"]["target_markets"]


def test_fill_from_answers():
    brief = intake.blank_brief()
    answers = {
        "offer": "Video production",
        "target_industries": "SaaS, E-commerce",
        "target_titles": "CMO, VP Marketing",
        "target_markets": "US, UK",
        "buying_signals": "hiring video editor",
        "exclusions": "agencies",
    }
    updated = intake.fill_from_answers(brief, answers, overwrite=True)
    assert updated["client"]["offer"] == "Video production"
    assert updated["icp"]["target_industries"] == ["SaaS", "E-commerce"]
    assert updated["icp"]["target_titles"] == ["CMO", "VP Marketing"]
    assert updated["icp"]["exclusions"] == ["agencies"]


def test_brief_summary():
    brief = intake.blank_brief()
    brief["client"]["name"] = "TestClient"
    brief["client"]["site"] = "https://test.com"
    brief["icp"]["target_industries"] = ["Tech"]
    brief["icp"]["target_titles"] = ["CEO"]

    summary = intake.brief_summary(brief)
    assert summary["client_name"] == "TestClient"
    assert summary["site"] == "https://test.com"
    assert summary["industries"] == ["Tech"]
    assert summary["titles"] == ["CEO"]
    assert isinstance(summary["gaps"], list)


def test_questions_for_ui():
    q_list = intake.questions_for_ui()
    assert len(q_list) > 0
    fields = [q["field"] for q in q_list]
    assert "offer" in fields
    assert "target_industries" in fields
    assert "target_titles" in fields


def test_text_from_scrape():
    text = "# Acme Corp\n\nWe build video editing tools for SaaS companies worldwide."
    name, offer = intake._text_from_scrape(text)
    assert name == "Acme Corp"
    assert "video editing" in offer


def test_from_site_scrape_fallback(monkeypatch):
    brief = intake.blank_brief()
    monkeypatch.setattr(intake.C, "is_linked", lambda app: True)
    monkeypatch.setattr(intake.C, "extract", lambda *a, **k: None)
    monkeypatch.setattr(intake.C, "scrape", lambda url: {
        "ok": True,
        "text": "# Fight Club\n\nCreative talent network for video editors.",
        "source": "local_http",
        "error": None,
    })
    result = intake.from_site("fightclub.com", brief)
    assert result["meta"]["last_site_read"]["ok"] is True
    assert result["client"]["name"] == "Fight Club"
    assert "video editors" in result["client"]["offer"]


def test_from_site_failure(monkeypatch):
    brief = intake.blank_brief()
    monkeypatch.setattr(intake.C, "is_linked", lambda app: True)
    monkeypatch.setattr(intake.C, "extract", lambda *a, **k: None)
    monkeypatch.setattr(intake.C, "scrape", lambda url: {
        "ok": False, "text": "", "source": None, "error": "all fetch methods failed",
    })
    result = intake.from_site("bad.example", brief)
    assert result["meta"]["last_site_read"]["ok"] is False
