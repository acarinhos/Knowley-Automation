"""Build one application data-fetch and execution contract per global skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "global_character_catalog.json"

PLAYER_READS = [
    "users/{uid}.categoryTotalScores.{category_name}",  # points_source: all-time category points
    "characters/{character_id}.unlock_threshold",
    "characters/{character_id}.level_thresholds",
    # level_from_points: <50 locked, 50-99 lvl1, 100-199 lvl2, >=200 lvl3 (derived, never stored)
    "users/{uid}/characterProgress/{characterId}.daily_uses.slot_{skill_slot}",
    "users/{uid}/characterProgress/{characterId}.last_reset_date",  # compare with reset_day_key
    "characters/{character_id}.daily_reset_hour_local",  # 10: reset_day_key = (device-local now - 10h).date(); daily_uses reset when it differs from last_reset_date
    "users/{uid}/characterProgress/{characterId}.is_equipped",
    "characters/{character_id}.skills[{skill_slot}].daily_limits.lvl{level_from_points}",
    "characters/{character_id}.skills[{skill_slot}].cooldown_questions",
]
BASE_Q = [
    "questions/{question_id}.correct_answer",
    "questions/{question_id}.translations.{locale}.options",
]
BASE_MATCH = [
    "match.current_question_id",
    "match.current_question_answer_state",
]

# Source paths are explicit contracts. Services whose collection/schema is not
# present in this repo are called out instead of assigning a made-up Firestore path.
RULES: dict[str, dict[str, Any]] = {
    "auto_correct": {
        "q": ["questions/{question_id}.correct_answer"],
        "match": ["match.current_question_id", "match.score_state", "match.attempt_state"],
        "services": ["authoritative_score_transaction"], "authority": "server_authoritative",
        "behavior": "Resolve the current answer as correct and apply the configured score rule; do not reveal the answer before resolution.",
        "validation": "The question is active, unanswered, and the effect trigger/conditions match.",
    },
    "binary_convert": {
        "q": ["questions/{question_id}.translations.{locale}.binary_fallback.statement", "questions/{question_id}.translations.{locale}.binary_fallback.is_true"],
        "match": ["match.current_question_answer_state"], "services": ["server_answer_validation"], "authority": "hybrid",
        "behavior": "Replace four options with localized true/false statement; grade using binary_fallback.is_true.",
        "validation": "Both localized statement and is_true are present; preserve the boolean unchanged.",
    },
    "clarify_options": {
        "q": ["questions/{question_id}.translations.{locale}.clarified_options", "questions/{question_id}.translations.{locale}.options", "questions/{question_id}.correct_answer"],
        "missing": ["Legacy questions may lack translations.{locale}.clarified_options; new question generation now creates it."],
        "match": ["match.current_question_answer_state"], "services": [], "authority": "client_with_server_validation",
        "behavior": "Show the precomputed clarified localized A-D options while preserving canonical keys and the correct answer.",
        "validation": "All four helper options exist and the correct option remains semantically unchanged; otherwise disable this ability without consuming a use.",
    },
    "complete_question_safely": {
        "q": [], "match": ["match.current_question_id", "match.health_state", "match.streak_state", "match.score_state"],
        "services": ["authoritative_match_transaction"], "authority": "server_authoritative",
        "behavior": "Close the current question without the configured penalty; do not infer correctness or award unspecified points.",
        "validation": "Apply only at the documented completion/timeout event and only once.",
    },
    "double_choice": {
        "q": BASE_Q, "match": BASE_MATCH + ["match.selected_option_keys"],
        "services": ["server_answer_validation"], "authority": "hybrid",
        "behavior": "Allow at most params.max_selections options; resolve as correct if any selected option is correct when success_if_any_correct is true.",
        "validation": "Selection count does not exceed the parameter; submitted option keys belong to the current question.",
    },
    "eliminate_contradictory_option": {
        "q": BASE_Q + ["questions/{question_id}.strongest_distractor_index"], "match": ["match.visible_option_keys"],
        "services": [], "authority": "client_with_server_validation",
        "behavior": "Hide the configured contradictory/strongest distractor index while retaining the correct answer.",
        "validation": "Index is 0..3 and differs from the correct answer index.",
    },
    "eliminate_strongest_distractor": {
        "q": BASE_Q + ["questions/{question_id}.strongest_distractor_index"], "match": ["match.visible_option_keys"],
        "services": [], "authority": "client_with_server_validation",
        "behavior": "Hide params.count option(s) selected by params.index_source; never remove the correct option.",
        "validation": "Required distractor indices are valid, unique, and differ from the correct answer index.",
    },
    "eliminate_to_two_options": {
        "q": BASE_Q + ["questions/{question_id}.strongest_distractor_index", "questions/{question_id}.weakest_distractor_index"], "match": ["match.visible_option_keys"],
        "services": [], "authority": "client_with_server_validation",
        "behavior": "Leave the correct option and strongest distractor visible; hide the other two wrong options to reach params.remaining_options.",
        "validation": "Correct and strongest indices are valid and distinct; never hide the answer.",
    },
    "eliminate_weakest_distractor": {
        "q": BASE_Q + ["questions/{question_id}.weakest_distractor_index"], "match": ["match.visible_option_keys"],
        "services": [], "authority": "client_with_server_validation",
        "behavior": "Hide params.count option(s) selected by params.index_source; never remove the correct option.",
        "validation": "Required distractor indices are valid, unique, and differ from the correct answer index.",
    },
    "eliminate_wrong_options": {
        "q": BASE_Q + ["questions/{question_id}.weakest_distractor_index", "questions/{question_id}.strongest_distractor_index"], "match": ["match.visible_option_keys"],
        "services": [], "authority": "client_with_server_validation",
        "behavior": "Hide params.count wrong options according to params.selection_strategy; preserve the correct answer.",
        "validation": "Selection strategy resolves to valid wrong-option indices and remaining options include the answer.",
    },
    "extend_timer": {
        "q": ["questions/{question_id}.duration_seconds", "questions/{question_id}.duration_global", "questions/{question_id}.duration_local"],
        "match": ["match.timer.deadline", "match.timer.remaining_seconds"], "services": ["authoritative_match_timer"], "authority": "server_authoritative",
        "behavior": "Add params.duration_seconds to the active timer when all params.conditions hold.",
        "validation": "Apply once; reject if trigger/condition is false or the question is already closed.",
    },
    "freeze_timer": {
        "q": ["questions/{question_id}.duration_seconds", "questions/{question_id}.duration_global", "questions/{question_id}.duration_local"],
        "match": ["match.timer.deadline", "match.timer.frozen_until"], "services": ["authoritative_match_timer"], "authority": "server_authoritative",
        "behavior": "Pause the timer for params.duration_seconds; resume from stored remaining time.",
        "validation": "A reviewed positive duration is required; effect rows flagged review_required must remain disabled until reviewed.",
    },
    "grant_free_skill_use": {
        "q": [], "match": ["match.current_question_id", "match.pending_skill_grants"],
        "services": ["authoritative_progression_transaction", "skill_type_resolver"], "authority": "server_authoritative",
        "behavior": "Grant params.charges to the next compatible skill resolved from params.next_skill_type.",
        "validation": "Resolve exactly one eligible target skill; atomically record and consume the grant.",
    },
    "lock_correct_option": {
        "q": BASE_Q, "match": ["match.visible_option_keys", "match.locked_option_keys"],
        "services": [], "authority": "client_with_server_validation",
        "behavior": "Pin/lock the option identified by params.source_field so it cannot be removed or changed.",
        "validation": "The source field resolves to a valid option key in the current question.",
    },
    "lock_or_promote_correct_option": {
        "q": BASE_Q, "match": ["match.visible_option_keys", "match.option_order", "match.locked_option_keys"],
        "services": [], "authority": "client_with_server_validation",
        "behavior": "Apply the configured lock/promote presentation action to the correct option without changing answer identity.",
        "validation": "The source field resolves to a valid option key; preserve canonical answer key for grading.",
    },
    "promote_correct_option": {
        "q": BASE_Q, "match": ["match.option_order"], "services": [], "authority": "client_with_server_validation",
        "behavior": "Move the correct option to the first display position without changing its canonical key.",
        "validation": "The source field resolves to a valid option key and grading remains key-based.",
    },
    "protect_health": {
        "q": [], "match": ["match.health.current", "match.health.max", "match.pending_health_protections"],
        "services": ["authoritative_match_transaction"], "authority": "server_authoritative",
        "behavior": "On the configured wrong-answer event, consume one charge and prevent the configured health loss for params.scope.",
        "validation": "Register before answer resolution; atomically consume only when a qualifying wrong answer occurs.",
    },
    "protect_streak": {
        "q": [], "match": ["match.streak.current", "match.pending_streak_protections"],
        "services": ["authoritative_match_transaction"], "authority": "server_authoritative",
        "behavior": "On the configured wrong-answer event, preserve the streak for params.scope and consume a finite charge when applicable.",
        "validation": "Match duration/charges must be explicit; do not convert unlimited to a finite value silently.",
    },
    "recharge_random_skill": {
        "q": [], "match": [], "services": ["authoritative_progression_transaction", "secure_random_skill_selector"], "authority": "server_authoritative",
        "behavior": "Select one eligible depleted skill and restore its daily-use counter to the current-level limit.",
        "validation": "Select only from unlocked/equipped eligible slots and apply the reset atomically with the daily limit.",
    },
    "reset_timer": {
        "q": ["questions/{question_id}.duration_seconds", "questions/{question_id}.duration_global", "questions/{question_id}.duration_local"],
        "match": ["match.timer.deadline", "match.timer.remaining_seconds"], "services": ["authoritative_match_timer"], "authority": "server_authoritative",
        "behavior": "Reset the active timer to the scope-appropriate question duration.",
        "validation": "Question remains active; reset only once per successful activation.",
    },
    "restore_health": {
        "q": [], "match": ["match.health.current", "match.health.max"],
        "services": ["authoritative_match_transaction"], "authority": "server_authoritative",
        "behavior": "Increase health by params.amount, capped at match.health.max.",
        "validation": "Amount is a reviewed nonnegative number; apply atomically and cap at max health.",
    },
    "retry_answer": {
        "q": BASE_Q, "match": BASE_MATCH + ["match.attempts_remaining"],
        "services": ["server_answer_validation"], "authority": "hybrid",
        "behavior": "Grant params.additional_attempts on the current question without advancing the question pointer.",
        "validation": "Question is still open and the extra attempt has not already been granted.",
    },
    "scale_timer_rate": {
        "q": ["questions/{question_id}.duration_seconds", "questions/{question_id}.duration_global", "questions/{question_id}.duration_local"],
        "match": ["match.timer.rate_factor", "match.timer.deadline"], "services": ["authoritative_match_timer"], "authority": "server_authoritative",
        "behavior": "Apply params.factor to countdown rate for params.duration_seconds.",
        "validation": "A reviewed positive factor and duration are required; review_required effects stay disabled.",
    },
    "score_bonus_percent": {
        "q": ["questions/{question_id}.base_points", "questions/{question_id}.difficulty"],
        "match": ["match.score.question", "match.score.total", "match.streak.current", "match.category_scores"],
        "services": ["authoritative_score_transaction"], "authority": "server_authoritative",
        "behavior": "Apply params.percent to the target named by params.applies_to; use the match ledger for match/streak/category totals.",
        "validation": "Apply once at the matching trigger; define rounding centrally and prevent duplicate event delivery.",
    },
    "score_multiplier": {
        "q": ["questions/{question_id}.base_points", "questions/{question_id}.difficulty"],
        "match": ["match.score.question", "match.score.total"], "services": ["authoritative_score_transaction"], "authority": "server_authoritative",
        "behavior": "Multiply the target named by params.applies_to by params.multiplier; honor params.conditions and duration scope.",
        "validation": "A positive multiplier is required; apply once and keep final score server-owned.",
    },
    "show_answer_pattern": {
        "q": BASE_Q, "match": [], "services": [], "authority": "client_with_server_validation",
        "behavior": "Derive the requested first letter, word length, or first word from the localized correct option text.",
        "validation": "Resolve correct_answer to an existing localized option; reveal only the configured pattern fields.",
    },
    "show_answer_probability_distribution": {
        "q": BASE_Q, "match": ["match.answer_probability_by_option"],
        "services": ["answer_probability_model (not present in this repo)"], "authority": "server_authoritative",
        "behavior": "Show the model's per-option probability distribution, not the correct answer key.",
        "validation": "Distribution has one value per option, is normalized, and is based on an approved model/data source.",
    },
    "show_answer_probability_hint": {
        "q": BASE_Q, "match": ["match.answer_probability_by_option"],
        "services": ["answer_probability_model (not present in this repo)"], "authority": "server_authoritative",
        "behavior": "Show a non-answer-revealing hint derived from the approved per-option probability model.",
        "validation": "Model output exists and does not expose the answer key directly.",
    },
    "show_community_distribution": {
        "q": ["questions/{question_id}.community_distribution (simulated at save time; meta.source=simulated)"],
        "match": ["match.live_vote_aggregate_by_option"],
        "services": ["live_vote_aggregation (collection/schema not present in this repo)"], "authority": "server_authoritative",
        "behavior": "Show per-option community vote percentages for the active question; prefer live votes once they exist, otherwise use the cached (simulated) field.",
        "validation": "Require a minimum vote sample, normalize percentages to 100, and simulated until real votes exist (community_distribution_meta.source).",
    },
    "show_correct_option_letter": {
        "q": ["questions/{question_id}.correct_answer"], "match": [], "services": [], "authority": "client_with_server_validation",
        "behavior": "Show the correct option letter only after the game rule permits revealing it.",
        "validation": "Check the reveal phase; do not leak the answer before lock-in.",
    },
    "show_explanation": {
        "q": ["questions/{question_id}.translations.{locale}.explanation"], "match": [], "services": [], "authority": "client_presentation",
        "behavior": "Display the explanation in the current question locale.",
        "validation": "Locale field is nonempty; otherwise disable the effect or use the app's explicitly configured fallback locale.",
    },
    "show_hint": {
        "q": ["questions/{question_id}.translations.{locale}.hint"], "match": [], "services": [], "authority": "client_presentation",
        "behavior": "Display the localized hint without changing answer or score state.",
        "validation": "Locale field is nonempty and does not directly disclose the answer.",
    },
    "show_keyword": {
        "q": ["questions/{question_id}.translations.{locale}.keyword"], "match": [], "services": [], "authority": "client_presentation",
        "behavior": "Highlight the localized keyword in the question stem.",
        "validation": "Keyword occurs in the localized stem; if not, display as a separate clue rather than altering the stem.",
    },
    "show_trap_note": {
        "q": ["questions/{question_id}.translations.{locale}.distractor_trap_note", "questions/{question_id}.strongest_distractor_index"],
        "match": ["match.visible_option_keys"], "services": [], "authority": "client_presentation",
        "behavior": "Display the localized explanation for the strongest distractor after the configured reveal stage.",
        "validation": "Localized note and a valid wrong-option index exist; do not expose before the allowed phase.",
    },
    "simplify_question": {
        "q": ["questions/{question_id}.translations.{locale}.simplified_question", "questions/{question_id}.translations.{locale}.question"],
        "missing": ["Legacy questions may lack translations.{locale}.simplified_question; new question generation now creates it."],
        "match": [], "services": [], "authority": "client_with_server_validation",
        "behavior": "Present the precomputed simplified localized question while preserving facts, constraints, and answer mapping.",
        "validation": "Require a nonempty helper field; otherwise disable this ability without consuming a use.",
    },
    "skip_replace_question": {
        "q": ["questions/{question_id}.scope", "questions/{question_id}.target_country_code", "questions/{question_id}.supported_languages", "questions/{question_id}.categories", "questions/{question_id}.sub_categories", "questions/{question_id}.status", "questions/{question_id}.is_active"],
        "match": ["match.current_question_id", "match.served_question_ids", "match.score_state", "match.health_state", "match.streak_state"],
        "services": ["question_bank_query", "authoritative_match_transaction"], "authority": "server_authoritative",
        "behavior": "Select an unserved, published, active replacement matching the same scope, target country, category/subcategory, and supported locale; apply params.no_penalty/bonus_points.",
        "validation": "Never repeat the current/served question; require a matching candidate before consuming the skill charge.",
    },
    "sort_options": {
        "q": BASE_Q, "match": ["match.option_order"],
        "services": [], "authority": "client_with_server_validation",
        "behavior": "Reorder display options using logical_order for that sort_by mode while retaining canonical answer keys.",
        "validation": "logical_order must be a permutation of option indices; when sort_by is difficulty/probability, a matching metric must be provided by an approved service.",
    },
    "transfer_previous_question_bonus": {
        "q": ["questions/{previous_question_id}.base_points"], "match": ["match.previous_question_score", "match.score.question", "match.current_question_id"],
        "services": ["authoritative_score_transaction"], "authority": "server_authoritative",
        "behavior": "Transfer the documented bonus from the previous question to the current question exactly once.",
        "validation": "Previous and current question IDs differ; transaction ledger prevents duplicate transfer.",
    },
    "manual_review": {
        "q": [], "match": [], "services": ["human_effect_mapping_review"], "authority": "disabled_until_review",
        "behavior": "Do not execute; preserve source prose for manual mapping.",
        "validation": "Must be replaced with an explicit supported effect before activation.",
    },
}


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def paths_for(effect: dict[str, Any], base_paths: list[str]) -> list[str]:
    paths = list(base_paths)
    params = effect.get("params") or {}
    source = params.get("source_field")
    if isinstance(source, str) and source:
        paths.append(source if source.startswith("questions/") else f"questions/{{question_id}}.{source}")
    index_source = params.get("index_source")
    if isinstance(index_source, str):
        paths.append(f"questions/{{question_id}}.{index_source}")
    conditions = effect.get("conditions") or {}
    if "difficulty" in conditions:
        paths.append("questions/{question_id}.difficulty")
    if "answer_within_seconds" in conditions or "timer_remaining_seconds_lte" in conditions or conditions.get("trigger") == "on_timeout":
        paths.extend(["match.timer.remaining_seconds", "match.timer.deadline"])
    return unique(paths)


def build_rows(catalog: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "skill_id", "character_id", "character_name", "category_id", "sub_category_id", "skill_slot",
        "skill_name_source", "source_description_tr", "ordered_effects_and_params_json", "question_read_paths_json",
        "question_fields_or_services_missing_json", "player_state_read_paths_json", "match_runtime_dependencies_json",
        "runtime_services_json", "execution_authority", "parameter_review_required", "parameter_review_notes",
        "runtime_readiness", "source_sheet", "source_row",
    ]
    rows: list[list[Any]] = []
    used_types: set[str] = set()
    for character in catalog["characters"]:
        for skill in character["skills"]:
            effect_plans = []
            question_reads: list[str] = []
            missing: list[str] = []
            match_reads: list[str] = []
            services: list[str] = []
            authorities: list[str] = []
            review_notes: list[str] = []
            review_required = False
            for item in skill["effects"]:
                kind = item["type"]
                used_types.add(kind)
                rule = RULES.get(kind)
                if rule is None:
                    rule = RULES["manual_review"]
                    missing.append(f"Unknown effect type: {kind}")
                    review_required = True
                qpaths = paths_for(item, rule.get("q", []))
                effect_match = list(rule.get("match", []))
                effect_services = list(rule.get("services", []))
                if kind == "sort_options":
                    sort_by = (item.get("params") or {}).get("sort_by", "logical_order")
                    if sort_by == "logical_order":
                        qpaths.append("questions/{question_id}.logical_order")
                    elif sort_by == "difficulty":
                        qpaths.append("questions/{question_id}.option_difficulty_order")
                        missing.append("Legacy questions may lack option_difficulty_order; new question generation now creates it.")
                    elif sort_by == "probability":
                        effect_match.append("match.answer_probability_by_option")
                        effect_services.append("answer_probability_model (not present in this repo)")
                question_reads.extend(qpaths)
                missing.extend(rule.get("missing", []))
                match_reads.extend(effect_match)
                services.extend(effect_services)
                authorities.append(rule.get("authority", "disabled_until_review"))
                inferred = bool(item.get("review_required"))
                review_required = review_required or inferred or kind == "manual_review"
                if inferred or kind == "manual_review":
                    review_notes.append(str(item.get("review_note") or "Effect contract needs human resolution."))
                elif item.get("resolution_note"):
                    review_notes.append(str(item["resolution_note"]))
                effect_plans.append({
                    "order": item.get("order"),
                    "type": kind,
                    "trigger": item.get("trigger", "on_activate"),
                    "params": item.get("params", {}),
                    "conditions": item.get("conditions", {}),
                    "review_required": inferred,
                    "review_note": item.get("review_note"),
                })

            source = character["source"]
            engine = "CATALOG_ONLY; runtime handler is not present in this repository"
            if review_required:
                engine += "; disable until parameter review is complete"
            rows.append([
                skill["skill_id"], character["id"], character["name"], character["category_id"],
                character["sub_category_id"], skill["skill_slot"], skill["name"], skill["source_description"],
                json.dumps(effect_plans, ensure_ascii=False, separators=(",", ":")),
                json.dumps(unique(question_reads), ensure_ascii=False, separators=(",", ":")),
                json.dumps(unique(missing), ensure_ascii=False, separators=(",", ":")),
                json.dumps(PLAYER_READS, ensure_ascii=False, separators=(",", ":")),
                json.dumps(unique(match_reads), ensure_ascii=False, separators=(",", ":")),
                json.dumps(unique(services), ensure_ascii=False, separators=(",", ":")),
                "; ".join(unique(authorities)),
                review_required,
                "; ".join(unique(review_notes)),
                engine,
                source["sheet"],
                source["row"],
            ])

    unmapped = used_types - set(RULES)
    if unmapped:
        raise ValueError(f"Effect types lack a runtime contract: {', '.join(sorted(unmapped))}")
    if len(rows) != 687:
        raise ValueError(f"Expected 687 global skill rows, got {len(rows)}")
    return headers, rows


def build_effect_rows(catalog: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "effect_type", "parameter_keys", "example_params_json", "trigger_values", "skill_row_count",
        "character_count", "review_count", "execution_contract", "required_question_paths_json",
        "match_state_dependencies_json", "required_runtime_services_json", "execution_authority",
        "validation_contract", "missing_data_policy",
    ]
    aggregates: dict[str, dict[str, Any]] = {}
    for character in catalog["characters"]:
        for skill in character["skills"]:
            for item in skill["effects"]:
                kind = item["type"]
                rule = RULES.get(kind, RULES["manual_review"])
                entry = aggregates.setdefault(kind, {
                    "params": set(), "example": item.get("params", {}), "triggers": set(),
                    "skills": set(), "characters": set(), "review": 0, "q": set(), "match": set(),
                    "services": set(), "authorities": set(),
                })
                entry["params"].update((item.get("params") or {}).keys())
                entry["triggers"].add(item.get("trigger", "on_activate"))
                entry["skills"].add(skill["skill_id"])
                entry["characters"].add(character["id"])
                entry["review"] += int(bool(item.get("review_required") or kind == "manual_review"))
                entry["q"].update(paths_for(item, rule.get("q", [])))
                entry["match"].update(rule.get("match", []))
                entry["services"].update(rule.get("services", []))
                entry["authorities"].add(rule.get("authority", "disabled_until_review"))
                if kind == "sort_options":
                    entry["q"].add("questions/{question_id}.option_difficulty_order (not currently generated)")
                    entry["services"].add("answer_probability_model (required when sort_by=probability)")
                    entry["match"].add("match.answer_probability_by_option (required when sort_by=probability)")

    rows = []
    for kind, entry in sorted(aggregates.items()):
        rule = RULES.get(kind, RULES["manual_review"])
        missing = list(rule.get("missing", []))
        if kind == "sort_options":
            missing.append("questions.option_difficulty_order is not generated; probability order needs an approved option probability model")
        rows.append([
            kind,
            ", ".join(sorted(entry["params"])),
            json.dumps(entry["example"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ", ".join(sorted(entry["triggers"])),
            len(entry["skills"]),
            len(entry["characters"]),
            entry["review"],
            rule.get("behavior", "Unsupported; do not execute."),
            json.dumps(sorted(entry["q"]), ensure_ascii=False, separators=(",", ":")),
            json.dumps(sorted(entry["match"]), ensure_ascii=False, separators=(",", ":")),
            json.dumps(sorted(entry["services"]), ensure_ascii=False, separators=(",", ":")),
            "; ".join(sorted(entry["authorities"])),
            rule.get("validation", "Unsupported type; stop and review."),
            "If any required path, service, or condition is unavailable, disable the entire skill and do not consume a use; never fabricate answer, score, vote, probability, or timer data."
            + (" Missing: " + "; ".join(missing) if missing else ""),
        ])
    return headers, rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", choices=["skills", "effects"], default="skills")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    headers, rows = build_rows(catalog) if args.table == "skills" else build_effect_rows(catalog)
    selected = rows[args.offset : args.offset + args.limit]
    print(json.dumps({"headers": headers, "offset": args.offset, "total": len(rows), "rows": selected}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
