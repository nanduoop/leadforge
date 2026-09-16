import sys, os, json
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import Lead, Evidence
from score import score_lead

# Fixed fixture, not the operator's live brief. config/brief.json is gitignored,
# so reading it here made the whole suite fail to collect in a fresh clone —
# and made these expected scores drift the moment anyone re-ran intake.
ICP = {
    "target_industries": ["media", "consumer brands", "SaaS", "Content Creation"],
    "target_titles": ["Head of Content", "Creative Director", "VP Marketing",
                      "Head of Production"],
    "target_markets": ["United States", "United Kingdom"],
    "company_size": "51-200",
    "buying_signals": ["hiring video editors", "hiring motion designers",
                       "raised funding"],
    "exclusions": ["recruitment agency", "staffing agency", "freelance marketplace"],
}
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
  # Exclusion matches against real page text (excerpt), never against our own claim.
  lead.add_evidence(Evidence(
      "Company description", "https://staffing.com", "company_site",
      excerpt="we are a staffing agency placing contractors",
      retrieved_at=RECENT))
  return lead


def test_strong_lead_scores_high():
  lead = _strong_lead()
  score_lead(lead, ICP)
  # Fit no longer scores against self-authored claims, so priority sits below the
  # old circular 93. A strong, multi-source lead still clears 70.
  assert lead.scores["priority"] >= 70
  assert lead.scores["fit"] > 0
  assert lead.scores["intent"] > 0


def test_weak_lead_scores_low():
  lead = _weak_lead()
  score_lead(lead, ICP)
  assert lead.scores["priority"] < 40


def test_excluded_lead_scores_zero():
  lead = _excluded_lead()
  score_lead(lead, ICP)
  assert lead.scores["priority"] == 0
  assert lead.scores["fit"] == 0
