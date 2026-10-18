"""Knowley üretim kontrol paneli için bağımlılıksız yerel web sunucusu."""

from __future__ import annotations

import argparse
import contextlib
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

from firebase_admin import auth as firebase_auth
from core.llm_manager import MultiLLMManager
from core.llm_settings import apply_selection, load_models, load_selection, model_env, save_selection, validate_models, validate_selection
from core.generator import QUESTION_SCHEMA_VERSION
from core.sheet_algorithm import get_sheet_algorithm_repository
from core.translation_service import LANGUAGE_CODE_TO_SHEET_NAME, SUPPORTED_LANGUAGE_CODES
from database.db_manager import init_firebase
from database.question_audit import fetch_question_audit
from database.question_report import (
    canonicalize_question_report_countries,
    fetch_question_report,
    question_report_country_label,
)
from database.question_requests import (
    RequestRateLimitExceeded,
    create_question_request,
    get_question_request_status,
    list_question_requests,
)
from worker import GenerationOptions, run_generation


ROOT_DIR = Path(__file__).resolve().parent
UI_DIR = ROOT_DIR / "ui"


@contextlib.contextmanager
def mock_env_if(active: bool, values: Dict[str, str]):
    """Temporarily set environment variables (restored afterwards) when `active`."""
    if not active:
        yield
        return
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


NO_KEY_MESSAGES = (
    "LLM_ALLOWED_PROVIDERS içinde kullanılabilir bir sağlayıcı anahtarı yok",  # core/llm_manager.py
    "Hiçbir LLM API anahtarı bulunamadı!",
)
ACTIVE_JOB_STATUSES = {"starting", "running", "stopping"}


class JobManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.repository = get_sheet_algorithm_repository()
        self.selection = load_selection()
        self.models = load_models()
        self.llm_manager, self.llm_error = self._make_manager(self.selection, self.models)

    @staticmethod
    def _make_manager(selection: Any, models: Any = None) -> tuple:
        """(manager, "") or (None, reason) for a selection. The env vars are set only while constructing."""
        try:
            with mock_env_if(True, model_env(selection, models)):
                return MultiLLMManager(), ""
        except ValueError as exc:
            if str(exc) not in NO_KEY_MESSAGES:
                raise
            return None, str(exc)

    def set_providers(self, selection: Any, models: Any = None) -> list:
        """Validate, build, persist and apply a provider selection (all or nothing).

        ValueError: empty/unknown selection or no API key for any selected provider (nothing changes).
        RuntimeError: a job is running; the change is rejected and can be repeated when it has finished."""
        chosen = validate_selection(selection)
        with self.lock:
            if any(job["status"] in ACTIVE_JOB_STATUSES for job in self.jobs.values()):
                raise RuntimeError("Aktif iş varken sağlayıcı seçimi değiştirilemez; iş bitince uygulanır.")
            manager, error = self._make_manager(chosen, models)
            if manager is None:
                raise ValueError(error)
            saved = save_selection(chosen, models=models)  # only after the manager could be built
            self.models = validate_models(models, saved)
            apply_selection(saved, self.models)
            self.selection, self.llm_manager, self.llm_error = saved, manager, ""
        return saved

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.llm_manager is None:
            raise RuntimeError(self.llm_error or "Soru üretimi için .env içinde en az bir LLM API anahtarı gerekli.")
        with self.lock:
            active = next((job for job in self.jobs.values() if job["status"] in {"starting", "running"}), None)
            if active:
                raise RuntimeError("Aynı anda yalnızca bir üretim işi çalıştırılabilir.")

        gen_mode = str(payload.get("generation_mode", "154"))
        custom_mode = str(payload.get("custom_mode") or "")
        if gen_mode == "154":
            default_count = 154
        elif gen_mode == "64":
            default_count = 64
        elif gen_mode == "custom":
            default_count = 160 if custom_mode == "equal" else 100
        else:
            default_count = 21
        question_count = int(payload.get("question_count") or default_count)

        options = GenerationOptions(
            question_count=question_count,
            category=None,
            subcategory=None,
            difficulty=payload.get("difficulty") or None,
            scope=None,
            country=payload.get("country") or None,
            country_profile_enabled=bool(payload.get("country_profile_enabled", False)),
            languages=list(payload.get("languages") or SUPPORTED_LANGUAGE_CODES),
            duration_seconds=None,
            generation_mode=gen_mode,
            custom_mode=custom_mode or None,
            difficulty_counts_per_10=dict(
                payload.get("difficulty_counts_per_10") or {"Kolay": 4, "Orta": 4, "Zor": 2}
            ),
            combo_percent=int(payload.get("combo_percent", 40)),
            sleep_between_requests=float(payload.get("sleep_between_requests", 1.5)),
            # Safe by default for local admin integrations. The request must
            # opt in and the server environment must explicitly allow writes.
            write_to_firestore=(
                os.getenv("KNOWLEY_GENERATION_WRITES_ENABLED", "false").strip().lower()
                in {"1", "true", "yes", "on"}
                and bool(payload.get("write_to_firestore", True))
            ),
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
            if any(item["status"] in ACTIVE_JOB_STATUSES for item in self.jobs.values()):
                raise RuntimeError("Aynı anda yalnızca bir üretim işi çalıştırılabilir.")
            # the job really starts now: new jobs (generation, translation, imports) use the selected providers
            apply_selection(self.selection, self.models)
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


MANAGER: Optional[JobManager] = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> JobManager:
    """Create the HTTP server's job manager only when that server is used."""
    global MANAGER
    if MANAGER is None:
        with _MANAGER_LOCK:
            if MANAGER is None:
                MANAGER = JobManager()
    return MANAGER


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


def firebase_question_report() -> Dict[str, Any]:
    """Tkinter rapor paneli için sınıflandırma alanlarını Firestore'dan okur."""
    repo = get_sheet_algorithm_repository()
    return canonicalize_question_report_countries(
        fetch_question_report(init_firebase()), repo.countries()
    )


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
        origin = self._allowed_request_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _allowed_request_origin(self) -> Optional[str]:
        origin = (self.headers.get("Origin") or "").strip()
        allowed = {
            item.strip().rstrip("/")
            for item in os.getenv("QUESTION_REQUESTS_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        }
        return origin if origin.rstrip("/") in allowed else None

    def _authenticate_firebase_user(self) -> tuple[Dict[str, Any], Any]:
        authorization = (self.headers.get("Authorization") or "").strip()
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.casefold() != "bearer" or not token.strip():
            raise PermissionError("Firebase oturum belirteci gerekli.")
        db = init_firebase()
        try:
            claims = firebase_auth.verify_id_token(token.strip(), check_revoked=True)
        except Exception as exc:
            raise PermissionError("Firebase oturumu geçersiz veya süresi dolmuş.") from exc
        if not claims.get("uid"):
            raise PermissionError("Geçerli kullanıcı oturumu bulunamadı.")
        return claims, db

    @staticmethod
    def _is_firebase_admin(claims: Dict[str, Any]) -> bool:
        return claims.get("admin") is True

    def _read_json(self) -> Dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/question-request-options":
            repo = get_manager().repository
            query = parse_qs(parsed.query)
            category = query.get("category", [""])[0].strip()
            country_code = query.get("country_code", [""])[0].strip().upper()
            country = next(
                (item for item in repo.countries() if str(item.get("code") or "").upper() == country_code),
                None,
            ) if country_code else None
            subcategories = (
                repo.subcategories(category, country.get("name") if country else None)
                if category in repo.categories()
                else []
            )
            self._json(
                {
                    "categories": repo.categories(),
                    "countries": [
                        {"code": item["code"], "name": item["name"], "languages": item["languages"]}
                        for item in repo.countries()
                    ],
                    "languages": [
                        {"code": code, "label": LANGUAGE_CODE_TO_SHEET_NAME[code]}
                        for code in SUPPORTED_LANGUAGE_CODES
                    ],
                    "subcategories": [item["subcategory"] for item in subcategories],
                }
            )
            return
        if parsed.path == "/api/question-requests" or parsed.path.startswith("/api/question-requests/"):
            try:
                claims, db = self._authenticate_firebase_user()
                request_id = unquote(parsed.path.removeprefix("/api/question-requests/")).strip("/")
                if not request_id or "/" in request_id:
                    self._json({"error": "Soru talebi kimliği gerekli."}, HTTPStatus.BAD_REQUEST)
                    return
                result = get_question_request_status(
                    db,
                    request_id=request_id,
                    user_uid=str(claims["uid"]),
                    is_admin=self._is_firebase_admin(claims),
                )
                self._json(result)
            except PermissionError as exc:
                status = HTTPStatus.FORBIDDEN if "yetkiniz" in str(exc).lower() else HTTPStatus.UNAUTHORIZED
                self._json({"error": str(exc)}, status)
            except KeyError:
                self._json({"error": "Soru talebi bulunamadı."}, HTTPStatus.NOT_FOUND)
            except Exception:
                self._json({"error": "Soru talebi şu anda okunamadı."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if parsed.path == "/api/admin/question-requests":
            try:
                claims, db = self._authenticate_firebase_user()
                if not self._is_firebase_admin(claims):
                    self._json({"error": "Bu liste yalnızca yöneticilere açıktır."}, HTTPStatus.FORBIDDEN)
                    return
                query = parse_qs(parsed.query)
                status_filter = query.get("status", [""])[0].strip() or None
                allowed_statuses = {
                    "queued", "checking_duplicate", "duplicate", "generating",
                    "needs_review", "published", "failed",
                }
                if status_filter and status_filter not in allowed_statuses:
                    self._json({"error": "Geçersiz durum filtresi."}, HTTPStatus.BAD_REQUEST)
                    return
                limit = int(query.get("limit", ["50"])[0])
                self._json(list_question_requests(db, status=status_filter, limit=limit))
            except PermissionError as exc:
                self._json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
            except (TypeError, ValueError):
                self._json({"error": "Limit sayısal olmalıdır."}, HTTPStatus.BAD_REQUEST)
            except Exception:
                self._json({"error": "Soru talepleri şu anda okunamadı."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if parsed.path == "/api/config":
            manager = get_manager()
            repo = manager.repository
            self._json(
                {
                    "algorithm": repo.summary(),
                    "question_schema_version": QUESTION_SCHEMA_VERSION,
                    "countries": repo.country_profiles(),
                    "all_countries": repo.countries(),
                    "local_targets": [
                        {
                            **country,
                            "minimum_count": repo.country_local_target_count(
                                country["name"], pool="154"
                            ),
                        }
                        for country in repo.countries()
                        if repo.country_local_target_count(country["name"], pool="154") > 0
                    ],
                    "categories": repo.categories(),
                    "languages": [
                        {"code": code, "label": LANGUAGE_CODE_TO_SHEET_NAME[code]}
                        for code in SUPPORTED_LANGUAGE_CODES
                    ],
                    "firebase": firebase_summary(live=False),
                    "providers": manager.llm_manager.status(live=False) if manager.llm_manager else {},
                    "latest_job": manager.latest(),
                }
            )
            return
        if parsed.path == "/api/subcategories":
            query = parse_qs(parsed.query)
            category = query.get("category", [""])[0]
            country = query.get("country", [""])[0] or None
            repository = get_manager().repository
            self._json({"items": repository.subcategories(category, country) if category else []})
            return
        if parsed.path == "/api/providers":
            live = parse_qs(parsed.query).get("live", ["0"])[0] == "1"
            manager = get_manager()
            self._json({"providers": manager.llm_manager.status(live=live) if manager.llm_manager else {}})
            return
        if parsed.path == "/api/firebase":
            self._json(firebase_summary(live=True))
            return
        if parsed.path == "/api/question-report":
            try:
                repo = get_manager().repository
                report = firebase_question_report()
                countries = [
                    {
                        "code": country.get("code") or "",
                        "name": country.get("name") or "",
                        "label": question_report_country_label(
                            country.get("code"), country.get("name")
                        ),
                    }
                    for country in repo.countries()
                ]
                countries.insert(0, {"code": "GL", "name": "Global", "label": "Global · GL"})
                self._json(
                    {
                        **report,
                        "countries": countries,
                        "categories": repo.categories(),
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as exc:
                self._json(
                    {"error": "Firebase soru raporu okunamadı.", "detail": str(exc)[:300]},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.split("/")[3]
            try:
                self._json(get_manager().get(job_id))
            except KeyError:
                self._json({"error": "İş bulunamadı."}, HTTPStatus.NOT_FOUND)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/question-requests":
                claims, db = self._authenticate_firebase_user()
                request = create_question_request(
                    db,
                    user_uid=str(claims["uid"]),
                    payload=self._read_json(),
                    repository=get_manager().repository,
                )
                self._json(request, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/jobs":
                self._json(get_manager().start(self._read_json()), HTTPStatus.ACCEPTED)
                return
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/stop"):
                job_id = parsed.path.split("/")[3]
                self._json(get_manager().stop(job_id))
                return
            self._json({"error": "Uç nokta bulunamadı."}, HTTPStatus.NOT_FOUND)
        except RequestRateLimitExceeded as exc:
            self._json({"error": str(exc), "code": "daily_limit"}, HTTPStatus.TOO_MANY_REQUESTS)
        except (ValueError, RuntimeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except KeyError:
            self._json({"error": "İş bulunamadı."}, HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            self._json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
        except Exception:
            self._json({"error": "Soru talebi kaydedilemedi."}, HTTPStatus.SERVICE_UNAVAILABLE)

    def do_OPTIONS(self) -> None:
        origin = self._allowed_request_origin()
        if not origin:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

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
