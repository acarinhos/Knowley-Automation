"""Firestore soru koleksiyonu için hafif raporlama verisi."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional


QUESTION_REPORT_VERSION = "1.1.0"

QUESTION_REPORT_FIELDS = [
    "dbVersion",
    "version",
    "categories",
    "category",
    "categories_meta",
    "target_country_code",
    "countries",
    "target_country",
    "scope",
    "difficulty",
    "difficulty_global",
    "difficulty_local",
    "difficulty_global_score",
    "difficulty_local_score",
    "difficulty_profile",
    "difficulty_meta",
]

# Older Firebase records sometimes store a readable country name where newer
# records store the region code. Keep these aliases pointed at the sheet's code.
COUNTRY_ALIASES_BY_CODE = {
    "TR": ("Turkey", "Turkiye", "Türkiye Cumhuriyeti"),
    "US": ("USA", "U.S.", "U.S.A.", "United States", "United States of America", "America", "Amerika Birleşik Devletleri"),
    "GB": ("UK", "U.K.", "United Kingdom", "Great Britain", "Britain", "İngiltere", "England"),
    "DE": ("Germany", "Deutschland"),
    "FR": ("France",),
    "IT": ("Italy", "Italia"),
    "ES": ("Spain", "España", "Espana"),
    "RU": ("Russia", "Russian Federation"),
    "CN": ("China", "PRC", "People's Republic of China", "People’s Republic of China", "Çin Halk Cumhuriyeti"),
    "KR": ("South Korea", "Republic of Korea", "Korea, Republic of", "Güney Kore"),
    "GR": ("Greece", "Hellas"),
    "EG": ("Egypt",),
    "IN": ("India",),
    "BD": ("Bangladesh",),
    "PL": ("Poland",),
    "CZ": ("Czechia", "Czech Republic"),
    "HU": ("Hungary",),
    "UA": ("Ukraine",),
    "NL": ("Netherlands", "Holland"),
    "TW": ("Taiwan",),
    "BAL": ("Balkans", "Balkan Region"),
    "ME": (
        "Middle East", "Middle Eastern Region", "United Arab Emirates", "UAE", "BAE",
        "Saudi Arabia", "Iran", "Iraq", "Israel", "Jordan", "Lebanon", "Syria",
        "Qatar", "Kuwait", "Oman", "Bahrain", "Yemen", "AE", "SA", "IR", "IQ",
        "IL", "JO", "LB", "SY", "QA", "KW", "OM", "BH", "YE",
    ),
    "SCA": (
        "Scandinavia", "Nordic Countries", "İskandinav Bölgesi", "Sweden", "Norway", "Denmark", "Finland",
        "Iceland", "SE", "NO", "DK", "FI", "IS",
    ),
    "NAF": ("North Africa", "Morocco", "Algeria", "Tunisia", "Libya", "Sudan", "MA", "DZ", "TN", "LY", "SD"),
    "WCA": (
        "West and Central Africa", "West & Central Africa", "West Africa", "Nigeria", "Ghana",
        "Senegal", "Ivory Coast", "Côte d’Ivoire", "Cameroon", "Democratic Republic of the Congo",
        "NG", "GH", "SN", "CI", "CM", "CD",
    ),
    "ESA": (
        "East and Southern Africa", "East & Southern Africa", "East Africa", "Kenya", "Ethiopia",
        "Tanzania", "South Africa", "Uganda", "KE", "ET", "TZ", "ZA", "UG",
    ),
    "SEA": (
        "Southeast Asia", "South East Asia", "Indonesia", "Malaysia", "Thailand", "Vietnam",
        "Philippines", "Singapore", "ID", "MY", "TH", "VN", "PH", "SG",
    ),
    "LATAM": (
        "Latin America", "Latin American Region", "Brazil", "Brezilya", "Mexico", "Meksika",
        "Argentina", "Arjantin", "Colombia", "Kolombiya", "Chile", "Şili", "Peru",
        "BR", "MX", "AR", "CO", "CL", "PE",
    ),
    "BAL": (
        "Balkans", "Balkan Region", "Bulgaria", "Serbia", "Croatia", "Bosnia and Herzegovina",
        "Albania", "North Macedonia", "Kosovo", "Romania", "Montenegro", "BG", "RS", "HR",
        "BA", "AL", "MK", "XK", "RO",
    ),
    "BE": ("Belgium", "Belçika"),
    "CH": ("Switzerland", "Swiss Confederation", "İsviçre"),
    "IE": ("Ireland", "Republic of Ireland", "İrlanda"),
    "NZ": ("New Zealand", "Aotearoa", "Yeni Zelanda"),
    "PT": ("Portugal", "Portuguese Republic", "Portekiz"),
    "AT": ("Austria", "Österreich"),
    "AU": ("Australia"),
    "AZ": ("Azerbaijan"),
}

LEGACY_COUNTRY_CODE_NAMES = {
    "BE": "Belçika",
    "CH": "İsviçre",
    "IE": "İrlanda",
    "NZ": "Yeni Zelanda",
    "PT": "Portekiz",
    "AT": "Avusturya",
    "AU": "Avustralya",
    "AZ": "Azerbaycan",
}


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _text_list(value: Any) -> List[str]:
    result: List[str] = []
    for item in _as_list(value):
        if isinstance(item, Mapping):
            item = item.get("name")
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _country_alias_key(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("ı", "i")
    decomposed = unicodedata.normalize("NFKD", text)
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", unaccented))


def canonicalize_question_report_countries(
    report: Mapping[str, Any], country_profiles: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Merge legacy country-name values into the current sheet region codes."""
    canonical_names: Dict[str, str] = {"GL": "Global", **LEGACY_COUNTRY_CODE_NAMES}
    alias_to_code: Dict[str, str] = {}

    for profile in country_profiles:
        code = str(profile.get("code") or "").strip().upper()
        name = str(profile.get("name") or "").strip()
        if not code or not name:
            continue
        canonical_names[code] = name
        alias_to_code[_country_alias_key(code)] = code
        alias_to_code[_country_alias_key(name)] = code

    for code, aliases in COUNTRY_ALIASES_BY_CODE.items():
        for alias in aliases:
            alias_to_code[_country_alias_key(alias)] = code

    questions: List[Dict[str, Any]] = []
    for source in report.get("questions", []) or []:
        if not isinstance(source, Mapping):
            continue
        question = dict(source)
        current_code = str(question.get("country_code") or "").strip()
        current_name = str(question.get("country_name") or "").strip()
        current_key = _country_alias_key(current_code)
        name_key = _country_alias_key(current_name)
        code = alias_to_code.get(current_key) or alias_to_code.get(name_key)
        if code:
            question["country_code"] = code
            question["country_name"] = canonical_names.get(code, current_name or code)
        else:
            question["country_code"] = current_code or "Bilinmeyen"
            question["country_name"] = (
                current_name
                if current_name and _country_alias_key(current_name) != _country_alias_key(current_code)
                else LEGACY_COUNTRY_CODE_NAMES.get(current_code.upper(), current_name or current_code or "Bilinmeyen")
            )
        questions.append(question)

    return {**dict(report), "question_count": len(questions), "questions": questions}


