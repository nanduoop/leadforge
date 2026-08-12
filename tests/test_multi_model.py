#!/usr/bin/env python3
"""
Extractor-envelope resilience tests.

LeadForge does not call an LLM API itself — extraction happens inside Firecrawl
and Browserbase, and their underlying models change without notice. What has to
survive that is the shape of what comes back: `{"people": [...]}` from one,
`{"data": {"people": [...]}}` from another, missing fields, empty arrays.

These fixtures are named after the model families whose envelopes they mimic.
They are not evidence that any particular model was used or evaluated.
"""
import sys
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import Lead, Evidence, domain_of, norm_name
from contacts import people_from_item
import score
import qualify
import verify


GEMINI_SAMPLE_OUTPUT = {
    "people": [
        {
            "name": "Sarah Connor",
            "title": "Vice President of Marketing",
            "email": "sarah.connor@cyberdyne.com",
            "linkedin": "https://linkedin.com/in/sarahconnor"
        },
        {
            "name": "John Connor",
            "title": "Head of Growth",
            "email": "john.connor@cyberdyne.com"
        }
    ]
}

CLAUDE_SAMPLE_OUTPUT = {
    "data": {
        "people": [
            {
                "name": "Sarah Connor",
                "title": "VP Marketing",
                "email": "sarah.connor@cyberdyne.com",
                "linkedin": "https://linkedin.com/in/sarahconnor"
            }
        ]
    }
}

GPT4_SAMPLE_OUTPUT = {
    "people": [
        {
            "name": "Dr. Miles Dyson",
            "title": "Chief Technology Officer",
            "email": "miles.dyson@cyberdyne.com",
            "linkedin": "https://linkedin.com/in/milesdyson"
        }
    ]
}

DEEPSEEK_SAMPLE_OUTPUT = {
    "people": [
        {
            "name": "Kyle Reese",
            "title": "Director of Security",
            "email": "kyle.reese@cyberdyne.com"
        }
    ]
}


def test_multi_model_people_schema_extraction():
    """Test extracting people payload from different LLM envelope shapes."""
    for model_name, payload in [
        ("Gemini", GEMINI_SAMPLE_OUTPUT),
        ("Claude", CLAUDE_SAMPLE_OUTPUT),
        ("GPT-4", GPT4_SAMPLE_OUTPUT),
        ("DeepSeek", DEEPSEEK_SAMPLE_OUTPUT),
    ]:
        block = people_from_item(payload)
        assert len(block) > 0, f"Failed to extract people from {model_name} payload envelope"
        person = block[0]
        assert person["name"]
        assert person["title"]
        assert person["email"]


def test_multi_model_lead_evidence_compounding():
    """Test evidence compounding when claims are backed by different model extractions."""
    lead = Lead(company="Cyberdyne Systems", domain="cyberdyne.com")
    
    lead.add_evidence(Evidence(
        claim="Hiring Vice President of Marketing",
        url="https://cyberdyne.com/careers",
        source_type="company_careers",
        excerpt="We are looking for a Vice President of Marketing to scale our AI robotics division."
    ))

    lead.add_evidence(Evidence(
        claim="Hiring Vice President of Marketing",
        url="https://boards.greenhouse.io/cyberdyne/jobs/101",
        source_type="ats_board",
        excerpt="Cyberdyne Systems VP Marketing posting"
    ))

    conf = lead.confidence_for("Hiring Vice President of Marketing")
    assert conf > 0.95, f"Expected compound confidence > 0.95, got {conf}"
    assert lead.corroboration == 2, f"Expected 2 distinct hosts, got {lead.corroboration}"


def test_multi_model_qualification_resilience():
    """Test qualification scoring under multi-model metadata variations."""
    icp = {
        "target_industries": ["Robotics", "AI", "Defense"],
        "target_titles": ["VP Marketing", "CTO", "Head of Growth"],
        "target_markets": ["US", "Global"],
        "buying_signals": ["hiring"],
    }

    company_payload = {
        "name": "Cyberdyne Systems Corp Robotics AI Defense",
        "domain": "cyberdyne.com",
        "signals": ["hiring VP Marketing"],
        "corroboration": 3,
        "titles": ["Vice President of Marketing", "Chief Technology Officer"],
        "urls": ["https://cyberdyne.com", "https://cyberdyne.com/careers"],
    }

    score_val, reasons = qualify.score(company_payload, icp)
    assert score_val >= 50, f"Expected qualification score >= 50, got {score_val}"
    assert len(reasons) >= 3


def test_multi_model_scoring_and_why_now():
    """Test multi-dimensional scoring and human-readable why_now generation across models."""
    icp = {
        "target_industries": ["AI"],
        "target_titles": ["VP Marketing"],
        "target_markets": ["US"],
        "buying_signals": ["hiring"],
    }

    lead = Lead(company="Cyberdyne", domain="cyberdyne.com")
    lead.contact = {
        "name": "Sarah Connor",
        "title": "Vice President of Marketing",
        "email": "sarah.connor@cyberdyne.com",
        "origin": "team_page"
    }
    lead.add_evidence(Evidence(
        claim="Cyberdyne is hiring a VP Marketing",
        url="https://cyberdyne.com/careers",
        source_type="company_careers"
    ))
    lead.verification = {"confidence": 95, "checks": {"syntax": {"pass": True}, "mx": {"pass": True}}}

    scored_lead = score.score_lead(lead, icp)
    assert scored_lead.scores["priority"] >= 40
    wn = score.why_now(scored_lead)
    assert "hiring" in wn.lower() or "cyberdyne" in wn.lower()


def test_llama_style_minimal_output():
    """Llama-style minimal JSON — single person, no linkedin."""
    payload = {
        "people": [{"name": "Alice Ng", "title": "CEO", "email": "alice@startup.io"}]
    }
    block = people_from_item(payload)
    assert len(block) == 1
    assert block[0]["email"].endswith("@startup.io")


def test_mistral_nested_data_output():
    """Mistral sometimes nests under data.people."""
    payload = {"data": {"people": [{"name": "Bob Vance", "title": "CTO", "email": "bob@co.io"}]}}
    block = people_from_item(payload)
    assert block[0]["name"] == "Bob Vance"


def test_empty_model_output_handled():
    """Empty arrays from any model should not crash extraction."""
    for payload in [{"people": []}, {"data": {"people": []}}, {}, None, {"people": "not a list"}]:
        assert people_from_item(payload) == []


def test_single_word_names_are_dropped():
    """A first name alone cannot be matched to a company or personalised."""
    payload = {"people": [{"name": "Sarah", "title": "CEO", "email": "s@co.io"},
                          {"name": "Sarah Connor", "title": "CEO", "email": "s@co.io"}]}
    block = people_from_item(payload)
    assert [p["name"] for p in block] == ["Sarah Connor"]
