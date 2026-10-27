"""Typed questions (true_false / fill_blank) must write the same Firestore fields as multiple_choice.

Runs the REAL generation + worker pipeline with a fake LLM, fake translation fan-out and a capturing fake db,
then compares the full dict handed to Firestore per type against multiple_choice.
"""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import generator
from core.question_types import ALL_QUESTION_COLLECTIONS, COLLECTION_BY_TYPE, collection_for
from core.sheet_algorithm import SheetAlgorithmRepository
from core.translation_service import SUPPORTED_LANGUAGE_CODES
from tests.test_question_types import payload
from worker import GenerationOptions, run_generation

# Differences that follow from the type itself.
ALLOWED_MISSING = {
    "true_false": set(),
    "fill_blank": set(),
}
ALLOWED_NULL = {  # keys present in both, but null for the type
    "true_false": {
        "weakest_distractor_index", "strongest_distractor_index",  # no distractors worth ranking with two options
        "logical_order", "option_difficulty_order",                 # permutations of 0-3; contract is 4 indices
    },
    "fill_blank": set(),
}
TYPE_VALUE_FIELDS = {"question_type", "correct_answer", "correct_option", "question_hash", "shuffle_key", "created_at",
                     "translations", "payload", "algorithm_source", "duration_local", "duration_global",
                     "duration_seconds", "topic_entity"}


