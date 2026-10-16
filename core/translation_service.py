"""Azure-first translation for generated multiple-choice questions."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
import uuid
from collections import deque
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import requests
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger("QuestionTranslation")

_LANGUAGE_NAMES = {
    "İngilizce": ("en", "English"),
    "Almanca": ("de", "German"),
    "Arapça": ("ar", "Arabic"),
    "Bengalce": ("bn", "Bengali (Bangladesh)"),
    "Danca": ("da", "Danish"),
    "Endonezce": ("id", "Indonesian"),
    "Farsça": ("fa", "Persian"),
    "Felemenkçe": ("nl", "Dutch"),
    "Filipince": ("fil", "Filipino"),
    "Fince": ("fi", "Finnish"),
    "Fransızca": ("fr", "French"),
    "Hintçe": ("hi", "Hindi"),
    "Japonca": ("ja", "Japanese"),
    "Korece": ("ko", "Korean"),
    "Lehçe": ("pl", "Polish"),
    "Macarca": ("hu", "Hungarian"),
    "Malayca": ("ms", "Malay (Malaysia)"),
    "Norveççe": ("no", "Norwegian"),
    "Portekizce": ("pt", "Portuguese (Brazil)"),
    "Rumence": ("ro", "Romanian"),
    "Rusça": ("ru", "Russian"),
    "Tayca": ("th", "Thai"),
    "Türkçe": ("tr", "Turkish"),
    "Ukraynaca": ("uk", "Ukrainian"),
    "Urduca": ("ur", "Urdu"),
    "Vietnamca": ("vi", "Vietnamese"),
    "Yunanca": ("el", "Greek"),
    "Çekçe": ("cs", "Czech"),
    "Çince (Mandarin)": ("zh", "Chinese (Simplified)"),
    "Çince (Geleneksel)": ("zh-TW", "Chinese (Traditional)"),
    "İbranice": ("he", "Hebrew"),
    "İspanyolca (Latin Amerika)": ("es-LA", "Spanish (Latin America)"),
    "İspanyolca": ("es", "Spanish (Spain)"),
    "İsveççe": ("sv", "Swedish"),
    "İtalyanca": ("it", "Italian"),
}


def _language_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    text = text.replace("ı", "i")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text)


LANGUAGE_NAME_TO_CODE = {
    _language_key(name): code for name, (code, _english_name) in _LANGUAGE_NAMES.items()
}
LANGUAGE_NAME_TO_CODE[_language_key("Romence")] = "ro"  # Sheets spelling variant of Rumence
LANGUAGE_CODE_TO_NAME = {
    code: english_name for code, english_name in _LANGUAGE_NAMES.values()
}
LANGUAGE_CODE_TO_SHEET_NAME = {
    code: name for name, (code, _english_name) in _LANGUAGE_NAMES.items()
}
SUPPORTED_LANGUAGE_CODES = tuple(LANGUAGE_CODE_TO_NAME)
COUNTRY_LANGUAGE_CODES = set(SUPPORTED_LANGUAGE_CODES)
_AZURE_REQUEST_USAGE: deque[tuple[float, int]] = deque()
_AZURE_USAGE_LOCK = Lock()
_AZURE_COOLDOWN_UNTIL = 0.0
_AZURE_DISABLED_REASON = ""
# Canonical (short) code -> Azure code, only where they differ.
AZURE_LANGUAGE_ALIASES = {
    "no": "nb",
    "zh": "zh-Hans",
    "zh-TW": "zh-Hant",
    "es-LA": "es",
}
# Legacy / regional spellings seen in stored data -> canonical short code (Latin-American Spanish is `es-LA`).
LANGUAGE_CODE_ALIASES = {
    **{old: new for old, new in {'de-DE': 'de', 'bn-BD': 'bn', 'da-DK': 'da', 'fa-IR': 'fa', 'nl-NL': 'nl', 'fi-FI': 'fi', 'fr-FR': 'fr', 'hi-IN': 'hi', 'ja-JP': 'ja', 'ko-KR': 'ko', 'pl-PL': 'pl', 'hu-HU': 'hu', 'ms-MY': 'ms', 'no-NO': 'no', 'pt-BR': 'pt', 'ru-RU': 'ru', 'tr-TR': 'tr', 'el-GR': 'el', 'cs-CZ': 'cs', 'zh-CN': 'zh', 'iw-IL': 'he', 'es-419': 'es-LA', 'es-ES': 'es', 'sv-SE': 'sv', 'it-IT': 'it'}.items()},
    "nb": "no", "he": "he", "iw": "he", "zh-Hans": "zh", "zh-Hant": "zh-TW", "es-419": "es-LA",
}


def canonical_language_code(code: Any) -> str:
    """Short canonical language code for a stored/Sheets code; unknown codes are returned unchanged."""
    text = str(code or "").strip()
    return LANGUAGE_CODE_ALIASES.get(text, text)


def azure_character_budget_per_minute() -> int:
    """Return a conservative rolling-minute request budget, configurable per subscription."""
    try:
        return max(
            1,
            int(os.getenv("AZURE_TRANSLATOR_CHARACTERS_PER_MINUTE", "30000")),
        )
    except (TypeError, ValueError):
        return 30_000


def _wait_for_azure_character_budget(characters: int) -> None:
    limit = azure_character_budget_per_minute()
    if characters <= 0 or characters > limit:
        return
    while True:
        with _AZURE_USAGE_LOCK:
            now = time.monotonic()
            while _AZURE_REQUEST_USAGE and _AZURE_REQUEST_USAGE[0][0] <= now - 60:
                _AZURE_REQUEST_USAGE.popleft()
            used = sum(amount for _at, amount in _AZURE_REQUEST_USAGE)
            if used + characters <= limit:
                _AZURE_REQUEST_USAGE.append((now, characters))
                return
            delay = max(0.5, _AZURE_REQUEST_USAGE[0][0] + 60 - now)
        time.sleep(delay)


def language_codes_from_sheet(value: Any, country_code: Any = None) -> list[str]:
    """Convert the Turkish language labels in the country list to BCP-47 tags."""
    if not value:
        return ["en"]
    codes: list[str] = []
    normalized_country = str(country_code or "").strip().upper()
    for label in re.split(r"\s*,\s*", str(value).strip()):
        code = LANGUAGE_NAME_TO_CODE.get(_language_key(label))
        if _language_key(label) == _language_key("İspanyolca"):
            code = "es-LA" if normalized_country == "LATAM" else "es"
        if code and code not in codes:
            codes.append(code)
    if "en" not in codes:
        codes.append("en")
    return codes


def _clean_content(content: Mapping[str, Any], fallback_filter_tag: str) -> Dict[str, Any]:
    options = content.get("options")
    options = options if isinstance(options, Mapping) else {}
    clarified_options = content.get("clarified_options")
    clarified_options = clarified_options if isinstance(clarified_options, Mapping) else None
    binary_fallback = content.get("binary_fallback")
    if isinstance(binary_fallback, Mapping):
        cleaned_binary = {
            "statement": str(binary_fallback.get("statement") or "").strip(),
            "is_true": bool(binary_fallback.get("is_true", False)),
        }
    else:
        cleaned_binary = None
    return {
        "question": str(content.get("question") or "").strip(),
        "options": {letter: str(options.get(letter) or "").strip() for letter in "ABCD"},
        "explanation": str(content.get("explanation") or "").strip(),
        "filter_tag": str(content.get("filter_tag") or fallback_filter_tag).strip(),
        "hint": str(content.get("hint") or "").strip(),
        "distractor_trap_note": str(content.get("distractor_trap_note") or "").strip(),
        "keyword": str(content.get("keyword") or "").strip(),
        "binary_fallback": cleaned_binary,
        "simplified_question": str(content.get("simplified_question") or "").strip(),
        "clarified_options": (
            {letter: str(clarified_options.get(letter) or "").strip() for letter in "ABCD"}
            if clarified_options is not None
            else None
        ),
    }


@lru_cache(maxsize=4)
def _azure_supported_languages(endpoint: str) -> set[str]:
    """Fetch the current Azure text-translation language catalog once per endpoint."""
    try:
        response = requests.get(
            f"{endpoint.rstrip('/')}/languages",
            params={"api-version": "3.0", "scope": "translation"},
            timeout=(5, 20),
        )
        response.raise_for_status()
        translation = response.json().get("translation")
        if not isinstance(translation, Mapping):
            raise ValueError("Azure language catalog omitted its translation list")
        return {str(language) for language in translation}
    except Exception as exc:
        logger.warning(
            "Could not load Azure's supported-language catalog; attempting all project languages: %s",
            str(exc)[:180],
        )
        return set(SUPPORTED_LANGUAGE_CODES)


def azure_translate_localized_batch(
    source_contents: Sequence[Mapping[str, Any]],
    source_language: str,
    target_languages: Sequence[str],
) -> Tuple[list[Dict[str, Dict[str, Any]]], list[list[str]], str]:
    """Translate several items per Azure request; return per-item languages needing fallback."""
    key = os.getenv("AZURE_TRANSLATOR_KEY", "").strip()
    if not key:
        raise RuntimeError("AZURE_TRANSLATOR_KEY is not configured")

    targets = [lang for lang in dict.fromkeys(target_languages) if lang != source_language]
    sources = [_clean_content(content, "trivia") for content in source_contents]
    results: list[Dict[str, Dict[str, Any]]] = [{} for _ in sources]
    if not sources:
        return results, [], ""
    failed_by_source: list[list[str]] = [[] for _ in sources]
    if not targets:
        return results, failed_by_source, ""

    field_names = [
        "question", "A", "B", "C", "D", "explanation", "filter_tag",
        "hint", "distractor_trap_note", "keyword", "binary_fallback.statement",
        "simplified_question", "clarified_A", "clarified_B", "clarified_C", "clarified_D",
    ]
    fields_by_source = [
        [
            source["question"],
            source["options"]["A"],
            source["options"]["B"],
            source["options"]["C"],
            source["options"]["D"],
            source["explanation"],
            source["filter_tag"],
            source["hint"],
            source["distractor_trap_note"],
            source["keyword"],
            (source["binary_fallback"] or {}).get("statement", ""),
            source["simplified_question"],
            *[
                (source["clarified_options"] or {}).get(letter, "")
                for letter in "ABCD"
            ],
        ]
        for source in sources
    ]
    fields = [field for item_fields in fields_by_source for field in item_fields]
    endpoint = os.getenv(
        "AZURE_TRANSLATOR_ENDPOINT",
        "https://api.cognitive.microsofttranslator.com",
    ).strip().rstrip("/")
    azure_languages = _azure_supported_languages(endpoint)
    target_api_codes = {
        lang: lang if lang in azure_languages else AZURE_LANGUAGE_ALIASES.get(lang, "")
        for lang in targets
    }
    unsupported_targets = [
        lang for lang in targets if target_api_codes[lang] not in azure_languages
    ]
    targets = [lang for lang in targets if target_api_codes[lang] in azure_languages]
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/json; charset=UTF-8",
        "X-ClientTraceId": str(uuid.uuid4()),
    }
    region = os.getenv("AZURE_TRANSLATOR_REGION", "").strip()
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region

    failures: list[str] = []
    if unsupported_targets:
        failures.append(
            "Azure does not support: " + ",".join(unsupported_targets)
        )
        for failed_languages in failed_by_source:
            failed_languages.extend(unsupported_targets)
    source_characters = max(1, sum(len(text) for text in fields))
    # Azure's character limit is aggregated across target languages. Keep
    # requests below that cap, with headroom for encoded text and accounting.
    request_character_limit = min(45_000, azure_character_budget_per_minute())
    languages_per_request = max(1, min(10, request_character_limit // source_characters))
    global _AZURE_COOLDOWN_UNTIL, _AZURE_DISABLED_REASON
    for start in range(0, len(targets), languages_per_request):
        batch_targets = targets[start : start + languages_per_request]
        api_to_canonical = {target_api_codes[lang]: lang for lang in batch_targets}
        request_characters = source_characters * len(batch_targets)
        with _AZURE_USAGE_LOCK:
            azure_cooldown = _AZURE_COOLDOWN_UNTIL
        if azure_cooldown > time.monotonic():
            for failed_languages in failed_by_source:
                failed_languages.extend(batch_targets)
            if azure_cooldown == float("inf"):
                failures.append(
                    f"Azure disabled: {_AZURE_DISABLED_REASON or 'access unavailable'}; skipped remaining requests"
                )
            else:
                failures.append(
                    f"Azure rate limit cooldown ({int(azure_cooldown - time.monotonic())}s remaining)"
                )
            continue
        params = [("api-version", "3.0"), ("from", source_language)]
        params.extend(("to", target_api_codes[lang]) for lang in batch_targets)
        try:
            for attempt in range(2):
                try:
                    _wait_for_azure_character_budget(request_characters)
                    response = requests.post(
                        f"{endpoint}/translate",
                        params=params,
                        headers=headers,
                        json=[{"Text": text} for text in fields],
                        timeout=(5, 30),
                    )
                    response.raise_for_status()
                    break
                except requests.HTTPError as http_error:
                    status_code = getattr(
                        getattr(http_error, "response", None), "status_code", None
                    )
                    if status_code != 429 or attempt == 1:
                        if status_code == 429:
                            with _AZURE_USAGE_LOCK:
                                _AZURE_COOLDOWN_UNTIL = time.monotonic() + 300
                        raise
                    retry_after = getattr(http_error.response, "headers", {}).get(
                        "Retry-After", ""
                    )
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = 3.0
                    delay = min(30.0, max(1.0, delay))
                    logger.warning(
                        "Azure Translator rate-limited a batch; one short retry in %.0fs (%d/1)",
                        delay,
                        attempt + 1,
                    )
                    time.sleep(delay)
            body = response.json()
            if not isinstance(body, list) or len(body) != len(fields):
                raise ValueError("Azure Translator returned an unexpected response shape")

            fields_by_item_and_language: list[Dict[str, list[Optional[str]]]] = [
                {lang: [None] * len(field_names) for lang in batch_targets}
                for _source in sources
            ]
            for field_index, item in enumerate(body):
                translations = item.get("translations") if isinstance(item, Mapping) else None
                if not isinstance(translations, list):
                    continue
                source_index = field_index // len(field_names)
                item_field_index = field_index % len(field_names)
                for translation in translations:
                    if not isinstance(translation, Mapping):
                        continue
                    language = str(translation.get("to") or "")
                    language = api_to_canonical.get(language, language)
                    text = str(translation.get("text") or "").strip() or None
                    if language in batch_targets:
                        fields_by_item_and_language[source_index][language][
                            item_field_index
                        ] = text

            for source_index, translated_by_language in enumerate(
                fields_by_item_and_language
            ):
                for language, translated_fields in translated_by_language.items():
                    source = sources[source_index]
                    required_fields = translated_fields[:7]
                    optional_sources = [
                        source["hint"],
                        source["distractor_trap_note"],
                        source["keyword"],
                        (source["binary_fallback"] or {}).get("statement", ""),
                        source["simplified_question"],
                        *[
                            (source["clarified_options"] or {}).get(letter, "")
                            for letter in "ABCD"
                        ],
                    ]
                    optional_fields = translated_fields[7:]
                    if any(not text for text in required_fields) or any(
                        source_text and not translated_text
                        for source_text, translated_text in zip(optional_sources, optional_fields)
                    ):
                        failed_by_source[source_index].append(language)
                        continue
                    localized = {
                        "question": translated_fields[0],
                        "options": dict(zip("ABCD", translated_fields[1:5])),
                        "explanation": translated_fields[5],
                        "filter_tag": translated_fields[6],
                        "hint": translated_fields[7] or source["hint"],
                        "distractor_trap_note": translated_fields[8] or source["distractor_trap_note"],
                        "keyword": translated_fields[9] or source["keyword"],
                    }
                    binary = source["binary_fallback"]
                    localized["binary_fallback"] = (
                        {"statement": translated_fields[10] or binary["statement"], "is_true": binary["is_true"]}
                        if binary
                        else None
                    )
                    localized["simplified_question"] = translated_fields[11] or source["simplified_question"]
                    clarified_options = source["clarified_options"]
                    localized["clarified_options"] = (
                        dict(zip("ABCD", translated_fields[12:16]))
                        if clarified_options is not None
                        else None
                    )
                    results[source_index][language] = localized
        except Exception as exc:
            failures.append(f"{','.join(batch_targets)}: {str(exc)[:180]}")
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {401, 403}:
                # Stop retrying after authentication/authorization failures.
                with _AZURE_USAGE_LOCK:
                    _AZURE_COOLDOWN_UNTIL = float("inf")
                    _AZURE_DISABLED_REASON = (
                        "authentication failed (HTTP 401)"
                        if status_code == 401
                        else "forbidden or quota limit (HTTP 403)"
                    )
            for failed_languages in failed_by_source:
                failed_languages.extend(batch_targets)
            if status_code in {401, 403, 408, 429, 500, 502, 503, 504}:
                for remaining_start in range(start + languages_per_request, len(targets), languages_per_request):
                    remaining_targets = targets[
                        remaining_start : remaining_start + languages_per_request
                    ]
                    for failed_languages in failed_by_source:
                        failed_languages.extend(remaining_targets)
                break
    for failed_languages in failed_by_source:
        failed_languages[:] = list(dict.fromkeys(failed_languages))
    return results, failed_by_source, "; ".join(failures)


def _azure_translate(
    source_content: Mapping[str, Any],
    source_language: str,
    target_languages: Sequence[str],
) -> Tuple[Dict[str, Dict[str, Any]], list[str], str]:
    results, failures, error = azure_translate_localized_batch(
        [source_content], source_language, target_languages
    )
    result = results[0] if results else {}
    failed = failures[0] if failures else []
    if not [lang for lang in target_languages if lang != source_language] and not failed:
        result[source_language] = _clean_content(source_content, "trivia")
    return result, failed, error


def _translation_map_from_response(
    data: Any,
    target_languages: Sequence[str],
    *,
    item_ids: Optional[Sequence[str]] = None,
) -> Optional[Mapping[str, Any]]:
    """Accept the required wrapper and repair recognizable wrapper omissions."""
    if not isinstance(data, Mapping):
        return None
    translations = data.get("translations")
    if isinstance(translations, Mapping):
        return translations

    # Some JSON-mode providers occasionally omit the outer key even when the
    # schema and prompt require it. Recover only recognizable locale/item maps.
    if item_ids is not None:
        if all(str(item_id) in data and isinstance(data[str(item_id)], Mapping) for item_id in item_ids):
            return {str(item_id): data[str(item_id)] for item_id in item_ids}
        return None
    if any(language in data and isinstance(data[language], Mapping) for language in target_languages):
        return {
            language: data[language]
            for language in target_languages
            if language in data and isinstance(data[language], Mapping)
        }
    return None


def _generate_translation_response(
    llm_manager: Any,
    prompt: str,
    response_schema: Mapping[str, Any],
    target_languages: Sequence[str],
    *,
    item_ids: Optional[Sequence[str]] = None,
) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Rotate past providers that return valid JSON in the wrong shape."""
    provider_count = len(getattr(llm_manager, "providers", ()) or ())
    attempts = max(1, provider_count)
    attempted: list[str] = []
    last_keys: list[str] = []

    for _attempt in range(attempts):
        data = llm_manager.generate_trivia(
            "You are a precise trivia localization engine. Return valid JSON only.",
            prompt,
            response_schema=dict(response_schema),
        )
        if isinstance(data, Mapping):
            provider = str(data.get("_provider") or "LLM").upper()
            last_keys = [str(key) for key in data.keys() if not str(key).startswith("_")][:12]
        else:
            provider = "LLM"
            last_keys = []
        translations = _translation_map_from_response(
            data, target_languages, item_ids=item_ids
        )
        if translations is not None:
            if translations is not data.get("translations"):
                data = {**data, "translations": dict(translations)}
            return data, translations

        attempted.append(provider)
        logger.warning(
            "Translation provider %s returned JSON without the requested translation map; "
            "trying the next configured provider (%d/%d). JSON keys: %s",
            provider,
            len(attempted),
            attempts,
            ", ".join(last_keys) or "none",
        )

    raise ValueError(
        "LLM fallback did not return a translations object after trying "
        f"{', '.join(attempted) or 'the configured provider'}; "
        f"last JSON keys: {', '.join(last_keys) or 'none'}"
    )


