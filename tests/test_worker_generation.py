import unittest
import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.sheet_algorithm import SheetAlgorithmRepository
from worker import GenerationOptions, run_generation, save_worker_question
from core.visual_questions import (
    VisualQuestionSchedule,
    is_visual_target,
    prioritize_visual_targets,
)
from core.generator import build_prompt, generate_question_with_fallback, normalize_generated_payload
from core.llm_manager import AllProvidersCoolingDown


class FakeManager:
    def status(self, live=False):
        return {"fake": {"configured": True}}


def fake_question(**kwargs):
    category = kwargs["primary_category"]
    subcategory = kwargs["target_subcategory"]
    return SimpleNamespace(
        categories=[category],
        sub_categories=[subcategory],
        translations={"tr": SimpleNamespace(question=f"{category} / {subcategory}")},
        generation_provider="FAKE",
        categories_meta=[],
        sub_categories_meta=[],
        algorithm_source=None,
        is_visual_question=bool(kwargs.get("visual_question")),
        question_image_search_query="recognizable subject" if kwargs.get("visual_question") else None,
        question_image_alt_text="Ayırt edici bir nesne" if kwargs.get("visual_question") else None,
        question_image_url=None,
    )


class FakeImageResolver:
    def __init__(self):
        self.resolved = []

    def resolve(self, question):
        question.question_image_url = f"https://example.test/{len(self.resolved)}.jpg"
        question.question_image_search_query = None
        self.resolved.append(question)
        return question.question_image_url


class NonBlockingStopEvent:
    def __init__(self):
        self.waits = []
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waits.append(seconds)
        return False

    def set(self):
        self.stopped = True


class RunGenerationCompletionTests(unittest.TestCase):
    def test_temporary_provider_outage_waits_then_resumes(self):
        repository = SheetAlgorithmRepository()
        events = []
        stop_event = NonBlockingStopEvent()
        options = GenerationOptions(
            question_count=1,
            generation_mode="basic",
            difficulty="Orta",
            combo_percent=0,
            sleep_between_requests=0,
            max_retries=3,
            write_to_firestore=False,
            visual_questions_enabled=False,
        )
        with patch(
            "worker.generate_question_with_fallback",
            side_effect=[
                AllProvidersCoolingDown(12, "temporary"),
                fake_question(
                    primary_category="Genel",
                    target_subcategory="Genel",
                    visual_question=False,
                ),
            ],
        ):
            summary = run_generation(
                options,
                repository=repository,
                llm_manager=FakeManager(),
                stop_event=stop_event,
                on_event=events.append,
            )

        self.assertEqual(summary["produced"], 1)
        self.assertFalse(summary["stopped"])
        self.assertEqual(stop_event.waits, [13])
        self.assertTrue(any(event["event"] == "provider_unavailable" for event in events))

    def test_missing_translation_details_are_repaired_without_new_api_call(self):
        payload = {
            "correct_answer": "B",
            "translations": {
                "tr": {
                    "question": "Soru?",
                    "options": {"A": "Yanlış", "B": "Doğru", "C": "Yanlış 2", "D": "Yanlış 3"},
                }
            },
        }
        normalized = normalize_generated_payload(payload, "Danslar")
        self.assertEqual(normalized["translations"]["tr"]["explanation"], "Doğru cevap: Doğru.")
        self.assertEqual(normalized["translations"]["tr"]["filter_tag"], "Danslar")

    def test_visual_question_without_image_url_is_not_written(self):
        db = Mock()
        question = {"is_visual_question": True, "question_image_url": None}

        self.assertFalse(save_worker_question(db, question))
        db.collection.assert_not_called()

    def test_visual_question_image_url_is_copied_into_payload(self):
        db = Mock()
        db.collection.return_value.document.return_value.get.return_value.exists = False
        question = {
            "is_visual_question": True,
            "question_image_url": "https://example.test/image.jpg",
            "question_image_search_query": None,
            "payload": {"question_image_url": None, "question_image_search_query": "subject"},
            "translations": {"tr": {"question": "Görselde ne var?"}},
            "duration_local": 15,
            "duration_global": 15,
            "duration_seconds": 15,
        }

        self.assertTrue(save_worker_question(db, question))
        saved = db.collection.return_value.document.return_value.set.call_args.args[0]
        self.assertEqual(saved["payload"]["question_image_url"], question["question_image_url"])
        self.assertIsNone(saved["payload"]["question_image_search_query"])

    def test_generator_accepts_preferred_local_country_from_worker(self):
        parameters = inspect.signature(generate_question_with_fallback).parameters
        self.assertIn("preferred_local_country", parameters)

    def test_duplicate_does_not_consume_credit_slot_or_complete_early(self):
        repository = SheetAlgorithmRepository()
        events = []
        options = GenerationOptions(
            question_count=3,
            generation_mode="basic",
            difficulty="Orta",
            combo_percent=0,
            sleep_between_requests=0,
            write_to_firestore=True,
            visual_questions_enabled=False,
        )

        with (
            patch("worker.generate_question_with_fallback", side_effect=fake_question),
            patch("worker.save_worker_question", side_effect=[False, True, True, True]),
        ):
            summary = run_generation(
                options,
                repository=repository,
                llm_manager=FakeManager(),
                db=object(),
                on_event=events.append,
            )

        self.assertEqual(summary["produced"], 3)
        self.assertEqual(summary["processed"], 4)
        self.assertEqual(summary["duplicates"], 1)
        self.assertEqual(events[-1]["event"], "completed")
        self.assertEqual(events[-1]["produced"], 3)

        planning = [event for event in events if event["event"] == "planning"]
        self.assertEqual(planning[0]["category"], planning[1]["category"])
        self.assertEqual(planning[0]["subcategory"], planning[1]["subcategory"])

    def test_exactly_three_of_ten_successful_questions_are_visual(self):
        repository = SheetAlgorithmRepository()
        events = []
        resolver = FakeImageResolver()
        options = GenerationOptions(
            question_count=10,
            generation_mode="basic",
            difficulty="Orta",
            combo_percent=0,
            sleep_between_requests=0,
            write_to_firestore=False,
            visual_questions_enabled=True,
        )

        with patch("worker.generate_question_with_fallback", side_effect=fake_question) as generator:
            summary = run_generation(
                options,
                repository=repository,
                llm_manager=FakeManager(),
                image_resolver=resolver,
                on_event=events.append,
            )

        self.assertEqual(summary["produced"], 10)
        self.assertEqual(len(resolver.resolved), 3)
        self.assertEqual(
            sum(bool(call.kwargs["visual_question"]) for call in generator.call_args_list),
            3,
        )
        saved = [event for event in events if event["event"] == "generated"]
        self.assertEqual(sum(bool(event["visual_question"]) for event in saved), 3)


