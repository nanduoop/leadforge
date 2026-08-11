import sys, os, json
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import Lead, Evidence
from score import score_lead

ROOT = os.path.join(os.path.dirname(__file__), "..")
ICP = json.load(open(os.path.join(ROOT, "config", "brief.json")))["icp"]
RECENT = datetime.now(timezone.utc).isoformat()


def _strong_lead():
  lead = Lead("Wattpad", "wattpad.com")
  lead.company_data = {"size": "120 employees", "industry": "media consumer brands"}
  lead.signals = ["hiring video editors", "raised funding"]
  lead.contact = {"name": "Sam Lee", "title": "Head of Content"}
  lead.add_evidence(Evidence(
      "Company is hiring video editors",
      "https://wattpad.com/careers", "company_careers", retrieved_at=RECENT))
  lead.add_evidence(Evidence(
      "media company in United States",
      "https://wattpad.com/about", "company_site", retrieved_at=RECENT))
  lead.add_evidence(Evidence(
      "raised funding recently",
      "https://techcrunch.com/wattpad", "news", retrieved_at=RECENT))
  return lead


def _weak_lead():
  lead = Lead("Obscure", "obscure.xyz")
  lead.add_evidence(Evidence(
      "random mention", "https://obscure.xyz", "search_result", retrieved_at=RECENT))
  return lead


def _excluded_lead():
  lead = Lead("Staffing Inc", "staffing.com")
  lead.add_evidence(Evidence(
      "we are a staffing agency", "https://staffing.com", "company_site",
      retrieved_at=RECENT))
  return lead


def test_strong_lead_scores_high():
  lead = _strong_lead()
  score_lead(lead, ICP)
  assert lead.scores["priority"] == 93


def test_weak_lead_scores_low():
  lead = _weak_lead()
  score_lead(lead, ICP)
  assert lead.scores["priority"] == 18


def test_excluded_lead_scores_zero():
  lead = _excluded_lead()
  score_lead(lead, ICP)
  assert lead.scores["priority"] == 0
