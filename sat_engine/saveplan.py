"""`--save-plan`: describe what `--save` would write to Firestore, WITHOUT writing. Only read operations are issued
(credential load, one count query, one batched get of the planned doc ids)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import ENV_DIR, FIRESTORE_COLLECTION, get_env, is_truthy
from .pipeline.session import OUTPUT_DIR, read_store
from .schema import SATQuestion
from .store import WRITES_ENV, _check_collection, build_doc, doc_id, get_db

LIMIT = 1024 * 1024  # Firestore document limit (1 MiB)
SUGGESTED = (
    "created_at (top level, server timestamp; today only meta.created_at as ISO string)",
    "source = 'sat_engine' (top level; distinguishes generated docs from hand-authored ones)",
    "status = 'draft' / 'approved' (review gate before the app serves an item)",
    "doc id is also stored as a field (content_hash) so queries can return it without reading the path",
    "visual.kind / meta.provider already exist; consider a top-level `has_visual` boolean for cheap filtering",
)


def _types(v):
    if isinstance(v, dict):
        return {k: _types(x) for k, x in v.items()}
    return type(v).__name__


def _flag(name: str) -> str:
    return "present" if name in os.environ and os.environ[name] != "" else "absent"


def firebase_readonly(ids: list[str]) -> list[str]:
    """Initialise firebase_admin and issue READ queries only. Never prints keys; errors are reported by class and a short message."""
    lines = []
    try:
        db = get_db()
        import firebase_admin
        app = firebase_admin.get_app()
        pid = getattr(app, "project_id", None) or getattr(getattr(app, "credential", None), "project_id", None)
        lines.append(f"firebase_admin: credentials loaded, project_id={pid}")
        col = db.collection(FIRESTORE_COLLECTION)
        try:
            n = int(col.count().get(timeout=30)[0][0].value)
            lines.append(f"existing docs in {FIRESTORE_COLLECTION}: {n}" + ("" if n else " (collection is empty / does not exist yet)"))
        except Exception as e:  # noqa: BLE001
            lines.append(f"count query failed: {type(e).__name__}: {str(e)[:120]}")
        try:
            have = sum(1 for d in db.get_all([col.document(i) for i in ids], timeout=30) if d.exists)
            lines.append(f"planned doc ids already present: {have}/{len(ids)} (those would be skipped by --save)")
        except Exception as e:  # noqa: BLE001
            lines.append(f"id existence check failed: {type(e).__name__}: {str(e)[:120]}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"firebase_admin init failed: {type(e).__name__}: {str(e)[:160]}")
    return lines


def save_plan(sid: str, output_dir: Path = OUTPUT_DIR, firebase=firebase_readonly) -> str:
    store = read_store(Path(output_dir) / f"{sid}.json")
    qs = [SATQuestion.model_validate(store[i]) for i in sorted(store, key=int)]
    ids = [doc_id(q) for q in qs]
    docs = [build_doc(q, sid) for q in qs]
    sizes = [len(json.dumps(d, ensure_ascii=False).encode("utf-8")) for d in docs]
    sample = max(docs, key=lambda d: (sum(v is not None for v in d.values()), len(json.dumps(d))), default={})  # richest doc (visual, passage)
    creds = get_env("FIREBASE_CREDENTIALS_PATH")
    cp = Path(creds).expanduser() if creds else None
    if cp is not None and not cp.is_absolute():
        cp = ENV_DIR / cp
    out = [
        f"save plan for session {sid} (NO writes are performed by this command)",
        f"collection: {_check_collection(FIRESTORE_COLLECTION)}",
        f"documents to write: {len(docs)} (unique ids {len(set(ids))}; existing ids are skipped, writes are idempotent)",
        "doc id scheme: sha256 hex (64 chars) of lowercased/whitespace-normalised stem + '\\n' + passage",
        f"doc size (UTF-8 JSON, SVG inline): max {max(sizes, default=0)} B, avg {round(sum(sizes) / max(1, len(sizes)))} B, "
        f"max = {max(sizes, default=0) / LIMIT:.2%} of the 1 MiB limit",
        f"fields of one sample doc (name: type): {json.dumps(_types(sample), sort_keys=True)}",
        f"env: {WRITES_ENV} {_flag(WRITES_ENV)} (truthy={is_truthy(get_env(WRITES_ENV))}); a write needs --save AND this flag truthy",
        f"env: FIREBASE_CREDENTIALS_PATH {'present' if creds else 'absent'}" + (f", file {'exists' if cp.is_file() else 'MISSING'}" if cp else ""),
        "suggested additions (NOT applied): " + "; ".join(SUGGESTED),
    ]
    return "\n".join(out + firebase(ids))
