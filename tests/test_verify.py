#!/usr/bin/env python3
"""
Unit tests for src/verify.py (Stage 5 email, domain, and MX verification logic).
"""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import verify
from schema import Lead


def test_check_syntax_valid():
    ok, reason = verify.check_syntax("john.doe@companydomain.com")
    assert ok is True
    assert reason == "ok"


def test_check_syntax_malformed():
    ok, reason = verify.check_syntax("not-an-email")
    assert ok is False
    assert reason == "malformed"

    ok, reason = verify.check_syntax("john@")
    assert ok is False


def test_check_syntax_placeholder():
    ok, reason = verify.check_syntax("test@example.com")
    assert ok is False
    assert "placeholder" in reason or "disposable" in reason

    ok, reason = verify.check_syntax("xxx@yourcompany.com")
    assert ok is False


def test_check_syntax_disposable():
    ok, reason = verify.check_syntax("user@mailinator.com")
    assert ok is False
    assert "disposable" in reason


def test_check_syntax_role_account():
    ok, reason = verify.check_syntax("info@companydomain.com")
    assert ok is False
    assert "role account" in reason

    ok, reason = verify.check_syntax("support@companydomain.com")
    assert ok is False
    assert "role account" in reason


def test_check_syntax_free_mail():
    ok, reason = verify.check_syntax("john.doe@gmail.com")
    assert ok is True
    assert "free provider" in reason


def test_check_pattern_matching_siblings():
    siblings = ["john.doe@companydomain.com", "alice.smith@companydomain.com", "bob.jones@companydomain.com"]
    pat, detail = verify.check_pattern("carol.white@companydomain.com", siblings)
    assert pat is True
    assert "matches company pattern" in detail


def test_check_pattern_unmatching_outlier():
    siblings = ["john.doe@companydomain.com", "alice.smith@companydomain.com", "bob.jones@companydomain.com"]
    pat, detail = verify.check_pattern("carol_white@companydomain.com", siblings)
    assert pat is False
    assert "unlike company norm" in detail


def test_verify_one_fails_on_syntax():
    lead_dict = {"email": "invalid-email", "domain": "companydomain.com", "corroboration": 1}
    res = verify.verify_one(lead_dict, {}, use_paid=False)
    assert res["confidence"] == 0
    assert "syntax" in res["reject_reason"]


def test_verify_leads_schema_integration():
    lead = Lead(company="Acme Corp", domain="companydomain.com")
    lead.contact = {"email": "john.doe@companydomain.com", "name": "John Doe"}
    
    # Run verification with free checks
    results = verify.verify_leads([lead], use_paid=False, workers=1)
    assert len(results) == 1
    assert "confidence" in results[0].verification
    assert "checks" in results[0].verification
    assert "syntax" in results[0].verification["checks"]
