"""Small provider layer: Gemini, Groq, Cohere, NVIDIA with fallback, retry and JSON repair."""
from __future__ import annotations

import collections
import functools
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional, Type

from pydantic import BaseModel, ValidationError

from .config import get_env

DEFAULT_ORDER = ["gemini", "groq", "cohere", "nvidia"]
KEY_ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "cohere": "COHERE_API_KEY", "nvidia": "NVIDIA_API_KEY"}
DEFAULT_MODELS = {
    "gemini": "gemini-3-flash-preview",
    "groq": "openai/gpt-oss-120b",
    "cohere": "command-a-plus-05-2026",
    "nvidia": "nvidia/nemotron-3-super-120b-a12b",
    "claude": "sonnet",
}
_last = threading.local()  # provider/model that answered the most recent successful call in this thread
MAX_RETRIES = 3
BACKOFF_BASE = 2.0


class LLMError(Exception):
    def __init__(self, msg: str, status: Optional[int] = None):
        super().__init__(msg)
        self.status = status


def model_for(provider: str) -> str:
    if provider == "claude":
        return get_env("SAT_CLAUDE_MODEL", DEFAULT_MODELS["claude"])
    return get_env(f"{provider.upper()}_MODEL", DEFAULT_MODELS[provider])


def _key(provider: str) -> str:
    k = get_env(KEY_ENV[provider])
    if not k:
        raise LLMError(f"{KEY_ENV[provider]} not set")
    return k


@functools.lru_cache(maxsize=None)
def _gemini_client(api_key: str):
    # Cached per key: an inline Client is garbage-collected and closes its HTTP session mid-request.
    logging.getLogger("google_genai").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from google import genai
    return genai.Client(api_key=api_key, http_options={"timeout": int(TIMEOUT * 1000)})


def last_used() -> dict:
    """{'provider': ..., 'model': ...} of the last successful generate_* call in this thread."""
    return dict(getattr(_last, "info", {}))


def _call_gemini(prompt, system, images, json_mode):
    from google.genai import types

    parts = [types.Part.from_bytes(data=b, mime_type="image/png") for b in (images or [])] + [prompt]
    cfg = types.GenerateContentConfig(
        system_instruction=system, response_mime_type="application/json" if json_mode else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    return _gemini_client(_key("gemini")).models.generate_content(
        model=model_for("gemini"), contents=parts, config=cfg).text


def _msgs(prompt, system):
    return ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]


def _call_groq(prompt, system, images, json_mode):
    from groq import Groq

    kw = {"response_format": {"type": "json_object"}} if json_mode else {}
    r = Groq(api_key=_key("groq"), timeout=TIMEOUT, max_retries=0).chat.completions.create(
        model=model_for("groq"), messages=_msgs(prompt, system), **kw)
    return r.choices[0].message.content


def _call_cohere(prompt, system, images, json_mode):
    import cohere

    kw = {"response_format": {"type": "json_object"}} if json_mode else {}
    r = cohere.ClientV2(api_key=_key("cohere"), timeout=TIMEOUT).chat(
        model=model_for("cohere"), messages=_msgs(prompt, system), **kw)
    return "".join(getattr(c, "text", None) or "" for c in r.message.content if getattr(c, "type", "text") == "text")


def _call_nvidia(prompt, system, images, json_mode):
    import requests

    base = (get_env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1") or "").rstrip("/")
    r = requests.post(
        f"{base}/chat/completions", timeout=_timeout("nvidia"),
        headers={"Authorization": f"Bearer {_key('nvidia')}"},
        json={"model": model_for("nvidia"), "messages": _msgs(prompt, system)})
    if r.status_code >= 400:
        ra = r.headers.get("retry-after")
        raise LLMError(f"nvidia HTTP {r.status_code}" + (f" retry in {ra}s" if ra else ""), r.status_code)
    return r.json()["choices"][0]["message"]["content"]


def claude_cmd() -> str | None:
    """Headless Claude Code CLI: SAT_CLAUDE_CMD, PATH, else the newest desktop-app install under APPDATA/Claude/claude-code."""
    c = get_env("SAT_CLAUDE_CMD") or shutil.which("claude")
    if c:
        return c
    root = os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code")
    found = []
    for d, _, fs in os.walk(root):
        found += [os.path.join(d, f) for f in fs if f in ("claude.exe", "claude")]
    return max(found, key=os.path.getmtime) if found else None


@functools.lru_cache(maxsize=None)
def claude_ready() -> bool:
    """CLI present AND logged in (an unauthenticated CLI would only burn retries)."""
    c = claude_cmd()
    if not c:
        return False
    try:
        r = subprocess.run([c, "auth", "status"], capture_output=True, text=True, timeout=30)
        return bool(json.loads(r.stdout).get("loggedIn"))
    except Exception:  # noqa: BLE001
        return False


def default_order() -> list[str]:
    return (["claude"] if claude_ready() else []) + DEFAULT_ORDER


_ENV_KEEP = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
             "PROGRAMFILES", "PROGRAMFILES(X86)", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "USERNAME", "USERDOMAIN",
             "TEMP", "TMP", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "TERM"}


