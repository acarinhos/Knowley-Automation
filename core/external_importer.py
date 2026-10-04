"""Dış trivia veri setlerini Knowley/Firestore şemasına dönüştürür.

Modül ağ veya arayüz bağımlılığı olmadan doğrulanabilir; URL okuma yalnızca
``load_source`` çağrıldığında yapılır. Firestore'a kendisi yazmaz. Böylece
önizleme ve insan onayı zorunlu bir kapı olarak kalır.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests

from config.categories_config import get_category_color, get_subcategory_image
from core.generator import QUESTION_SCHEMA_VERSION, normalize_text_for_search
from core.sheet_algorithm import get_sheet_algorithm_repository
from core.translation_service import SUPPORTED_LANGUAGE_CODES


DEFAULT_SHARED_SOURCE = "https://share.gemini.google/QodHwCqezsIr"
MAX_OPTION_CHARS = 80
DIFFICULTY_BONUS = {"Kolay": 0, "Orta": 2, "Zor": 4}
SUPPORTED_LANGUAGES = list(SUPPORTED_LANGUAGE_CODES)

IMAGE_KEYWORDS = (
    "bayrak",
    "flag",
    "harita",
    "map",
    "tablo",
    "painting",
    "portre",
    "anıt",
    "heykel",
    "mimari",
    "yapı",
    "saray",
    "cami",
    "kilise",
    "katedral",
    "köprü",
    "yemek",
    "tatlı",
    "mutfak",
)

CATEGORY_ALIASES = {
    "general knowledge": "Genel Kültür",
    "general": "Genel Kültür",
    "mythology": "Mitoloji",
    "history": "Tarih",
    "geography": "Coğrafya",
    "science nature": "Biyoloji",
    "science": "Biyoloji",
    "biology": "Biyoloji",
    "physics": "Fizik",
    "chemistry": "Kimya",
    "sports": "Spor",
    "sport": "Spor",
    "music": "Müzik",
    "film": "Sinema",
    "television": "Sinema",
    "entertainment film": "Sinema",
    "books": "Edebiyat",
    "literature": "Edebiyat",
    "computers": "Teknoloji",
    "technology": "Teknoloji",
    "video games": "Oyun",
    "art": "Resim",
    "food": "Gastronomi",
    "politics": "Siyaset",
    "astronomy": "Astronomi",
    "medicine": "Sağlık",
    "psychology": "Psikoloji",
    "economics": "Ekonomi",
    "philosophy": "Felsefe",
    "american history": "Tarih",
    "world history": "Tarih",
    "history": "Tarih",
    "world geography": "Coğrafya",
    "geography": "Coğrafya",
    "literature": "Edebiyat",
    "science": "Biyoloji",
    "potpourri": "Genel Kültür",
}

_TAXONOMY_CACHE: Dict[int, Dict[str, Any]] = {}


@dataclass
class RejectedQuestion:
    index: int
    reason: str
    raw_question: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "reason": self.reason, "question": self.raw_question}


@dataclass
class ImportReport:
    source: str
    raw_count: int = 0
    accepted: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[RejectedQuestion] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def as_dict(self, include_questions: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "source": self.source,
            "created_at": self.created_at,
            "raw_count": self.raw_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "rejected": [item.as_dict() for item in self.rejected],
        }
        if include_questions:
            data["questions"] = self.accepted
        return data


def _clean(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize_text_for_search(_clean(value))).strip()


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    lowered = {_key(key): value for key, value in row.items()}
    for key in keys:
        value = lowered.get(_key(key))
        if value not in (None, "", []):
            return value
    return None


def _extract_json_payload(text: str) -> Any:
    stripped = text.lstrip("\ufeff\r\n\t ")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", stripped, re.IGNORECASE):
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for marker in ("[", "{"):
        start = stripped.find(marker)
        while start >= 0:
            try:
                payload, _ = decoder.raw_decode(stripped[start:])
                return payload
            except json.JSONDecodeError:
                start = stripped.find(marker, start + 1)
    raise ValueError("Kaynak içinde işlenebilir JSON soru dizisi bulunamadı.")


def _records_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("questions", "results", "items", "data", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
        if _first(payload, "question", "soru", "question_text"):
            return [dict(payload)]
    return []


def parse_source_text(text: str, hint: str = "") -> List[Dict[str, Any]]:
    suffix = Path(urlparse(hint).path).suffix.lower()
    first_line = text.partition("\n")[0]
    if suffix in {".csv", ".tsv"} or (not text.lstrip().startswith(("[", "{")) and ("," in first_line or "\t" in first_line)):
        delimiter = "\t" if suffix == ".tsv" or first_line.count("\t") > first_line.count(",") else ","
        return [dict(row) for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)]
    return _records_from_payload(_extract_json_payload(text))


def load_source(source: str, timeout: float = 25.0) -> List[Dict[str, Any]]:
    source = _clean(source)
    if not source:
        raise ValueError("Bir JSON/CSV dosyası veya doğrudan veri URL'si seçin.")
    if source.lower().startswith(("http://", "https://")):
        response = requests.get(source, timeout=timeout, headers={"User-Agent": "Knowley-Importer/1.0"})
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        records = parse_source_text(response.text, source)
    else:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Veri dosyası bulunamadı: {path}")
        if path.suffix.lower() not in {".json", ".csv", ".tsv"}:
            raise ValueError("Yalnızca JSON, CSV ve TSV dosyaları destekleniyor.")
        records = parse_source_text(path.read_text(encoding="utf-8-sig"), str(path))
    if not records:
        raise ValueError("Kaynak açıldı ancak hiçbir soru kaydı bulunamadı.")
    return records


def _normalize_options(raw: Mapping[str, Any]) -> Tuple[Dict[str, str], Optional[str]]:
    options = _first(raw, "options", "choices", "answers", "secenekler", "şıklar")
    correct_value = _first(raw, "correct_answer", "answer", "correct", "dogru_cevap", "doğru cevap")
    incorrect = _first(raw, "incorrect_answers", "wrong_answers", "yanlis_cevaplar")

    if isinstance(options, Mapping):
        normalized = {letter: _clean(options.get(letter) or options.get(letter.lower())) for letter in "ABCD"}
    elif isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
        values = [_clean(value.get("text") if isinstance(value, Mapping) else value) for value in options]
        normalized = dict(zip("ABCD", values[:4]))
    elif isinstance(incorrect, Sequence) and not isinstance(incorrect, (str, bytes)) and correct_value:
        values = [_clean(correct_value)] + [_clean(value) for value in incorrect]
        normalized = dict(zip("ABCD", values[:4]))
    else:
        normalized = {
            letter: _clean(_first(raw, letter, f"option_{letter.lower()}", f"option{letter}", f"{letter}_sikki"))
            for letter in "ABCD"
        }

    if any(not normalized.get(letter) for letter in "ABCD"):
        return normalized, None

    answer = _clean(correct_value)
    if answer.upper() in normalized:
        correct_letter = answer.upper()
    else:
        answer_key = _key(answer)
        correct_letter = next((letter for letter, value in normalized.items() if _key(value) == answer_key), None)
    return normalized, correct_letter


def _has_four_options(raw: Mapping[str, Any]) -> bool:
    options, correct_letter = _normalize_options(raw)
    return bool(correct_letter) and len({_key(value) for value in options.values() if value}) == 4


def _normalize_difficulty(value: Any, question: str) -> Tuple[str, int]:
    key = _key(value)
    if key in {"easy", "kolay", "1"}:
        return "Kolay", 3
    if key in {"hard", "difficult", "zor", "3"}:
        return "Zor", 7
    if key in {"medium", "normal", "orta", "2"}:
        return "Orta", 5
    # Eksik zorluk için kısa ve doğrudan soruları kolay, diğerlerini orta kabul et.
    return ("Kolay", 3) if len(question) <= 55 else ("Orta", 5)


def calculate_import_duration(question: str, difficulty: str) -> int:
    """Paylaşılan veri belgesindeki 10 + ceil(len/10) + bonus formülü."""
    return min(35, 10 + math.ceil(len(question) / 10) + DIFFICULTY_BONUS[difficulty])


def _canonical_category(raw_category: Any, question: str, repository: Any) -> Optional[str]:
    raw_key = _key(raw_category)
    cache = _taxonomy_cache(repository)
    categories = cache["categories"]
    exact = cache["category_exact"]
    if raw_key in exact:
        return exact[raw_key]
    for alias, canonical in CATEGORY_ALIASES.items():
        if alias in raw_key and canonical in categories:
            return canonical

    question_key = _key(question)
    keyword_routes = (
        (("futbol", "football", "basketbol", "basketball", "olimpiyat", "olympic", "tenis", "tennis", "baseball"), "Spor"),
        (("roman", "novel", "yazar", "author", "şiir", "poem", "kitap", "book", "literature"), "Edebiyat"),
        (("film", "movie", "dizi", "television", "actor", "actress", "oyuncu", "yönetmen", "director"), "Sinema"),
        (("gezegen", "planet", "yıldız", "star", "uzay", "space", "güneş", "solar"), "Astronomi"),
        (("ülke", "country", "başkent", "capital", "nehir", "river", "lake", "dağ", "mountain", "kıta", "continent"), "Coğrafya"),
        (("beste", "composer", "şarkı", "song", "müzik", "music", "albüm", "album"), "Müzik"),
        (("war", "king", "queen", "president", "century", "empire", "battle"), "Tarih"),
        (("element", "chemical", "atom", "molecule"), "Kimya"),
        (("animal", "bird", "plant", "species", "cell", "organ"), "Biyoloji"),
        (("computer", "software", "internet", "programming"), "Teknoloji"),
        (("food", "dish", "recipe", "cuisine"), "Gastronomi"),
    )
    for words, canonical in keyword_routes:
        if any(word in question_key for word in words) and canonical in categories:
            return canonical
    # Jeopardy gibi on binlerce serbest kategori kullanan kaynaklarda konu adı
    # tek başına güvenilir değildir. Kayıt kaybetmek yerine Sheets'teki kanonik
    # genel kültür alanına düşürürüz; çıktı hiçbir zaman dış kategori taşımaz.
    fallback = "Genel Kültür"
    return fallback if fallback in categories else (categories[0] if categories else None)


def _is_jeopardy_record(raw: Mapping[str, Any]) -> bool:
    keys = {_key(key) for key in raw}
    return {"round", "clue value", "category", "answer", "question"}.issubset(keys)


def _difficulty_from_jeopardy_value(value: Any) -> str:
    try:
        amount = int(float(_clean(value) or 0))
    except ValueError:
        amount = 0
    if amount and amount <= 400:
        return "easy"
    if amount and amount >= 1200:
        return "hard"
    return "medium"


def _select_distractors(
    category_pool: Sequence[str], global_pool: Sequence[str], correct: str, seed_text: str
) -> List[str]:
    correct_key = _key(correct)
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
    distractors: List[str] = []
    distractor_keys: set[str] = {correct_key}
    for pool in (category_pool, global_pool):
        if not pool:
            continue
        start = seed % len(pool)
        for offset in range(len(pool)):
            item = pool[(start + offset) % len(pool)]
            item_key = _key(item)
            if not item_key or item_key in distractor_keys:
                continue
            distractor_keys.add(item_key)
            distractors.append(item)
            if len(distractors) == 3:
                return distractors
    return distractors


def _prepare_records(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Kaynak kayıtlarını normalize eder ve eksik şıkları veri havuzundan tamamlar."""
    category_pools: Dict[str, List[str]] = {}
    global_pool: List[str] = []
    category_seen: Dict[str, set[str]] = {}
    global_seen: set[str] = set()
    for raw in records:
        category = _clean(_first(raw, "category", "ana kategori", "main_category", "topic"))
        response = _clean(
            _first(raw, "question") if _is_jeopardy_record(raw)
            else _first(raw, "correct_answer", "answer", "correct", "dogru_cevap", "doğru cevap")
        )
        response_key = _key(response)
        if not response_key:
            continue
        cat_key = _key(category)
        if response_key not in category_seen.setdefault(cat_key, set()):
            category_seen[cat_key].add(response_key)
            category_pools.setdefault(cat_key, []).append(response)
        if response_key not in global_seen:
            global_seen.add(response_key)
            global_pool.append(response)

    prepared: List[Dict[str, Any]] = []
    for index, raw in enumerate(records):
        is_jeopardy = _is_jeopardy_record(raw)
        clue = _clean(_first(raw, "answer") if is_jeopardy else _first(raw, "question_text", "soru", "prompt", "question"))
        response = _clean(
            _first(raw, "question") if is_jeopardy
            else _first(raw, "correct_answer", "answer", "correct", "dogru_cevap", "doğru cevap")
        )
        category = _clean(_first(raw, "category"))
        adapted = dict(raw)
        if clue:
            adapted["question_text"] = clue
        if response:
            adapted["correct_answer"] = response
        if response and not _has_four_options(adapted):
            adapted["incorrect_answers"] = _select_distractors(
                category_pools.get(_key(category), []), global_pool, response, f"{index}|{clue}|{response}"
            )
        if is_jeopardy:
            adapted.update(
                {
                    "difficulty": _difficulty_from_jeopardy_value(_first(raw, "clue_value")),
                    "language": "en",
                    "explanation": f"Jeopardy correct response: {response}.",
                    "source": "jeopardy_clue_dataset",
                }
            )
        prepared.append(adapted)
    return prepared