def _llm_translate(
    source_content: Mapping[str, Any],
    source_language: str,
    target_languages: Sequence[str],
    llm_manager: Any,
) -> Tuple[Dict[str, Dict[str, Any]], str, list[str]]:
    source = _clean_content(source_content, "trivia")
    targets = [lang for lang in dict.fromkeys(target_languages) if lang != source_language]
    if not targets:
        return {}, "LLM", []
    response_schema = _translation_response_schema(
        {lang: _translation_content_schema() for lang in targets}
    )
    prompt = json.dumps(
        {
            "task": (
                "Translate the multiple-choice trivia item without changing its facts, "
                "meaning, correct answer, or A/B/C/D option positions. Translate any non-empty "
                "hint, distractor note, keyword, and binary fallback statement. Translate the simplified "
                "question and clarified A-D options too, preserving answer mappings. Preserve binary truth value. Return only JSON."
            ),
            "source_language": source_language,
            "target_languages": {
                lang: LANGUAGE_CODE_TO_NAME.get(lang, lang) for lang in targets
            },
            "correct_option": "Preserve the source option order exactly.",
            "output_shape": (
                'Return exactly this structure: {"translations": {"locale-code": '
                '{"question": "...", "options": {"A": "...", "B": "...", '
                '"C": "...", "D": "..."}, "explanation": "...", '
                '"filter_tag": "...", "hint": "...", "distractor_trap_note": "...", '
                '"keyword": "...", "binary_fallback": {"statement": "...", "is_true": true}, '
                '"simplified_question": "...", "clarified_options": {"A": "...", "B": "...", "C": "...", "D": "..."}}}}. '
                'Omit binary_fallback when absent; preserve its boolean when present. The top-level "translations" key is required.'
            ),
            "source_content": source,
        },
        ensure_ascii=False,
    )
    data, translations = _generate_translation_response(
        llm_manager,
        prompt,
        response_schema,
        targets,
    )
    result: Dict[str, Dict[str, Any]] = {}
    missing: list[str] = []
    for lang in targets:
        content = translations.get(lang)
        if not isinstance(content, Mapping):
            missing.append(lang)
            continue
        normalized = _clean_content(content, source["filter_tag"])
        if not normalized["question"] or any(not value for value in normalized["options"].values()):
            missing.append(lang)
            continue
        if any(source[field] and not normalized[field] for field in ("hint", "distractor_trap_note", "keyword")):
            missing.append(lang)
            continue
        if source["simplified_question"] and not normalized["simplified_question"]:
            missing.append(lang)
            continue
        if source["clarified_options"] and any(
            not normalized["clarified_options"][letter] for letter in "ABCD"
        ):
            missing.append(lang)
            continue
        if source["binary_fallback"]:
            binary = normalized.get("binary_fallback")
            if not isinstance(binary, Mapping) or not binary.get("statement"):
                missing.append(lang)
                continue
            normalized["binary_fallback"] = {
                "statement": str(binary["statement"]).strip(),
                "is_true": source["binary_fallback"]["is_true"],
            }
        normalized["explanation"] = normalized["explanation"] or source["explanation"]
        result[lang] = normalized
    provider = str(data.get("_provider") or "LLM").upper()
    return result, provider, missing


