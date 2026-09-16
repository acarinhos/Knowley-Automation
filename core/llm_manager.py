"""Güncel LLM sağlayıcıları, fallback ve gözlemlenebilir kota durumu."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import cohere
import requests
from dotenv import load_dotenv
from google import genai
from groq import Groq


env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLMManager")


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

    GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"]
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
        self.usage: Dict[str, Dict[str, Any]] = {}
        self.last_call: Dict[str, Any] = {}

        self.gemini_models = _csv_env("GEMINI_MODELS", self.GEMINI_MODELS)
        self.groq_models = _csv_env("GROQ_MODELS", self.GROQ_MODELS)
        self.cohere_models = _csv_env("COHERE_MODELS", self.COHERE_MODELS)
        self.nvidia_models = _csv_env("NVIDIA_MODELS", self.NVIDIA_MODELS)

        self.gemini_key = os.getenv("GEMINI_API_KEY")
        if self.gemini_key:
            try:
                self.gemini_client = genai.Client(api_key=self.gemini_key, http_options={"timeout": 25000})
                self.providers.append("gemini")
            except Exception as exc:
                logger.warning("Gemini başlatılamadı: %s", exc)

        self.groq_key = os.getenv("GROQ_API_KEY")
        if self.groq_key:
            try:
                self.groq_client = Groq(api_key=self.groq_key, timeout=25.0)
                self.providers.append("groq")
            except Exception as exc:
                logger.warning("Groq başlatılamadı: %s", exc)

        self.cohere_key = os.getenv("COHERE_API_KEY")
        if self.cohere_key:
            try:
                self.cohere_client = cohere.ClientV2(api_key=self.cohere_key, timeout=25.0)
                self.providers.append("cohere")
            except Exception as exc:
                logger.warning("Cohere başlatılamadı: %s", exc)

        self.nvidia_key = os.getenv("NVIDIA_API_KEY")
        self.nvidia_base_url = os.getenv("NVIDIA_BASE_URL", self.NVIDIA_BASE_URL).rstrip("/")
        if self.nvidia_key:
            self.providers.append("nvidia")

        if not self.providers:
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
                    "max_tokens": 2048,
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
                if getattr(exc, "status_code", None) == 413 or "413" in str(exc):
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
                continue
        raise RuntimeError(f"Cohere modellerinin hiçbirine erişilemedi: {last_error}")

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        last_error: Optional[Exception] = None
        for model_name in self.gemini_models:
            try:
                response = self.gemini_client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config={
                        "system_instruction": system_prompt,
                        "response_mime_type": "application/json",
                        "temperature": 0.7,
                    },
                )
                usage = getattr(response, "usage_metadata", None)
                self._record_success(
                    "gemini",
                    model_name,
                    getattr(usage, "prompt_token_count", 0) if usage else 0,
                    getattr(usage, "candidates_token_count", 0) if usage else 0,
                )
                return response.text or "{}"
            except Exception as exc:
                last_error = exc
                continue
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
        if "413" in text or "payload too large" in text:
            return 600
        if "429" in text or "resource_exhausted" in text:
            return 120
        if "timeout" in text or "timed out" in text:
            return 300
        return 45

    def generate_trivia(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        attempts = len(self.providers)
        errors: List[str] = []
        for _ in range(attempts):
            provider = self.providers[self.current_idx]
            self.current_idx = (self.current_idx + 1) % len(self.providers)
            cooldown_until = float(self._provider_cooldown_until.get(provider, 0.0))
            if cooldown_until > time.time():
                errors.append(f"{provider}: cooldown ({int(cooldown_until - time.time())}s)")
                continue
            try:
                if provider == "groq":
                    raw = self._call_groq(system_prompt, user_prompt)
                elif provider == "cohere":
                    raw = self._call_cohere(system_prompt, user_prompt)
                elif provider == "nvidia":
                    raw = self._call_nvidia(system_prompt, user_prompt)
                else:
                    raw = self._call_gemini(system_prompt, user_prompt)
                clean = raw.strip()
                if clean.startswith("```"):
                    clean = clean.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                data = json.loads(clean)
                data["_provider"] = provider
                data["_quota"] = dict(self.last_call)
                self._provider_cooldown_until.pop(provider, None)
                return data
            except Exception as exc:
                self._record_error(provider, exc)
                self._provider_cooldown_until[provider] = time.time() + self._cooldown_for_error(exc)
                errors.append(f"{provider}: {exc}")
                logger.warning("%s çağrısı başarısız oldu; sıradaki sağlayıcı deneniyor.", provider.upper())
        raise RuntimeError("Havuzdaki tüm LLM motorlarının kotası doldu! Tüm LLM sağlayıcıları hata verdi. " + " | ".join(errors))

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


def get_llm_manager() -> MultiLLMManager:
    global _global_llm_manager
    if _global_llm_manager is None:
        _global_llm_manager = MultiLLMManager()
    return _global_llm_manager
