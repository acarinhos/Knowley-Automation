"""Emit a bounded sheet-table slice from the generated global character catalogue."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "global_character_catalog.json"
from core.character_catalog import CHARACTER_LOCALES, CHARACTER_SCHEMA_VERSION  # noqa: E402


def json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_tables(catalog: dict[str, Any]) -> dict[str, tuple[list[str], list[list[Any]]]]:
    characters = catalog["characters"]
    by_id = {character["id"]: character for character in characters}
    character_headers = [
        "character_id", "schema_version", "character_type", "region_code", "category_id",
        "category_name", "sub_category_id", "sub_category_name", "name", "title_en", "role_en",
        "avatar_url", "unlock_threshold", "level_thresholds_json", "is_active", "source_sheet", "source_row",
        "scope_basis", "question_mix",
    ]
    character_rows = []
    locale_headers = ["character_id", "locale", "name", "title", "role", "translation_source", "source_sheet", "source_row"]
    locale_rows = []
    skill_headers = [
        "character_id", "character_name", "category_id", "category_name", "sub_category_id",
        "sub_category_name", "skill_id", "skill_slot", "skill_name_source", "source_description_tr",
        "action_type", "action_value", "duration_seconds", "primary_trigger", "conditions_json",
        "effects_json", "daily_limits_json", "cooldown_questions", "implementation_status",
        "review_required", "review_notes", "source_sheet", "source_row",
    ]
    skill_rows = []
    taxonomy: dict[tuple[str, str, str], dict[str, Any]] = {}
    effect_examples: dict[str, dict[str, Any]] = {}
    for character in characters:
        source = character["source"]
        character_rows.append([
            character["id"], character["schema_version"], character["character_type"], character["region_code"],
            character["category_id"], character["category_name"], character["sub_category_id"],
            character["sub_category_name"], character["name"], character["title"], character["role"],
            character["avatar_url"], character["unlock_threshold"], json_cell(character["level_thresholds"]), character["is_active"],
            source["sheet"], source["row"], source["scope_basis"], None,
        ])
        for locale in CHARACTER_LOCALES:
            localized = character["translations"][locale]
            locale_rows.append([
                character["id"], locale, localized["name"], localized["title"], localized["role"],
                "Gemini Free-tier; source proper name preserved", source["sheet"], source["row"],
            ])
        taxonomy_key = (character["category_id"], character["category_name"], character["sub_category_name"])
        taxonomy.setdefault(taxonomy_key, {
            "category_id": character["category_id"],
            "category_name": character["category_name"],
            "sub_category_id": character["sub_category_id"],
            "source_subcategory": character["sub_category_name"],
            "question_algorithm_alias": character["sub_category_name"],
            "mapping_status": "exact_label_requires_snapshot_check",
            "character_count": 0,
            "source_sheets": set(),
        })
        taxonomy[taxonomy_key]["character_count"] += 1
        taxonomy[taxonomy_key]["source_sheets"].add(source["sheet"])
        for skill in character["skills"]:
            triggers = list(dict.fromkeys(effect["trigger"] for effect in skill["effects"]))
            conditions = [effect["conditions"] for effect in skill["effects"] if effect.get("conditions")]
            review_effects = [effect for effect in skill["effects"] if effect.get("review_required")]
            review_notes = list(dict.fromkeys(effect.get("review_note", "Unspecified balance parameter inferred from source prose.") for effect in review_effects))
            skill_rows.append([
                character["id"], character["name"], character["category_id"], character["category_name"],
                character["sub_category_id"], character["sub_category_name"], skill["skill_id"],
                skill["skill_slot"], skill["name"], skill["source_description"], skill["action_type"],
                skill["action_value"], skill["duration_seconds"], ", ".join(triggers),
                json_cell(conditions), json_cell(skill["effects"]), json_cell(skill["daily_limits"]),
                skill["cooldown_questions"], skill["implementation_status"], bool(review_effects),
                "; ".join(review_notes), source["sheet"], source["row"],
            ])
            for item in skill["effects"]:
                entry = effect_examples.setdefault(item["type"], {
                    "type": item["type"], "example_params": item["params"],
                    "parameter_keys": set(), "triggers": set(), "skill_rows": 0,
                    "characters": set(), "review_count": 0,
                })
                entry["parameter_keys"].update(item["params"].keys())
                entry["triggers"].add(item["trigger"])
                entry["skill_rows"] += 1
                entry["characters"].add(character["id"])
                entry["review_count"] += int(bool(item.get("review_required")))

    category_aliases = {
        ("Teknoloji", "Donanım Yapıları & Cihazlar"): ("", "needs_split_mapping", "The source combines two subcategories in the question taxonomy."),
        ("Teknoloji", "İnternet & Ağ Sistemleri"): ("", "missing_in_question_taxonomy", "No matching question subcategory was found in the local snapshot."),
        ("Sağlık", "Temel Sağlık"): ("Temel Sağlık & Anatomi", "alias_match", "Source label differs from the question taxonomy label."),
        ("Resim", "Temel Resim"): ("Temel Resim & Sanat", "alias_match", "Source label differs from the question taxonomy label."),
        ("Felsefe", "Epistemoloji & Bilgi"): ("Epistemoloji & Bilgi Felsefesi", "alias_match", "Source label differs from the question taxonomy label."),
        ("Fizik", "Elektromanyetizma & Kuantum"): ("Elektromanyetizma & Modern/Kuantum", "alias_match", "Source label differs from the question taxonomy label."),
        ("Spor", "Temel Spor"): ("Localize Sub", "placeholder_unresolved", "Question generation taxonomy still uses a localize-sub placeholder."),
        ("Edebiyat", "Temel Edebiyat"): ("Localize Sub", "placeholder_unresolved", "Question generation taxonomy still uses a localize-sub placeholder."),
        ("Müzik", "Klasik Müzik Efsaneleri"): ("Localize Sub", "placeholder_unresolved", "Question generation taxonomy still uses a localize-sub placeholder."),
        ("Müzik", "Pop, Rock & Caz Efsaneleri"): ("Localize Sub", "placeholder_unresolved", "Question generation taxonomy still uses a localize-sub placeholder."),
        ("Ekonomi", "Kripto Dünyası"): ("Kripto Dünyası & Şirketler", "combined_alias", "Question taxonomy merges this source subcategory with Şirketler."),
        ("Ekonomi", "Şirketler"): ("Kripto Dünyası & Şirketler", "combined_alias", "Question taxonomy merges this source subcategory with Kripto Dünyası."),
    }
    taxonomy_headers = ["category_id", "category_name", "sub_category_id", "source_subcategory", "question_algorithm_alias", "mapping_status", "character_count", "source_sheets", "notes"]
    taxonomy_rows = []
    for data in taxonomy.values():
        alias, status, note = category_aliases.get((data["category_name"], data["source_subcategory"]), (data["source_subcategory"], "exact_label", "Source and algorithm labels match; IDs remain source-derived."))
        taxonomy_rows.append([
            data["category_id"], data["category_name"], data["sub_category_id"], data["source_subcategory"],
            alias, status, data["character_count"], ", ".join(sorted(data["source_sheets"])), note,
        ])

    reference_headers = ["effect_type", "parameter_keys", "example_params_json", "trigger_values", "skill_row_count", "character_count", "review_count", "execution_contract"]
    reference_rows = []
    for kind, item in sorted(effect_examples.items()):
        reference_rows.append([
            kind, ", ".join(sorted(item["parameter_keys"])), json_cell(item["example_params"]),
            ", ".join(sorted(item["triggers"])), item["skill_rows"], len(item["characters"]), item["review_count"],
            "Apply effects in effects[].order; re-read current localized question fields at activation time.",
        ])

    question_rows = [
        ["weakest_distractor_index", "integer|null", "questions root", "Question authoring", "A=0/B=1/C=2/D=3; remapped by option balancing.", "eliminate_weakest_distractor"],
        ["strongest_distractor_index", "integer|null", "questions root", "Question authoring", "A=0/B=1/C=2/D=3; must be a plausible wrong option.", "eliminate_strongest_distractor, show_trap_note"],
        ["hint", "string|null", "translations.{locale}.hint", "Question authoring + translation", "Short hint that does not reveal the answer.", "show_hint"],
        ["distractor_trap_note", "string|null", "translations.{locale}.distractor_trap_note", "Question authoring + translation", "Why the strongest wrong option is wrong.", "show_trap_note"],
        ["keyword", "string|null", "translations.{locale}.keyword", "Question authoring + translation", "Exact localized phrase from the question stem.", "show_keyword"],
        ["logical_order", "array<int>|null", "questions root", "Question authoring", "Permutation of canonical 0..3 indices; option-balancing remaps it.", "sort_options"],
        ["binary_fallback", "map|null", "translations.{locale}.binary_fallback", "Question authoring + translation", "{statement: localized string, is_true: boolean}.", "binary_convert"],
        ["community_distribution", "array<int>|null", "questions root/runtime", "Simulated at save time, replaced by live vote aggregation", "Four percentages summing to 100; simulated until real votes exist (community_distribution_meta.source).", "show_community_distribution"],
        ["base_points", "integer", "questions root", "Question default", "Default 100; score abilities multiply or add to this value.", "score_multiplier, score_bonus_percent"],
        ["difficulty", "Kolay|Orta|Zor|null", "questions root", "Question authoring", "Global uses difficulty_global; local uses difficulty_local.", "difficulty-conditioned effects"],
        ["target_country_code", "string", "questions root", "Question persistence", "GL for global; exact region code from country list for local.", "scope guard"],
        ["countries", "array<string>", "questions root", "Question persistence", "Contains the same region code as target_country_code.", "scope guard"],
        ["option_difficulty_order", "array<int>|null", "questions root", "Question authoring", "New questions generate a 0-based [0..3] permutation from easiest-to-recognize to hardest-to-recognize for the target audience; option balancing remaps it. Legacy records may be null.", "sort_options"],
        ["simplified_question", "string", "translations.{locale}.simplified_question", "Question authoring + translation", "Generated and translated for each supported locale; preserve facts, constraints, and correct-answer mapping. Legacy records need a backfill or runtime transform.", "simplify_question"],
        ["clarified_options", "map<string,string>", "translations.{locale}.clarified_options", "Question authoring + translation", "Localized A-D map generated for each supported locale; preserve canonical option keys and correct-answer meaning. Legacy records need a backfill or runtime transform.", "clarify_options"],
    ]
    progression_rows = [
        ["points_source", "users/{uid}.categoryTotalScores.{category_name}", "read-only", "Question scoring", "All-time points the player earned from question scores in the character's category."],
        ["level_from_points", "rule", "derived", "App/server", "points < 50 locked; 50-99 lvl1; 100-199 lvl2; >= 200 lvl3 (characters.level_thresholds / unlock_threshold). Not stored."],
        ["(no users/{uid}/characters documents are created by the catalogue)", "-", "-", "App", "The app creates the fields below on demand."],
        ["daily_uses", "map", "{slot_1:0,slot_2:0,slot_3:0}", "Server transaction", "Reset by date; enforce slot-specific daily_limits from the character skill."],
        ["last_reset_date", "YYYY-MM-DD", "empty until initialized", "Device local clock", "reset_day_key = (device-local now - characters.daily_reset_hour_local hours).date(); different from last_reset_date -> daily_uses reset to 0 and key stored. Default 10:00 device-local, any timezone, 12h/24h irrelevant."],
        ["is_equipped", "boolean", False, "Server transaction", "Enforce one equipped character per category if that is the game rule."],
        ["schema_version", "string", CHARACTER_SCHEMA_VERSION, "Application", "Progression document contract version."],
    ]
    return {
        "characters": (character_headers, character_rows),
        "skills": (skill_headers, skill_rows),
        "locales": (locale_headers, locale_rows),
        "effects": (reference_headers, reference_rows),
        "question_fields": (["field_name", "type", "storage_path", "owner", "contract", "consuming_effects"], question_rows),
        "progression": (["field_name", "type", "initial_value", "authority", "contract"], progression_rows),
        "taxonomy": (taxonomy_headers, taxonomy_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True, choices=["characters", "skills", "locales", "effects", "question_fields", "progression", "taxonomy"])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=250)
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    tables = build_tables(catalog)
    headers, rows = tables[args.table]
    selected = rows[args.offset : args.offset + args.limit]
    print(json.dumps({"headers": headers, "offset": args.offset, "total": len(rows), "rows": selected}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