class VisualQuestionScheduleTests(unittest.TestCase):
    def test_three_random_positions_per_ten_without_affecting_other_state(self):
        schedule = VisualQuestionSchedule(__import__("random").Random(13))
        values = []
        for _ in range(20):
            values.append(schedule.peek())
            schedule.commit()
        self.assertEqual(sum(values[:10]), 3)
        self.assertEqual(sum(values[10:]), 3)

    def test_excludes_technical_and_abstract_categories(self):
        excluded = ["Kimya", "Fizik", "Felsefe", "Psikoloji", "Sağlık", "Teknoloji"]
        self.assertTrue(all(not is_visual_target(category) for category in excluded))
        self.assertTrue(is_visual_target("Gastronomi"))
        self.assertTrue(is_visual_target("Coğrafya"))
        self.assertTrue(is_visual_target("Edebiyat"))
        self.assertTrue(is_visual_target(SimpleNamespace(category="Fizik", subcategory="Temel Fizik")))
        self.assertFalse(
            is_visual_target(SimpleNamespace(category="Fizik", subcategory="Elektromanyetizma"))
        )

    def test_reorders_only_queue_order_to_keep_three_visuals_in_full_block(self):
        targets = [SimpleNamespace(category="Fizik", marker=index) for index in range(10)]
        targets.extend(
            SimpleNamespace(category="Gastronomi", marker=10 + index) for index in range(3)
        )
        original_markers = {target.marker for target in targets}
        prioritize_visual_targets(targets)
        self.assertEqual(sum(is_visual_target(target) for target in targets[:10]), 3)
        self.assertEqual({target.marker for target in targets}, original_markers)

        schedule = VisualQuestionSchedule(__import__("random").Random(4))
        values = []
        for _ in range(10):
            values.append(schedule.peek(targets[:10]))
            schedule.commit()
        self.assertEqual(sum(values), 3)

    def test_visual_prompt_is_recognition_only_and_rejects_physics(self):
        prompt = build_prompt("Gastronomi", "Geleneksel Yemekler", visual_question=True)
        self.assertIn("yemeğin kendisi", prompt)
        self.assertIn("formül", prompt)
        with self.assertRaisesRegex(ValueError, "uygun değil"):
            build_prompt("Fizik", "Elektromanyetizma", visual_question=True)
        scientist_prompt = build_prompt("Fizik", "Temel Fizik", visual_question=True)
        self.assertIn("Einstein", scientist_prompt)


if __name__ == "__main__":
    unittest.main()