def _llm_translate_batch(
    source_contents: Sequence[Mapping[str, Any]],
    source_language: str,
    target_languages: Sequence[str],
    llm_manager: Any,
) -> Tuple[list[Dict[str, Dict[str, Any]]], list[list[str]], str]:
    """Translate several independent question items in one LLM request."""
    targets = [lang for lang in dict.fromkeys(target_languages) if lang != source_language]
    sources = [_clean_content(content, "trivia") for content in source_contents]
    results: list[Dict[str, Dict[str, Any]]] = [{} for _ in sources]
    missing_by_source: list[list[str]] = [[] for _ in sources]
    if not sources or not targets:
        return results, missing_by_source, "LLM"

    content_schema = _translation_content_schema()
    item_schema = {
        str(index): _translation_response_schema(
            {lang: content_schema for lang in targets}
        )["properties"]["translations"]
        for index in range(len(sources))
    }
    response_schema = {
        "type": "OBJECT",
        "properties": {"translations": {"type": "OBJECT", "properties": item_schema}},
        "required": ["translations"],
    }
    prompt = json.dumps(
        {
            "task": (
                "Translate every multiple-choice trivia item without changing its facts, "
                "meaning, correct answer, or A/B/C/D option positions. Translate all non-empty "
                "question helper fields, simplified question, and clarified options too; preserve each binary fallback truth value. Return only JSON."
            ),
            "source_language": source_language,
            "target_languages": {
                lang: LANGUAGE_CODE_TO_NAME.get(lang, lang) for lang in targets
            },
            "output_shape": (
                'Return exactly this structure: {"translations": {"item-id": '
                '{"locale-code": {"question": "...", "options": {"A": "...", '
                '"B": "...", "C": "...", "D": "..."}, '
                '"explanation": "...", "filter_tag": "...", "hint": "...", '
                '"distractor_trap_note": "...", "keyword": "...", '
                '"binary_fallback": {"statement": "...", "is_true": true}, '
                '"simplified_question": "...", "clarified_options": {"A": "...", "B": "...", "C": "...", "D": "..."}}}}. '
                'Both the top-level "translations" key and every item id are required.'
            ),
            "source_items": [
                {"id": str(index), "source_content": source}
                for index, source in enumerate(sources)
            ],
        },
        ensure_ascii=False,
    )
    item_ids = [str(index) for index in range(len(sources))]
    data, translated_items = _generate_translation_response(
        llm_manager,
        prompt,
        response_schema,
        targets,
        item_ids=item_ids,
    )

    for index, source in enumerate(sources):
        item_translations = translated_items.get(str(index))
        if not isinstance(item_translations, Mapping):
            missing_by_source[index].extend(targets)
            continue
        for lang in targets:
            content = item_translations.get(lang)
            if not isinstance(content, Mapping):
                missing_by_source[index].append(lang)
                continue
            normalized = _clean_content(content, source["filter_tag"])
            if not normalized["question"] or any(
                not value for value in normalized["options"].values()
            ):
                missing_by_source[index].append(lang)
                continue
            if any(source[field] and not normalized[field] for field in ("hint", "distractor_trap_note", "keyword")):
                missing_by_source[index].append(lang)
                continue
            if source["simplified_question"] and not normalized["simplified_question"]:
                missing_by_source[index].append(lang)
                continue
            if source["clarified_options"] and any(
                not normalized["clarified_options"][letter] for letter in "ABCD"
            ):
                missing_by_source[index].append(lang)
                continue
            if source["binary_fallback"]:
                binary = normalized.get("binary_fallback")
                if not isinstance(binary, Mapping) or not binary.get("statement"):
                    missing_by_source[index].append(lang)
                    continue
                normalized["binary_fallback"] = {
                    "statement": str(binary["statement"]).strip(),
                    "is_true": source["binary_fallback"]["is_true"],
                }
            normalized["explanation"] = normalized["explanation"] or source["explanation"]
            results[index][lang] = normalized
    provider = str(data.get("_provider") or "LLM").upper()
    return results, missing_by_source, provider


