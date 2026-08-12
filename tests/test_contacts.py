#!/usr/bin/env python3
"""
Unit tests for src/contacts.py (Stage 4 decision maker search and email pattern derivation).
"""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import contacts
from schema import Lead


def test_title_matches():
    wanted = ["Head of Marketing", "CMO", "VP Marketing", "Video Producer"]
    assert contacts.title_matches("Head of Marketing & Sales", wanted)
    assert contacts.title_matches("Chief Marketing Officer (CMO)", wanted)
    assert contacts.title_matches("Senior Vice President of Marketing", wanted)
    assert contacts.title_matches("Lead Video Producer", wanted)
    assert not contacts.title_matches("Software Engineer", wanted)
    assert not contacts.title_matches("Accountant", wanted)
    assert not contacts.title_matches("", wanted)


def test_pattern_from_first_last_dot():
    # known address is john.doe@acme.com, so pattern is first.last
    known = "john.doe@acme.com"
    pattern = contacts.pattern_from(known, "John", "Doe", "acme.com")
    assert pattern == "john.doe@acme.com"


def test_pattern_from_first_last_underscore():
    # known address is john_doe@acme.com
    known = "john_doe@acme.com"
    pattern = contacts.pattern_from(known, "John", "Doe", "acme.com")
    assert pattern == "john_doe@acme.com"


def test_pattern_from_first_initial_last():
    # known address is jdoe@acme.com
    known = "jdoe@acme.com"
    pattern = contacts.pattern_from(known, "John", "Doe", "acme.com")
    assert pattern == "jdoe@acme.com"


def test_pattern_from_first_name_only():
    # known address is john@acme.com
    known = "john@acme.com"
    pattern = contacts.pattern_from(known, "John", "Doe", "acme.com")
    assert pattern == "john@acme.com"


def test_pattern_from_fallback_when_no_known_email():
    # If no known email address on page, fallback to standard first.last@domain
    pattern = contacts.pattern_from("", "Alice", "Smith", "acme.com")
    assert pattern == "alice.smith@acme.com"



def test_find_for_no_domain():
    lead = Lead(company="No Domain Inc", domain="")
    result = contacts.find_for(lead, ["CEO"])
    assert result == []
