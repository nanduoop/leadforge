# LeadForge: full session handover

Written 11 August 2026. Everything here was verified by running it against live APIs,
not inferred from documentation. Where something is unproven it says so.

Project: `/Users/madiwal/Documents/leadforge`
Prior project (superseded, still on disk): `/Users/madiwal/Documents/fightclub-outreach-engine`

---

## 1. What this is

A **universal lead generation system**. The user describes their business in any form
(website, ICP doc, pitch, or one sentence), and the system derives the search strategy,
finds companies across several independent paths, proves each lead is real, scores it
on six dimensions, and exports to Google Sheets or CSV with evidence attached.

It is not industry-specific. The same engine that finds companies hiring video editors
finds luxury hotels planning renovations, because the search plan is derived from the
brief rather than hardcoded.

**Two rules are frozen in the design:**

1. **Nothing is trusted because a model said it.** Every claim carries evidence: URL,
   source type, timestamp, excerpt. Confidence is computed from how many *independent*
   sources agree, weighted by how hard each source type is to fake.
2. **Discovery never verifies its own claims.** The component that finds a lead does
   not rule on whether it is real. Verification reads the record fresh.

---

## 2. Architecture (frozen)

The user corrected an early mistake of mine: I was treating Composio as the single
connector for everything. It is the **application auth layer only**. Browserbase,
Firecrawl and Agent Reach are research infrastructure and are called directly.

| Layer | Tool | Role |
|---|---|---|
| App auth | **Composio** | Sheets, CRM, verification APIs. One link per app, no key on disk |
| Web extraction | **Firecrawl** | Search, scrape, structured extract |
| Interactive browser | **Browserbase / Stagehand** | JS-heavy pages, real logins |
| Specialist access | **Agent Reach** | Semantic and social discovery |
| B2B records | **Clay** | Not wired in, see section 6 |

Tool choice is **deterministic** (`router.py`). The model decides *what information is
needed*; the router decides *which infrastructure fetches it*. Same task plus same
availability always yields the same route, so failures reproduce.

Every source returns an explicit status: `success`, `partial`, `blocked`, `not_found`,
`rate_limited`, `invalid`, `error`. A failure never silently becomes an empty list.

---

## 3. Pipeline

```
intake -> discover -> dedup -> qualify -> contacts -> verify -> score -> export
```

Each stage writes to disk and emits an event to `data/job.json`. Stages are
independently retryable: `./run.sh --resume`, `--from verify`, `--status`.

| File | Purpose | Status |
|---|---|---|
| `src/schema.py` | Canonical `Lead` + `Evidence` model | tested |
| `src/connectors.py` | All outside calls, secret loading | tested live |
| `src/router.py` | Deterministic source routing | tested |
| `src/intake.py` | Any input -> structured brief | written, not run end to end |
| `src/discover.py` | 5 parallel paths | tested live |
| `src/dedup.py` | Identity resolution, union-find | tested |
| `src/qualify.py` | Cheap ICP gate | tested live |
| `src/contacts.py` | Decision-maker discovery | **BROKEN, see section 5** |
| `src/verify.py` | Six independent checks | unit tested, not run on real leads |
| `src/score.py` | Six-dimension scoring | tested |
| `src/output.py` | Sheets / CSV / HubSpot | written, **never executed** |
| `src/run.py` | Event-driven orchestrator | tested live |
| `src/preflight.py` | Dependency checks | tested live |

Supporting: `setup.sh`, `run.sh`, `README.md`, `docs/known-issues.md`,
`config/secrets.example.json`, `.gitignore`.

Env: `.venv` (Python 3.12.13), deps `stagehand` 4.0.0 + `dnspython`. Not a git repo
with commits yet — `git init` was run, nothing committed.

---

## 4. Verified findings (all empirical)

**Composio connections, live:** ACTIVE = browserbase_tool, firecrawl, google_maps,
googledrive, googlesheets, hubspot(x2), instagram, linkedin, notion, reddit, youtube.
EXPIRED = apollo(x2), veo, whatsapp. Clay + Miro need OAuth, cannot be done
non-interactively.

**Composio does NOT cover research infra:**
- `browserbase_tool` is ACTIVE but exposes **no usable tool slugs**.
- `linkedin` exposes only `LINKEDIN_GET_AUDIENCE_COUNTS` and
  `LINKEDIN_SEARCH_AD_TARGETING_ENTITIES` — advertising only, **no people search**.
This is why the architecture split above is correct rather than merely tidy.

**Stagehand 4.0.0 contradicts both its own docs.** Flat client API
(`act/extract/observe/create/close`), keyword-only constructor
`(*, _token, browser, create_config)`. The README's `client.sessions.*` is v3; the
onboarding doc's `env="BROWSERBASE"` is neither. `__version__` is unset; use
`pip show stagehand`. Leave `MODEL_API_KEY` unset (Model Gateway); do not set
`BROWSERBASE_PROJECT_ID`.

**Env keys are invisible to unattended runs.** `BROWSERBASE_API_KEY` is in `~/.zshrc`,
which non-interactive shells do not source — confirmed absent from a subprocess.
`connectors.secret()` falls back to parsing `~/.zshrc`. This would have broken the
first cron run silently.

