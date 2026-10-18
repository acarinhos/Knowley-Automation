import unittest

from core.community_distribution import apply_simulated_distribution, simulate_distribution


def doc(**over):
    base = {"correct_answer": "B", "difficulty": "Orta", "strongest_distractor_index": 2}
    base.update(over)
    return base


class SimulationTests(unittest.TestCase):
    def test_shape_and_determinism(self):
        first = simulate_distribution("abc", doc())
        self.assertEqual(first, simulate_distribution("abc", doc()))
        dist, sample = first
        self.assertEqual((len(dist), sum(dist)), (4, 100))
        self.assertTrue(all(isinstance(x, int) and x >= 0 for x in dist))
        self.assertTrue(300 <= sample <= 1500)
        self.assertEqual(dist.count(max(dist)), 1)  # a clear leader
        self.assertTrue(35 <= max(dist) <= 75)

    def test_leader_statistics_follow_difficulty(self):
        def correct_rate(level):
            hits = 0
            for i in range(3000):
                dist, _ = simulate_distribution(f"q{i}", doc(difficulty=level))
                hits += dist.index(max(dist)) == 1
            return hits / 3000

        easy, hard = correct_rate("Kolay"), correct_rate("Zor")
        self.assertTrue(0.88 < easy < 0.96, easy)
        self.assertTrue(0.62 < hard < 0.78, hard)
        self.assertGreater(easy, hard)

    def test_apply_sets_fields_once_and_skips_invalid(self):
        data = doc()
        self.assertTrue(apply_simulated_distribution("id1", data, now="2026-01-01T00:00:00+00:00"))
        self.assertEqual(data["community_distribution_meta"]["source"], "simulated")
        before = list(data["community_distribution"])
        self.assertFalse(apply_simulated_distribution("other", data))
        self.assertEqual(data["community_distribution"], before)
        self.assertIsNone(simulate_distribution("x", doc(correct_answer="Z")))


if __name__ == "__main__":
    unittest.main()
