"""Güncel LLM sağlayıcıları, fallback ve gözlemlenebilir kota durumu."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import re
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import cohere
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from groq import Groq


env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLMManager")
# SDK'nin başarılı her HTTP isteğini terminale yazması panel logunu gereksiz
# kalabalıklaştırıyor. Hatalar ve uyarılar görünmeye devam eder.
logging.getLogger("httpx").setLevel(logging.WARNING)


class AllProvidersCoolingDown(RuntimeError):
    """No provider can be called now without repeating a known failure."""

    def __init__(
        self,
        retry_after: int,
        details: str,
        *,
        quota_limited: bool = False,
        retryable: bool = False,
    ):
        self.retry_after = max(1, int(retry_after))
        self.quota_limited = quota_limited
        self.retryable = retryable or quota_limited
        super().__init__(
            f"Tüm LLM sağlayıcıları geçici olarak kullanılamıyor; "
            f"en erken {self.retry_after}s sonra yeniden deneyin. {details}"
        )


class _GeminiQuotaGuard:
    """Conservative, persistent client-side pacing for free-tier backfills.

    Gemini does not expose remaining project quota in normal generate-content
    responses. This guard therefore caps our own requests and token estimates;
    API 429 responses remain authoritative and add a model-specific cooldown.
    """

    def __init__(self, models: List[str]):
        self.models = list(dict.fromkeys(models))
        self.rpm = max(1, _int_env("GEMINI_RPM", 8) or 8)
        self.tpm = max(1, _int_env("GEMINI_TPM", 200_000) or 200_000)
        self.rpd = max(1, _int_env("GEMINI_RPD", 450) or 450)
        ledger = os.getenv("GEMINI_QUOTA_LEDGER_PATH", "").strip()
        self.path = Path(ledger) if ledger else Path(__file__).resolve().parent.parent / ".gemini_quota_ledger.json"
        self.lock = threading.Lock()
        self.last_model_index = -1
        self.state: Dict[str, Dict[str, Any]] = {model: {"requests": [], "tokens": [], "day": "", "daily": 0, "cooldown_until": 0.0} for model in self.models}
        self._load()

    @classmethod
    def _pacific_datetime(cls, timestamp: Optional[float] = None) -> datetime:
        utc_now = datetime.fromtimestamp(timestamp or time.time(), timezone.utc)
        year = utc_now.year
        march_first = date(year, 3, 1)
        november_first = date(year, 11, 1)
        second_sunday_march = 1 + ((6 - march_first.weekday()) % 7) + 7
        first_sunday_november = 1 + ((6 - november_first.weekday()) % 7)
        dst_start_utc = datetime(year, 3, second_sunday_march, 10, tzinfo=timezone.utc)
        dst_end_utc = datetime(year, 11, first_sunday_november, 9, tzinfo=timezone.utc)
        offset = -7 if dst_start_utc <= utc_now < dst_end_utc else -8
        return utc_now.astimezone(timezone(timedelta(hours=offset)))

    @classmethod
    def _day_key(cls, timestamp: Optional[float] = None) -> str:
        return cls._pacific_datetime(timestamp).date().isoformat()

    def _load(self) -> None:
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        today = self._day_key()
        for model in self.models:
            old = stored.get(model, {}) if isinstance(stored, dict) else {}
            if not isinstance(old, dict):
                continue
            self.state[model] = {
                "requests": [float(v) for v in old.get("requests", []) if _is_number(v)],
                "tokens": [[float(row[0]), int(row[1])] for row in old.get("tokens", []) if isinstance(row, list) and len(row) == 2 and _is_number(row[0]) and _is_number(row[1])],
                "day": str(old.get("day") or ""),
                "daily": int(old.get("daily", 0)) if old.get("day") == today else 0,
                "cooldown_until": float(old.get("cooldown_until", 0.0)),
            }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.state, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)

    def reserve(self, prompt_tokens: int) -> tuple[str, int]:
        now = time.time()
        today = self._day_key(now)
        with self.lock:
            waits: list[int] = []
            count = len(self.models)
            for offset in range(count):
                index = (self.last_model_index + 1 + offset) % count
                model = self.models[index]
                state = self.state[model]
                if state["day"] != today:
                    state["day"], state["daily"] = today, 0
                state["requests"] = [stamp for stamp in state["requests"] if now - stamp < 60]
                state["tokens"] = [row for row in state["tokens"] if now - row[0] < 60]
                wait_for = max(0, int(float(state["cooldown_until"]) - now))
                if len(state["requests"]) >= self.rpm:
                    wait_for = max(wait_for, int(60 - (now - state["requests"][0])) + 1)
                if int(state["daily"]) >= self.rpd:
                    now_pacific = self._pacific_datetime(now)
                    next_day = now_pacific.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    wait_for = max(wait_for, int((next_day - now_pacific).total_seconds()))
                used_tokens = sum(row[1] for row in state["tokens"])
                if used_tokens + prompt_tokens > self.tpm:
                    earliest = state["tokens"][0][0] if state["tokens"] else now
                    wait_for = max(wait_for, int(60 - (now - earliest)) + 1)
                if wait_for > 0:
                    waits.append(wait_for)
                    continue
                state["requests"].append(now)
                state["tokens"].append([now, max(1, int(prompt_tokens))])
                state["daily"] = int(state["daily"]) + 1
                self.last_model_index = index
                self._save()
                return model, 0
            self._save()
            return "", max(1, min(waits) if waits else 60)

    def fail(self, model: str, error: Exception) -> int:
        text = str(error).lower()
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        retry_match = re.search(r"(?:retry(?:-|_|\s+after|\s+in)?|retrydelay)\D{0,24}(\d+(?:\.\d+)?)", text)
        if retry_match:
            delay = max(2, min(86_400, int(float(retry_match.group(1))) + 1))
        elif status == 429 or "429" in text or "resource_exhausted" in text or "quota" in text:
            if any(marker in text for marker in ("perday", "per_day", "requests_per_day", "per day", "daily limit")):
                now_local = self._pacific_datetime()
                next_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                delay = max(60, int((next_day - now_local).total_seconds()))
            else:
                delay = 60
        elif status == 404 or "not found" in text or "not supported" in text:
            delay = 86_400
        else:
            delay = 30
        with self.lock:
            self.state[model]["cooldown_until"] = max(float(self.state[model]["cooldown_until"]), time.time() + delay)
            self._save()
        return delay

    def succeed(self, model: str, actual_prompt_tokens: Optional[int] = None) -> None:
        with self.lock:
            self.state[model]["cooldown_until"] = 0.0
            if actual_prompt_tokens and self.state[model]["tokens"]:
                self.state[model]["tokens"][-1][1] = max(1, int(actual_prompt_tokens))
            self._save()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _csv_env(name: str, defaults: List[str]) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return list(defaults)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(defaults)


def _int_env(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class MultiLLMManager:
    """Gemini, Groq, Cohere ve NVIDIA round-robin/fallback yöneticisi.

    Hesaba özgü limitleri hiçbir sağlayıcı ortak bir kota API'siyle sunmadığı
    için UI iki sinyali ayrı gösterir: bu süreçte gözlenen kullanım ve API'nin
    döndürdüğü gerçek rate-limit başlıkları (Groq). Ortam değişkeniyle girilen
    limitler tahmin olarak etiketlenir.
    """

    GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"]
    GROQ_MODELS = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
        "qwen/qwen3.6-27b",
    ]
    COHERE_MODELS = ["command-a-03-2025", "command-r-08-2024", "command-r7b-12-2024"]
    # 20B modeli NVIDIA katalog/probe çağrısında yaygın ve hızlı erişilebilir;
    # büyük model yalnızca fallback olarak denenir.
    NVIDIA_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
    NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

    def __init__(self):
        self.providers: List[str] = []
        self.current_idx = 0
        self._lock = threading.Lock()
        self._probe_cache: Dict[str, Any] = {"at": 0.0, "providers": {}}
        self._provider_cooldown_until: Dict[str, float] = {}
        self._provider_cooldown_retryable: Dict[str, bool] = {}
        self._provider_cooldown_quota: Dict[str, bool] = {}
        self.usage: Dict[str, Dict[str, Any]] = {}
        self.last_call: Dict[str, Any] = {}

        self.gemini_models = _csv_env("GEMINI_MODELS", self.GEMINI_MODELS)
        self.gemini_quota_guard = (
            _GeminiQuotaGuard(self.gemini_models)
            if os.getenv("GEMINI_QUOTA_GUARD", "").strip().casefold() in {"1", "true", "yes", "on"}
            else None
        )
        self.groq_models = _csv_env("GROQ_MODELS", self.GROQ_MODELS)
        self.cohere_models = _csv_env("COHERE_MODELS", self.COHERE_MODELS)
        self.nvidia_models = _csv_env("NVIDIA_MODELS", self.NVIDIA_MODELS)
        raw_provider_allowlist = os.getenv("LLM_ALLOWED_PROVIDERS")
        allowed_providers = (
            {provider.strip().casefold() for provider in raw_provider_allowlist.split(",") if provider.strip()}
            if raw_provider_allowlist is not None
            else None
        )

        self.gemini_key = os.getenv("GEMINI_API_KEY")
        if self.gemini_key and (allowed_providers is None or "gemini" in allowed_providers):
            try:
                self.gemini_client = genai.Client(api_key=self.gemini_key, http_options={"timeout": 25000})
                self.providers.append("gemini")
            except Exception as exc:
                logger.warning("Gemini başlatılamadı: %s", exc)

        self.groq_key = os.getenv("GROQ_API_KEY")
        if self.groq_key and (allowed_providers is None or "groq" in allowed_providers):
            try:
                self.groq_client = Groq(api_key=self.groq_key, timeout=25.0)
                self.providers.append("groq")
            except Exception as exc:
                logger.warning("Groq başlatılamadı: %s", exc)

        self.cohere_key = os.getenv("COHERE_API_KEY")
        if self.cohere_key and (allowed_providers is None or "cohere" in allowed_providers):
            try:
                self.cohere_client = cohere.ClientV2(api_key=self.cohere_key, timeout=25.0)
                self.providers.append("cohere")
            except Exception as exc:
                logger.warning("Cohere başlatılamadı: %s", exc)

        self.nvidia_key = os.getenv("NVIDIA_API_KEY")
        self.nvidia_base_url = os.getenv("NVIDIA_BASE_URL", self.NVIDIA_BASE_URL).rstrip("/")
        if self.nvidia_key and (allowed_providers is None or "nvidia" in allowed_providers):
            self.providers.append("nvidia")

        if not self.providers:
            if allowed_providers is not None:
                raise ValueError(
                    "LLM_ALLOWED_PROVIDERS içinde kullanılabilir bir sağlayıcı anahtarı yok"
                )
            raise ValueError("Hiçbir LLM API anahtarı bulunamadı!")

        for provider in self.providers:
            self.usage[provider] = {
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "last_model": None,
                "last_error": None,
                "rate_headers": {},
            }
        logger.info("Aktif LLM Sağlayıcıları: %s", self.providers)

    def _record_success(
        self,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        headers: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            state = self.usage.setdefault(provider, {})
            state["requests"] = int(state.get("requests", 0)) + 1
            state["input_tokens"] = int(state.get("input_tokens", 0)) + int(input_tokens or 0)
            state["output_tokens"] = int(state.get("output_tokens", 0)) + int(output_tokens or 0)
            state["last_model"] = model
            state["last_error"] = None
            if headers:
                interesting = {
                    str(key).lower(): str(value)
                    for key, value in headers.items()
                    if str(key).lower().startswith("x-ratelimit-") or str(key).lower() == "retry-after"
                }
                state["rate_headers"] = interesting
            self.last_call = {
                "provider": provider,
                "model": model,
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
            }

    def _record_error(self, provider: str, error: Exception) -> None:
        with self._lock:
            self.usage.setdefault(provider, {})["last_error"] = str(error)[:300]

    def _call_groq(self, system_prompt: str, user_prompt: str) -> str:
        last_error: Optional[Exception] = None
        for model_name in self.groq_models:
            try:
                request = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    # Free/on-demand Groq hesaplarında istem + azami çıktı
                    # birlikte TPM limitine sayılır. 2048 bu projenin uzun
                    # istemiyle 8000'i az farkla aşıyordu.
                    "max_tokens": _int_env("GROQ_MAX_OUTPUT_TOKENS", 1800),
                    "temperature": 0.7,
                }
                headers: Dict[str, Any] = {}
                try:
                    raw = self.groq_client.chat.completions.with_raw_response.create(**request)
                    completion = raw.parse()
                    headers = dict(raw.headers)
                except AttributeError:
                    completion = self.groq_client.chat.completions.create(**request)
                usage = getattr(completion, "usage", None)
                self._record_success(
                    "groq",
                    model_name,
                    getattr(usage, "prompt_tokens", 0) if usage else 0,
                    getattr(usage, "completion_tokens", 0) if usage else 0,
                    headers,
                )
                return completion.choices[0].message.content or "{}"
            except Exception as exc:
                last_error = exc
                error_text = str(exc).lower()
                if (
                    getattr(exc, "status_code", None) in (413, 429)
                    or "413" in error_text
                    or "429" in error_text
                    or "resource_exhausted" in error_text
                ):
                    # İstek tüm modeller için aynı boyutta; aynı payload'ı tekrar
                    # göndermek yalnızca gecikme yaratır.
                    break
                continue
        raise RuntimeError(f"Groq modellerinin hiçbirine erişilemedi: {last_error}")

    def _call_cohere(self, system_prompt: str, user_prompt: str) -> str:
        last_error: Optional[Exception] = None
        for model_name in self.cohere_models:
            try:
                response = self.cohere_client.chat(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"{user_prompt}\n\nNOT: Yalnızca ham JSON nesnesi döndür."},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=4096,
                    temperature=0.7,
                )
                usage = getattr(response, "usage", None)
                tokens = getattr(usage, "tokens", None) if usage else None
                self._record_success(
                    "cohere",
                    model_name,
                    getattr(tokens, "input_tokens", 0) if tokens else 0,
                    getattr(tokens, "output_tokens", 0) if tokens else 0,
                )
                return response.message.content[0].text
            except Exception as exc:
                last_error = exc
                error_text = str(exc).lower()
                if (
                    getattr(exc, "status_code", None) == 429
                    or "429" in error_text
                    or "too many requests" in error_text
                ):
                    break
                continue
        raise RuntimeError(f"Cohere modellerinin hiçbirine erişilemedi: {last_error}")

    def _call_gemini(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        last_error: Optional[Exception] = None
        attempted: set[str] = set()
        quota_waits: list[int] = []
        local_quota_block = False
        api_errors_are_quota = True
        api_errors_are_retryable = True
        while len(attempted) < len(self.gemini_models):
            if self.gemini_quota_guard:
                # Treat each prompt character as one token for quota pacing.
                # This is intentionally conservative for multilingual prompts.
                prompt_tokens = max(1, len(system_prompt) + len(user_prompt))
                model_name, wait_seconds = self.gemini_quota_guard.reserve(prompt_tokens)
                if not model_name:
                    quota_waits.append(wait_seconds)
                    local_quota_block = True
                    break
            else:
                model_name = next((model for model in self.gemini_models if model not in attempted), "")
                if not model_name:
                    break
            attempted.add(model_name)
            try:
                config: Dict[str, Any] = {
                    "system_instruction": system_prompt,
                    "response_mime_type": "application/json",
                    "temperature": 0.7,
                    "max_output_tokens": _int_env("GEMINI_MAX_OUTPUT_TOKENS", 65536),
                    "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
                }
                if response_schema:
                    config["response_schema"] = response_schema
                response = self.gemini_client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=config,
                )
                usage = getattr(response, "usage_metadata", None)
                self._record_success(
                    "gemini",
                    model_name,
                    getattr(usage, "prompt_token_count", 0) if usage else 0,
                    getattr(usage, "candidates_token_count", 0) if usage else 0,
                )
                if self.gemini_quota_guard:
                    self.gemini_quota_guard.succeed(model_name)
                return response.text or "{}"
            except Exception as exc:
                last_error = exc
                if self.gemini_quota_guard:
                    delay = self.gemini_quota_guard.fail(model_name, exc)
                    quota_waits.append(delay)
                    text = str(exc).lower()
                    status = getattr(exc, "status_code", None)
                    if status is None:
                        status = getattr(getattr(exc, "response", None), "status_code", None)
                    is_quota_error = (
                        status == 429
                        or status == 403 and "quota" in text
                        or "429" in text
                        or "resource_exhausted" in text
                        or "quota" in text
                    )
                    is_retryable_error = (
                        is_quota_error
                        or status in (408, 425, 500, 502, 503, 504)
                        or any(marker in text for marker in ("timeout", "deadline_exceeded", "temporarily unavailable"))
                        or bool(re.search(r"\b(?:408|425|500|502|503|504)\b", text))
                    )
                    if not is_quota_error:
                        api_errors_are_quota = False
                    if not is_retryable_error:
                        api_errors_are_retryable = False
        if quota_waits and self.gemini_quota_guard and (
            local_quota_block or api_errors_are_quota or api_errors_are_retryable
        ):
            wait_seconds = min(quota_waits)
            raise AllProvidersCoolingDown(
                wait_seconds,
                f"Gemini free-tier model quotas/cooldowns are active; next check in {wait_seconds}s. "
                f"Last API error: {last_error}",
                quota_limited=local_quota_block or api_errors_are_quota,
                retryable=local_quota_block or api_errors_are_retryable,
            )
        raise RuntimeError(f"Gemini modellerinin hiçbirine erişilemedi: {last_error}")

    def _call_nvidia(self, system_prompt: str, user_prompt: str) -> str:
        last_error: Optional[Exception] = None
        for model_name in self.nvidia_models:
            try:
                response = requests.post(
                    f"{self.nvidia_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.nvidia_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 4096,
                        "temperature": 0.7,
                    },
                    timeout=float(os.getenv("NVIDIA_TIMEOUT_SECONDS", "20")),
                )
                response.raise_for_status()
                payload = response.json()
                usage = payload.get("usage") or {}
                self._record_success(
                    "nvidia",
                    model_name,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    dict(response.headers),
                )
                return payload["choices"][0]["message"]["content"] or "{}"
            except Exception as exc:
                last_error = exc
                if isinstance(exc, requests.Timeout):
                    break
        raise RuntimeError(f"NVIDIA modellerinin hiçbirine erişilemedi: {last_error}")

    @staticmethod
    def _cooldown_for_error(error: Exception) -> int:
        text = str(error).lower()
        retry_match = re.search(r"(?:retry(?:-|_after| in)?|cooldown)\D{0,20}(\d+(?:\.\d+)?)", text)
        if retry_match:
            return max(5, min(900, int(float(retry_match.group(1))) + 1))
        if "413" in text or "payload too large" in text:
            return 30
        if "429" in text or "resource_exhausted" in text:
            return 120
        if "timeout" in text or "timed out" in text:
            return 300
        return 45

    def generate_trivia(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        attempts = len(self.providers)
        errors: List[str] = []
        waitable_errors: List[bool] = []
        quota_errors: List[bool] = []
        for _ in range(attempts):
            with self._lock:
                provider = self.providers[self.current_idx]
                self.current_idx = (self.current_idx + 1) % len(self.providers)
                cooldown_until = float(self._provider_cooldown_until.get(provider, 0.0))
            if cooldown_until > time.time():
                errors.append(f"{provider}: cooldown ({int(cooldown_until - time.time())}s)")
                waitable_errors.append(self._provider_cooldown_retryable.get(provider, False))
                quota_errors.append(self._provider_cooldown_quota.get(provider, False))
                continue
            try:
                if provider == "groq":
                    raw = self._call_groq(system_prompt, user_prompt)
                elif provider == "cohere":
                    raw = self._call_cohere(system_prompt, user_prompt)
                elif provider == "nvidia":
                    raw = self._call_nvidia(system_prompt, user_prompt)
                else:
                    raw = self._call_gemini(system_prompt, user_prompt, response_schema)
                clean = raw.strip()
                if clean.startswith("```"):
                    clean = clean.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                data = json.loads(clean)
                data["_provider"] = provider
                with self._lock:
                    data["_quota"] = dict(self.last_call)
                    self._provider_cooldown_until.pop(provider, None)
                    self._provider_cooldown_retryable.pop(provider, None)
                    self._provider_cooldown_quota.pop(provider, None)
                return data
            except Exception as exc:
                self._record_error(provider, exc)
                with self._lock:
                    retry_after = getattr(exc, "retry_after", None)
                    cooldown = int(retry_after) if retry_after is not None else self._cooldown_for_error(exc)
                    self._provider_cooldown_until[provider] = time.time() + max(1, cooldown)
                status = getattr(exc, "status_code", None)
                if status is None:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                retryable = (
                    bool(getattr(exc, "retryable", False))
                    or status in (408, 425, 429, 500, 502, 503, 504)
                    or "timeout" in str(exc).lower()
                    or "deadline_exceeded" in str(exc).lower()
                    or "temporarily unavailable" in str(exc).lower()
                    or bool(re.search(r"\b(?:408|425|429|500|502|503|504)\b", str(exc)))
                )
                quota_limited = bool(getattr(exc, "quota_limited", False))
                with self._lock:
                    self._provider_cooldown_retryable[provider] = retryable
                    self._provider_cooldown_quota[provider] = quota_limited
                waitable_errors.append(retryable)
                quota_errors.append(quota_limited)
                errors.append(f"{provider}: {exc}")
                logger.warning("%s çağrısı başarısız oldu; sıradaki sağlayıcı deneniyor.", provider.upper())
        now = time.time()
        remaining = [
            max(1, int(until - now))
            for provider, until in self._provider_cooldown_until.items()
            if provider in self.providers and until > now
        ]
        raise AllProvidersCoolingDown(
            min(remaining) if remaining else 30,
            "Tüm LLM sağlayıcıları hata verdi. " + " | ".join(errors),
            quota_limited=bool(quota_errors) and all(quota_errors),
            retryable=bool(waitable_errors) and all(waitable_errors),
        )

    def _limits(self, provider: str) -> Dict[str, Any]:
        if provider == "groq":
            return {
                "rpm": _int_env("GROQ_RPM", 30),
                "period_requests": _int_env("GROQ_RPD", 1000),
                "period": "day",
                "tpm": _int_env("GROQ_TPM", 8000),
                "source": "configured_or_public_baseline",
            }
        if provider == "cohere":
            return {
                "rpm": _int_env("COHERE_RPM", 20),
                "period_requests": _int_env("COHERE_MONTHLY_REQUESTS", 1000),
                "period": "month",
                "tpm": None,
                "source": "configured_or_trial_baseline",
            }
        if provider == "nvidia":
            return {
                "rpm": _int_env("NVIDIA_RPM", None),
                "period_requests": _int_env("NVIDIA_RPD", None),
                "period": "day",
                "tpm": _int_env("NVIDIA_TPM", None),
                "source": "configured; exact limits depend on the NVIDIA account",
            }
        return {
            "rpm": _int_env("GEMINI_RPM", None),
            "period_requests": _int_env("GEMINI_RPD", None),
            "period": "day",
            "tpm": _int_env("GEMINI_TPM", None),
            "source": "configured; exact project quota is shown in AI Studio",
        }

    def _probe_provider(self, provider: str) -> Dict[str, Any]:
        if provider == "gemini":
            names = [str(getattr(model, "name", "")).removeprefix("models/") for model in self.gemini_client.models.list()]
        elif provider == "groq":
            names = [str(model.id) for model in self.groq_client.models.list().data]
        elif provider == "nvidia":
            response = requests.get(
                f"{self.nvidia_base_url}/models",
                headers={"Authorization": f"Bearer {self.nvidia_key}"},
                timeout=15,
            )
            response.raise_for_status()
            names = [str(model.get("id", "")) for model in response.json().get("data", [])]
        else:
            response = self.cohere_client.models.list(page_size=100, endpoint="chat")
            names = [str(model.name) for model in response.models]
        preferred = getattr(self, f"{provider}_models")
        return {
            "reachable": True,
            "available_models": [name for name in preferred if name in names],
            "available_model_count": len(names),
        }

    def status(self, live: bool = False) -> Dict[str, Any]:
        now = time.time()
        if live and now - float(self._probe_cache.get("at", 0)) > 60:
            probed: Dict[str, Any] = {}
            for provider in self.providers:
                try:
                    probed[provider] = self._probe_provider(provider)
                except Exception as exc:
                    probed[provider] = {"reachable": False, "error": str(exc)[:240]}
            self._probe_cache = {"at": now, "providers": probed}
        result: Dict[str, Any] = {}
        for provider in ("gemini", "groq", "cohere", "nvidia"):
            configured = provider in self.providers
            usage = dict(self.usage.get(provider, {}))
            limits = self._limits(provider)
            period_limit = limits.get("period_requests")
            local_requests = int(usage.get("requests", 0))
            headers = usage.get("rate_headers", {}) or {}
            exact_remaining = headers.get("x-ratelimit-remaining-requests")
            estimated_remaining = (
                max(0, int(period_limit) - local_requests) if period_limit is not None else None
            )
            result[provider] = {
                "configured": configured,
                "models": getattr(self, f"{provider}_models"),
                "limits": limits,
                "session_usage": usage,
                "remaining_questions": int(exact_remaining) if str(exact_remaining).isdigit() else estimated_remaining,
                "remaining_is_exact": exact_remaining is not None,
                "live": self._probe_cache.get("providers", {}).get(provider),
            }
        return result


_global_llm_manager: Optional[MultiLLMManager] = None
_global_llm_manager_lock = threading.Lock()


def get_llm_manager() -> MultiLLMManager:
    global _global_llm_manager
    if _global_llm_manager is None:
        with _global_llm_manager_lock:
            if _global_llm_manager is None:
                _global_llm_manager = MultiLLMManager()
    return _global_llm_manager
