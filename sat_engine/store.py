"""Firestore writer restricted to the `generated_sat_questions` collection."""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Iterable, Optional

from .config import ENV_DIR, FIRESTORE_COLLECTION, get_env, is_truthy
from .schema import TUR, SATQuestion

WRITES_ENV = "KNOWLEY_GENERATION_WRITES_ENABLED"


def _check_collection(name: str) -> str:
    if name != FIRESTORE_COLLECTION or name == "questions":
        raise ValueError(f"only collection {FIRESTORE_COLLECTION!r} may be used, got {name!r}")
    return name


def doc_id(q: SATQuestion) -> str:
    norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())  # noqa: E731
    return hashlib.sha256(f"{norm(q.stem)}\n{norm(q.passage)}".encode()).hexdigest()


def build_doc(q: SATQuestion, session_id: str) -> dict:
    """The exact Firestore document written for a question (shared with --save-plan)."""
    return {**q.model_dump(), "session_id": session_id}


def get_db():
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        p = Path(get_env("FIREBASE_CREDENTIALS_PATH") or "").expanduser()
        if not p.is_absolute():
            p = ENV_DIR / p
        firebase_admin.initialize_app(credentials.Certificate(str(p)))
    return firestore.client()


def save_questions(questions: Iterable[SATQuestion], dry_run: bool = True, session_id: Optional[str] = None,
                   collection: str = FIRESTORE_COLLECTION, db=None) -> dict:
    """Write questions (idempotent by content hash). Real writes need dry_run=False AND the env flag."""
    _check_collection(collection)
    questions = list(questions)
    session_id = session_id or uuid.uuid4().hex
    counts = {"total": len(questions), "written": 0, "skipped": 0, "dry_run": True, "session_id": session_id}
    if dry_run or not is_truthy(get_env(WRITES_ENV)):
        counts["skipped"] = len(questions)
        return counts
    counts["dry_run"] = False
    db = db or get_db()
    col = db.collection(collection)
    for q in questions:
        ref = col.document(doc_id(q))
        if ref.get().exists:
            counts["skipped"] += 1
            continue
        ref.set(build_doc(q, session_id))
        counts["written"] += 1
    return counts


def backfill_tur(dry_run: bool = True, db=None) -> dict:
    """Set top-level `tur` (from `section`) on existing docs where it is missing/wrong. Updates ONLY that field; never creates/deletes.
    Real writes need dry_run=False AND the env flag. Idempotent."""
    _check_collection(FIRESTORE_COLLECTION)
    db = db or get_db()
    col = db.collection(FIRESTORE_COLLECTION)
    c = {"total": 0, "ok": 0, "to_update": 0, "unknown_section": 0, "updated": 0, "dry_run": True}
    todo = []
    for d in col.select(["section", "tur"]).stream():
        v = d.to_dict() or {}
        c["total"] += 1
        want = TUR.get(v.get("section"))
        if want is None:
            c["unknown_section"] += 1
        elif v.get("tur") == want:
            c["ok"] += 1
        else:
            todo.append((d.reference, want))
    c["to_update"] = len(todo)
    if dry_run or not is_truthy(get_env(WRITES_ENV)):
        return c
    c["dry_run"] = False
    for i in range(0, len(todo), 400):
        batch = db.batch()
        for ref, want in todo[i:i + 400]:
            batch.update(ref, {"tur": want})
        batch.commit()
        c["updated"] += len(todo[i:i + 400])
    return c
