import unittest

from core.generator import validate_skill_support_fields


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def question(**over):
    content = Obj(hint="h", keyword="k", distractor_trap_note="t", binary_fallback=Obj(statement="s", is_true=True))
    for key in ("hint", "keyword", "binary_fallback"):
        if key in over:
            setattr(content, key, over.pop(key))
    base = dict(translations={"en": content}, correct_answer="A", weakest_distractor_index=1, strongest_distractor_index=2)
    base.update(over)
    return Obj(**base)


class SkillSupportFieldTests(unittest.TestCase):
    def test_complete_question_passes(self):
        validate_skill_support_fields(question(), "en")

    def test_missing_text_field_fails(self):
        with self.assertRaises(ValueError):
            validate_skill_support_fields(question(hint=" "), "en")

    def test_missing_binary_fallback_fails(self):
        with self.assertRaises(ValueError):
            validate_skill_support_fields(question(binary_fallback=None), "en")

    def test_distractor_index_must_differ_from_answer_and_each_other(self):
        for weakest, strongest in ((0, 2), (1, 1), (None, 2)):
            with self.assertRaises(ValueError):
                validate_skill_support_fields(question(weakest_distractor_index=weakest, strongest_distractor_index=strongest), "en")


if __name__ == "__main__":
    unittest.main()
