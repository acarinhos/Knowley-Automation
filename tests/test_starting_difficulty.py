import random
import unittest

from core.starting_difficulty import StartingDifficultyAssigner


class StartingDifficultyAssignerTests(unittest.TestCase):
    def test_assigns_exactly_three_easy_and_one_medium_per_ten(self):
        questions = [
            {"difficulty_local": level, "sentinel": index}
            for index, level in enumerate(
                ["Orta", "Kolay", "Zor", "Kolay", "Orta", "Kolay", "Zor", "Orta", "Kolay", "Orta"]
            )
        ]
        assigner = StartingDifficultyAssigner(random.Random(7))
        original_levels = [item["difficulty_local"] for item in questions]
        for question in questions:
            assigner.assign(question)

        tagged = [item for item in questions if "starting_difficulty" in item]
        tagged_easy = [item for item in tagged if item["difficulty_local"] == "Kolay"]
        tagged_medium = [item for item in tagged if item["difficulty_local"] == "Orta"]
        self.assertEqual(len(tagged), 4)
        self.assertEqual([item["starting_difficulty"] for item in tagged_easy], ["Kolay", "Orta", "Zor"])
        self.assertEqual([item["starting_difficulty"] for item in tagged_medium], ["Zor"])
        self.assertEqual([item["difficulty_local"] for item in questions], original_levels)

    def test_resets_for_each_ten_question_block(self):
        levels = ["Kolay"] * 4 + ["Orta"] * 4 + ["Zor"] * 2
        questions = [{"difficulty_local": level} for level in levels + levels]
        assigner = StartingDifficultyAssigner(random.Random(11))
        for question in questions:
            assigner.assign(question)
        self.assertEqual(sum("starting_difficulty" in item for item in questions[:10]), 4)
        self.assertEqual(sum("starting_difficulty" in item for item in questions[10:]), 4)

    def test_duplicate_rollback_does_not_consume_block_position(self):
        assigner = StartingDifficultyAssigner(random.Random(5))
        duplicate = {"difficulty_local": "Kolay"}
        assigner.assign(duplicate)
        assigner.rollback(duplicate)

        questions = [
            {"difficulty_local": level}
            for level in ["Kolay"] * 4 + ["Orta"] * 4 + ["Zor"] * 2
        ]
        for question in questions:
            assigner.assign(question)
            assigner.commit()
        self.assertEqual(sum("starting_difficulty" in item for item in questions), 4)


if __name__ == "__main__":
    unittest.main()
