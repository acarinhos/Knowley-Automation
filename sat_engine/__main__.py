"""python -m sat_engine --section rw|math|both --seed N --limit N --save"""
import argparse
import json
import logging
import os
from pathlib import Path

from . import llm
from .pipeline import run_session
from .pipeline.importer import import_questions
from .pipeline.session import OUTPUT_DIR, audit_session, open_slots
from .progress import Progress


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sat_engine", description=__doc__)
    ap.add_argument("--section", choices=["rw", "math", "both"], default="both")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--limit", type=int, help="max slots per section")
    ap.add_argument("--workers", type=int, default=10, help="concurrent slots")
    ap.add_argument("--save", action="store_true", help="write to Firestore (also needs KNOWLEY_GENERATION_WRITES_ENABLED)")
    ap.add_argument("--session", help="session id, e.g. seed101-rw-math (default: from --seed/--section)")
    ap.add_argument("--import", dest="import_path", help="JSON list of externally authored SATQuestion dicts to verify and merge")
    ap.add_argument("--list-open", action="store_true", help="print and write output/open_slots.json, then exit")
    ap.add_argument("--audit", action="store_true", help="offline re-check of the accepted questions in --session (no LLM)")
    ap.add_argument("--drop-failed", action="store_true", help="with --audit: remove failing questions (backup <file>.bak)")
    ap.add_argument("--balance", action="store_true", help="rebalance the accepted questions in --session: key length bias, then key letters")
    ap.add_argument("--dry-run", action="store_true", help="with --balance/--clean: no LLM calls, no writes")
    ap.add_argument("--clean", action="store_true", help="mechanical formatting clean-up of --session (backup <file>.pre_clean.bak)")
    ap.add_argument("--dedupe-topics", action="store_true", help="with --clean: regenerate later items of near-duplicate scenario pairs")
    ap.add_argument("--save-plan", action="store_true", help="print what --save would write to Firestore for --session (read-only, writes nothing)")
    ap.add_argument("--report", action="store_true", help="read-only Firestore report of the SAT collection (counts, breakdowns); add --json for JSON")
    ap.add_argument("--json", action="store_true", help="with --report: print JSON")
    ap.add_argument("--providers", help="comma list, order = preference (gemini,groq,cohere,nvidia,claude); default: all configured")
    ap.add_argument("--models", help="model overrides for this run only, e.g. gemini=gemini-2.5-pro,groq=openai/gpt-oss-20b")
    ap.add_argument("--output-dir", help="directory holding the session files (default: sat_engine/output)")
    a = ap.parse_args(argv)
    providers, models = None, {}
    try:
        if a.providers:
            providers, warns = llm.parse_providers(a.providers)
            for w in warns:
                print("uyarı: " + w, flush=True)
            if len(providers) == 1:
                print(f"bilgi: tek sağlayıcı seçili ({providers[0]}): hakem üreticiyle aynı olacak.", flush=True)
        if a.models:
            models = llm.parse_models(a.models)
    except ValueError as e:
        ap.error(str(e))
    saved = {k: os.environ.get(k) for k in (llm.model_env_name(p) for p in models)}
    try:
        os.environ.update({llm.model_env_name(p): m for p, m in models.items()})  # this process only
        return _dispatch(a, ap, providers)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _dispatch(a, ap, providers) -> int:
    sid = a.session or (f"seed{a.seed}-{'-'.join(('rw', 'math') if a.section == 'both' else (a.section,))}" if a.seed is not None else None)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if a.report:  # read-only: never reaches run_session/save
        from .report import build_report, fetch_docs, format_text
        try:
            r = build_report(fetch_docs())
        except Exception as e:  # noqa: BLE001
            print(f"rapor alınamadı: {type(e).__name__}: {str(e)[:200]}")
            return 1
        print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else format_text(r))
        return 0
    if a.audit:
        if not sid:
            ap.error("--audit needs --session or --seed")
        r = audit_session(sid, Path(a.output_dir) if a.output_dir else OUTPUT_DIR, drop_failed=a.drop_failed)
        print(f"audit {sid}: {r['failed']}/{r['total']} failed {r['by_check']}; dropped {r['dropped']}")
        return 0
    if a.clean:
        if not sid:
            ap.error("--clean needs --session or --seed")
        od = Path(a.output_dir) if a.output_dir else OUTPUT_DIR
        if a.dedupe_topics:
            from .pipeline.dedupe import dedupe_session, format_report as fmt
            logging.basicConfig(level=logging.WARNING, handlers=[logging.FileHandler(od / "clean_debug.log", encoding="utf-8")])
            print(fmt(dedupe_session(sid, od, dry_run=a.dry_run, workers=min(a.workers, 3), providers=providers, progress=lambda m: print(m, flush=True))))
        else:
            from .pipeline.clean import clean_session, format_report as fmt
            print(fmt(clean_session(sid, od, dry_run=a.dry_run)))
        return 0
    if a.save_plan:
        if not sid:
            ap.error("--save-plan needs --session or --seed")
        from .saveplan import save_plan
        print(save_plan(sid, Path(a.output_dir) if a.output_dir else OUTPUT_DIR))
        return 0
    if a.balance:
        if not sid:
            ap.error("--balance needs --session or --seed")
        from .pipeline.balance import balance_session, format_report
        logging.basicConfig(level=logging.WARNING, handlers=[logging.FileHandler(OUTPUT_DIR / "balance_debug.log", encoding="utf-8")])
        print(format_report(balance_session(sid, Path(a.output_dir) if a.output_dir else OUTPUT_DIR, dry_run=a.dry_run, providers=providers, workers=min(a.workers, 3),
                                            progress=lambda m: print(m, flush=True))))
        return 0
    if a.list_open or a.import_path:
        if not sid:
            ap.error("--list-open/--import need --session or --seed")
        if a.import_path:
            r = import_questions(a.import_path, sid, providers=providers)
            print(f"imported {r['accepted']}/{r['input']} into {sid}; open slots left {r['open_left']}")
            for p in r["placed"]:
                loose = f" (LOOSE match: {p['difficulty']} item in {p['slot_difficulty']} slot, difficulty kept)" if p["loose"] else ""
                print(f"  item {p['item']} -> slot {p['slot']}{loose}")
            for x in r["rejected"]:
                print(f"  rejected item {x['item']}: {x['reason'][:200]}")
        slots = open_slots(sid)
        (OUTPUT_DIR / "open_slots.json").write_text(json.dumps(slots, indent=1), encoding="utf-8")
        print(f"{len(slots)} open slots in {sid} -> {OUTPUT_DIR / 'open_slots.json'}")
        if a.list_open:
            for x in slots:
                print(f"  {x['index']:>3} {x['section']} m{x['module']} {x['domain']} | {x['skill']} | {x['difficulty']}"
                      f"{' visual' if x['needs_visual'] else ''}{' grid' if x['answer_type'] == 'grid_in' else ''}")
        return 0  # full log (incl. SDK/HTTP retries) goes to a file, console shows progress only
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[logging.FileHandler(OUTPUT_DIR / "session.log", encoding="utf-8")])
    prog = Progress(OUTPUT_DIR / "progress.json", echo=True, heartbeat=30)
    r = run_session(("rw", "math") if a.section == "both" else (a.section,), a.seed, a.limit, dry_run=not a.save, providers=providers,
                    workers=a.workers, progress=prog)
    for sec, v in r["sections"].items():
        print(f"{sec}: {v['accepted']}/{v['target']} accepted{'' if v['complete'] else ' (INCOMPLETE)'}")
    for f in r["failures"]:
        print(f"failed slot {f['slot']} ({f['skill']}): {f['reason']}")
    for why, n in r.get("rejections", {}).items():
        print(f"rejected x{n}: {why}")
    print(f"session={r['session_id']} store={r['store']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
