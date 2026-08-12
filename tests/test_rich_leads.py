#!/usr/bin/env python3
"""
TDD Test Suite: Rich Lead Quality & Completeness Verification.

Validates that high-quality leads in LeadForge contain complete, actionable data:
- Full contact profile (name, title, verified non-role email, linkedin)
- Rich firmographics (company, domain, size, industry, location)
- Multi-source evidence corroboration (urls, claims, confidence >= 70%)
- Clear action sentence (why_now)
- Proper priority scoring reflecting lead richness
"""
import pytest
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from schema import Lead, Evidence
from score import score_lead, why_now
from output import row_for


def test_rich_lead_has_all_key_attributes_and_scores_high():
    icp = {
        "target_industries": ["SaaS", "Software"],
        "target_markets": ["US", "North America"],
        "company_size": "50-200",
        "target_titles": ["Head of Marketing", "VP Growth"],
        "buying_signals": ["Hiring video editor", "Series A"],
        "exclusions": ["Agency"]
    }

    lead = Lead(
        company="Acme AI Corp",
        domain="acme.ai",
        contact={
            "name": "Jane Doe",
            "title": "Head of Marketing",
            "email": "jane@acme.ai",
            "linkedin": "https://linkedin.com/in/janedoe",
            "origin": "team_page"
        },
        company_data={
            "size": "100",
            "industry": "SaaS",
            "location": "San Francisco, CA, US",
            "funding": "$15M Series A"
        },
        signals=["Hiring video editor", "Series A funding"],
        sources=["ats_board", "company_site", "linkedin"]
    )

    lead.add_evidence(Evidence(
        claim="Hiring video editor",
        url="https://boards.greenhouse.io/acme/jobs/101",
        source_type="ats_board",
        excerpt="Acme is hiring a Head of Content and Video Editor"
    ))
    lead.add_evidence(Evidence(
        claim="Hiring video editor",
        url="https://acme.ai/careers/video-editor",
        source_type="company_careers",
        excerpt="Join our marketing team as a video editor"
    ))
    lead.add_evidence(Evidence(
        claim="Raised $15M Series A",
        url="https://techcrunch.com/2026/acme-series-a",
        source_type="news",
        excerpt="Acme AI closes $15M Series A led by venture firm"
    ))

    lead.verification = {"confidence": 100, "status": "deliverable"}

    score_lead(lead, icp)

    # Rich lead assertions
    assert lead.scores["priority"] >= 75, f"Priority score {lead.scores['priority']} should be >= 75 for rich lead"
    assert lead.scores["fit"] >= 75
    assert lead.scores["persona"] >= 80
    assert lead.scores["quality"] >= 75
    assert lead.scores["confidence"] >= 70

    # Human actionable why_now text check
    why = why_now(lead)
    assert "Hiring video editor" in why or "Series A" in why
    assert "% confidence" in why

    # Row formatting check
    row = row_for(lead)
    # Row: Company, Domain, Contact, Title, Email, LinkedIn, Priority, Fit, Intent, Confidence, Why Now, Buying Signal, Evidence, Sources, Verified, Status
    assert row[0] == "Acme AI Corp"
    assert row[1] == "acme.ai"
    assert row[2] == "Jane Doe"
    assert row[3] == "Head of Marketing"
    assert row[4] == "jane@acme.ai"
    assert row[5] == "https://linkedin.com/in/janedoe"
    assert row[6] >= 75
    assert len(row[12]) > 0  # Evidence URLs present


def test_is_rich_lead_helper():
    """Test a dedicated validator that checks if a lead meets the bar of a 'rich lead'."""
    from schema import is_rich_lead

    incomplete_lead = Lead(
        company="Bare Corp",
        domain="bare.com",
        contact={"name": "", "email": "info@bare.com"},
    )
    assert not is_rich_lead(incomplete_lead)

    rich_lead = Lead(
        company="Rich Tech",
        domain="richtech.com",
        contact={"name": "Alice Smith", "title": "VP Growth", "email": "alice@richtech.com"},
        company_data={"industry": "SaaS", "size": "50"},
        signals=["Hiring"]
    )
    rich_lead.add_evidence(Evidence(claim="Hiring", url="https://richtech.com/careers", source_type="company_careers"))
    rich_lead.verification = {"confidence": 90}
    rich_lead.scores = {"priority": 80}

    assert is_rich_lead(rich_lead)


def test_rich_lead_quality_threshold_filtering():
    """Verify filtering a batch of leads to retain only rich, high-conviction leads."""
    from schema import is_rich_lead

    l1 = Lead(company="Incomplete Inc", domain="inc.com", contact={"name": ""})
    l2 = Lead(company="No Contact LLC", domain="nocontact.com", contact={})
    l3 = Lead(
        company="Good Lead Co",
        domain="goodlead.co",
        contact={"name": "Bob Vance", "title": "VP Sales", "email": "bob@goodlead.co"},
        signals=["Hiring Sales Reps"]
    )
    l3.add_evidence(Evidence(claim="Hiring Sales Reps", url="https://goodlead.co/jobs"))

    batch = [l1, l2, l3]
    rich_batch = [l for l in batch if is_rich_lead(l)]

    assert len(rich_batch) == 1
    assert rich_batch[0].company == "Good Lead Co"

