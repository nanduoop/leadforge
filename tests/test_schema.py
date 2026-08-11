import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import Lead, Evidence, SOURCE_WEIGHT


CLAIM = "Company is hiring a video editor"


def _lead_with(*source_types_and_hosts):
    lead = Lead("Acme", "acme.com")
    for i, (st, host) in enumerate(source_types_and_hosts):
        lead.add_evidence(Evidence(CLAIM, f"https://{host}/page{i}", st))
    return lead


def test_three_independent_sources_compound():
  """Three distinct hosts compound toward certainty."""
  lead = _lead_with(
      ("company_site", "acme.com"),
      ("ats_board", "greenhouse.io"),
      ("news", "techcrunch.com"),
  )
  assert lead.confidence_for(CLAIM) == 0.999


def test_aggregator_alone_is_weak():
  lead = _lead_with(("aggregator", "republish.com"))
  assert lead.confidence_for(CLAIM) == SOURCE_WEIGHT["aggregator"]


def test_syndication_does_not_fake_corroboration():
  """Five URLs from the same host count once — anti-syndication property."""
  lead = Lead("Acme", "acme.com")
  for i in range(5):
      lead.add_evidence(Evidence(CLAIM, f"https://syndicate.com/{i}", "aggregator"))
  assert lead.confidence_for(CLAIM) == 0.3
