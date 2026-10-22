"""A provider that omits `clarified_options` for a locale must mark it missing, not crash the whole question."""

import unittest

from core.translation_service import _llm_translate

FULL = {"question": "q", "options": {k: k for k in "ABCD"}, "explanation": "e", "filter_tag": "t", "hint": "h",
        "distractor_trap_note": "n", "keyword": "k", "binary_fallback": {"statement": "s", "is_true": True},
        "simplified_question": "sq", "clarified_options": {k: k for k in "ABCD"}}
SOURCE = {**FULL, "question": "Q", "hint": "H", "keyword": "K"}


class FakeLLM:
    providers = ["gemini"]

    def __init__(self, drop):
        self.drop = drop

    def generate_trivia(self, system, prompt, response_schema=None, **_kw):
        partial = {k: v for k, v in FULL.items() if k != self.drop}
        return {"_provider": "fake", "translations": {"bn-BD": dict(FULL), "da-DK": partial}}


class MissingClarifiedOptionsTests(unittest.TestCase):
    def test_missing_clarified_options_marks_only_that_locale_missing(self):
        result, _provider, missing = _llm_translate(SOURCE, "en", ["bn-BD", "da-DK"], FakeLLM("clarified_options"))
        self.assertEqual(missing, ["da-DK"])
        self.assertEqual(set(result), {"bn-BD"})

    def test_complete_translations_pass(self):
        result, _provider, missing = _llm_translate(SOURCE, "en", ["bn-BD", "da-DK"], FakeLLM("nothing"))
        self.assertEqual((missing, set(result)), ([], {"bn-BD", "da-DK"}))


if __name__ == "__main__":
    unittest.main()
