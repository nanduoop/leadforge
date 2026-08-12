#!/usr/bin/env python3
"""
Unit tests for src/output.py (Stage 6 export formats, CSV, Sheets, HubSpot).
"""
import sys
import csv
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import output
from schema import Lead, Evidence


def test_row_for_lead():
    lead = Lead(company="Acme Corp", domain="acme.com")
    lead.contact = {"name": "Jane Doe", "title": "CMO", "email": "jane@acme.com"}
    lead.scores = {"priority": 85, "fit": 90, "intent": 80, "confidence": 85}
    lead.verification = {"confidence": 90}
    lead.add_evidence(Evidence(claim="Hiring video editor", url="https://acme.com/jobs", source_type="company_careers"))

    row = output.row_for(lead)
    assert len(row) == len(output.COLUMNS)
    assert row[0] == "Acme Corp"
    assert row[1] == "acme.com"
    assert row[2] == "Jane Doe"
    assert row[3] == "CMO"
    assert row[4] == "jane@acme.com"
    assert row[6] == 85
    assert "90%" in row[14]


def test_to_csv(tmp_path):
    lead = Lead(company="Test Co", domain="test.com")
    lead.contact = {"name": "Alice Smith", "email": "alice@test.com"}
    lead.scores = {"priority": 75}

    csv_file = tmp_path / "test_leads.csv"
    path = output.to_csv([lead], str(csv_file))

    assert csv_file.exists()
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert len(reader) == 2  # Header + 1 row
        assert reader[0] == output.COLUMNS
        assert reader[1][0] == "Test Co"
