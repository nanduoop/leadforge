#!/usr/bin/env python3
"""
Unit tests for src/qualify.py (Stage 3 company qualification against ICP).
"""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import qualify


def test_blob_helper():
    company = {
        "name": "Acme Corp",
        "domain": "acme.com",
        "titles": ["VP Marketing"],
        "urls": ["https://acme.com/jobs"],
    }
    b = qualify.blob(company)
    assert "acme corp" in b
    assert "acme.com" in b
    assert "vp marketing" in b
    assert "https://acme.com/jobs" in b


def test_score_full_match():
    company = {
        "name": "Acme SaaS",
        "domain": "acmesaas.com",
        "signals": ["hiring video editor"],
        "corroboration": 3,
        "titles": ["Head of Marketing"],
        "urls": ["https://acmesaas.com/1", "https://acmesaas.com/2"],
    }
    icp = {
        "target_industries": ["SaaS"],
        "target_titles": ["Head of Marketing"],
        "target_markets": ["US"],
    }
    pts, reasons = qualify.score(company, icp)
    assert pts >= 70
    assert "buying signal" in reasons
    assert "3 sources" in reasons
    assert any("industry:" in r for r in reasons)
    assert any("title:" in r for r in reasons)


def test_score_low_match():
    company = {
        "name": "Local Bakery",
        "domain": "bakery.com",
        "signals": [],
        "corroboration": 1,
        "titles": [],
        "urls": ["https://bakery.com"],
    }
    icp = {
        "target_industries": ["SaaS"],
        "target_titles": ["Head of Marketing"],
        "target_markets": ["US"],
    }
    pts, _ = qualify.score(company, icp)
    assert pts < 35


def test_score_excludes_government_and_educational_domains():
    gov_company = {
        "name": "Education Dept",
        "domain": "education.maharashtra.gov.in",
        "signals": ["hiring"],
        "corroboration": 2,
        "urls": ["https://education.maharashtra.gov.in"]
    }
    icp = {
        "target_industries": ["SaaS", "Education"],
        "target_titles": ["Head of Marketing"],
        "target_markets": ["US"],
        "exclusions": ["government", "agency"]
    }
    pts, reasons = qualify.score(gov_company, icp)
    assert pts == 0
    assert any("excluded" in r for r in reasons)


def test_score_excludes_dictionary_and_reference_domains():
    dict_company = {
        "name": "Merriam-Webster",
        "domain": "merriam-webster.com",
        "signals": ["hiring"],
        "corroboration": 2,
        "urls": ["https://merriam-webster.com"]
    }
    icp = {
        "target_industries": ["Education", "Content"],
        "target_titles": ["Head of Marketing"]
    }
    pts, reasons = qualify.score(dict_company, icp)
    assert pts == 0
    assert any("excluded" in r for r in reasons)


def test_score_excludes_nsfw_and_adult_domains():
    nsfw_company = {
        "name": "Adult Site",
        "domain": "adultclip.com",
        "signals": ["hiring"],
        "corroboration": 2,
        "urls": ["https://adultclip.com"]
    }
    icp = {
        "target_industries": ["Media"],
        "target_titles": ["Head of Content"]
    }
    pts, reasons = qualify.score(nsfw_company, icp)
    assert pts == 0
    assert any("excluded" in r for r in reasons)




