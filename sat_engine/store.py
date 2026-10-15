"""Firestore writer restricted to the `generated_sat_questions` collection."""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Iterable, Optional

from .config import ENV_DIR, FIRESTORE_COLLECTION, get_env, is_truthy
from .schema import SATQuestion

WRITES_ENV = "KNOWLEY_GENERATION_WRITES_ENABLED"


def _check_collection(name: str) -> str:
    if name != FIRESTORE_COLLECTION or name == "questions":
        raise ValueError(f"only collection {FIRESTORE_COLLECTION!r} may be used, got {name!r}")
    return name


def doc_id(q: SATQuestion) -> str:
    norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())  # noqa: E731
    return hashlib.sha256(f"{norm(q.stem)}\n{norm(q.passage)}".encode()).hexdigest()


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
        ref.set({**q.model_dump(), "session_id": session_id})
        counts["written"] += 1
    return counts
