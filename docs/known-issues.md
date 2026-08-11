# Known issues and design decisions

Everything here was found by running the system, not by reading documentation. Each
entry says what happened, why, and what the code does about it.

---

## Contact discovery is the slow stage

**Observed.** A run over 8 qualified companies spent more than 25 minutes in
`contacts`, while discovery over 12 parallel queries finished in about a minute.

**Cause.** `FIRECRAWL_EXTRACT` is given 6 candidate URLs per company (`/`, `/about`,
`/about-us`, `/team`, `/our-team`, `/leadership`). Most companies do not have most of
those paths, so the extractor spends its time on 404s, and the call carries a 300
second timeout. Six workers over eight companies is only two batches, so one slow
company holds up an entire batch.

**Mitigations available**, in order of payoff:

1. Cut `PAGES` in `contacts.py` to the three that actually pay: `/`, `/about`, `/team`.
2. Drop the extract timeout from 300s to 120s. A team page that has not responded in
   two minutes is not going to.
3. Raise `--workers` above the default 6. The work is IO-bound, so 12 to 16 is safe.
4. Discover the real team-page URL first with a cheap scrape, then extract only that.

This is the single biggest throughput constraint in the pipeline. Everything else
finishes in seconds.

---

## Composio does not cover the research infrastructure

**Checked 11 Aug 2026.**

- `browserbase_tool` shows ACTIVE in `composio connections list` but exposes **no
  usable tool slugs**. Searching its toolkit returns nothing callable.
- `linkedin` exposes only `LINKEDIN_GET_AUDIENCE_COUNTS` and
  `LINKEDIN_SEARCH_AD_TARGETING_ENTITIES`. Both are advertising tools. **There is no
  people search and no company search.**

**Consequence.** Browserbase is called through the Stagehand SDK directly, and any
LinkedIn data has to come through the browser layer. Composio is the auth layer for
*business applications* (Sheets, CRM, verification APIs), which is what it is good at.
Forcing crawlers and browsers to pretend they are OAuth apps would add a hop and a
failure mode without adding a capability.

---

## Stagehand 4.0.0 does not match its own documentation

The installed package exposes a **flat client API**:

```
act  browser  close  create  experimental_batch  extract  initialized  metrics  observe
```

and a keyword-only constructor:

```python
Stagehand.__init__(self, *, _token=None, browser=None, create_config=None)
```

Both published sources are wrong for this version. The Python README shows
`Stagehand(server="remote", ...)` with nested `client.sessions.act(...)`, which is the
v3 shape. The Browserbase onboarding doc shows `Stagehand(env="BROWSERBASE")`, which is
neither. `stagehand.__version__` is not set, so `pip show stagehand` is the only
reliable version check.

Leave `MODEL_API_KEY` unset. On the free plan the Browserbase key routes model calls
through Model Gateway. Setting a model key bypasses that and bills elsewhere. Do not
set `BROWSERBASE_PROJECT_ID`; it is not needed.

---

## Environment keys are invisible to unattended runs

`BROWSERBASE_API_KEY` is exported in `~/.zshrc`, which is **not sourced by
non-interactive shells**. Confirmed: the key is present in an interactive terminal and
absent from a subprocess, cron job or CI step. That is precisely when unattended runs
happen, so the key would go missing exactly when nobody is watching.

`connectors.secret()` therefore reads the environment, then `config/secrets.json`,
then parses `export` lines out of `~/.zshrc`. This is why preflight reports
`browserbase ok (key found via ~/.zshrc fallback)`.

---

## Listicles rank for the queries that find real companies

**Observed.** A live run qualified "List of Recently Funded Startups"
(`fundraiseinsider.com`) and "100 Top Consumer Companies in United States"
(`f6s.com`) at priority 47. Both are directory pages. Neither is a prospect.

They score well because their page text is dense with exactly the ICP vocabulary the
query used, so keyword-driven fit scoring rewards them.

**Fix.** `is_listicle()` in `discover.py` matches by *shape* rather than by host:
leading ordinals, "top/best/list of/guide to", "companies in", "X vs Y",
"alternatives", and titles with four or more commas. A host blocklist was rejected
because there is always another listicle farm.

The check is deliberately **not** applied to ATS board URLs, where the slug is an
employer by construction and a job title can legitimately read like a listicle.

---

## Hiring signals are not buyer titles

**Bug, fixed.** The hiring path originally searched ATS boards for
`target_titles` ("Head of Content"), which finds companies recruiting *marketing
leaders*. That is a different market from the intended one.

The buying signal is the company hiring the role the client's service supplies (a
video editor). The buyer is the person contacted about it (Head of Content). The
hiring path now derives roles from `buying_signals` and depluralises them, while
`target_titles` is used only in `contacts.py` and persona scoring.

---

## A small --limit used to starve every path but the first

`plan()` built all hiring queries, then all signal queries, and so on, then sliced to
the cap. With `--limit 20`, all 20 went to hiring and the other four paths never ran,
which silently removed the corroboration the whole design depends on.

Queries are now assembled per path and selected **round-robin**, so a small cap
narrows every path evenly. Hiring goes first in each cycle because it carries the
strongest intent.

---

## Vendors return confident, well-formed, wrong answers

Recorded because they justify the verification design:

- A provider returned `mp@xxx.com` as a real address.
- `manutd.com` resolved to a company literally named **"FDC Fake Company"**, a test record.
- `experian.com` resolved to a 290-person US HR subsidiary.
- `formula1.com` resolved to an automation machinery firm in Dudley.

Every one passes a syntax check and a single API call, which is why a single API call
is not sufficient and why `FAKE_TOKENS` is an explicit list.

---

## Clay and Apollo are deliberately not wired in

**Clay.** `contactFilters` is not in the `search-contacts` schema and is silently
ignored. `dslQuery` with `job_title` returns "Unknown field for entity 'people'", and
`title` gives the same. Unfiltered, one call returned four VCs, two recruiters, a
professor and a celebrity. Its enrichment (`add-contact-data-points`) does work once
you already hold entity IDs; the **search** is what is broken. Its MCP connection also
needs authorization, which cannot be completed non-interactively.

**Apollo.** 92,881 correctly-filtered ICP contacts sit behind a bulk-select paywall
("You've reached your record selection limit"). Its Composio connection is EXPIRED.

Both are worth adding once the blockers clear. Neither is trusted today.

---

## Corroboration needs volume

A 12-query run produced 36 unique companies and **zero** corroborated by two or more
sources. Corroboration is the core anti-hallucination signal, and it only appears once
enough paths overlap. Below roughly 40 queries the signal is mostly absent, so
`--limit` defaults to 60.

---

## Cold email volume is capped by the inbox, not this tool

Unrelated to lead quality, but it constrains what a lead list is worth: the safe
ceiling is 20 to 50 cold emails **per inbox per day**, never per domain. Microsoft
flags senders whose volume more than triples inside 48 hours. Unwarmed domains reach
the inbox 40 to 70% of the time against 90 to 95% warmed. Generating 500 verified
leads does not mean 500 emails can be sent this week.
