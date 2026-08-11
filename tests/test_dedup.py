import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import Lead, Evidence
from dedup import dedup_companies


def test_transitive_merge_via_domain_linkedin_and_name():
  a = Lead("Acme Inc.", "acme.com")
  a.add_evidence(Evidence("hiring", "https://acme.com/careers", "company_careers"))
  a.company_data["linkedin"] = "https://linkedin.com/company/acme"

  b = Lead("Acme", "www.acme.com/jobs")
  b.add_evidence(Evidence("hiring", "https://boards.greenhouse.io/acme/1", "ats_board"))

  c = Lead("Acme Corporation", "")
  c.company_data["linkedin"] = "https://linkedin.com/company/acme"

  d = Lead("Globex", "globex.com")

  out = dedup_companies([a, b, c, d])
  assert len(out) == 2
  names = {l.company for l in out}
  assert "Globex" in names
  acme = next(l for l in out if l.company != "Globex")
  assert acme.domain == "acme.com"
  assert len(acme.evidence) == 2
