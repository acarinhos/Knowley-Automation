import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from core.topic_diversity import MAX_TERMS_PER_KEY, SEED_TIMEOUT_S, TopicDiversityTracker, avoid_line, topic_terms


class TopicDiversityTests(unittest.TestCase):
    def test_recent_orders_by_count_dedups_and_limits(self):
        t = TopicDiversityTracker()
        t.record("Bilim", "Astronomi", ["Mars"])
        t.record("bilim", "astronomi ", ["Satürn", "mars"])
        t.record("Bilim", "Astronomi", ["Satürn", "Jüpiter"])
        t.record("Bilim", "Satürn", ["x"])
        self.assertEqual(sorted(t.recent("Bilim", "Astronomi")[:2]), ["Mars", "Satürn"])
        self.assertEqual(len(t.recent("Bilim", "Astronomi")), 3)
        self.assertEqual(len(t.recent("Bilim", "Astronomi", limit=2)), 2)
        self.assertEqual(t.recent("Bilim", "Yok"), [])

    def test_avoid_line_only_when_topics(self):
        t = TopicDiversityTracker()
        self.assertEqual(avoid_line(t, "A", "B"), "")
        t.record("A", "B", ["Satürn"])
        self.assertIn("Satürn", avoid_line(t, "A", "B"))

    def test_topic_terms_and_seed(self):
        q = NS(correct_answer="B", translations={"en": NS(keyword="rings", options={"A": "Mars", "B": "Saturn"})})
        self.assertEqual(topic_terms(q), ["rings", "Saturn"])
        doc = MagicMock()
        doc.to_dict.return_value = {"categories": ["A"], "correct_answer": "A",
                                    "translations": {"en": {"keyword": "ring", "options": {"A": "Venus"}}}}
        db = MagicMock()
        db.collection().where().limit().stream.return_value = [doc]
        t = TopicDiversityTracker()
        t.seed_from_db(db, "A", "B")
        self.assertEqual(t.recent("A", "B"), ["ring", "Venus"])
        db.collection().where().limit().stream.assert_called_with(timeout=SEED_TIMEOUT_S)

    def test_seed_failure_is_logged_and_retried(self):
        t = TopicDiversityTracker()
        bad = MagicMock()
        bad.collection.side_effect = RuntimeError("down")
        with self.assertLogs(level="WARNING"):
            t.seed_from_db(bad, "C", "D")  # swallowed, not marked seeded
        doc = MagicMock()
        doc.to_dict.return_value = {"translations": {"en": {"keyword": "Pulsar", "options": {}}}}
        ok = MagicMock()
        ok.collection().where().limit().stream.return_value = [doc]
        t.seed_from_db(ok, "C", "D")
        self.assertEqual(t.recent("C", "D"), ["Pulsar"])
        t.seed_from_db(bad, "C", "D")  # already seeded: DB untouched
        self.assertEqual(bad.collection.call_count, 1)

    def test_ties_prefer_newest_beyond_limit(self):
        t = TopicDiversityTracker()
        for i in range(60):
            t.record("A", "B", [f"konu{i:02d}"])
        got = t.recent("A", "B", limit=40)
        self.assertEqual(got[0], "konu59")
        self.assertIn("konu20", got)
        self.assertNotIn("konu19", got)
        t.record("A", "B", ["konu00"])  # repeated old topic rises by count
        self.assertEqual(t.recent("A", "B")[0], "konu00")

    def test_memory_cap(self):
        t = TopicDiversityTracker()
        for i in range(MAX_TERMS_PER_KEY + 50):
            t.record("A", "B", [f"konu{i:04d}"])
        self.assertEqual(len(t.recent("A", "B", limit=10**6)), MAX_TERMS_PER_KEY)

    def test_term_filter(self):
        t = TopicDiversityTracker()
        t.record("A", "B", ["42", "1,5", "ab", "x" * 41, "  Sirius   A ", "Ay"])
        self.assertEqual(t.recent("A", "B"), ["Sirius A"])

    def test_worker_records_only_saved(self):
        from tests.test_question_concurrency import Harness
        h = Harness(texts=lambda n: "q1" if n == 2 else f"q{n}")
        with patch.object(TopicDiversityTracker, "record") as rec:
            summary = h.run(1, count=5)
        self.assertEqual(summary["duplicates"], 1)
        self.assertEqual(rec.call_count, summary["produced"])


if __name__ == "__main__":
    unittest.main()
