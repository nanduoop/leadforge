#!/usr/bin/env python3
"""
The job controller: runs the pipeline as resumable stages, not one long loop.

    python3 src/run.py --site acme.com                 # whole pipeline
    python3 src/run.py --resume                        # continue where it stopped
    python3 src/run.py --from verify                   # rerun one stage onward
    python3 src/run.py --status                        # what happened last time

A single agent loop that fails at minute forty loses forty minutes of work and the
credits that paid for it. So each stage is separate, writes its output to disk, and
emits an event. If verification fails, verification retries. Discovery does not run
again, because its results are already on disk and nothing about them has changed.

Stage order is fixed and each depends only on the one before it:

    intake -> discover -> dedup -> qualify -> contacts -> verify -> score -> export

State lives in data/job.json, so a resume works across processes, terminals and days.
"""
import json, os, sys, argparse, traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connectors as C
from schema import Lead, save, load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
JOB = os.path.join(DATA, "job.json")

STAGES = ["intake", "discover", "dedup", "qualify", "contacts", "verify", "score", "export"]

# Which file each stage produces. Presence of the file plus a completed event is what
# makes a resume safe.
ARTIFACT = {
    "intake":   os.path.join(ROOT, "config", "brief.json"),
    "discover": os.path.join(DATA, "discovered.json"),
    "dedup":    os.path.join(DATA, "deduped.json"),
    "qualify":  os.path.join(DATA, "qualified.json"),
    "contacts": os.path.join(DATA, "contacts.json"),
    "verify":   os.path.join(DATA, "verified.json"),
    "score":    os.path.join(DATA, "scored.json"),
    "export":   os.path.join(DATA, "export.json"),
}


def now():
    return datetime.now(timezone.utc).isoformat()


class Job:
    """Job state and its event log. Every transition is recorded."""

    def __init__(self, path=JOB):
        self.path = path
        self.state = {"created_at": now(), "events": [], "stages": {}, "config": {}}
        if os.path.exists(path):
            try:
                self.state = json.load(open(path))
            except Exception:
                pass

    def emit(self, event, **detail):
        self.state["events"].append({"event": event, "at": now(), **detail})
        self.save()

    def reset_stages(self, names):
        """Clear stage status so a new run does not inherit stale completions."""
        for name in names:
            self.state["stages"][name] = {"status": "pending"}
        self.save()

    def start(self, stage):
        if stage in STAGES:
            idx = STAGES.index(stage)
            for downstream in STAGES[idx + 1:]:
                self.state["stages"][downstream] = {"status": "pending"}
        self.state["stages"][stage] = {"status": "running", "started_at": now()}
        self.emit(f"{stage.upper()}_STARTED")

    def progress(self, stage, current, total, message=None):
        """Update in-stage progress for the UI without flooding the event log."""
        pct = round(100 * current / total) if total else 0
        info = self.state["stages"].get(stage, {})
        info.update({
            "status": "running",
            "progress": {
                "current": current,
                "total": total,
                "percent": pct,
                "message": message or "",
            },
        })
        self.state["stages"][stage] = info
        tick = max(1, total // 25) if total else 1
        if current == total or current == 1 or current % tick == 0:
            self.emit(
                f"{stage.upper()}_PROGRESS",
                current=current,
                total=total,
                message=message or "",
            )
        self.save()

    def status_from_events(self, stage):
        """Derive display status from the event log for the active run.

        The window opens at whichever came last: the start of a run, or a reset.
        Including PIPELINE_RESET is what makes the reset button mean anything —
        it clears `stages`, and this view reads events, so without the reset
        marker every completion from the previous run leaks through forever.
        """
        events = self.state.get("events", [])
        run_start = 0
        for i, e in enumerate(events):
            if e.get("event") in ("LEAD_GEN_STARTED", "PIPELINE_RESET"):
                run_start = i
        status = "pending"
        prefix = stage.upper()
        for e in events[run_start:]:
            ev = e.get("event", "")
            if ev == f"{prefix}_STARTED":
                status = "running"
            elif ev == f"{prefix}_COMPLETED":
                status = "completed"
            elif ev == f"{prefix}_FAILED":
                status = "failed"
        return status

    def done(self, stage, **stats):
        self.state["stages"][stage] = {
            "status": "completed", "finished_at": now(), **stats}
        self.emit(f"{stage.upper()}_COMPLETED", **stats)

    def fail(self, stage, error):
        self.state["stages"][stage] = {
            "status": "failed", "failed_at": now(), "error": str(error)[:500]}
        self.emit(f"{stage.upper()}_FAILED", error=str(error)[:200])

    def status_of(self, stage):
        return self.state["stages"].get(stage, {}).get("status", "pending")

    def completed(self, stage):
        """Completed means the event fired AND the artifact is still on disk."""
        return (self.status_of(stage) == "completed"
                and os.path.exists(ARTIFACT.get(stage, "")))

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        json.dump(self.state, open(self.path, "w"), indent=1)


# --------------------------------------------------------------------------- stages

def stage_intake(job, args):
    if os.path.exists(ARTIFACT["intake"]) and not args.reintake:
        brief = json.load(open(ARTIFACT["intake"]))
        return {"reused": True, "gaps": len(brief.get("meta", {}).get("gaps", []))}

    import intake
    argv = sys.argv
    sys.argv = ["intake"]
    if args.site:
        sys.argv += ["--site", args.site]
    if args.text:
        sys.argv += ["--text", args.text]
    if args.file:
        sys.argv += ["--file", args.file]
    if args.answers:
        sys.argv += ["--answers", args.answers]
    if args.reintake:
        sys.argv += ["--reintake"]
    if args.yes:
        sys.argv += ["--yes"]
    try:
        intake.main()
    except SystemExit as e:
        if e.code:
            raise RuntimeError(f"intake exited: {e.code}")
    finally:
        sys.argv = argv
    brief = json.load(open(ARTIFACT["intake"]))
    return {"gaps": len(brief.get("meta", {}).get("gaps", []))}


def stage_discover(job, args):
    import discover
    brief = json.load(open(ARTIFACT["intake"]))
    queries = discover.plan(brief, cap=args.limit,
                            paths=[p for p in args.paths.split(",") if p] or None)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    leads, statuses = [], {}
    total = len(queries)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(discover.execute_path, p, q, c, args.per_query): p
                for p, q, c in queries}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                got, res = fut.result()
                leads.extend(got)
                statuses[res.status] = statuses.get(res.status, 0) + 1
            except Exception:
                statuses["error"] = statuses.get("error", 0) + 1
            job.progress(
                "discover", i, total,
                message=f"{len(leads)} companies found across {i} queries",
            )

    save(leads, ARTIFACT["discover"])
    return {"queries": len(queries), "raw_hits": len(leads), "statuses": statuses}


