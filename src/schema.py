#!/usr/bin/env python3
"""
The canonical lead record, and the evidence model underneath it.

Multi-source lead generation has one structural problem: every source returns a
different shape. Firecrawl returns pages, Clay returns entities, a job board returns
postings, a browser returns whatever the DOM had. If each stage handles each shape,
the combinations multiply and the pipeline rots.

So this file is the contract. Sources normalise into `Lead` and nothing downstream
ever sees a vendor's native format again.

The second idea here matters more. Every claim carries its evidence: where it came
from, when, the exact excerpt, and how much that kind of source is worth. A lead is
not "qualified" because a model said so. It is qualified because three independent
sources agree, and the record can prove it.

    lead.add_evidence(Evidence(
        claim="Company is hiring a video editor",
        url="https://boards.greenhouse.io/acme/jobs/123",
        source_type="ats_board",
        excerpt="We're looking for a Senior Video Editor..."))

    lead.confidence_for("Company is hiring a video editor")   # 0.0 - 1.0
"""
import json, hashlib, re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from urllib.parse import urlparse

# How much a claim is worth by where it came from. A company's own careers page is
# near-certain. An aggregator that republishes scraped listings is close to worthless
# on its own, because it is often stale and sometimes fabricated.
SOURCE_WEIGHT = {
    "company_site":    0.95,   # their own words on their own domain
    "company_careers": 0.95,   # a posting on their own careers page
    "ats_board":       0.90,   # Greenhouse, Lever, Ashby. Employer-controlled.
    "press_release":   0.85,
    "news":            0.75,
    "linkedin":        0.75,
    "structured_db":   0.70,   # Clay, Apollo. Good coverage, known to return wrong entities.
    "job_board":       0.60,
    "social":          0.50,
    "search_result":   0.45,   # a search snippet alone proves very little
    "aggregator":      0.30,   # republished, often stale
    "inferred":        0.20,   # derived by us, e.g. a guessed email pattern
}

# Outcomes a source can report. The point is that a failure is never silently an
# empty list: "blocked" and "not_found" mean opposite things for what to do next.
STATUS = ("success", "partial", "blocked", "not_found",
          "rate_limited", "invalid", "error")


def now():
    return datetime.now(timezone.utc).isoformat()


