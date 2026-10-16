"""Fill simulated community_distribution (+ community_distribution_meta) on ACTIVE 1.1.0 questions.

Dry-run by default (statistics only). --apply needs --project-id knowley-1, backs up the affected
documents (local_backups/, not committed), writes only the two fields (merge-only, update-time
precondition) and records finished ids for --resume. Existing distributions are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.community_distribution import LETTERS, simulate_distribution

BACKUP_DIR = ROOT / "local_backups"
PROGRESS_FILE = BACKUP_DIR / "community_distribution_progress.json"
logger = logging.getLogger("CommunityDistribution")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.apply and args.project_id != "knowley-1":
        parser.error("--apply requires --project-id knowley-1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import firebase_admin
    from google.cloud.firestore_v1._helpers import LastUpdateOption

    from database.db_manager import init_firebase
    from scripts.backfill_v110_translations import _version_documents

    db = init_firebase()
    if args.apply and firebase_admin.get_app().project_id != args.project_id:
        raise SystemExit("Refusing: initialized Firebase project differs from --project-id.")
    done = set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))) if args.resume and PROGRESS_FILE.exists() else set()
    plan, skipped = [], {"inactive": 0, "has_distribution": 0, "no_answer": 0, "done": 0}
    stats = defaultdict(lambda: [0, 0, 0])  # difficulty -> [docs, leader correct, leader pct sum]
    for doc_id, snap in sorted(_version_documents(db).items()):
        data = snap.to_dict() or {}
        if doc_id in done:
            skipped["done"] += 1
        elif data.get("is_active") is False:
            skipped["inactive"] += 1
        elif data.get("community_distribution"):
            skipped["has_distribution"] += 1
        else:
            simulated = simulate_distribution(doc_id, data)
            if simulated is None:
                skipped["no_answer"] += 1
                continue
            distribution, sample = simulated
            row = stats[data.get("difficulty") or "Orta"]
            row[0] += 1
            row[1] += LETTERS[distribution.index(max(distribution))] == data["correct_answer"]
            row[2] += max(distribution)
            plan.append((doc_id, snap, data, distribution, sample))
    if args.limit > 0:
        plan = plan[: args.limit]
    total = sum(r[0] for r in stats.values())
    logger.info("To fill: %d docs; skipped %s", len(plan), skipped)
    for level, (n, ok, psum) in sorted(stats.items()):
        logger.info("  %-6s docs=%d leader=correct %.1f%% avg leader %.1f%%", level, n, 100 * ok / max(n, 1), psum / max(n, 1))
    if total:
        logger.info("  all    docs=%d leader=correct %.1f%% avg leader %.1f%%", total,
                    100 * sum(r[1] for r in stats.values()) / total, sum(r[2] for r in stats.values()) / total)
    if not args.apply:
        logger.info("Dry run only. Nothing was written.")
        return 0

    BACKUP_DIR.mkdir(exist_ok=True)
    backup = BACKUP_DIR / f"community_distribution_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_text(json.dumps({d: {"community_distribution": data.get("community_distribution"),
                                      "community_distribution_meta": data.get("community_distribution_meta")}
                                  for d, _s, data, _x, _y in plan}, ensure_ascii=False), encoding="utf-8")
    logger.info("Backup written: %s", backup)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    states, done_now = {}, set(done)
    for doc_id, snap, _data, distribution, sample in plan:
        try:
            snap.reference.update(
                {"community_distribution": distribution,
                 "community_distribution_meta": {"source": "simulated", "sample_size": sample, "generated_at": now}},
                option=LastUpdateOption(snap.update_time))
            done_now.add(doc_id)
            states[doc_id] = "written"
        except Exception as exc:
            states[doc_id] = f"skipped: {type(exc).__name__}: {str(exc)[:100]}"
            logger.warning("%s -> %s", doc_id, states[doc_id])
    PROGRESS_FILE.write_text(json.dumps(sorted(done_now)), encoding="utf-8")
    logger.info("Result: written %d, skipped %d", sum(v == "written" for v in states.values()), sum(v != "written" for v in states.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