**Firecrawl costs, measured:**
- `FIRECRAWL_SEARCH` = **2 credits**
- `FIRECRAWL_EXTRACT` = **21 credits for one company**, and is **DEPRECATED**
  ("/v1/extract/:jobId is deprecated. Use /v2/scrape with formats including a 'json'
  format object")
- `FIRECRAWL_SCRAPE` supports `formats:["json"]` + `jsonOptions{prompt,schema}` — the
  supported, cheaper replacement. **This migration was in progress when the session ended.**

---

## 5. Live run results and the open bug

Test brief: Fight Club ICP (video editors / motion designers, US+UK, consumer brands /
media / SaaS / agencies).

```
discover  12 queries -> 46 raw hits (7 success, 5 not_found)
dedup     46 -> 36 unique
qualify   36 -> 8 qualified
contacts  8 companies -> 0 contacts     <-- BROKEN
```

Real companies correctly found and qualified: **Wattpad, ClickUp, Intro, Snowball,
Superpower** — all genuinely hiring video editors on public ATS boards.

### The open bug

`contacts.py` returns **zero contacts**. Root cause identified but **not yet fixed**:
it calls the deprecated `FIRECRAWL_EXTRACT`, which costs 21 credits per company and
returned `{"people": []}` even for a valid page.

**The fix, ready to apply:** switch `connectors.extract()` from `FIRECRAWL_EXTRACT` to
`FIRECRAWL_SCRAPE` with:
```json
{"url": "...", "formats": ["json"],
 "jsonOptions": {"prompt": "...", "schema": {...}}}
```
Note `SCRAPE` takes a **single url**, not a list, so `contacts.find_for()` must loop
over its 3 candidate pages (or scrape `/` first, find the real team-page link, then
scrape that one). Also set `ignoreInvalidURLs` where the array form is kept.

Until this is fixed the pipeline produces qualified **companies** but no **people**,
so `verify`, `score` and `export` have never run on real data.

### Fixes already applied this session

- **Contacts stage was 25+ min for 8 companies.** Cut `PAGES` from 6 to 3, extract
  timeout 300s -> 120s. Now completes in seconds (but returns nothing, see above).
- **`--limit` starved all paths but the first.** `plan()` now assembles per path and
  selects round-robin, so a small cap narrows every path evenly.
- **Hiring path searched buyer titles.** It searched "Head of Content" on ATS boards,
  which finds companies recruiting marketing leaders — the wrong market. Now derives
  roles from `buying_signals` and depluralises ("video editors" -> "video editor").
  `target_titles` is used only for contacts and persona scoring.
- **Listicles qualified as companies.** "List of Recently Funded Startups"
  (fundraiseinsider.com) and "100 Top Consumer Companies" (f6s.com) scored 47.
  `is_listicle()` now matches by *shape* (leading ordinals, top/best/list of,
  "X vs Y", 4+ commas), not by host blocklist. Tested: rejects 8/8 listicles, keeps
  6/6 real companies. Deliberately not applied to ATS URLs.

### Verified working (unit tested)

- Evidence math: 3 independent sources -> 0.997; aggregator alone -> 0.30; **5 URLs
  from the same host stays 0.30** (syndication cannot fake corroboration).
- Verification: catches `mp@xxx.com`, `info@acme.com`, disposable domains; live MX
  and domain-liveness checks correct; pattern matching correct.
- Dedup: transitive union-find merges Acme Inc./Acme/Acme Corporation via
  domain + LinkedIn while keeping Globex separate.
- Scoring: strong lead 84, weak 22, excluded exactly 0.

---

## 6. Deliberately not wired in

**Clay.** `contactFilters` is not in the `search-contacts` schema and is silently
ignored. `dslQuery` with `job_title` returns "Unknown field for entity 'people'";
`title` gives the same. Unfiltered, one call returned four VCs, two recruiters, a
professor and a celebrity. Its *enrichment* (`add-contact-data-points`) does work once
you hold entity IDs — the **search** is what is broken. MCP also needs OAuth.

**Apollo.** 92,881 correctly-filtered ICP contacts behind a bulk-select paywall.
Composio connection EXPIRED. ~$49/month is the most direct unlock.

---

## 7. Next steps, in order

1. **Fix `contacts.py`** — migrate to `FIRECRAWL_SCRAPE` + `formats:["json"]`. This is
   the only thing between the current state and a working end-to-end run.
2. Run the full pipeline and confirm `verify`, `score`, `export` on real data. None of
   the three has executed against real leads yet.
3. `composio link neverbounce` — verification currently runs free checks only, which
   caps confidence.
4. Re-authorize Clay if its DSL field name for `people` can be found.
5. Corroboration needs volume: a 12-query run produced **zero** leads corroborated by
   2+ sources. Below ~40 queries the core anti-hallucination signal is mostly absent.
   `--limit` defaults to 60 for this reason.

---

## 8. Constraints that carry forward

- Cold email ceiling is **20-50 per inbox per day**, per inbox and never per domain.
  Microsoft flags volume tripling inside 48h. Unwarmed domains reach the inbox 40-70%
  vs 90-95% warmed. 500 verified leads does not mean 500 sends this week.
- No credential is ever written into the repo. `config/secrets.json`, `config/brief.json`
  and `data/` are gitignored. App auth happens through Composio in the user's browser.
- Voice spec for any outreach copy: `~/AI flow automation/content-engine/style/human-voice.md`.
  No em dashes, no "not X but Y", no three-item lists, no stock AI vocabulary.

---

## 9. Next session direction

The user's stated next step is a **multi-agent system**. Nothing has been built for it
yet. The natural mapping onto the existing pipeline, using the user's own frozen
architecture:

```
Context Agent    -> intake.py        understand messy input, ask only what matters
Search Planner   -> discover.plan()  ICP -> search hypotheses
Source Orchestr. -> router.py        deterministic tool selection (already built)
Discovery Agents -> discover.py      parallel, one per path
Verification     -> verify.py        MUST stay a separate agent from discovery
Scoring          -> score.py         six dimensions
Export           -> output.py
```

The invariant to preserve: **the agent that makes a claim must never be the agent that
verifies it.** That separation is already enforced structurally in the code and should
survive the move to agents.

Not yet built: search-strategy memory (learning which signals produce qualified leads
across runs), and the job controller's pause/retry UI.
