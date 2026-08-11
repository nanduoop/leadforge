# Parallel agent tasks

Split of `docs/00-session-handover.md` §7 into work that can run in separate Conductor
workspaces. Each workspace is its own git worktree, so agents will not collide on files,
but they also will not see each other's work until you merge.

Read `docs/00-session-handover.md` first. It is tracked, so every worktree has it.

## What each workspace starts with

`.worktreeinclude` copies `config/brief.json` and `data/` in, and `scripts.setup` runs
`./setup.sh --install` to build `.venv`. So a new workspace already has the Fight Club
brief and the stage outputs from the last live run, including the 8 qualified companies
in `data/qualified.json`.

That matters for cost. Re-running `discover` is 12 queries at 2 Firecrawl credits each.
Start from the stage you actually need:

```bash
./run.sh --from contacts     # uses the existing data/qualified.json
./run.sh --status            # what has run and what has not
```

**`data/qualified.json` is stale in one specific way.** It was written before the
listicle fix in §5 landed, so two of its eight records are the exact listicles that fix
was written to reject:

```
fundraiseinsider.com    List of Recently Funded Startups in the USA (2026)
f6s.com                 100 Top Consumer Companies in United States · July 2026
```

`is_listicle()` now lives at `discover.py:167` and is applied at `discover.py:201`, so a
fresh run would drop both. Until someone re-runs discovery, expect those two to yield
nothing from any downstream stage. That is the stale data, not a regression. The six real
records are digitalmediamanagement.com, intro.com, wattpad.com, snowball.com, clickup.com
and superpower.com.

---

## Agent 1 — Migrate `contacts.py` off `FIRECRAWL_EXTRACT`

**Start here.** §7 items 2 and 5 both depend on this. Until it lands, the pipeline
produces qualified companies but no people, so `verify`, `score` and `export` have never
run on real data.

`FIRECRAWL_EXTRACT` is deprecated, costs 21 credits per company, and returns
`{"people": []}` even for a valid page. The replacement is `FIRECRAWL_SCRAPE` with
`formats: ["json"]` and `jsonOptions: {prompt, schema}`.

Three edit sites:

- **`src/connectors.py:210`** — `extract()` sends `{"urls": [...], "schema", "prompt"}` to
  `FIRECRAWL_EXTRACT`. Switch to `FIRECRAWL_SCRAPE` with a single `url`, `formats:
  ["json"]`, and `jsonOptions`. `scrape()` at `connectors.py:197` already calls
  `FIRECRAWL_SCRAPE` correctly and shows the response-unwrapping idiom — the `d.get(...)`
  chain with the nested `d["data"]` fallback. Reuse it rather than writing a new one.
- **`src/contacts.py:89`** — `find_for()` builds `urls = [base + p for p in PAGES]` (three
  pages) and sends the list in one `R.Task`. `SCRAPE` takes a single url, so this has to
  loop. Cheapest shape: scrape `/` first, find the real team-page link in it, then scrape
  that one page.
- **`src/router.py:121`** — `_firecrawl_extract` unpacks `payload["urls"]`. It is wired to
  both `page_extract` and `structured_pages` (`router.py:141-142`), so check the other
  caller before changing the payload key.

Already handled — do not re-fix. `contacts.py:111` reads both `item["people"]` and
`item["data"]["people"]`, so either response envelope parses.

**Done when** `.venv/bin/python src/contacts.py` returns a non-zero contact count against
the existing `data/qualified.json`. Judge it on the six real records — the two listicles
noted above have no team page and will correctly yield nothing.

## Agent 2 — Tests for `schema.py`, `dedup.py`, `score.py`

`docs/00-session-handover.md` marks these "tested" and "unit tested", but `git ls-files |
grep -i test` returns nothing. Those checks were run ad hoc and never committed. This is
writing them from scratch, not adding coverage.

§5 "Verified working" records the numbers those ad hoc runs produced. Encode them:

- **Evidence math** (`schema.py`, `SOURCE_WEIGHT` at line 34, `Evidence.weight` at line
  96): 3 independent sources → 0.997; an aggregator alone → 0.30; **5 URLs from the same
  host stays 0.30**. The last one is the anti-syndication property and is the test worth
  having.
- **Dedup** (`dedup.py`, `dedup_companies` at line 41): transitive union-find merges
  Acme Inc. / Acme / Acme Corporation via domain plus LinkedIn, while keeping Globex
  separate.
- **Scoring** (`score.py`, `score_lead` at line 231, `WEIGHTS` at line 28): strong lead 84,
  weak 22, excluded exactly 0.

`pytest` is not installed — `setup.sh:59` installs only `stagehand` and `dnspython`. Add it
there. No other agent touches that line.

## Agent 3 — needs a decision before launching

The original third slot was "search-strategy memory, section 23 of your spec". There is no
section 23. `docs/00-session-handover.md` has nine sections, and the only description of
this feature is one line in §9: *"search-strategy memory (learning which signals produce
qualified leads across runs)"*. That is not enough to build from.

Options:

1. **Point it at the real spec.** If the document that has a section 23 exists somewhere
   outside this repo, add it under `docs/` first so every worktree gets it.
2. **Leave the slot empty** and run two agents. Agent 1 is the bottleneck for everything
   downstream anyway.

Note on §7 item 3, `composio link neverbounce`: the code is already written.
`verify.py:183` calls `NEVERBOUNCE_SINGLE_CHECK` whenever `C.is_linked("neverbounce")` is
true, and falls back to free checks otherwise. So there is nothing to implement — it is a
browser OAuth step you have to do yourself, then re-run `verify`. Not agent work.

## Merge order

Agent 1 and Agent 2 touch disjoint files (`connectors.py` / `contacts.py` / `router.py`
versus new test files plus one line of `setup.sh`), so either can merge first. If you
revive search-strategy memory, it will likely touch `score.py`, so merge the tests before
it.
