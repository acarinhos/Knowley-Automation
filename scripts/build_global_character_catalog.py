"""Build the global-only, localized and typed character catalogue from sheet snapshots."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "global_character_source.json"
LOCALIZED = ROOT / "data" / "global_character_locales.json"
OUTPUT = ROOT / "data" / "global_character_catalog.json"
ALIASES = ROOT / "data" / "global_character_aliases.json"
from core.character_catalog import (  # noqa: E402
    CHARACTER_LOCALES, CHARACTER_SCHEMA_VERSION, DAILY_RESET_HOUR_LOCAL, LEVEL_THRESHOLDS, UNLOCK_THRESHOLD, level_avatar_urls,
)
from core.translation_service import canonical_language_code  # noqa: E402


def clean(text: Any) -> str:
    return str(text or "").strip()


def slug_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").replace("ı", "i"))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")


def key(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", clean(text).lower().replace("ı", "i"))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


def rename_text(text: str, mapping: dict[str, str]) -> str:
    """Replace whole words (case-insensitive, keeping title/upper case) using a word -> alias map."""
    lowered = {k.casefold(): v for k, v in mapping.items()}

    def swap(match: re.Match) -> str:
        word = match.group()
        alias = lowered.get(word.casefold())
        if alias is None:
            return word
        return alias.upper() if word.isupper() and len(word) > 1 else alias

    return WORD.sub(swap, text)


def name_tokens(name: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", name)


def alias_map(original: str, alias: str) -> dict[str, str]:
    """Full name plus positional tokens (>=3 letters) so titles/roles quoting the name are covered."""
    tokens = list(zip(name_tokens(original), name_tokens(alias)))
    return {original: alias, **{o: a for o, a in tokens if len(o) >= 3 and o != a}}


def effect(
    kind: str,
    params: dict[str, Any] | None = None,
    *,
    trigger: str = "on_activate",
    conditions: dict[str, Any] | None = None,
    inferred: bool = False,
    resolution_note: str | None = None,
    review_note: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": kind,
        "trigger": trigger,
        "params": params or {},
        "conditions": conditions or {},
    }
    if inferred:
        value["review_required"] = True
        value["review_note"] = review_note or "A numeric or timing value was not specified in the source sheet."
    if resolution_note:
        value["resolution_note"] = resolution_note
    return value


def classify(text: str) -> list[dict[str, Any]]:
    """Map Turkish sheet prose to an ordered English effect contract."""
    source_text = clean(text)
    s = key(text)
    effects: list[dict[str, Any]] = []

    def add(kind: str, params: dict[str, Any] | None = None, **kwargs: Any) -> None:
        item = effect(kind, params, **kwargs)
        signature = (item["type"], json.dumps(item["params"], sort_keys=True), json.dumps(item["conditions"], sort_keys=True))
        if all((existing["type"], json.dumps(existing["params"], sort_keys=True), json.dumps(existing["conditions"], sort_keys=True)) != signature for existing in effects):
            effects.append(item)

    number = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:saniye\w*|sn\b)", source_text, re.IGNORECASE)
    duration = int(float(number.group(1).replace(",", "."))) if number else None

    # Preserve decimal separators before key() strips punctuation (2.5x must not become 5x).
    multiplier_match = re.search(r"(\d+(?:[.,]\d+)?)\s*x\b|(\d+(?:[.,]\d+)?)\s*kat[a-z]*\b", source_text, re.IGNORECASE)
    if multiplier_match:
        multiplier = float((multiplier_match.group(1) or multiplier_match.group(2)).replace(",", "."))
        add("score_multiplier", {"multiplier": multiplier, "applies_to": "question_score"})
    bonus_match = re.search(r"%\s*(\d+)|(\d+)\s*%", text)
    if bonus_match and any(term in s for term in ("puan", "skor", "seri", "kategori")):
        applies_to = "match_total" if "tur bitim" in s or "toplam skora" in s else "question_score"
        add("score_bonus_percent", {"percent": int(bonus_match.group(1) or bonus_match.group(2)), "applies_to": applies_to})
    fast_match = re.search(r"ilk\s*(\d+)\s*saniye\w*", s)
    forgives_misclick = "yanlis tik" in s and any(term in s for term in ("affet", "affed", "yok say"))
    if forgives_misclick:
        seconds = int(fast_match.group(1)) if fast_match else 3
        add(
            "retry_answer",
            {"additional_attempts": 1},
            trigger="on_wrong_answer",
            conditions={"answer_within_seconds": seconds},
            resolution_note=(
                f"Misclick forgiveness is one retry within {seconds}s."
                if fast_match
                else "Unspecified early-misclick window standardized to 3s; effect grants one retry."
            ),
        )
    elif fast_match:
        existing = next((item for item in effects if item["type"] == "score_multiplier"), None)
        if existing is None:
            existing = effect("score_multiplier", {"multiplier": 2.0, "applies_to": "question_score"})
            effects.append(existing)
        existing["conditions"] = {"answer_within_seconds": int(fast_match.group(1))}
    elif "hizli cevap" in s and not forgives_misclick:
        existing = next((item for item in effects if item["type"] == "score_multiplier"), None)
        if existing is None:
            existing = effect("score_multiplier", {"multiplier": 2.0, "applies_to": "question_score"})
            effects.append(existing)
        if "answer_within_seconds" not in existing["conditions"]:
            existing["conditions"] = {"answer_within_seconds": 3}
            existing["resolution_note"] = "Generic fast-answer threshold standardized to 3s."

    if "en zayif" in s or "zayif yaniltici" in s:
        add("eliminate_weakest_distractor", {"count": 1, "index_source": "weakest_distractor_index"})
    if any(term in s for term in ("en guclu cel", "en tehlikeli", "en celistirici", "en cok yaniltan", "en cok yaniltani", "en cel")):
        add("eliminate_strongest_distractor", {"count": 1, "index_source": "strongest_distractor_index"})
    wrong_count = (
        re.search(r"(?:yanlis|hatali)\s*(\d+)\s*(?:secene\w*|sik\w*)", s)
        or re.search(r"(\d+)\s*(?:yanlis|hatali)\s*(?:secene\w*|sik\w*)", s)
    )
    if wrong_count and int(wrong_count.group(1)) == 2:
        add("eliminate_to_two_options", {"remaining_options": 2})
    if not wrong_count and any(term in s for term in ("2 secen", "2 ye", "2 ade", "iki secenek", "ikiye indir", "yari yariya", "%50", "2 sik", "iki sikk")):
        add("eliminate_to_two_options", {"remaining_options": 2})
    if wrong_count and int(wrong_count.group(1)) != 2:
        add("eliminate_wrong_options", {"count": int(wrong_count.group(1)), "selection_strategy": "weakest_distractor_index"})
    elif not wrong_count and any(term in s for term in ("yanlis sik", "yanlis secen", "yaniltici sik", "yaniltici secen", "celistirici secen", "celdirici secen", "celdirici secenegi", "celistirici secenegi", "bariz yanlis", "alakasiz", "sacma yanlis", "onemsiz sik", "bilgi kirliligi", "yapay olan yaniltici", "terim karmasasi", "yaniltici ekleri", "gurultuyu kov", "gurultuyu")):
        if any(term in s for term in ("celistirici", "tehlikeli", "yaniltici", "yanlis")):
            selector = "strongest_distractor_index"
        else:
            selector = "weakest_distractor_index"
        add("eliminate_wrong_options", {"count": 1, "selection_strategy": selector})
    if "celiskili" in s or "neden sonuc" in s or "mantik celiski" in s:
        add("eliminate_contradictory_option", {"index_source": "strongest_distractor_index"})
    if ("dogru ve yanlis" in s or "dogru yanlis" in s) and ("kutb" in s or "ikili secim" in s or "ikili format" in s or "ikiye ayir" in s):
        add("binary_convert", {"source_field": "translations.{locale}.binary_fallback"})

    if ("dogru" in s and any(term in s for term in ("kabul", "onay", "saydir", "saydirir", "sayar", "say", "isaretler", "isaretle", "dogru kabul", "dogrulat", "dogruya donustur", "tam puan", "tam skor", "acar"))) or "onaylat" in s:
        add("auto_correct", {"correct_answer": True})
    if ("dogru secenegi" in s or "dogru sikki" in s or "dogru cevabi" in s) and any(term in s for term in ("kilitle", "one dogru cek", "one dogru ceker", "ilk siraya tas", "ilk siraya", "en ust siraya")):
        add("lock_or_promote_correct_option", {"source_field": "correct_answer"})
    if "dogru sikki" in s and ("kilitle" in s or "isaretle" in s):
        add("auto_correct", {"correct_answer": True})
    if ("dogru secenegi" in s or "dogru sikki" in s) and ("one dogru cek" in s or "one dogru ceker" in s or "ilk siraya" in s or "en ust siraya" in s):
        add("promote_correct_option", {"source_field": "correct_answer"})

    if any(term in s for term in ("can kayb", "can git", "can eksil", "hasar", "cezasiz affet", "yanlisi affet", "yanlis yapilan", "yanlis basilan", "yanlis tiklanan", "yanlis cevapta", "ceza almadan", "ilk yanlisi")):
        if any(term in s for term in ("can", "hasar", "cezasiz", "affet", "affed", "ceza almadan")) and "sonraki soruda" not in s:
            add("protect_health", {"charges": 1, "scope": "current_question"}, trigger="on_wrong_answer")
    if any(term in s for term in ("seriyi", "seri", "kombo")) and any(term in s for term in ("kor", "kurtar", "bozul", "kir", "sifirlan", "kilitle", "bloke")):
        duration_scope = "match" if "sonuna kadar" in s or "tur bitim" in s else "current_question"
        add("protect_streak", {"charges": "unlimited" if duration_scope == "match" else 1, "scope": duration_scope}, trigger="on_wrong_answer")
    if "can" in s and any(term in s for term in ("geri doldur", "geri kazan", "geri al", "aninda doldur")):
        add("restore_health", {"amount": 1})
    if "sonraki soruda" in s and "can" in s:
        add("protect_health", {"charges": 1, "scope": "next_question"}, trigger="on_wrong_answer")

    skip_replace = any(term in s for term in ("pas gec", "es gec", "es gecip", "soruyu iptal", "yerine yeni soru", "yeni soru getir", "yeni soru ister", "yeni soru ceker", "kopyala", "kayipsiz gecer", "sorunun uzerinden", "soruyu sifir kayipla")) or bool(re.search(r"\b(?:atla|atlat)\w*\b", s))
    if skip_replace:
        if "kopyala" in s and "bonus" in s:
            add("transfer_previous_question_bonus", {"source": "previous_question", "target": "current_question"})
        elif "atla" in s or "pas" in s or "es gec" in s or "iptal" in s or "yeni soru" in s or "kayipsiz gecer" in s or "sorunun uzerinden" in s or "soruyu sifir kayipla" in s:
            params = {"replacement": "same_subcategory", "no_penalty": any(term in s for term in ("kayipsiz", "ceza almadan", "bedelsiz", "sifir risk"))}
            if "bonus" in s:
                params["bonus_points"] = True
            add("skip_replace_question", params)
    if "ikinci deneme" in s or "2 deneme" in s or "1 hak daha" in s or "bir kez daha sec" in s or "1 kez daha sec" in s or "secme hakki tanir" in s or "ikinci bir" in s or "2 bir deneme" in s:
        add("retry_answer", {"additional_attempts": 1})
    if ("iki sik" in s or "2 sik" in s) and any(term in s for term in ("ayni anda", "birden deneme", "secme hak", "secme hakk", "secme yetki")):
        add("double_choice", {"max_selections": 2, "success_if_any_correct": True})
    if ("yetene" in s and "bedelsiz" in s and "tetikle" in s) or ("joker" in s and "bedelsiz" in s and "tetikle" in s):
        add("grant_free_skill_use", {"next_skill_type": "eliminate_wrong_options", "charges": 1})
    if "yetene" in s and "haklarini" in s and "sarj" in s:
        add("recharge_random_skill", {"target": "random_depleted_skill", "charges": "restore_to_daily_limit"})

    if "sayac" in s or "sure" in s or "zaman" in s:
        if any(term in s for term in ("durdur", "dondur", "dond")) and not any(term in s for term in ("yavaslat", "yavas")):
            add(
                "freeze_timer",
                {"duration_seconds": duration or 8},
                resolution_note=("Standard 8s freeze duration applied because source text omits a duration." if duration is None else None),
            )
        if any(term in s for term in ("uzat", "ek sure", "dusunme suresi", "ek zaman", "fazladan", "ekler")):
            condition = {"timer_remaining_seconds_lte": 5} if "kritik seviye" in s else {}
            if "sure bit" in s:
                condition = {"trigger": "on_timeout"}
            add(
                "extend_timer",
                {"duration_seconds": duration or 8},
                conditions=condition,
                resolution_note=("Standard 8s extension applied because source text omits a duration." if duration is None else None),
            )
        if any(term in s for term in ("tersine islet", "akisini yari", "hizini yari", "yari yariya dusur", "yavaslat")):
            add(
                "scale_timer_rate",
                {"factor": 0.5, "duration_seconds": duration},
                resolution_note=("Null duration applies the rate change until the current question closes." if duration is None else None),
            )
        if "sayaci sifirla" in s or "basa sar" in s:
            add("reset_timer", {"target": "question_duration"})

    if "ipucu" in s or "anahtar terimi aciklar" in s:
        add("show_hint", {"source_field": "translations.{locale}.hint"})
    if any(term in s for term in ("tuzak", "neden yanlis", "neden yanlis oldugunu", "mantik celiskilerini belirten")):
        add("show_trap_note", {"source_field": "translations.{locale}.distractor_trap_note"})
    if any(term in s for term in ("anahtar kelime", "anahtar kavram", "kritik kelime", "kritik terim", "metot analizi")):
        add("show_keyword", {"source_field": "translations.{locale}.keyword"})
    if "bas harf" in s or "kelime uzunlugu" in s or "kelime uzun" in s or "ilk kelime" in s:
        add("show_answer_pattern", {"include_first_letter": True, "include_word_length": "kelime uzun" in s, "include_first_word": "ilk kelime" in s})
    if any(term in s for term in ("dogru sikkin harf", "dogru sikkin harf yeri", "dogru cevabin harf", "harf bilgisini", "harf yerini", "harfini fısıldar", "harfini fisildar")):
        add("show_correct_option_letter", {"source_field": "correct_answer"})
    if "aciklama" in s and any(term in s for term in ("metin", "panele", "panelde", "verir")):
        add("show_explanation", {"source_field": "translations.{locale}.explanation"})
    if any(term in s for term in ("topluluk", "anket", "oy yuzde", "oy dagil", "oy tercih", "sik tercih", "en cok isaret", "en guvendigi", "topluluk en")):
        add("show_community_distribution", {"source_field": "community_distribution", "requires_live_votes": True})
    if any(term in s for term in ("olasiligina gore", "dogruluk olasiligi", "en olasi dogru", "istatistiksel")):
        add("show_answer_probability_hint", {"requires_probability_model": True})
    if "dogruluk oranlarini" in s:
        add("show_answer_probability_distribution", {"requires_probability_model": True})
    if "mantik siras" in s or "mantik sirasi" in s or "kronoloji" in s or "zorluk derecesine gore" in s or "yukaridan asagiya" in s:
        sort_by = "difficulty" if "zorluk" in s else "probability" if any(term in s for term in ("olasil", "dogruluk orani")) else "logical_order"
        add("sort_options", {"sort_by": sort_by, "source_field": "logical_order" if sort_by == "logical_order" else None})
    if any(term in s for term in ("sadelestir", "en sade dille", "tek ve net bir cumle", "ozetle", "anlasilir bicimde", "akici bir dille", "yeniden kur")):
        add("simplify_question", {"target_field": "translations.{locale}.question", "requires_llm_runtime": True})
    if "terim karmasasi" in s or "kavram odagi" in s:
        add("clarify_options", {"requires_llm_runtime": True})
    if "yanlis kurulan neden sonuc" in s:
        add("eliminate_contradictory_option", {"index_source": "strongest_distractor_index"})
    if "serinin" in s and "sonuna kadar" in s and "puan" in s:
        add("protect_streak", {"scope": "match", "charges": "unlimited"})
    if "kayipsiz tamamlat" in s or "soruyu kayipsiz tamamla" in s:
        add("complete_question_safely", {"penalty": False})
    if "bonus puan" in s and "hizli cevap" in s and not any(item["type"].startswith("score_") for item in effects):
        add("score_bonus", {"amount": 1, "applies_to": "question_score"}, inferred=True)
    if "dogru cevabi" in s and "kilitle" in s:
        add("lock_correct_option", {"source_field": "correct_answer"})
    if "iki soru boyunca" in s and "dogru say" in s:
        add("auto_correct", {"correct_answer": True, "duration_questions": 2})
    if "dogru cevaba giden mantik koprusu" in s:
        add("show_hint", {"source_field": "translations.{locale}.hint"})
    if "yanlis cevap sonrasinda" in s and bonus_match:
        add("score_bonus_percent", {"percent": int(bonus_match.group(1) or bonus_match.group(2)), "applies_to": "streak_score"}, trigger="on_wrong_answer")
    if "yanlis yapilsa bile soruyu dogru say" in s or "kararsiz soruyu dogru say" in s or "kararsiz kalinan soruyu" in s and "dogru say" in s:
        add("auto_correct", {"correct_answer": True})
    if "oyuncuyu olumden kurtar" in s:
        add("protect_health", {"charges": 1, "scope": "current_question"}, trigger="on_wrong_answer")
        if "dogru say" in s:
            add("auto_correct", {"correct_answer": True})
    if "yildiz gucu" in s and "2 soru boyunca" in s:
        add("protect_health", {"charges": "unlimited", "duration_questions": 2}, trigger="on_wrong_answer")
    if "tur bitene kadar" in s and any(term in s for term in ("puanlari", "skor")) and multiplier_match:
        add("score_multiplier", {"multiplier": float(multiplier_match.group(1) or multiplier_match.group(2)), "applies_to": "match_score"}, conditions={"duration": "match"})
    if "kategori puanini" in s and bonus_match:
        add("score_bonus_percent", {"percent": int(bonus_match.group(1) or bonus_match.group(2)), "applies_to": "category_score"})
    if "dogru cevapta seriye ekstra" in s and bonus_match:
        add("score_bonus_percent", {"percent": int(bonus_match.group(1) or bonus_match.group(2)), "applies_to": "streak_score"}, trigger="on_correct_answer")
    if "zorluk seviyesi zor" in s and not any(item["type"].startswith("score_") for item in effects):
        add("score_multiplier", {"multiplier": 3.5, "applies_to": "question_score"}, conditions={"difficulty": "Zor"})

    dedicated_indices = {
        item.get("params", {}).get("index_source")
        for item in effects
        if item["type"] in {"eliminate_weakest_distractor", "eliminate_strongest_distractor"}
        and item.get("params", {}).get("count") == 1
    }
    if any(
        item["type"] == "eliminate_wrong_options"
        and item.get("params", {}).get("count", 0) >= 2
        for item in effects
    ):
        effects = [item for item in effects if item["type"] != "eliminate_to_two_options"]
    if dedicated_indices:
        effects = [
            item for item in effects
            if not (
                item["type"] == "eliminate_wrong_options"
                and item.get("params", {}).get("count") == 1
                and item.get("params", {}).get("selection_strategy") in dedicated_indices
            )
        ]

    if not effects:
        # Keep the sheet record runnable only after a person resolves its prose.
        add(
            "manual_review",
            {"source_text": clean(text)},
            inferred=True,
            review_note="Source prose does not define a complete, executable effect contract.",
        )
    for index, item in enumerate(effects, start=1):
        item["order"] = index
    return effects


def quota_for_slot(slot: int) -> dict[str, int]:
    return {
        1: {"lvl1": 3, "lvl2": 5, "lvl3": 7},
        2: {"lvl1": 0, "lvl2": 3, "lvl3": 5},
        3: {"lvl1": 0, "lvl2": 0, "lvl3": 2},
    }[slot]


def value_from_effects(effects: list[dict[str, Any]]) -> float | None:
    for item in effects:
        params = item.get("params", {})
        for field in ("multiplier", "percent", "duration_seconds", "count", "amount"):
            value = params.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def main() -> int:
    source_rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    locale_texts = {
        text: {canonical_language_code(k): v for k, v in by_locale.items()}
        for text, by_locale in json.loads(LOCALIZED.read_text(encoding="utf-8")).items()
    }
    if not isinstance(locale_texts, dict):
        raise ValueError("Localized title/role mapping must be a JSON object.")

    if not ALIASES.exists():
        raise FileNotFoundError(f"{ALIASES} is required; refusing to build a catalogue with original (unaliased) names.")
    aliases = json.loads(ALIASES.read_text(encoding="utf-8"))
    skill_terms = aliases.get("skill_terms", {})

    output: list[dict[str, Any]] = []
    for row in source_rows:
        category = clean(row.get("category"))
        subcategory = clean(row.get("subcategory")) or category
        source_title = clean(row.get("role")) or f"{subcategory} Uzmanı"
        source_role = f"{category} kategorisinde, {subcategory} alanına odaklanan küresel karakter."
        missing_locales = [
            text
            for text in (source_title, source_role)
            if text not in locale_texts or set(CHARACTER_LOCALES) - set(locale_texts[text])
        ]
        if missing_locales:
            raise ValueError(f"Missing locale coverage for display text: {missing_locales[0]!r}")

        raw_id = "gl_" + "_".join(
            part for part in (slug_id(category), slug_id(subcategory), slug_id(clean(row.get("name")))) if part
        )
        # Ids stay derived from the original name; only visible text uses the alias.
        entry = aliases.get("characters", {}).get(raw_id)
        if entry is None:
            raise ValueError(f"No alias for {raw_id}; add it to {ALIASES.name}.")
        display_name = entry["alias"]
        names = alias_map(entry["original"], entry["alias"])
        skills: list[dict[str, Any]] = []
        for slot, source_text in enumerate(row.get("skills") or [], start=1):
            source_text = rename_text(clean(source_text), {**skill_terms, **names})
            if not source_text:
                raise ValueError(f"{raw_id} is missing source skill slot {slot}.")
            label, _, description = source_text.partition(":")
            effects = classify(source_text)
            action_type = effects[0]["type"] if len(effects) == 1 else "composite"
            duration = next(
                (item.get("params", {}).get("duration_seconds") for item in effects if isinstance(item.get("params", {}).get("duration_seconds"), int)),
                None,
            )
            needs_review = any(item.get("review_required") or item["type"] == "manual_review" for item in effects)
            skills.append({
                "skill_slot": slot,
                "skill_id": f"sk_{raw_id}_l{slot}",
                "name": label.strip() or f"Skill {slot}",
                "source_description": description.strip(),
                "source_sheet_text": source_text,
                "action_type": action_type,
                "action_value": value_from_effects(effects),
                "duration_seconds": duration,
                "daily_limits": quota_for_slot(slot),
                "cooldown_questions": 0,
                "effects": effects,
                "implementation_status": "review_required" if needs_review else "catalog_only",
            })

        translations = {
            locale: {
                "name": display_name,
                "title": rename_text(locale_texts[source_title][locale], names),
                "role": rename_text(locale_texts[source_role][locale], names),
            }
            for locale in CHARACTER_LOCALES
        }
        output.append({
            "schema_version": CHARACTER_SCHEMA_VERSION,
            "id": raw_id,
            "name": display_name,
            "title": rename_text(locale_texts[source_title]["en"], names),
            "role": rename_text(locale_texts[source_role]["en"], names),
            "category_id": slug_id(category),
            "category_name": category,
            "sub_category_id": slug_id(subcategory),
            "sub_category_name": subcategory,
            "region_code": "GL",
            "character_type": "global",
            "avatar_url": level_avatar_urls(raw_id)["lvl1"],
            "avatar_urls": level_avatar_urls(raw_id),
            "unlock_threshold": UNLOCK_THRESHOLD,
            "level_thresholds": dict(LEVEL_THRESHOLDS),
            "daily_reset_hour_local": DAILY_RESET_HOUR_LOCAL,
            "is_active": True,
            "translations": translations,
            "skills": skills,
            "source": {
                "spreadsheet_id": "1ADZE7FNZxjhOK1EoZ4dTcn9_d8BzkuvTphHWo361kFY",
                "sheet": row.get("source_sheet"),
                "row": row.get("source_row"),
                "scope_basis": row.get("scope_basis"),
                "source_category_name": category,
                "source_subcategory_name": subcategory,
                "taxonomy_status": "source_sheet_taxonomy",
            },
        })

    ids = [row["id"] for row in output]
    if len(ids) != len(set(ids)):
        raise ValueError("Stable character id collision in source rows.")
    total_skills = sum(len(row["skills"]) for row in output)
    manual = sum(skill["action_type"] == "manual_review" for row in output for skill in row["skills"])
    OUTPUT.write_text(
        json.dumps({"schema_version": CHARACTER_SCHEMA_VERSION, "characters": output}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Built {len(output)} global characters and {total_skills} skill contracts across {len(CHARACTER_LOCALES)} locales.")
    print(f"Skills needing manual mapping review: {manual}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
