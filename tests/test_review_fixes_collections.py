import unittest
from unittest.mock import MagicMock, patch

import ui_server
from core.question_types import ALL_QUESTION_COLLECTIONS
from database.db_manager import save_question_to_firestore


class FirebaseSummaryTests(unittest.TestCase):
    def test_one_count_failure_keeps_others(self):
        db = MagicMock()

        def coll(name):
            c = MagicMock()
            if name == ALL_QUESTION_COLLECTIONS[1]:
                c.count.side_effect = RuntimeError("boom")
            else:
                c.count().get.return_value = [[MagicMock(value=3)]]
            return c
        db.collection.side_effect = coll
        with patch.object(ui_server, "init_firebase", return_value=db):
            r = ui_server.firebase_summary(live=True)
        self.assertTrue(r["reachable"])
        self.assertIsNone(r["collection_counts"][ALL_QUESTION_COLLECTIONS[1]])
        self.assertEqual(r["question_count"], 3 * (len(ALL_QUESTION_COLLECTIONS) - 1))


class SaveLegacyHashTests(unittest.TestCase):
    def _db(self, legacy_exists):
        db = MagicMock()

        def coll(name):
            c = MagicMock()
            c.document().get.return_value = MagicMock(exists=legacy_exists if name == "questions" else False)
            return c
        db.collection.side_effect = coll
        return db

    Q = {"question": "Is the sky blue?", "question_type": "true_false"}

    def test_typed_skips_when_legacy_has_hash(self):
        self.assertFalse(save_question_to_firestore(self._db(True), dict(self.Q)))

    def test_explicit_collection_ignores_legacy(self):
        db = self._db(True)
        save_question_to_firestore(db, dict(self.Q), collection_name="tfquestions")
        self.assertNotIn("questions", [c.args[0] for c in db.collection.call_args_list])


if __name__ == "__main__":
    unittest.main()