def _canonical_subcategory(raw_subcategory: Any, category: str, question: str, repository: Any) -> Optional[str]:
    records = _taxonomy_cache(repository)["subcategories"].get(category, [])
    if not records:
        return None
    exact = {_key(record["subcategory"]): record["subcategory"] for record in records}
    raw_key = _key(raw_subcategory)
    if raw_key in exact:
        return exact[raw_key]
    if raw_key:
        scored = []
        raw_tokens = set(raw_key.split())
        for record in records:
            tokens = set(_key(record["subcategory"]).split())
            overlap = len(raw_tokens & tokens) / max(1, len(raw_tokens | tokens))
            scored.append((overlap, record["subcategory"]))
        best_score, best_name = max(scored)
        if best_score >= 0.34:
            return best_name

    question_tokens = {token for token in _key(question).split() if len(token) > 3}
    category_tokens = {token for token in _key(raw_subcategory).split() if len(token) > 3}
    ranked: List[Tuple[float, int, str]] = []
    for record in records:
        target_tokens = {
            token
            for token in _key(f"{record['subcategory']} {record.get('guide', '')}").split()
            if len(token) > 3
        }
        overlap = len((question_tokens | category_tokens) & target_tokens) / max(1, len(target_tokens))
        ranked.append((overlap, int(record.get("weight_154", 0) or 0), record["subcategory"]))
    # Odak kılavuzu/alt kategori ile metinsel eşleşme yoksa kredi ağırlığı en
    # yüksek kanonik alt hedef seçilir. Dış kaynak hiçbir zaman yeni sub üretmez.
    return max(ranked)[2] if ranked else None