def _claude_env() -> dict:
    """Minimal child environment: no *_API_KEY / project secrets, but what the CLI needs to find its own login."""
    return {k: v for k, v in os.environ.items()
            if (k.upper() in _ENV_KEEP or k.upper().startswith("CLAUDE_CODE_OAUTH")) and not k.upper().endswith("_API_KEY")}


@functools.lru_cache(maxsize=None)
def _claude_flags(cmd: str) -> tuple:
    """Isolation flags this CLI build advertises in --help (never runs a prompt)."""
    want = ("--strict-mcp-config", "--setting-sources")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            h = subprocess.run([cmd, "--help"], capture_output=True, text=True, timeout=30, env=_claude_env(), cwd=tmp).stdout
    except Exception:  # noqa: BLE001
        return ()
    return tuple(f for f in want if f in h)


def _kill_tree(p) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        p.kill()
    except Exception:  # noqa: BLE001
        pass


def _call_claude(prompt, system, images, json_mode):
    exe = claude_cmd() or "claude"
    flags = _claude_flags(exe)
    cmd = [exe, "-p", "--model", model_for("claude"), "--tools", "", "--no-session-persistence",
           "--disable-slash-commands", "--output-format", "text"]
    cmd += (["--strict-mcp-config"] if "--strict-mcp-config" in flags else []) + (["--setting-sources", ""] if "--setting-sources" in flags else [])
    cmd += ["--system-prompt", system] if system else []
    t = _timeout("claude")
    with tempfile.TemporaryDirectory() as tmp:  # neutral cwd: no project CLAUDE.md / settings are picked up
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             env=_claude_env(), cwd=tmp, start_new_session=os.name != "nt")
        try:
            out, err = p.communicate(prompt, timeout=t)
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            p.communicate()
            raise TimeoutError(f"claude CLI timed out after {t:.0f}s") from None
    if p.returncode or not out.strip():
        raise LLMError(f"claude CLI exit {p.returncode}: {(err or out).strip()[:150]}")
    return out


# Tests replace entries here; no network is touched by the module itself.
PROVIDERS: dict[str, Callable] = {
    "gemini": _call_gemini, "groq": _call_groq, "cohere": _call_cohere, "nvidia": _call_nvidia, "claude": _call_claude}


def _status(e: Exception) -> Optional[int]:
    # google-genai: .status is a string ("RESOURCE_EXHAUSTED"), the int lives in .code
    return next((v for v in (getattr(e, a, None) for a in ("status", "status_code", "code")) if isinstance(v, int)), None)