def question_report_country_label(code: Any, name: Any) -> str:
    """A single display format shared by the country filter and ranking table."""
    country_code = str(code or "Bilinmeyen").strip()
    country_name = str(name or "").strip()
    if not country_name or _country_alias_key(country_name) == _country_alias_key(country_code):
        country_name = LEGACY_COUNTRY_CODE_NAMES.get(country_code.upper(), country_code)
    if _country_alias_key(country_name) == _country_alias_key(country_code):
        return country_name
    return f"{country_name} · {country_code}"


def _difficulty_level(value: Any, score: Any) -> str:
    label = str(value or "").strip().casefold()
    aliases = {
        "kolay": "Kolay",
        "easy": "Kolay",
        "orta": "Orta",
        "medium": "Orta",
        "moderate": "Orta",
        "zor": "Zor",
        "hard": "Zor",
        "difficult": "Zor",
    }
    if label in aliases:
        return aliases[label]

    candidate = score if score is not None else value
    try:
        numeric = int(float(candidate))
    except (TypeError, ValueError, OverflowError):
        return "Bilinmiyor"
    if 1 <= numeric <= 4:
        return "Kolay"
    if 5 <= numeric <= 8:
        return "Orta"
    if 9 <= numeric <= 10:
        return "Zor"
    return "Bilinmiyor"


