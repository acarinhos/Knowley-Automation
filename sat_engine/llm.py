"""Small provider layer: Gemini, Groq, Cohere, NVIDIA with fallback, retry and JSON repair."""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Optional, Type

from pydantic import BaseModel, ValidationError

from .config import get_env

DEFAULT_ORDER = ["gemini", "groq", "cohere", "nvidia"]
KEY_ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "cohere": "COHERE_API_KEY", "nvidia": "NVIDIA_API_KEY"}
DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "cohere": "command-a-03-2025",
    "nvidia": "meta/llama-3.3-70b-instruct",
}
MAX_RETRIES = 3
BACKOFF_BASE = 2.0


class LLMError(Exception):
    def __init__(self, msg: str, status: Optional[int] = None):
        super().__init__(msg)
        self.status = status


def model_for(provider: str) -> str:
    return get_env(f"{provider.upper()}_MODEL", DEFAULT_MODELS[provider])


def _key(provider: str) -> str:
    k = get_env(KEY_ENV[provider])
    if not k:
        raise LLMError(f"{KEY_ENV[provider]} not set")
    return k


def _call_gemini(prompt, system, images, json_mode):
    from google import genai
    from google.genai import types

    parts = [types.Part.from_bytes(data=b, mime_type="image/png") for b in (images or [])] + [prompt]
    cfg = types.GenerateContentConfig(
        system_instruction=system, response_mime_type="application/json" if json_mode else None)
    return genai.Client(api_key=_key("gemini")).models.generate_content(
        model=model_for("gemini"), contents=parts, config=cfg).text


def _msgs(prompt, system):
    return ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]


def _call_groq(prompt, system, images, json_mode):
    from groq import Groq

    kw = {"response_format": {"type": "json_object"}} if json_mode else {}
    r = Groq(api_key=_key("groq")).chat.completions.create(
        model=model_for("groq"), messages=_msgs(prompt, system), **kw)
    return r.choices[0].message.content


def _call_cohere(prompt, system, images, json_mode):
    import cohere

    kw = {"response_format": {"type": "json_object"}} if json_mode else {}
    r = cohere.ClientV2(api_key=_key("cohere")).chat(
        model=model_for("cohere"), messages=_msgs(prompt, system), **kw)
    return r.message.content[0].text


def _call_nvidia(prompt, system, images, json_mode):
    import requests

    base = (get_env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1") or "").rstrip("/")
    r = requests.post(
        f"{base}/chat/completions", timeout=120,
        headers={"Authorization": f"Bearer {_key('nvidia')}"},
        json={"model": model_for("nvidia"), "messages": _msgs(prompt, system)})
    if r.status_code >= 400:
        raise LLMError(f"nvidia HTTP {r.status_code}", r.status_code)
    return r.json()["choices"][0]["message"]["content"]


# Tests replace entries here; no network is touched by the module itself.
PROVIDERS: dict[str, Callable] = {
    "gemini": _call_gemini, "groq": _call_groq, "cohere": _call_cohere, "nvidia": _call_nvidia}


def _status(e: Exception) -> Optional[int]:
    s = getattr(e, "status", None) or getattr(e, "status_code", None) or getattr(e, "code", None)
    return s if isinstance(s, int) else None


def _call_with_retry(name, prompt, system, images, json_mode) -> str:
    for attempt in range(MAX_RETRIES + 1):
        try:
            return PROVIDERS[name](prompt, system, images, json_mode)
        except Exception as e:  # noqa: BLE001 - SDKs raise varied types
            s = _status(e)
            if (s == 429 or (s is not None and s >= 500)) and attempt < MAX_RETRIES:
                time.sleep(BACKOFF_BASE ** attempt)
                continue
            raise


def _run(prompt, system, images, providers, json_mode, accept: Callable[[str], object]):
    errors = []
    for name in providers or DEFAULT_ORDER:
        if name not in PROVIDERS:
            raise ValueError(f"unknown provider {name!r}")
        if not get_env(KEY_ENV[name]) and PROVIDERS[name].__module__ == __name__:
            errors.append(f"{name}: no key")
            continue
        try:
            return accept(_call_with_retry(name, prompt, system, images, json_mode)), name
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:200]}")
    raise LLMError("all providers failed: " + "; ".join(errors))


def parse_json(text: str):
    """Parse JSON, tolerating code fences, surrounding prose and trailing commas."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"[\[{].*[\]}]", t, re.S)
    if not m:
        raise ValueError("no JSON found in response")
    return json.loads(re.sub(r",\s*([\]}])", r"\1", m.group(0)))


def generate_text(prompt, system=None, providers=None, images=None) -> str:
    return _run(prompt, system, images, providers, False, lambda t: t.strip() or _fail("empty response"))[0]


def _fail(msg):
    raise ValueError(msg)


def generate_json(prompt, system=None, schema: Optional[Type[BaseModel]] = None, providers=None, images=None) -> dict:
    """Returns a dict (validated and re-dumped when `schema` is given); falls back across providers on failure."""
    def accept(text):
        data = parse_json(text)
        if schema is not None:
            try:
                return schema.model_validate(data).model_dump()
            except ValidationError as e:
                raise ValueError(f"schema validation failed: {e.error_count()} errors") from e
        return data

    return _run(prompt, system, images, providers, True, accept)[0]