def domain_of(url_or_domain):
    """Reduce anything url-shaped to a bare registrable domain."""
    s = (url_or_domain or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        s = urlparse(s).netloc
    s = s.split("/")[0]
    if s.startswith("www."):
        s = s[4:]
    parts = [p for p in s.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    two = {"co.uk", "com.au", "co.nz", "co.in", "com.sg", "co.za", "com.br", "co.jp"}
    return ".".join(parts[-3:]) if ".".join(parts[-2:]) in two else ".".join(parts[-2:])


def norm_name(name):
    """Normalise a company name enough to match 'Acme, Inc.' with 'acme inc'."""
    n = (name or "").lower().strip()
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|bv|plc|co|company|"
               r"group|holdings|pvt|private)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


@dataclass
class Evidence:
    """One piece of proof for one claim. Immutable once recorded."""
    claim: str
    url: str = ""
    source_type: str = "search_result"
    excerpt: str = ""
    retrieved_at: str = field(default_factory=now)

    @property
    def weight(self):
        return SOURCE_WEIGHT.get(self.source_type, 0.3)

    def to_dict(self):
        d = asdict(self)
        d["weight"] = round(self.weight, 2)
        return d


@dataclass
class SourceResult:
    """
    What a source returns. Always this, never a bare list.

    `status` is what lets the orchestrator tell "this source is rate limited, retry
    later" apart from "this source worked and the answer is genuinely nothing".
    """
    source: str
    status: str = "success"
    items: list = field(default_factory=list)
    error: str = ""
    cost: int = 0

    @property
    def ok(self):
        return self.status in ("success", "partial")

    @property
    def retryable(self):
        return self.status in ("rate_limited", "error", "blocked")


class Lead:
    """
    One company, optionally one person at it, plus everything we can prove.

    Identity is the domain. Two records with the same domain are the same company
    even when the names differ, which is what makes dedup reliable.
    """

    def __init__(self, company="", domain="", **kw):
        self.company = (company or "").strip()
        self.domain = domain_of(domain or "")
        self.contact = kw.get("contact", {})       # name, title, email, linkedin, origin
        self.company_data = kw.get("company_data", {})  # size, funding, tech, location
        self.signals = kw.get("signals", [])       # buying signals observed
        self.sources = kw.get("sources", [])       # which sources found this
        self.evidence = []
        self.scores = {}
        self.verification = {}
        self.status = "discovered"

    # ---------------------------------------------------------------- identity

    @property
    def key(self):
        """Primary identity for dedup. Domain, falling back to a normalised name."""
        return self.domain or f"name:{norm_name(self.company)}"

    @property
    def contact_key(self):
        email = (self.contact.get("email") or "").strip().lower()
        if email:
            return f"email:{email}"
        li = (self.contact.get("linkedin") or "").strip().lower().rstrip("/")
        if li:
            return f"li:{li}"
        name = norm_name(self.contact.get("name", ""))
        return f"person:{name}@{self.key}" if name else ""

    # ---------------------------------------------------------------- evidence

    def add_evidence(self, ev):
        """Record proof. Identical claim from the same URL is not counted twice."""
        for existing in self.evidence:
            if existing.claim == ev.claim and existing.url == ev.url:
                return
        self.evidence.append(ev)

    def evidence_for(self, claim):
        return [e for e in self.evidence if e.claim == claim]

    def confidence_for(self, claim):
        """
        Combined confidence that one claim is true, on 0..1.

        Independent sources compound rather than average, so two mediocre sources
        that agree beat one good source alone. Sources are counted once per host, so
        the same story syndicated across five sites does not read as five confirmations.

        Uses the standard "probability that not all sources are wrong" form:
            1 - product(1 - weight)
        """
        evs = self.evidence_for(claim)
        if not evs:
            return 0.0
        best_per_host = {}
        for e in evs:
            host = domain_of(e.url) or e.source_type
            if e.weight > best_per_host.get(host, 0):
                best_per_host[host] = e.weight
        product = 1.0
        for w in best_per_host.values():
            product *= (1.0 - w)
        return round(1.0 - product, 3)

    @property
    def claims(self):
        return sorted({e.claim for e in self.evidence})

    @property
    def corroboration(self):
        """Distinct hosts backing any claim. The core anti-hallucination signal."""
        return len({domain_of(e.url) for e in self.evidence if e.url})

    # ---------------------------------------------------------------- merging

    def merge(self, other):
        """Fold a duplicate in. Longer non-empty values win; evidence unions."""
        if len(other.company) > len(self.company):
            self.company = other.company
        for f in ("contact", "company_data"):
            mine, theirs = getattr(self, f), getattr(other, f)
            for k, v in theirs.items():
                if v and not mine.get(k):
                    mine[k] = v
        self.signals = sorted(set(self.signals) | set(other.signals))
        self.sources = sorted(set(self.sources) | set(other.sources))
        for e in other.evidence:
            self.add_evidence(e)
        return self

    # ---------------------------------------------------------------- io

    def to_dict(self):
        return {
            "company": self.company,
            "domain": self.domain,
            "contact": self.contact,
            "company_data": self.company_data,
            "signals": self.signals,
            "sources": self.sources,
            "evidence": [e.to_dict() for e in self.evidence],
            "claims": {c: self.confidence_for(c) for c in self.claims},
            "corroboration": self.corroboration,
            "scores": self.scores,
            "verification": self.verification,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d):
        lead = cls(company=d.get("company", ""), domain=d.get("domain", ""),
                   contact=d.get("contact", {}) or {},
                   company_data=d.get("company_data", {}) or {},
                   signals=d.get("signals", []) or [],
                   sources=d.get("sources", []) or [])
        for e in d.get("evidence", []) or []:
            lead.evidence.append(Evidence(
                claim=e.get("claim", ""), url=e.get("url", ""),
                source_type=e.get("source_type", "search_result"),
                excerpt=e.get("excerpt", ""),
                retrieved_at=e.get("retrieved_at", now())))
        lead.scores = d.get("scores", {}) or {}
        lead.verification = d.get("verification", {}) or {}
        lead.status = d.get("status", "discovered")
        return lead

    def __repr__(self):
        return f"<Lead {self.company or '?'} {self.domain} ev={len(self.evidence)}>"


def save(leads, path):
    json.dump([l.to_dict() for l in leads], open(path, "w"), indent=1)


def load(path):
    return [Lead.from_dict(d) for d in json.load(open(path))]
