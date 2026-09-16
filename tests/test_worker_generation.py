import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.sheet_algorithm import SheetAlgorithmRepository
from worker import GenerationOptions, run_generation


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
    )


class RunGenerationCompletionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