class CaptureDb:
    def __init__(self):
        self.written = {}  # collection -> [dict]
        self.reads = []

    def collection(self, name):
        db = self

        class Col:
            def where(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def stream(self, *a, **k):
                db.reads.append((name, "seed"))
                return iter(())

            def document(self, doc_id):
                class Doc:
                    def get(self):
                        db.reads.append((name, doc_id))
                        return SimpleNamespace(exists=False)

                    def set(self, data):
                        db.written.setdefault(name, []).append(data)
                return Doc()
        return Col()


def run_pipeline(qtype):
    """One question of `qtype` through run_generation (generation_mode matches the type)."""
    data = {**payload(qtype), "_provider": "gemini", "topic_entity": "Jupiter"}
    manager = Mock()
    manager.generate_trivia.return_value = data
    manager.status = lambda live=False: {}

    def fake_translate(source, source_language, langs, **kw):
        return {lang: copy.deepcopy(source) for lang in langs}, "P"

    db = CaptureDb()
    mode = "basic" if qtype == "multiple_choice" else "question_types"
    options = GenerationOptions(
        question_count=1, generation_mode=mode, difficulty=None, combo_percent=0, sleep_between_requests=0,
        write_to_firestore=True, concurrency=1, visual_questions_enabled=False,
        **({} if qtype == "multiple_choice" else {"question_types": qtype}),
    )
    with patch.object(generator, "translate_localized_content", side_effect=fake_translate):
        summary = run_generation(options, repository=SheetAlgorithmRepository(), llm_manager=manager, db=db)
    assert summary["produced"] == 1, summary
    (collection, docs), = db.written.items()
    return collection, docs[0], db


class CollectionRoutingTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(collection_for("multiple_choice"), "questions")
        self.assertEqual(collection_for(None), "questions")
        self.assertEqual(collection_for("fill_blank"), "fsquestions")
        self.assertEqual(collection_for("true_false"), "tfquestions")
        self.assertEqual(set(ALL_QUESTION_COLLECTIONS), {"questions", "fsquestions", "tfquestions"})

    def test_each_type_lands_in_its_collection_and_dedupe_checks_both(self):
        for qtype, expected in COLLECTION_BY_TYPE.items():
            collection, doc, db = run_pipeline(qtype)
            self.assertEqual(collection, expected)
            self.assertEqual(doc["question_type"], qtype)
            self.assertIn((expected, doc["question_hash"]), db.reads)
            self.assertIn((expected, "seed"), db.reads)  # topic-diversity seed reads the type's own collection
            if expected != "questions":
                self.assertIn(("questions", doc["question_hash"]), db.reads)  # legacy pool is checked too
                self.assertIn(("questions", "seed"), db.reads)


class FieldParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = {qtype: run_pipeline(qtype)[1] for qtype in COLLECTION_BY_TYPE}

    def test_key_sets_match_normal_question(self):
        normal = set(self.docs["multiple_choice"]) - {"starting_difficulty"}  # conditional: only 4 of every 10 normal questions
        for qtype in ("true_false", "fill_blank"):
            typed = set(self.docs[qtype]) - {"starting_difficulty"}
            self.assertEqual(normal - typed, ALLOWED_MISSING[qtype] & normal, f"{qtype}: missing vs normal")
            self.assertFalse(typed - normal, f"{qtype}: extra keys {typed - normal}")

    def test_null_only_where_type_requires(self):
        for qtype in ("true_false", "fill_blank"):
            for key, value in self.docs[qtype].items():
                if value is None and self.docs["multiple_choice"].get(key) is not None:
                    self.assertIn(key, ALLOWED_NULL[qtype], f"{qtype}.{key} is null but normal has a value")

    def test_non_type_values_have_same_shape(self):
        normal = self.docs["multiple_choice"]
        for qtype in ("true_false", "fill_blank"):
            doc = self.docs[qtype]
            for key in normal:
                if key in TYPE_VALUE_FIELDS or key not in doc or key in ALLOWED_NULL[qtype] or normal[key] is None:
                    continue
                self.assertIs(type(doc[key]), type(normal[key]), f"{qtype}.{key}")

    def test_community_distribution_has_one_percentage_per_option(self):
        for qtype, count in (("multiple_choice", 4), ("fill_blank", 4), ("true_false", 2)):
            doc = self.docs[qtype]
            self.assertEqual(len(doc["community_distribution"]), count, qtype)
            self.assertEqual(sum(doc["community_distribution"]), 100, qtype)
            self.assertEqual(doc["community_distribution_meta"]["source"], "simulated", qtype)

    def test_required_pipeline_fields_present(self):
        for qtype, doc in self.docs.items():
            for key in ("difficulty", "difficulty_local", "difficulty_local_score", "difficulty_global",
                        "difficulty_global_score", "difficulty_profile", "difficulty_meta",
                        "duration_local", "duration_global", "duration_seconds", "categories_meta",
                        "sub_categories_meta", "algorithm_source", "shuffle_key", "topic_entity", "created_at", "scope",
                        "target_country", "target_country_code", "base_points", "supported_languages", "question_hash"):
                self.assertIn(key, doc, f"{qtype} lacks {key}")
            self.assertTrue(doc["categories_meta"] and doc["categories_meta"][0]["color"])
            self.assertEqual(doc["algorithm_source"]["question_type"], qtype)

    def test_translation_fields_and_languages_match(self):
        normal = self.docs["multiple_choice"]["translations"]
        for qtype in ("true_false", "fill_blank"):
            typed = self.docs[qtype]["translations"]
            self.assertEqual(set(typed), set(normal))
            self.assertEqual(set(typed), set(SUPPORTED_LANGUAGE_CODES))
            letters = "AB" if qtype == "true_false" else "ABCD"
            for lang, content in typed.items():
                self.assertEqual(set(content) - {"options", "clarified_options"},
                                 set(normal[lang]) - {"options", "clarified_options"})
                self.assertEqual(set(content["options"]), set(letters), lang)
                self.assertEqual(set(content["clarified_options"]), set(letters), lang)
                for field in ("hint", "distractor_trap_note", "keyword", "explanation", "filter_tag",
                              "simplified_question"):
                    self.assertTrue(content[field], f"{qtype}.{lang}.{field}")
                if qtype == "fill_blank":
                    self.assertEqual(content["question"].count("_____"), 1, lang)
                    self.assertEqual(content["simplified_question"].count("_____"), 1, lang)
                else:
                    self.assertTrue(content["binary_fallback"]["is_true"], lang)


class TypedCollectionReadersTests(unittest.TestCase):
    def test_two_option_distribution_is_valid_for_many_ids(self):
        from core.community_distribution import simulate_distribution

        for i in range(300):
            for answer in "AB":
                result = simulate_distribution(f"id{i}", {"question_type": "true_false", "correct_answer": answer,
                                                           "difficulty": ("Kolay", "Orta", "Zor")[i % 3]})
                distribution, _ = result
                self.assertEqual((len(distribution), sum(distribution)), (2, 100))
                self.assertGreater(max(distribution), 50)
        self.assertIsNone(simulate_distribution("x", {"question_type": "true_false", "correct_answer": "C"}))

    def test_audit_and_report_read_every_collection(self):
        from database.question_audit import fetch_question_audit
        from database.question_report import fetch_question_report

        seen = []

        class Query:
            def __init__(self, name):
                self.name = name

            def select(self, *a, **k):
                seen.append(self.name)
                return self

            def stream(self):
                return iter(())

        class Db:
            def collection(self, name):
                return Query(name)

        fetch_question_audit(Db())
        fetch_question_report(Db())
        self.assertEqual(sorted(seen), sorted(list(ALL_QUESTION_COLLECTIONS) * 2))


if __name__ == "__main__":
    unittest.main()