def _translation_content_schema() -> Dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "question": {"type": "STRING"},
            "options": {
                "type": "OBJECT",
                "properties": {letter: {"type": "STRING"} for letter in "ABCD"},
                "required": list("ABCD"),
            },
            "explanation": {"type": "STRING"},
            "filter_tag": {"type": "STRING"},
            "hint": {"type": "STRING"},
            "distractor_trap_note": {"type": "STRING"},
            "keyword": {"type": "STRING"},
            "simplified_question": {"type": "STRING"},
            "clarified_options": {
                "type": "OBJECT",
                "properties": {letter: {"type": "STRING"} for letter in "ABCD"},
                "required": list("ABCD"),
            },
            "binary_fallback": {
                "type": "OBJECT",
                "properties": {
                    "statement": {"type": "STRING"},
                    "is_true": {"type": "BOOLEAN"},
                },
                "required": ["statement", "is_true"],
            },
        },
        "required": ["question", "options", "explanation", "filter_tag", "simplified_question", "clarified_options"],
    }


def _translation_response_schema(targets: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "translations": {
                "type": "OBJECT",
                "properties": dict(targets),
                "required": list(targets),
            }
        },
        "required": ["translations"],
    }


def _llm_translate_with_cooldown(
    source_content: Mapping[str, Any],
    source_language: str,
    target_languages: Sequence[str],
    llm_manager: Any,
) -> Tuple[Dict[str, Dict[str, Any]], str, list[str]]:
    """Wait for a configured provider cooldown instead of failing a whole record immediately."""
    guarded_backfill = os.getenv("GEMINI_QUOTA_GUARD", "").strip().casefold() in {"1", "true", "yes", "on"}
    attempt = 0
    while guarded_backfill or attempt < 6:
        try:
            return _llm_translate(
                source_content,
                source_language,
                target_languages,
                llm_manager,
            )
        except Exception as exc:
            retry_after = getattr(exc, "retry_after", None)
            if (
                retry_after is None
                or guarded_backfill and not getattr(exc, "quota_limited", False)
                or not guarded_backfill and attempt >= 5
            ):
                raise
            delay = max(1, int(retry_after)) if guarded_backfill else min(300, max(1, int(retry_after)))
            logger.warning(
                "LLM quota/cooldown active; retrying translation in %ds (attempt %d)",
                delay,
                attempt + 1,
            )
            time.sleep(delay)
            attempt += 1
    raise RuntimeError("LLM translation retries were exhausted")


