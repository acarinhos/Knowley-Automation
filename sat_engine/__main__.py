"""python -m sat_engine --section rw|math|both --seed N --limit N --save"""
import argparse
import logging

from .pipeline import run_session
from .pipeline.session import OUTPUT_DIR
from .progress import Progress


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sat_engine", description=__doc__)
    ap.add_argument("--section", choices=["rw", "math", "both"], default="both")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--limit", type=int, help="max slots per section")
    ap.add_argument("--workers", type=int, default=10, help="concurrent slots")
    ap.add_argument("--save", action="store_true", help="write to Firestore (also needs KNOWLEY_GENERATION_WRITES_ENABLED)")
    a = ap.parse_args(argv)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)  # full log (incl. SDK/HTTP retries) goes to a file, console shows progress only
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[logging.FileHandler(OUTPUT_DIR / "session.log", encoding="utf-8")])
    prog = Progress(OUTPUT_DIR / "progress.json", echo=True, heartbeat=30)
    r = run_session(("rw", "math") if a.section == "both" else (a.section,), a.seed, a.limit, dry_run=not a.save,
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
