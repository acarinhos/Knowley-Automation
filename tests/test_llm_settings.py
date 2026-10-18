import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import llm_settings
from core.llm_settings import (
    DEFAULT_SELECTION, PROVIDERS, allowed_env_value, apply_selection, key_status, load_selection, save_selection,
    validate_selection,
)


class SelectionLogicTests(unittest.TestCase):
    def test_default_is_gemini_and_groq_only(self):
        self.assertEqual(DEFAULT_SELECTION, ("gemini", "groq"))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_selection(Path(tmp) / "missing.json"), ["gemini", "groq"])

    def test_selection_to_allowed_list(self):
        self.assertEqual(allowed_env_value(["groq", "gemini"]), "gemini,groq")  # canonical order
        self.assertEqual(allowed_env_value(["Cohere", "gemini", "gemini"]), "gemini,cohere")
        self.assertEqual(validate_selection(PROVIDERS), list(PROVIDERS))

    def test_empty_or_unknown_selection_is_rejected(self):
        for bad in ([], [""], ["openai"], ["gemini", "bogus"]):
            with self.assertRaises(ValueError):
                validate_selection(bad)

    def test_save_and_load_round_trip_and_invalid_file_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sel.json"
            self.assertEqual(save_selection(["nvidia", "groq"], path), ["groq", "nvidia"])
            self.assertEqual(load_selection(path), ["groq", "nvidia"])
            with self.assertRaises(ValueError):
                save_selection([], path)
            self.assertEqual(load_selection(path), ["groq", "nvidia"])  # untouched by the rejected save
            path.write_text(json.dumps({"providers": []}), encoding="utf-8")
            self.assertEqual(load_selection(path), ["gemini", "groq"])
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(load_selection(path), ["gemini", "groq"])

    def test_key_status_hides_values(self):
        status = key_status({"GEMINI_API_KEY": "x", "GROQ_API_KEY": " ", "COHERE_API_KEY": ""})
        self.assertEqual(status, {"gemini": True, "groq": False, "cohere": False, "nvidia": False})

    def test_apply_sets_env_and_resets_shared_manager(self):
        from core import llm_manager

        with patch.dict(os.environ, {}, clear=False):
            llm_manager._global_llm_manager = object()
            self.assertEqual(apply_selection(["groq"]), ["groq"])
            self.assertEqual(os.environ["LLM_ALLOWED_PROVIDERS"], "groq")
            self.assertIsNone(llm_manager._global_llm_manager)
            with self.assertRaises(ValueError):
                apply_selection([])
            self.assertEqual(os.environ["LLM_ALLOWED_PROVIDERS"], "groq")  # invalid input changes nothing

    def test_manager_honours_the_allowed_list(self):
        from core.llm_manager import MultiLLMManager

        env = {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "r", "COHERE_API_KEY": "c", "NVIDIA_API_KEY": "n"}
        with patch.dict(os.environ, {**env, "LLM_ALLOWED_PROVIDERS": allowed_env_value(DEFAULT_SELECTION)}):
            with patch("core.llm_manager.genai.Client"), patch("core.llm_manager.Groq", create=True):
                manager = MultiLLMManager()
        self.assertEqual(manager.providers, ["gemini", "groq"])


if __name__ == "__main__":
    unittest.main()