def _llm_translate_batch_with_cooldown(
    source_contents: Sequence[Mapping[str, Any]],
    source_language: str,
    target_languages: Sequence[str],
    llm_manager: Any,
) -> Tuple[list[Dict[str, Dict[str, Any]]], list[list[str]], str]:
    guarded_backfill = os.getenv("GEMINI_QUOTA_GUARD", "").strip().casefold() in {"1", "true", "yes", "on"}
    attempt = 0
    while guarded_backfill or attempt < 2:
        try:
            return _llm_translate_batch(
                source_contents,
                source_language,
                target_languages,
                llm_manager,
            )
        except Exception as exc:
            retry_after = getattr(exc, "retry_after", None)
            if (
                retry_after is None
                or guarded_backfill and not getattr(exc, "quota_limited", False)
                or not guarded_backfill and (attempt >= 1 or int(retry_after) > 2)
            ):
                raise
            delay = max(1, int(retry_after)) if guarded_backfill else min(2, max(1, int(retry_after)))
            logger.warning(
                "LLM quota/cooldown active; retrying %d-item batch in %ds (attempt %d)",
                len(source_contents),
                delay,
                attempt + 1,
            )
            time.sleep(delay)
            attempt += 1
    raise RuntimeError("LLM translation batch retry was exhausted")