def stage_dedup(job, args):
    import dedup
    leads = load(ARTIFACT["discover"])
    before = len(leads)
    leads = dedup.run(leads, verbose=False)
    save(leads, ARTIFACT["dedup"])
    return {"before": before, "after": len(leads),
            "corroborated": sum(1 for l in leads if l.corroboration > 1)}


def stage_qualify(job, args):
    """
    Cheap ICP filter before anything expensive.

    Scoring runs twice on purpose. Here it is a coarse gate on fit and intent so that
    contact discovery and verification only ever touch plausible companies. The full
    six-dimension score runs later, once evidence and verification exist.
    """
    from score import score_lead
    icp = json.load(open(ARTIFACT["intake"]))["icp"]
    leads = load(ARTIFACT["dedup"])
    for l in leads:
        score_lead(l, icp)

    kept = [l for l in leads
            if l.scores["fit"] >= args.min_fit or l.scores["intent"] >= 40]
    kept.sort(key=lambda l: l.scores["priority"], reverse=True)
    kept = kept[:args.max_companies]
    save(kept, ARTIFACT["qualify"])
    return {"before": len(leads), "after": len(kept)}


def stage_contacts(job, args):
    import contacts
    icp = json.load(open(ARTIFACT["intake"]))["icp"]
    leads = load(ARTIFACT["qualify"])
    def on_progress(current, total, found):
        job.progress(
            "contacts", current, total,
            message=f"{found} contacts from {current} of {total} companies",
        )

    out = contacts.enrich(
        leads, icp, use_browser=args.browser, workers=args.workers,
        on_progress=on_progress,
    )
    save(out, ARTIFACT["contacts"])
    with_email = sum(1 for l in out if (l.contact or {}).get("email"))
    return {"companies": len(leads), "contacts": len(out), "with_email": with_email}


