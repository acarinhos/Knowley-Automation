"""Translate character display titles/roles using the configured Gemini Free-tier only."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "global_character_source.json"
OUTPUT_PATH = ROOT / "data" / "global_character_locales.json"
MODEL = "gemini-3.5-flash-lite"
LOCALES = (
    "en", "de-DE", "ar", "bn-BD", "da-DK", "id", "fa-IR", "nl-NL", "fil",
    "fi-FI", "fr-FR", "hi-IN", "ja-JP", "ko-KR", "pl-PL", "hu-HU", "ms-MY",
    "no-NO", "pt-BR", "ro", "ru-RU", "th", "tr-TR", "uk", "ur", "vi",
    "el-GR", "cs-CZ", "zh-CN", "zh-TW", "iw-IL", "es-419", "es-ES", "sv-SE", "it-IT",
)
LANGUAGE_NAMES = {
    "en": "English", "de-DE": "German", "ar": "Arabic", "bn-BD": "Bengali (Bangladesh)",
    "da-DK": "Danish", "id": "Indonesian", "fa-IR": "Persian", "nl-NL": "Dutch",
    "fil": "Filipino", "fi-FI": "Finnish", "fr-FR": "French (France)", "hi-IN": "Hindi",
    "ja-JP": "Japanese", "ko-KR": "Korean", "pl-PL": "Polish", "hu-HU": "Hungarian",
    "ms-MY": "Malay (Malaysia)", "no-NO": "Norwegian", "pt-BR": "Portuguese (Brazil)",
    "ro": "Romanian", "ru-RU": "Russian", "th": "Thai", "tr-TR": "Turkish",
    "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese", "el-GR": "Greek",
    "cs-CZ": "Czech", "zh-CN": "Chinese (Simplified)", "zh-TW": "Chinese (Traditional)",
    "iw-IL": "Hebrew", "es-419": "Spanish (Latin America)", "es-ES": "Spanish (Spain)",
    "sv-SE": "Swedish", "it-IT": "Italian",
}


def load_dotenv_key(key_name: str) -> str:
    value = os.environ.get(key_name, "").strip()
    if value:
        return value.strip("\"'")
    env_file = ROOT / ".env"
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, candidate = line.split("=", 1)
        if key.strip() == key_name:
            return candidate.strip().strip("\"'")
    return ""


def collect_display_texts(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    texts: dict[str, str] = {}
    for row in rows:
        category = str(row.get("category") or "General Knowledge")
        subcategory = str(row.get("subcategory") or category)
        source_title = str(row.get("role") or "").strip() or f"{subcategory} Uzmanı"
        source_role = f"{category} kategorisinde, {subcategory} alanına odaklanan küresel karakter."
        texts[source_title] = "title"
        texts[source_role] = "role"
    return list(texts.items())


def translate_batch(api_key: str, batch: list[tuple[str, str]], offset: int) -> dict[str, dict[str, str]]:
    prompt = {
        "task": "Translate each Turkish character title or role naturally for a trivia game. Keep titles concise and preserve factual meaning. Translate into every listed locale, including English. Keep proper names in roles as written. Return valid JSON only.",
        "locales": {code: LANGUAGE_NAMES[code] for code in LOCALES},
        "items": [{"id": str(offset + index), "kind": kind, "source": source} for index, (source, kind) in enumerate(batch)],
        "required_output": {"items": [{"id": str(offset), "translations": {code: "localized text" for code in LOCALES}}]},
    }
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "items": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "id": {"type": "STRING"},
                                "translations": {
                                    "type": "OBJECT",
                                    "properties": {locale: {"type": "STRING"} for locale in LOCALES},
                                    "required": list(LOCALES),
                                },
                            },
                            "required": ["id", "translations"],
                        },
                    }
                },
                "required": ["items"],
            },
            "temperature": 0.2,
            "maxOutputTokens": 32768,
        },
    }, ensure_ascii=False).encode("utf-8")
    url = "https://generativelanguage.googleapis.com/v1beta/models/" + MODEL + ":generateContent?" + urlencode({"key": api_key})
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # Never print the request URL or API key.
        message = ""
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
            message = str(error_body.get("error", {}).get("message", ""))[:240]
        except Exception:
            pass
        raise RuntimeError(f"Gemini Free-tier request returned HTTP {exc.code}: {message}") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Gemini Free-tier request failed: {type(exc).__name__}") from None

    try:
        content = payload["candidates"][0]["content"]["parts"][0]["text"]
        translated = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        finish_reason = str(payload.get("candidates", [{}])[0].get("finishReason", "unknown"))
        fragment = locals().get("content", "")
        if isinstance(exc, json.JSONDecodeError) and fragment:
            start = max(0, exc.pos - 80)
            fragment = repr(fragment[start : exc.pos + 80])
        else:
            fragment = ""
        raise RuntimeError(f"Gemini returned an unreadable/partial translation response ({finish_reason}); context={fragment}") from exc
    items = translated.get("items") if isinstance(translated, dict) else None
    if isinstance(items, dict):
        items = [{"id": index, **(item if isinstance(item, dict) else {})} for index, item in items.items()]
    if not isinstance(items, list):
        raise RuntimeError("Gemini returned no items array.")

    result: dict[str, dict[str, str]] = {}
    index_to_source = {str(offset + index): source for index, (source, _kind) in enumerate(batch)}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        source = index_to_source.get(item_id)
        values = item.get("translations")
        if not source or not isinstance(values, dict):
            continue
        normalized = {locale: str(values.get(locale) or "").strip() for locale in LOCALES}
        if any(not value for value in normalized.values()):
            absent = [locale for locale, value in normalized.items() if not value]
            raise RuntimeError(f"Gemini omitted locales {absent[:5]} for display text {source!r}.")
        result[source] = normalized
    if len(result) != len(batch):
        raise RuntimeError(f"Gemini returned {len(result)} of {len(batch)} requested display strings.")
    return result


def main() -> int:
    api_key = load_dotenv_key("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not configured; no translations were requested.")
    source_rows = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    entries = collect_display_texts(source_rows)
    translations: dict[str, dict[str, str]] = {}
    batch_size = 16
    for start in range(0, len(entries), batch_size):
        batch = entries[start : start + batch_size]
        translations.update(translate_batch(api_key, batch, start))
        print(f"Translated {min(start + len(batch), len(entries))}/{len(entries)} character display strings via Gemini Free tier only.", flush=True)
        if start + batch_size < len(entries):
            time.sleep(4.5)  # Stay comfortably below the project's 15 RPM free-tier ceiling.
    OUTPUT_PATH.write_text(json.dumps(translations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(translations)} display strings × {len(LOCALES)} locales. API key not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