def translate_localized_content_batch(
    source_contents: Sequence[Mapping[str, Any]],
    source_language: str,
    target_languages: Iterable[str],
    *,
    llm_manager: Any = None,
    item_batch_size: int = 4,
) -> Tuple[list[Dict[str, Dict[str, Any]]], str]:
    """Translate groups of items together, retrying only incomplete items singly."""
    targets = list(dict.fromkeys(str(lang) for lang in target_languages if str(lang)))
    results: list[Dict[str, Dict[str, Any]]] = [{} for _ in source_contents]
    if not targets or not source_contents:
        return results, "SOURCE"
    if llm_manager is None:
        from core.llm_manager import get_llm_manager

        llm_manager = get_llm_manager()

    providers: list[str] = []
    batch_size = max(1, int(item_batch_size))
    for start in range(0, len(source_contents), batch_size):
        chunk = list(source_contents[start : start + batch_size])
        batch_results, missing_by_source, provider = _llm_translate_batch_with_cooldown(
            chunk,
            source_language,
            targets,
            llm_manager,
        )
        results[start : start + len(chunk)] = batch_results
        if provider not in providers:
            providers.append(provider)
        for offset, missing in enumerate(missing_by_source):
            if not missing:
                continue
            try:
                retry_result, retry_provider, still_missing = _llm_translate_with_cooldown(
                    chunk[offset],
                    source_language,
                    missing,
                    llm_manager,
                )
                results[start + offset].update(retry_result)
                if retry_provider not in providers:
                    providers.append(retry_provider)
                if still_missing:
                    logger.warning(
                        "LLM did not produce %d language(s) for batched item %d: %s",
                        len(still_missing),
                        start + offset,
                        ", ".join(still_missing),
                    )
            except Exception as exc:
                logger.warning(
                    "LLM single-item repair failed for batch item %d: %s",
                    start + offset,
                    str(exc)[:180],
                )
    return results, "+".join(providers) or "LLM"