def _env_num(name, default):
    try:
        return float(get_env(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


TIMEOUT = _env_num("SAT_REQUEST_TIMEOUT", 90)  # per-request cap so a hung call frees its worker
PROVIDER_TIMEOUT = {"nvidia": 150, "claude": 180}  # slower providers; override: <PROVIDER>_TIMEOUT
CONCURRENCY = {"nvidia": 4, "claude": 3}  # override: <PROVIDER>_CONCURRENCY


def _timeout(name) -> float:
    return _env_num(f"{name.upper()}_TIMEOUT", PROVIDER_TIMEOUT.get(name, TIMEOUT))


SHORT_COOLDOWN = 120  # cooldowns longer than this (daily quota) are skipped, never waited for
WINDOW, WINDOW_S = 20, 300  # health = last 20 events within 5 minutes
_SEMS: dict[str, threading.BoundedSemaphore] = {}
_limits: dict[str, int] = {}
_cooldown: dict[str, float] = {}  # provider -> monotonic time until which it is skipped
_events: dict[str, collections.deque] = {}  # provider -> deque[(t, kind, latency)] kind: ok|429|timeout|err
_inflight: collections.Counter = collections.Counter()
_total429: collections.Counter = collections.Counter()
_hl = threading.Lock()
log = logging.getLogger("sat_engine.llm")


def reset_health():
    with _hl:
        _cooldown.clear(); _events.clear(); _inflight.clear(); _total429.clear()


def _sem(name):
    with _hl:
        if name not in _SEMS:
            _limits[name] = int(_env_num(f"{name.upper()}_CONCURRENCY", CONCURRENCY.get(name, 3)))
            _SEMS[name] = threading.BoundedSemaphore(max(1, _limits[name]))
        return _SEMS[name]


def _record(name, kind, latency):
    with _hl:
        _events.setdefault(name, collections.deque(maxlen=WINDOW)).append((time.monotonic(), kind, latency))
        if kind == "429":
            _total429[name] += 1


def _penalty(name) -> float:
    """0 = healthy; grows with 429/timeout/error rate, slowness and saturation of the provider's slots."""
    now = time.monotonic()
    with _hl:
        ev = [e for e in _events.get(name, ()) if now - e[0] <= WINDOW_S]
        load = _inflight[name] / max(1, _limits.get(name, 3))
    if not ev:
        return load
    n = len(ev)
    r429 = sum(e[1] == "429" for e in ev) / n
    bad = sum(e[1] in ("timeout", "err") for e in ev) / n
    lat = [e[2] for e in ev if e[1] == "ok"]
    slow = min(sum(lat) / len(lat) / 60, 2) if lat else 0
    return r429 * 4 + bad * 2 + slow + load


def stats() -> dict:
    now = time.monotonic()
    with _hl:
        return {n: {"inflight": _inflight[n], "n429": _total429[n],
                    "cooldown_s": max(0, round(_cooldown.get(n, 0) - now)),
                    "calls": len(_events.get(n, ()))} for n in sorted(set(DEFAULT_ORDER) | set(_events))}


def _retry_after(e) -> float:
    """Seconds from a Retry-After header or a 'retry in 10h56m12.6s' style message (0 if absent)."""
    h = getattr(getattr(e, "response", None), "headers", None)
    try:
        if h and h.get("retry-after"):
            return float(h["retry-after"])
    except (TypeError, ValueError):
        pass
    m = re.search(r"retry in ((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)", str(e))
    if not m or not m.group(1):
        return 0.0
    return sum(float(v) * {"h": 3600, "m": 60, "s": 1}[u] for v, u in re.findall(r"([\d.]+)([hms])", m.group(1)))


def _call_with_retry(name, prompt, system, images, json_mode, fast_fail=False) -> str:
    """fast_fail: another provider is healthy, so give up on the first 429/5xx/timeout instead of backing off."""
    for attempt in range(MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            with _sem(name):
                with _hl:
                    _inflight[name] += 1
                try:
                    out = PROVIDERS[name](prompt, system, images, json_mode)
                finally:
                    with _hl:
                        _inflight[name] -= 1
            _record(name, "ok", time.monotonic() - t0)
            _cooldown.pop(name, None)
            return out
        except Exception as e:  # noqa: BLE001 - SDKs raise varied types
            s = _status(e)
            wait = _retry_after(e)
            timeout = "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower()
            retryable = s == 429 or (s is not None and s >= 500) or timeout
            _record(name, "429" if s == 429 else "timeout" if timeout else "err", time.monotonic() - t0)
            if s == 429 and (wait > 60 or fast_fail or attempt >= MAX_RETRIES):
                _cooldown[name] = time.monotonic() + min(wait or 20, 3600)
                raise
            if retryable and attempt < MAX_RETRIES and not fast_fail:
                time.sleep(max(BACKOFF_BASE ** attempt, min(wait, 30)))
                continue
            raise


def _run(prompt, system, images, providers, json_mode, accept: Callable[[str], object], exclude=()):
    errors, keyed = [], []
    for name in providers or default_order():
        if name not in PROVIDERS:
            raise ValueError(f"unknown provider {name!r}")
        if name in KEY_ENV and not get_env(KEY_ENV[name]) and PROVIDERS[name].__module__ == __name__:
            errors.append(f"{name}: no key")
        else:
            keyed.append(name)
    ex = {e for e in exclude or () if e}
    if ex and any(n in ex for n in keyed):  # HARD exclusion (judge != generator); same provider only if nothing else is configured
        others = [n for n in keyed if n not in ex]
        if others:
            keyed = others
        else:
            log.warning("no provider other than %s is configured: judge == generator", ",".join(sorted(ex)))
    tried, deadline = set(), time.monotonic() + 600
    while True:
        now = time.monotonic()
        pending = [n for n in keyed if n not in tried]
        avail = [n for n in pending if _cooldown.get(n, 0) <= now]
        if not avail:
            waits = [_cooldown[n] - now for n in pending if 0 < _cooldown[n] - now <= SHORT_COOLDOWN]
            if waits and now < deadline:  # every provider is cooling briefly: wait for the first one instead of failing
                time.sleep(min(min(waits), 15))
                continue
            errors += [f"{n}: quota cooldown" for n in pending]
            break
        avail.sort(key=lambda n: round(_penalty(n)))  # stable: ties keep the caller's order (judge != generator)
        name = avail[0]
        tried.add(name)
        try:
            res = accept(_call_with_retry(name, prompt, system, images, json_mode, fast_fail=len(avail) > 1))
            _last.info = {"provider": name, "model": model_for(name)}
            return res, name
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            log.warning("provider %s (%s) failed: %s", name, model_for(name), msg)
            errors.append(f"{name}: {msg}")
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


def generate_text(prompt, system=None, providers=None, images=None, exclude=()) -> str:
    return _run(prompt, system, images, providers, False, lambda t: t.strip() or _fail("empty response"), exclude)[0]


def _fail(msg):
    raise ValueError(msg)


def generate_json(prompt, system=None, schema: Optional[Type[BaseModel]] = None, providers=None, images=None, exclude=()) -> dict:
    """Returns a dict (validated and re-dumped when `schema` is given); falls back across providers on failure."""
    def accept(text):
        data = parse_json(text)
        if schema is not None:
            try:
                return schema.model_validate(data).model_dump()
            except ValidationError as e:
                raise ValueError(f"schema validation failed: {e.error_count()} errors") from e
        return data

    return _run(prompt, system, images, providers, True, accept, exclude)[0]