def _taxonomy_cache(repository: Any) -> Dict[str, Any]:
    cache_key = id(repository)
    cached = _TAXONOMY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    categories = repository.categories()
    cached = {
        "categories": categories,
        "category_exact": {_key(item): item for item in categories},
        "subcategories": {category: repository.subcategories(category) for category in categories},
    }
    _TAXONOMY_CACHE[cache_key] = cached
    return cached


def _image_metadata(question: str, options: Mapping[str, str]) -> Tuple[bool, Optional[str]]:
    combined = _key(" ".join([question, *options.values()]))
    matched = next((word for word in IMAGE_KEYWORDS if _key(word) in combined), None)
    if not matched:
        return False, None
    return True, f"Sorudaki {matched} konusunu açık ve yanıltıcı olmayan biçimde gösteren bilgi yarışması görseli."


def normalize_question(raw: Mapping[str, Any], repository: Any = None) -> Dict[str, Any]:
    repository = repository or get_sheet_algorithm_repository()
    question = _clean(_first(raw, "question_text", "soru", "prompt", "question"))
    if not question:
        raise ValueError("Soru metni eksik.")
    options, correct_letter = _normalize_options(raw)
    if len(set(_key(value) for value in options.values() if value)) != 4:
        raise ValueError("Sistem bu kayıt için üç benzersiz çeldirici üretemedi.")
    if not correct_letter:
        raise ValueError("Doğru cevap kaynak kayıttan belirlenemedi.")

    raw_category = _first(raw, "category", "ana kategori", "main_category", "topic")
    category = _canonical_category(raw_category, question, repository)
    if not category:
        raise ValueError(f"Kategori Sheets taksonomisiyle eşleştirilemedi: {_clean(raw_category) or 'boş'}")
    raw_subcategory = _first(raw, "sub_category", "subcategory", "alt kategori", "subtopic")
    subcategory = _canonical_subcategory(raw_subcategory, category, question, repository)
    if not subcategory:
        raise ValueError(f"{category} için geçerli bir alt kategori bulunamadı.")

    difficulty, score = _normalize_difficulty(_first(raw, "difficulty", "zorluk", "level"), question)
    target = repository.target(category, subcategory)
    duration = calculate_import_duration(question, difficulty)
    has_image, image_prompt = _image_metadata(question, options)
    explanation = _clean(_first(raw, "explanation", "aciklama", "açıklama", "solution"))
    if not explanation:
        explanation = f"Doğru cevap {options[correct_letter]} seçeneğidir."
    language = _clean(_first(raw, "language", "lang", "dil")) or ("tr" if re.search(r"[çğıöşüİ]", question) else "en")
    language = language.lower()[:2]
    if language not in {"tr", "en", "es", "pt", "de"}:
        language = "tr"
    from core.generation_planner import get_generation_planner

    plan = get_generation_planner().create_plan(category=category, subcategory=subcategory)
    scope = plan.scope
    target_country = plan.target_country
    is_global_eligible = plan.is_global_eligible
    from core.sheet_algorithm import get_sheet_algorithm_repository
    target_country_code = get_sheet_algorithm_repository().country_code(
        target_country, scope
    )
    global_difficulty = difficulty if is_global_eligible else None
    global_score = score if is_global_eligible else None
    created_at = datetime.now(timezone.utc).isoformat()

    return {
        "categories": [category],
        "categories_meta": [{"name": category, "color": get_category_color(category)}],
        "sub_categories": [subcategory],
        "sub_categories_meta": [{"name": subcategory, "image_url": get_subcategory_image(category, subcategory)}],
        "question_image_url": None,
        "has_image": has_image,
        "image_prompt": image_prompt,
        "is_combo": False,
        "scope": scope,
        "target_country": target_country,
        "target_country_code": target_country_code,
        "target_country_credit": plan.target_country_credit,
        "is_global_eligible": is_global_eligible,
        "countries": [target_country_code],
        "dbVersion": QUESTION_SCHEMA_VERSION,
        "difficulty_local": difficulty,
        "difficulty_local_score": score,
        "difficulty_global": global_difficulty,
        "difficulty_global_score": global_score,
        "difficulty_meta": {
            "local": {"level": difficulty, "score": score, "context_country": target_country},
            "global": {"level": global_difficulty, "score": global_score} if global_difficulty else None,
        },
        "difficulty_profile": {
            "local": {"label": difficulty, "score": score},
            "global": {"label": global_difficulty, "score": global_score} if global_difficulty else None,
        },
        "correct_answer": correct_letter,
        "correct_option": correct_letter,
        "duration_local": duration,
        "duration_global": duration,
        "duration_seconds": duration,
        "correct_count": 0,
        "wrong_count": 0,
        "created_at": created_at,
        "shuffle_key": random.random(),
        "supported_languages": [language],
        "translations": {
            language: {
                "question": question,
                "options": options,
                "explanation": explanation[:240],
                "filter_tag": _clean(_first(raw, "filter_tag", "tag")) or subcategory,
            }
        },
        "generation_provider": "EXTERNAL_IMPORT",
        "algorithm_source": target.source_sheet,
        "algorithm_version": repository.version,
        "import_source": _clean(_first(raw, "source", "source_url")) or None,
        "import_audit": {
            "normalized_at": datetime.now(timezone.utc).isoformat(),
            "ruleset": "taxonomy-focus-difficulty-v2",
            "suitability": "matched",
            "canonical_category": category,
            "canonical_subcategory": subcategory,
            "focus_guide": target.guide,
            "duration_formula": "min(35, 10 + ceil(question_chars / 10) + difficulty_bonus)",
        },
    }


