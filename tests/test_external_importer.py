import json
import tempfile
import unittest
from pathlib import Path

from core.external_importer import (
    calculate_import_duration,
    parse_source_text,
    validate_and_transform,
)
from database.db_manager import save_question_to_firestore


class ExternalImporterTests(unittest.TestCase):
    def sample(self):
        return {
            "category": "Geography",
            "question": "Türkiye'nin başkenti hangisidir?",
            "options": ["İstanbul", "Ankara", "İzmir", "Bursa"],
            "answer": "Ankara",
            "difficulty": "easy",
            "explanation": "Türkiye'nin başkenti Ankara'dır.",
            "language": "tr",
        }

    def test_transforms_generic_record_to_knowley_schema(self):
        report = validate_and_transform([self.sample()], source="fixture.json")
        self.assertEqual(report.accepted_count, 1)
        question = report.accepted[0]
        self.assertEqual(question["categories"], ["Coğrafya"])
        self.assertIn(question["sub_categories"][0], question["sub_categories_meta"][0]["name"])
        self.assertEqual(question["correct_answer"], "B")
        self.assertIsNone(question["question_image_url"])
        self.assertEqual(question["generation_provider"], "EXTERNAL_IMPORT")

    def test_accepts_long_but_rejects_unenrichable_questions(self):
        long_row = self.sample()
        long_row["question"] = "x" * 161
        incomplete = self.sample()
        incomplete["options"] = ["A", "B", "C"]
        report = validate_and_transform([long_row, incomplete])
        self.assertEqual(report.accepted_count, 1)
        self.assertEqual(report.rejected_count, 1)
        self.assertEqual(report.accepted[0]["translations"]["tr"]["question"], "x" * 161)

    def test_parses_opentdb_style_json(self):
        payload = {
            "results": [{
                "category": "Sports",
                "question": "Futbolda bir takım sahada kaç oyuncuyla oynar?",
                "correct_answer": "11",
                "incorrect_answers": ["9", "10", "12"],
                "difficulty": "easy",
            }]
        }
        records = parse_source_text(json.dumps(payload), "questions.json")
        report = validate_and_transform(records)
        self.assertEqual(report.accepted_count, 1)
        self.assertEqual(report.accepted[0]["correct_answer"], "A")

    def test_full_schema_translation_uses_all_supported_languages(self):
        class TranslationManager:
            def generate_trivia(self, _system, _prompt):
                return {
                    "_provider": "fixture",
                    "translations": {
                        lang: {
                            "question": f"{lang} soru",
                            "options": {"A": f"{lang} A", "B": f"{lang} B", "C": f"{lang} C", "D": f"{lang} D"},
                            "explanation": f"{lang} açıklama",
                            "filter_tag": f"{lang} etiket",
                        }
                        for lang in ("tr", "en", "es", "pt", "de")
                    },
                }

        report = validate_and_transform(
            [self.sample()], translate_all=True, llm_manager=TranslationManager()
        )
        self.assertEqual(report.accepted_count, 1)
        question = report.accepted[0]
        self.assertEqual(question["supported_languages"], ["tr", "en", "es", "pt", "de"])
        self.assertEqual(set(question["translations"]), {"tr", "en", "es", "pt", "de"})
        self.assertEqual(question["translations"]["tr"]["question"], self.sample()["question"])
        self.assertEqual(question["generation_provider"], "EXTERNAL_IMPORT_FIXTURE")
        for field in ("created_at", "shuffle_key", "difficulty_meta", "difficulty_profile", "algorithm_source", "algorithm_version"):
            self.assertIn(field, question)

    def test_duration_formula_matches_shared_document(self):
        self.assertEqual(calculate_import_duration("x" * 21, "Kolay"), 13)
        self.assertEqual(calculate_import_duration("x" * 21, "Orta"), 15)
        self.assertEqual(calculate_import_duration("x" * 400, "Zor"), 35)

    def test_parses_and_enriches_jeopardy_tsv(self):
        text = (
            "round\tclue_value\tdaily_double_value\tcategory\tcomments\tanswer\tquestion\tair_date\tnotes\n"
            "1\t200\t0\tWORLD GEOGRAPHY\t\tOnly nation to cover an entire continent\tAustralia\t1983-09-18\t\n"
            "1\t200\t0\tWORLD GEOGRAPHY\t\tThis country's capital is Ottawa\tCanada\t1983-09-18\t\n"
            "1\t200\t0\tWORLD GEOGRAPHY\t\tThis country contains the city of Kyoto\tJapan\t1983-09-18\t\n"
            "1\t200\t0\tWORLD GEOGRAPHY\t\tThis country contains the city of Recife\tBrazil\t1983-09-18\t\n"
        )
        records = parse_source_text(text, "jeopardy.tsv")
        report = validate_and_transform(records, source="jeopardy.tsv")
        self.assertEqual(report.accepted_count, 4)
        first = report.accepted[0]
        self.assertEqual(first["categories"], ["Coğrafya"])
        self.assertEqual(first["dbVersion"], "1.1.0")
        self.assertNotIn("version", first)
        self.assertEqual(len(first["translations"]["en"]["options"]), 4)
        self.assertIn("Australia", first["translations"]["en"]["options"].values())

    def test_firestore_write_replaces_version_with_db_version(self):
        class Snapshot:
            exists = False

        class Document:
            def __init__(self):
                self.saved = None

            def get(self):
                return Snapshot()

            def set(self, value):
                self.saved = value

        class Collection:
            def __init__(self, document):
                self._document = document

            def document(self, _doc_id):
                return self._document

        class Database:
            def __init__(self):
                self.document = Document()
                self.collection_name = None

            def collection(self, name):
                self.collection_name = name
                return Collection(self.document)

        database = Database()
        question = {
            "version": "1.1.0",
            "categories": ["Mitoloji"],
            "categories_meta": [{"name": "Mitoloji", "color": "#000000"}],
            "translations": {"tr": {"question": "Türkiye'nin başkenti hangisidir?", "options": {"A": "İzmir", "B": "Ankara", "C": "Bursa", "D": "Antalya"}}},
            "correct_answer": "B",
            "correct_option": "B",
            "duration_local": 14,
            "duration_global": 14,
            "duration_seconds": 14,
        }
        self.assertTrue(save_question_to_firestore(database, question, collection_name="dbquestions"))
        self.assertEqual(database.collection_name, "dbquestions")
        self.assertEqual(database.document.saved["dbVersion"], "1.1.0")
        self.assertEqual(database.document.saved["categories_meta"], [{"name": "Mitoloji", "color": "#AA00FF"}])
        self.assertNotIn("version", database.document.saved)


if __name__ == "__main__":
    unittest.main()