def normalize_question_report_record(data: Mapping[str, Any], document_id: str = "") -> Dict[str, Any]:
    categories = _text_list(data.get("categories") or data.get("category"))
    if not categories:
        categories = _text_list(data.get("categories_meta"))
    if not categories:
        categories = ["Bilinmeyen"]

    raw_scope = str(data.get("scope") or "").strip().casefold()
    countries = _text_list(data.get("countries"))
    raw_code = str(data.get("target_country_code") or "").strip()
    target_country = str(data.get("target_country") or "").strip()
    candidate_country = raw_code or (countries[0] if countries else "") or target_country
    is_global = (
        raw_scope in {"global", "gl"}
        or candidate_country.casefold() in {"gl", "global"}
        or (not candidate_country and not raw_scope)
    )
    if is_global:
        country_code = "GL"
    elif raw_code or (candidate_country.isupper() and " " not in candidate_country) or (
        candidate_country.islower() and len(candidate_country) <= 3
    ) or ("-" in candidate_country and " " not in candidate_country):
        country_code = candidate_country.upper()
    else:
        # Older records may contain only the readable country name.
        country_code = candidate_country
    if not country_code:
        country_code = "Bilinmeyen"

    country_name = target_country
    if not country_name and countries:
        country_name = countries[0]
    if is_global:
        country_name = "Global"

    local_scope = raw_scope == "local" or (not raw_scope and not is_global)
    scope_key = "local" if local_scope else "global"
    profile = data.get("difficulty_profile") or {}
    if isinstance(profile, Mapping):
        scope_profile = profile.get(scope_key)
        if scope_profile is None and scope_key == "global":
            scope_profile = profile.get("global_scope")
    else:
        scope_profile = None
    meta = data.get("difficulty_meta") or {}
    scope_meta = meta.get(scope_key) if isinstance(meta, Mapping) else None
    profile_label = scope_profile.get("label") if isinstance(scope_profile, Mapping) else None
    profile_score = scope_profile.get("score") if isinstance(scope_profile, Mapping) else None
    meta_label = (scope_meta.get("level") or scope_meta.get("label")) if isinstance(scope_meta, Mapping) else None
    meta_score = scope_meta.get("score") if isinstance(scope_meta, Mapping) else None

    if local_scope:
        score_value = data.get("difficulty_local_score")
        if score_value is None:
            score_value = profile_score if profile_score is not None else meta_score
        difficulty = _difficulty_level(
            data.get("difficulty_local") or data.get("difficulty") or profile_label or meta_label,
            score_value,
        )
    else:
        score_value = data.get("difficulty_global_score")
        if score_value is None:
            score_value = profile_score if profile_score is not None else meta_score
        difficulty = _difficulty_level(
            data.get("difficulty_global") or data.get("difficulty") or profile_label or meta_label,
            score_value,
        )
    try:
        difficulty_score = int(float(score_value))
        if not 1 <= difficulty_score <= 10:
            difficulty_score = None
    except (TypeError, ValueError, OverflowError):
        difficulty_score = None

    return {
        "id": str(document_id or ""),
        "categories": categories,
        "country_code": country_code,
        "country_name": country_name or country_code,
        "difficulty": difficulty,
        "difficulty_score": difficulty_score,
    }


def build_question_report(documents: Iterable[Any]) -> Dict[str, Any]:
    questions: List[Dict[str, Any]] = []
    for snapshot in documents:
        if hasattr(snapshot, "to_dict"):
            data = snapshot.to_dict() or {}
            document_id = getattr(snapshot, "id", "")
        elif isinstance(snapshot, Mapping):
            data = snapshot
            document_id = ""
        else:
            continue
        # Include legacy documents that use `version`, while keeping every
        # other release out of this report.
        version = str(data.get("dbVersion") or data.get("version") or "").strip()
        if version != QUESTION_REPORT_VERSION:
            continue
        questions.append(normalize_question_report_record(data, document_id))
    return {
        "version": QUESTION_REPORT_VERSION,
        "question_count": len(questions),
        "questions": questions,
    }


def fetch_question_report(db: Any) -> Dict[str, Any]:
    """Only fetches classification fields, leaving translation payloads in Firestore."""
    query = db.collection("questions").select(QUESTION_REPORT_FIELDS)
    return build_question_report(query.stream())
