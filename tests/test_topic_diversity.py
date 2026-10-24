import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from core.topic_diversity import TopicDiversityTracker, avoid_line, topic_terms


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
                                    "translations": {"en": {"keyword": "k", "options": {"A": "Venus"}}}}
        db = MagicMock()
        db.collection().where().limit().stream.return_value = [doc]
        t = TopicDiversityTracker()
        t.seed_from_db(db, "A", "B")
        self.assertEqual(t.recent("A", "B"), ["k", "Venus"])
        t.seed_from_db(MagicMock(side_effect=RuntimeError), "C", "D")  # swallowed


if __name__ == "__main__":
    unittest.main()