def translate_localized_content(
    source_content: Mapping[str, Any],
    source_language: str,
    target_languages: Iterable[str],
    *,
    llm_manager: Any = None,
    prefer_azure: bool = True,
    llm_batch_size: int = 5,
) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """Translate with Azure first by default, or use the existing LLM path directly."""
    source_language = str(source_language or "en")
    targets = list(dict.fromkeys(str(lang) for lang in target_languages if str(lang)))
    if not targets:
        raise ValueError("At least one target language is required")
    source = _clean_content(source_content, "trivia")
    result: Dict[str, Dict[str, Any]] = {}
    if source_language in targets:
        result[source_language] = source
    other_targets = [lang for lang in targets if lang != source_language]
    if not other_targets:
        return result, "SOURCE"

    if prefer_azure:
        try:
            azure_result, azure_failed_targets, azure_error = _azure_translate(
                source, source_language, other_targets
            )
            result.update(azure_result)
            if not azure_failed_targets:
                return result, "AZURE_TRANSLATOR"
        except Exception as azure_exception:
            azure_result = {}
            azure_failed_targets = list(other_targets)
            azure_error = str(azure_exception)
    else:
        azure_result = {}
        azure_failed_targets = list(other_targets)
        azure_error = "Azure skipped for this operation"

    if azure_failed_targets and prefer_azure:
        azure_detail = str(azure_error or "").strip() or (
            "Azure returned no usable translation for " + ", ".join(azure_failed_targets)
        )
        logger.warning(
            "Azure translation unavailable for %d language(s) [%s]; "
            "falling back to the existing LLM translator: %s",
            len(azure_failed_targets),
            ", ".join(azure_failed_targets),
            azure_detail[:240],
        )

    if llm_manager is None:
        from core.llm_manager import get_llm_manager

        llm_manager = get_llm_manager()
    fallback_providers = []
    batch_size = max(1, int(llm_batch_size))
    for start in range(0, len(azure_failed_targets), batch_size):
        batch = azure_failed_targets[start : start + batch_size]
        try:
            fallback_result, provider, missing_languages = _llm_translate_with_cooldown(
                source, source_language, batch, llm_manager
            )
        except Exception as batch_error:
            if getattr(batch_error, "retry_after", None) is not None:
                raise
            if len(batch) == 1:
                raise
            logger.warning(
                "LLM translation batch failed; retrying its %d languages individually: %s",
                len(batch),
                str(batch_error)[:180],
            )
            languages_to_retry = batch
        else:
            result.update(fallback_result)
            if provider not in fallback_providers:
                fallback_providers.append(provider)
            if missing_languages:
                logger.warning(
                    "LLM omitted %d language(s) from a batch; retrying only: %s",
                    len(missing_languages),
                    ", ".join(missing_languages),
                )
            languages_to_retry = missing_languages

        for language in languages_to_retry:
            retry_result, retry_provider, still_missing = _llm_translate_with_cooldown(
                source, source_language, [language], llm_manager
            )
            if still_missing or language not in retry_result:
                raise ValueError(f"LLM fallback did not produce {language}")
            result.update(retry_result)
            if retry_provider not in fallback_providers:
                fallback_providers.append(retry_provider)
    if azure_result:
        fallback_providers.insert(0, "AZURE_TRANSLATOR")
    return result, "+".join(fallback_providers)
