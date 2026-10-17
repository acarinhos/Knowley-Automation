"""Why a UI run can log 'Toplam Başarılı Soru' with zero Firestore documents: dry-run vs write mode."""

import unittest
from unittest.mock import MagicMock, patch

from core.sheet_algorithm import SheetAlgorithmRepository
from tests.test_worker_generation import FakeManager, fake_question
from ui_tkinter import firestore_writes_enabled, produced_from_events
from worker import GenerationOptions, run_generation


def run(write):
    events, db = [], MagicMock()
    options = GenerationOptions(question_count=2, generation_mode="basic", difficulty="Orta", combo_percent=0,
                                sleep_between_requests=0, write_to_firestore=write, visual_questions_enabled=False)
    with patch("worker.generate_question_with_fallback", side_effect=fake_question), \
            patch("worker.save_worker_question", return_value=True) as save:
        summary = run_generation(options, repository=SheetAlgorithmRepository(), llm_manager=FakeManager(),
                                 db=db, on_event=events.append)
    return summary, events, save


class WriteModeTests(unittest.TestCase):
    def test_write_mode_saves_and_emits_saved_events(self):
        summary, events, save = run(True)
        self.assertEqual(save.call_count, 2)
        self.assertEqual([e["event"] for e in events if e["event"] in {"saved", "generated"}], ["saved", "saved"])
        self.assertTrue(next(e for e in events if e["event"] == "started")["firestore"])
        self.assertEqual(summary["produced"], 2)

    def test_dry_run_counts_successes_but_never_writes(self):
        summary, events, save = run(False)
        save.assert_not_called()  # nothing reaches Firestore ...
        self.assertEqual(summary["produced"], 2)  # ... yet the run reports successes
        self.assertEqual([e["event"] for e in events if e["event"] in {"saved", "generated"}], ["generated", "generated"])
        self.assertFalse(next(e for e in events if e["event"] == "started")["firestore"])

    def test_save_worker_question_really_sets_the_document(self):
        from worker import save_worker_question

        db = MagicMock()
        db.collection.return_value.document.return_value.get.return_value.exists = False
        question = {"translations": {"en": {"question": "Unique test question?"}}, "scope": "global",
                    "correct_answer": "A", "difficulty": "Orta"}
        self.assertTrue(save_worker_question(db, question))
        db.collection.assert_called_with("questions")
        db.collection.return_value.document.return_value.set.assert_called_once()


class UiCounterTests(unittest.TestCase):
    def test_counter_counts_saved_and_generated_events(self):
        self.assertEqual(produced_from_events([{"event": "saved", "produced": 3}, {"event": "generated", "produced": 5},
                                               {"event": "planning"}]), 5)
        self.assertEqual(produced_from_events([]), 0)

    def test_dry_run_is_detected_from_started_event_or_options(self):
        self.assertFalse(firestore_writes_enabled({}, [{"event": "started", "firestore": False}]))
        self.assertTrue(firestore_writes_enabled({}, [{"event": "started", "firestore": True}]))
        self.assertFalse(firestore_writes_enabled({"write_to_firestore": False}, []))
        self.assertTrue(firestore_writes_enabled({}, []))


if __name__ == "__main__":
    unittest.main()
