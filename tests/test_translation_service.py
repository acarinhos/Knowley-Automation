import os
import unittest
from unittest.mock import Mock, patch

from core.llm_manager import AllProvidersCoolingDown
from core.translation_service import (
    _llm_translate_with_cooldown,
    translate_localized_content,
)


class TestTranslationServiceRecovery(unittest.TestCase):
    def test_missing_languages_are_repaired_as_a_group(self):
        manager = Mock()
        localized = lambda language: {"question": f"Question in {language}"}

        with patch.dict(os.environ, {"GEMINI_QUOTA_GUARD": "false"}), patch(
            "core.translation_service._llm_translate_with_cooldown",
            side_effect=[
                ({"de-DE": localized("de-DE")}, "GEMINI", ["fr-FR", "it-IT"]),
                (
                    {"fr-FR": localized("fr-FR"), "it-IT": localized("it-IT")},
                    "GROQ",
                    [],
                ),
            ],
        ) as translate:
            translations, _provider = translate_localized_content(
                {"question": "Question", "options": {"A": "A", "B": "B", "C": "C", "D": "D"}},
                "en",
                ["en", "de-DE", "fr-FR", "it-IT"],
                llm_manager=manager,
                prefer_azure=False,
            )

        self.assertEqual(translate.call_count, 2)
        self.assertEqual(translate.call_args_list[0].args[2], ["de-DE", "fr-FR", "it-IT"])
        self.assertEqual(translate.call_args_list[1].args[2], ["fr-FR", "it-IT"])
        self.assertEqual(set(translations), {"en", "de-DE", "fr-FR", "it-IT"})

    def test_long_provider_cooldown_bubbles_without_sleeping(self):
        cooldown = AllProvidersCoolingDown(96, "free provider quotas exhausted", quota_limited=True)
        with patch.dict(os.environ, {"GEMINI_QUOTA_GUARD": "false"}), patch(
            "core.translation_service._llm_translate", side_effect=cooldown
        ), patch("core.translation_service.time.sleep") as sleep:
            with self.assertRaises(AllProvidersCoolingDown):
                _llm_translate_with_cooldown({}, "en", ["fr-FR"], Mock())

        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