def stage_verify(job, args):
    import verify
    leads = load(ARTIFACT["contacts"])
    def on_progress(current, total, passed):
        job.progress(
            "verify", current, total,
            message=f"{passed} passed verification ({current}/{total})",
        )

    out = verify.verify_leads(
        leads, use_paid=not args.no_paid, workers=args.workers,
        on_progress=on_progress,
    )
    save(out, ARTIFACT["verify"])
    passed = sum(1 for l in out
                 if (l.verification or {}).get("confidence", 0) >= args.min_confidence)
    return {"checked": len(out), "passed": passed}


def stage_score(job, args):
    from score import rank
    icp = json.load(open(ARTIFACT["intake"]))["icp"]
    leads = load(ARTIFACT["verify"])
    leads = [l for l in leads
             if (l.verification or {}).get("confidence", 0) >= args.min_confidence]
    leads = rank(leads, icp)
    for l in leads:
        l.status = "qualified"
    save(leads, ARTIFACT["score"])
    top = sum(1 for l in leads if l.scores["priority"] >= 70)
    return {"scored": len(leads), "priority_70_plus": top}


def stage_export(job, args):
    import output
    leads = load(ARTIFACT["score"])
    leads = [l for l in leads if l.scores.get("priority", 0) >= args.min_priority]
    if not leads:
        return {"exported": 0, "note": "nothing met the priority threshold"}

    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    csv_path, dash_path = output.write_local(leads, dest_dir=DATA, title=args.title or f"LeadForge {stamp}")
    result = {
        "exported": len(leads),
        "csv": os.path.relpath(csv_path, ROOT),
        "dashboard": os.path.relpath(dash_path, ROOT),
    }

    if args.to in ("auto", "sheets") and C.is_linked("googlesheets"):
        url, err = output.to_sheets(leads, args.title or f"LeadForge {stamp}")
        result["sheet"] = url or ""
        if err:
            result["sheet_error"] = err
    save(leads, ARTIFACT["export"])
    return result


RUNNERS = {
    "intake": stage_intake, "discover": stage_discover, "dedup": stage_dedup,
    "qualify": stage_qualify, "contacts": stage_contacts, "verify": stage_verify,
    "score": stage_score, "export": stage_export,
}


# ---------------------------------------------------------------------------- driver

def plan_report(brief, cap):
    """What a discovery run would cost, without spending anything.

    This is the gate in front of the only stages that bill. An agent has to be
    able to reach it in one command, or it will skip the estimate and run.
    """
    import discover
    plan = discover.plan(brief, cap=cap)
    by_path = {}
    for path_name, _query, _cap in plan:
        by_path[path_name] = by_path.get(path_name, 0) + 1
    return {
        "total_queries": len(plan),
        "estimated_credits": len(plan) * 2,
        "by_path": by_path,
        "sample": [{"path": p, "query": q} for p, q, _ in plan[:8]],
    }


def status_report(job):
    """Pipeline state as data. Agents drive this repo; give them something to parse."""
    stages = []
    for s in STAGES:
        info = job.state["stages"].get(s, {})
        artifact = ARTIFACT.get(s, "")
        stages.append({
            "stage": s,
            "status": job.status_from_events(s),
            "artifact": os.path.relpath(artifact, ROOT) if artifact else None,
            "artifact_exists": os.path.exists(artifact) if artifact else False,
            "stats": {k: v for k, v in info.items()
                      if k not in ("status", "started_at", "finished_at", "failed_at")},
        })
    return {
        "job": os.path.relpath(JOB, ROOT),
        "stages": stages,
        "next_stage": next((s["stage"] for s in stages if s["status"] != "completed"), None),
        "complete": all(s["status"] == "completed" for s in stages),
        "failed": [s["stage"] for s in stages if s["status"] == "failed"],
        "events": job.state.get("events", [])[-10:],
    }


def show_status(job):
    print(f"\njob state  {os.path.relpath(JOB, ROOT)}")
    print("-" * 62)
    for s in STAGES:
        info = job.state["stages"].get(s, {})
        st = info.get("status", "pending")
        mark = {"completed": "ok  ", "failed": "FAIL", "running": "... "}.get(st, "    ")
        extra = {k: v for k, v in info.items()
                 if k not in ("status", "started_at", "finished_at", "failed_at")}
        print(f"  {mark} {s:10} {st:10} "
              f"{json.dumps(extra)[:60] if extra else ''}")
    events = job.state.get("events", [])
    if events:
        print(f"\nlast events:")
        for e in events[-5:]:
            print(f"  {e['at'][11:19]}  {e['event']}")
    print()


