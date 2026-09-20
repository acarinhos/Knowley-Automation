"""Knowley üretim kontrol paneli için bağımlılıksız yerel web sunucusu."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import traceback
import uuid
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from core.llm_manager import MultiLLMManager
from core.generator import QUESTION_SCHEMA_VERSION
from core.sheet_algorithm import get_sheet_algorithm_repository
from database.db_manager import init_firebase
from database.question_audit import fetch_question_audit
from worker import GenerationOptions, run_generation


ROOT_DIR = Path(__file__).resolve().parent
UI_DIR = ROOT_DIR / "ui"


class JobManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.repository = get_sheet_algorithm_repository()
        try:
            self.llm_manager = MultiLLMManager()
        except ValueError as exc:
            if "Hiçbir LLM API anahtarı bulunamadı" not in str(exc):
                raise
            self.llm_manager = None

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.llm_manager is None:
            raise RuntimeError("Soru üretimi için .env içinde en az bir LLM API anahtarı gerekli.")
        with self.lock:
            active = next((job for job in self.jobs.values() if job["status"] in {"starting", "running"}), None)
            if active:
                raise RuntimeError("Aynı anda yalnızca bir üretim işi çalıştırılabilir.")

        gen_mode = str(payload.get("generation_mode", "154"))
        default_count = 154 if gen_mode == "154" else (64 if gen_mode == "64" else 21)
        question_count = int(payload.get("question_count") or default_count)

        options = GenerationOptions(
            question_count=question_count,
            category=None,
            subcategory=None,
            difficulty=payload.get("difficulty") or None,
            scope=None,
            country=payload.get("country") or None,
            country_profile_enabled=bool(payload.get("country_profile_enabled", False)),
            languages=list(payload.get("languages") or ["tr", "en", "es", "pt", "de"]),
            duration_seconds=None,
            generation_mode=gen_mode,
            combo_percent=int(payload.get("combo_percent", 40)),
            sleep_between_requests=float(payload.get("sleep_between_requests", 1.5)),
            write_to_firestore=True,
        ).validate()

        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "status": "starting",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "options": options.__dict__.copy(),
            "events": [],
            "summary": None,
            "error": None,
            "event_sequence": 0,
            "stop_event": threading.Event(),
        }
        with self.lock:
            self.jobs[job_id] = job

        def on_event(event: Dict[str, Any]) -> None:
            with self.lock:
                job["event_sequence"] += 1
                event["sequence"] = job["event_sequence"]
                job["events"].append(event)
                job["events"] = job["events"][-250:]
                if event["event"] == "started":
                    job["status"] = "running"
                elif event["event"] in {"completed", "stopped"}:
                    job["status"] = event["event"]

        def runner() -> None:
            try:
                job["summary"] = run_generation(
                    options,
                    stop_event=job["stop_event"],
                    on_event=on_event,
                    repository=self.repository,
                    llm_manager=self.llm_manager,
                )
            except Exception as exc:
                with self.lock:
                    job["status"] = "failed"
                    job["error"] = str(exc)
                    job["event_sequence"] += 1
                    job["events"].append(
                        {
                            "event": "failed",
                            "message": str(exc),
                            "timestamp": datetime.now(timezone.utc).timestamp(),
                            "sequence": job["event_sequence"],
                        }
                    )
                traceback.print_exc()

        thread = threading.Thread(target=runner, name=f"knowley-job-{job_id}", daemon=True)
        job["thread"] = thread
        thread.start()
        return self.public_job(job)

    def stop(self, job_id: str) -> Dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            job["stop_event"].set()
            if job["status"] in {"starting", "running"}:
                job["status"] = "stopping"
            return self.public_job(job)

    def get(self, job_id: str) -> Dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return self.public_job(job)

    def latest(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            if not self.jobs:
                return None
            return self.public_job(next(reversed(self.jobs.values())))

    @staticmethod
    def public_job(job: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in job.items()
            if key not in {"thread", "stop_event", "event_sequence"}
        }


MANAGER = JobManager()


def firebase_summary(live: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "project_id": "knowley-1",
        "database": "(default)",
        "edition": "Firestore Native",
        "location": "europe-west3",
        "collection": "questions",
        "reachable": None,
        "question_count": None,
    }
    if not live:
        return result
    try:
        db = init_firebase()
        aggregation = db.collection("questions").count().get()
        result["question_count"] = int(aggregation[0][0].value)
        result["reachable"] = True
    except Exception as exc:
        result["reachable"] = False
        result["error"] = str(exc)[:300]
    return result


def firebase_question_audit(minimum_version: str = "1.1.0") -> Dict[str, Any]:
    """UI için questions koleksiyonunu dönem/kategori/alt kategori bazında özetler."""
    return fetch_question_audit(init_firebase(), minimum_version=minimum_version)


class KnowleyHandler(BaseHTTPRequestHandler):
    server_version = "KnowleyUI/1.0.5"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            repo = MANAGER.repository
            self._json(
                {
                    "algorithm": repo.summary(),
                    "question_schema_version": QUESTION_SCHEMA_VERSION,
                    "countries": repo.country_profiles(),
                    "languages": [
                        {"code": "tr", "label": "Türkçe"},
                        {"code": "en", "label": "English"},
                        {"code": "es", "label": "Español"},
                        {"code": "pt", "label": "Português"},
                        {"code": "de", "label": "Deutsch"},
                    ],
                    "firebase": firebase_summary(live=False),
                    "providers": MANAGER.llm_manager.status(live=False) if MANAGER.llm_manager else {},
                    "latest_job": MANAGER.latest(),
                }
            )
            return
        if parsed.path == "/api/subcategories":
            query = parse_qs(parsed.query)
            category = query.get("category", [""])[0]
            country = query.get("country", [""])[0] or None
            self._json({"items": MANAGER.repository.subcategories(category, country) if category else []})
            return
        if parsed.path == "/api/providers":
            live = parse_qs(parsed.query).get("live", ["0"])[0] == "1"
            self._json({"providers": MANAGER.llm_manager.status(live=live) if MANAGER.llm_manager else {}})
            return
        if parsed.path == "/api/firebase":
            self._json(firebase_summary(live=True))
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.split("/")[3]
            try:
                self._json(MANAGER.get(job_id))
            except KeyError:
                self._json({"error": "İş bulunamadı."}, HTTPStatus.NOT_FOUND)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/jobs":
                self._json(MANAGER.start(self._read_json()), HTTPStatus.ACCEPTED)
                return
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/stop"):
                job_id = parsed.path.split("/")[3]
                self._json(MANAGER.stop(job_id))
                return
            self._json({"error": "Uç nokta bulunamadı."}, HTTPStatus.NOT_FOUND)
        except (ValueError, RuntimeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except KeyError:
            self._json({"error": "İş bulunamadı."}, HTTPStatus.NOT_FOUND)

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else unquote(request_path.lstrip("/"))
        candidate = (UI_DIR / relative).resolve()
        try:
            candidate.relative_to(UI_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowley üretim kontrol paneli")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("KNOWLEY_UI_PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), KnowleyHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Knowley kontrol paneli: {url}")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