def enrich_question_translations(question: Dict[str, Any], llm_manager: Any = None) -> Dict[str, Any]:
    """Complete translations with Azure first and the existing LLM path as fallback."""
    from core.translation_service import translate_localized_content

    source_language = question["supported_languages"][0]
    source = question["translations"][source_language]
    requested_languages = list(SUPPORTED_LANGUAGE_CODES)
    if str(question.get("scope") or "").strip().lower() == "local":
        repository = get_sheet_algorithm_repository()
        country = str(question.get("target_country") or "").strip()
        if not country:
            code = str(question.get("target_country_code") or "").strip()
            country = next(
                (item["name"] for item in repository.countries() if item.get("code") == code),
                "",
            )
        requested_languages = repository.languages_for_country(country) or requested_languages
    normalized, provider = translate_localized_content(
        source,
        source_language,
        requested_languages,
        llm_manager=llm_manager,
    )
    question["translations"] = normalized
    question["supported_languages"] = list(normalized)
    question["generation_provider"] = f"EXTERNAL_IMPORT_{provider}"
    question["translation_provider"] = provider
    question["import_audit"]["translation_status"] = "complete"
    return question


def validate_and_transform(
    records: Iterable[Mapping[str, Any]],
    source: str = "",
    translate_all: bool = False,
    llm_manager: Any = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> ImportReport:
    record_list = list(records)
    record_list = _prepare_records(record_list)
    report = ImportReport(source=source, raw_count=len(record_list))
    repository = get_sheet_algorithm_repository()
    seen = set()
    for index, raw in enumerate(record_list, start=1):
        raw_question = _clean(_first(raw, "question_text", "soru", "prompt", "question"))
        try:
            normalized = normalize_question(raw, repository=repository)
            fingerprint = _key(next(iter(normalized["translations"].values()))["question"])
            if fingerprint in seen:
                raise ValueError("Aynı soru veri setinde birden fazla kez bulunuyor.")
            seen.add(fingerprint)
            if translate_all:
                normalized = enrich_question_translations(normalized, llm_manager=llm_manager)
            report.accepted.append(normalized)
        except Exception as exc:
            report.rejected.append(RejectedQuestion(index=index, reason=str(exc), raw_question=raw_question[:180]))
        if progress_callback:
            progress_callback(index, len(record_list))
    return report


def save_report(report: ImportReport, output_path: str | Path) -> Path:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
