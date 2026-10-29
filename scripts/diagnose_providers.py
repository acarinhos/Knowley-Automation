"""Live LLM provider diagnosis: one tiny ping per provider/model, then ONE production-size prompt per provider.

Usage: python scripts/diagnose_providers.py --yes [--ping-only]
Sends LIVE requests; --yes is required. Uses the keys in .env (worktree root or any parent folder). Never prints key values: every error goes through
MultiLLMManager._safe_error_summary. Costs ~15 requests in total.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

for _dir in (ROOT, *ROOT.parents):
    if (_dir / ".env").is_file():
        load_dotenv(_dir / ".env")
        break

from core import llm_manager as lm  # noqa: E402
from core.generator import SYSTEM_PROMPT, build_prompt  # noqa: E402

PING_SYSTEM = "You are a health check."
PING_USER = 'Reply with the JSON {"ok": true} and nothing else.'
KEYS = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "cohere": "COHERE_API_KEY", "nvidia": "NVIDIA_API_KEY"}

manager = lm.MultiLLMManager()


def _status(exc: Exception):
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status


def call(provider: str, model: str, system: str, user: str, max_tokens: int) -> str:
    """One request to one model with the manager's own client/timeout settings."""
    if provider == "gemini":
        from google.genai import types
        cfg = {"system_instruction": system, "response_mime_type": "application/json", "temperature": 0.7,
               "max_output_tokens": max_tokens, "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True)}
        return manager.gemini_client.models.generate_content(model=model, contents=user, config=cfg).text or ""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if provider == "groq":
        r = manager.groq_client.chat.completions.create(
            model=model, messages=messages, response_format={"type": "json_object"}, max_tokens=max_tokens, temperature=0.7)
        return r.choices[0].message.content or ""
    if provider == "cohere":
        r = manager.cohere_client.chat(model=model, messages=messages, response_format={"type": "json_object"},
                                       max_tokens=max_tokens, temperature=0.7)
        return r.message.content[0].text
    if provider == "nvidia":
        r = lm.requests.post(
            f"{manager.nvidia_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {manager.nvidia_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "response_format": {"type": "json_object"},
                  "max_tokens": max_tokens, "temperature": 0.7},
            timeout=lm._float_env("NVIDIA_TIMEOUT_SECONDS", 30.0))
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""
    if provider == "claude":
        return lm.run_claude_cli(system, user, model, lm._float_env("CLAUDE_TIMEOUT_SECONDS", 60.0))
    raise ValueError(provider)


def run(kind: str, provider: str, model: str, system: str, user: str, max_tokens: int) -> None:
    start = time.monotonic()
    try:
        out = call(provider, model, system, user, max_tokens)
        status, detail = "OK", f"{len(out)} chars"
    except Exception as exc:  # noqa: BLE001 - diagnosis must report every failure kind
        text = manager._safe_error_summary(exc)
        status = f"FAIL HTTP {_status(exc)}" if _status(exc) else f"FAIL {type(exc).__name__}"
        nums = re.findall(r"(?i)\b(Limit|Requested|Used)\s+(\d+)", str(exc))  # Groq TPM figures: numbers only
        detail = text + (f" | {', '.join(f'{k} {v}' for k, v in nums)}" if nums else "")
    print(f"{kind:5} {provider:7} {model:28} {time.monotonic() - start:6.1f}s  {status}  {detail}", flush=True)


def main() -> None:
    ping_only = "--ping-only" in sys.argv
    print("WARNING: this script sends LIVE requests to the configured LLM providers (may use quota/cost).")
    print("key defined:", {p: bool(os.getenv(env)) for p, env in KEYS.items()}, "claude_ready:", lm.claude_ready())
    # Same defaults as core/llm_manager.py client construction.
    print("timeouts: GEMINI_TIMEOUT_MS=%s GROQ=%ss COHERE=%ss NVIDIA=%ss" % (
        lm._int_env("GEMINI_TIMEOUT_MS", 60000), lm._float_env("GROQ_TIMEOUT_SECONDS", 15.0),
        lm._float_env("COHERE_TIMEOUT_SECONDS", 45.0), lm._float_env("NVIDIA_TIMEOUT_SECONDS", 30.0)))
    models = {"gemini": manager.gemini_models, "groq": manager.groq_models, "cohere": manager.cohere_models,
              "nvidia": manager.nvidia_models, "claude": manager.claude_models[:1]}
    active = [p for p in models if p in manager.providers]
    n_pings = sum(len(models[p]) for p in active)
    n_requests = n_pings if ping_only else n_pings + len(active) + 1  # + one prompt per provider, + extra true_false
    print(f"will send ~{n_requests} live requests")
    if "--yes" not in sys.argv:
        sys.exit("Refusing to send live requests without --yes (add --yes to confirm).")
    for provider in active:
        for model in models[provider]:
            run("ping", provider, model, PING_SYSTEM, PING_USER, 32)
    if ping_only:
        return
    prompts = {qt: build_prompt("Kimya", target_subcategory="Temel Kimya", question_type=qt,
                                true_false_answer=True if qt == "true_false" else None, languages=["en"])
               for qt in ("multiple_choice", "true_false")}
    for qt, prompt in prompts.items():
        print(f"{qt}: system+user ~{(len(SYSTEM_PROMPT) + len(prompt)) // 4} tokens (chars/4)")
    for provider in active:  # one request per provider (first model); true_false only on the first provider
        for qt, prompt in prompts.items():
            if qt == "true_false" and provider != active[0]:
                continue
            run(qt[:5], provider, models[provider][0], SYSTEM_PROMPT, prompt, 8192 if provider == "gemini" else 1800)


if __name__ == "__main__":
    main()