def main():
    ap = argparse.ArgumentParser(description="LeadForge pipeline controller")
    ap.add_argument("--site"); ap.add_argument("--text"); ap.add_argument("--file")
    ap.add_argument("--answers",
                    help="JSON object of intake field -> answer, or a path to one")
    ap.add_argument("--yes", action="store_true", help="never prompt during intake")
    ap.add_argument("--resume", action="store_true", help="skip completed stages")
    ap.add_argument("--from", dest="from_stage", choices=STAGES, help="rerun from here")
    ap.add_argument("--only", choices=STAGES, help="run exactly one stage")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan", action="store_true",
                    help="what a run would cost, without spending anything")
    ap.add_argument("--reintake", action="store_true", help="rebuild the brief")
    ap.add_argument("--limit", type=int, default=60, help="max discovery queries")
    ap.add_argument("--per-query", type=int, default=10)
    ap.add_argument("--paths", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-companies", type=int, default=60)
    ap.add_argument("--min-fit", type=int, default=35)
    ap.add_argument("--min-confidence", type=int, default=70)
    ap.add_argument("--min-priority", type=int, default=0)
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--no-paid", action="store_true")
    ap.add_argument("--to", default="auto", choices=["auto", "sheets", "csv"])
    ap.add_argument("--title", default=None)
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output, for agents driving this repo")
    a = ap.parse_args()

    job = Job()
    if a.status:
        if a.json:
            print(json.dumps(status_report(job), indent=2))
        else:
            show_status(job)
        return

    if a.plan:
        brief_path = ARTIFACT["intake"]
        if not os.path.exists(brief_path):
            print("no brief yet. run --only intake first.")
            sys.exit(1)
        report = plan_report(json.load(open(brief_path)), a.limit)
        if a.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"\n{report['total_queries']} search queries "
                  f"(~{report['estimated_credits']} Firecrawl credits)")
            for name, n in report["by_path"].items():
                print(f"  {name:10} {n}")
            print("\nnothing has been spent. add --resume to run.\n")
        return

    if a.only:
        todo = [a.only]
    elif a.from_stage:
        todo = STAGES[STAGES.index(a.from_stage):]
    elif a.resume:
        todo = [s for s in STAGES if not job.completed(s)]
        if not todo:
            print("every stage already completed. use --from <stage> to rerun one.")
            return
    else:
        todo = list(STAGES)

    job.state["config"] = {k: v for k, v in vars(a).items() if v not in (None, False, "")}
    job.emit("LEAD_GEN_STARTED", stages=todo)
    job.reset_stages(todo)

    # Stages outside `todo` are carried over from an earlier run rather than
    # re-run. The UI derives status from events since the last LEAD_GEN_STARTED,
    # so without a marker here they read as "pending" alongside a finished
    # export — the "25% complete / all stages finished" screen. Record the reuse.
    for stage in STAGES:
        if stage not in todo and job.completed(stage):
            job.emit(f"{stage.upper()}_COMPLETED", reused=True)

    print(f"\nLeadForge  |  {len(todo)} stage(s): {' -> '.join(todo)}")
    caps = C.available()
    active = [k for k, v in caps.items() if v and not k.startswith("_")]
    print(f"available: {', '.join(active)}\n")

    for stage in todo:
        if a.resume and job.completed(stage):
            print(f"  skip     {stage} (already done)")
            continue

        print(f"  ------- {stage}")
        job.start(stage)
        try:
            stats = RUNNERS[stage](job, a) or {}
            job.done(stage, **stats)
            summary = ", ".join(f"{k}={v}" for k, v in stats.items()
                                if not isinstance(v, (dict, list)))
            print(f"  done     {stage}  {summary}")
        except Exception as e:
            job.fail(stage, e)
            print(f"\n  FAILED   {stage}: {e}")
            traceback.print_exc(limit=3)
            print(f"\nStages before {stage} are saved. Fix the cause, then:")
            print(f"  python3 src/run.py --from {stage}")
            sys.exit(1)

    job.emit("LEAD_GEN_COMPLETED")
    export = job.state["stages"].get("export", {})
    print(f"\n{'=' * 60}")
    print(f"  done. {export.get('exported', 0)} leads exported")
    if export.get("csv"):
        print(f"  csv    {export['csv']}")
    if export.get("sheet"):
        print(f"  sheet  {export['sheet']}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
