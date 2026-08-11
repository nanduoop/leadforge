#!/usr/bin/env python3
"""
Identity resolution, run before anything expensive.

Six discovery paths return the same company under six names. "Acme Inc.",
"acme.com", "Acme" from a job board and "linkedin.com/company/acme" are one
organisation, and enriching them separately pays four times for one answer.

So dedup happens immediately after discovery and before enrichment or verification.
That ordering is the whole point: it is a cost-control stage, not a tidiness stage.

Identity, in order of trust:
  company   domain, then LinkedIn company URL, then normalised name
  contact   email, then LinkedIn profile URL, then name + company domain

Merging unions the evidence rather than discarding it, so a company found by four
paths ends up with four sources' worth of corroboration on one record. Deduplication
strengthens the evidence base instead of shrinking it.
"""
import sys, os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import Lead, norm_name, domain_of


def company_keys(lead):
    """Every identifier this lead can be matched on."""
    keys = []
    if lead.domain:
        keys.append(("domain", lead.domain))
    li = (lead.company_data.get("linkedin") or "").strip().lower().rstrip("/")
    if li and "linkedin.com/company/" in li:
        keys.append(("li", li.split("linkedin.com/company/")[-1].split("/")[0]))
    n = norm_name(lead.company)
    if n and len(n) > 2:
        keys.append(("name", n))
    return keys


def dedup_companies(leads):
    """
    Union-find over shared identifiers.

    Transitive by design: if A shares a domain with B, and B shares a LinkedIn URL
    with C, then A, B and C are one company even though A and C share nothing
    directly. A simple group-by-domain misses exactly that case, which is the common
    one when different sources each know a different identifier.
    """
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # A name alone is weak evidence, so it only merges when nothing stronger exists.
    strong_owner = {}
    for i, lead in enumerate(leads):
        node = f"lead:{i}"
        find(node)
        for kind, val in company_keys(lead):
            if kind == "name":
                continue
            key = f"{kind}:{val}"
            union(node, key)
            strong_owner.setdefault(key, node)

    by_name = defaultdict(list)
    for i, lead in enumerate(leads):
        if not lead.domain:                        # only merge on name when domainless
            n = norm_name(lead.company)
            if n and len(n) > 2:
                by_name[n].append(f"lead:{i}")
    for nodes in by_name.values():
        for n in nodes[1:]:
            union(nodes[0], n)

    groups = defaultdict(list)
    for i, lead in enumerate(leads):
        groups[find(f"lead:{i}")].append(lead)

    merged = []
    for group in groups.values():
        # Keep the record with the most evidence as the base; it has the best data.
        group.sort(key=lambda l: (len(l.evidence), bool(l.domain), len(l.company)),
                   reverse=True)
        base = group[0]
        for other in group[1:]:
            base.merge(other)
        merged.append(base)
    return merged


def dedup_contacts(leads):
    """
    Collapse duplicate people.

    Runs across the whole set rather than per company, because the same person can
    arrive attached to two different company records before those get merged.
    """
    seen, out = {}, []
    for lead in leads:
        key = lead.contact_key
        if not key:
            out.append(lead)
            continue
        if key in seen:
            seen[key].merge(lead)
        else:
            seen[key] = lead
            out.append(lead)
    return out


def report(before, after, label):
    removed = before - after
    pct = (100 * removed / before) if before else 0
    print(f"  {label:22} {before:5} -> {after:5}  ({removed} merged, {pct:.0f}%)")


def run(leads, verbose=True):
    """Full pass. Returns deduplicated leads."""
    if verbose:
        print("\ndeduplicating before enrichment")
    n0 = len(leads)
    leads = dedup_companies(leads)
    if verbose:
        report(n0, len(leads), "company identity")
    n1 = len(leads)
    leads = dedup_contacts(leads)
    if verbose:
        report(n1, len(leads), "contact identity")

    if verbose:
        multi = sum(1 for l in leads if l.corroboration > 1)
        print(f"  {multi} lead(s) now backed by more than one source")
    return leads


if __name__ == "__main__":
    from schema import Evidence
    a = Lead("Acme Inc.", "acme.com")
    a.add_evidence(Evidence("hiring", "https://acme.com/careers", "company_careers"))
    b = Lead("Acme", "www.acme.com/jobs")
    b.add_evidence(Evidence("hiring", "https://boards.greenhouse.io/acme/1", "ats_board"))
    c = Lead("Acme Corporation", "")
    c.company_data["linkedin"] = "https://linkedin.com/company/acme"
    d = Lead("Globex", "globex.com")

    # links to a via LinkedIn only, proving transitive merging
    a.company_data["linkedin"] = "https://linkedin.com/company/acme"

    out = run([a, b, c, d])
    print("\nresult:")
    for l in out:
        print(f"  {l.company:20} {l.domain:14} evidence={len(l.evidence)} "
              f"corroboration={l.corroboration}")
