import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import llm_settings
from core.llm_settings import (
    MODEL_ENV, apply_selection, available_models, load_models, model_env, save_selection, validate_models,
)
from core.llm_manager import MultiLLMManager

KEYS = {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "r", "COHERE_API_KEY": "c", "NVIDIA_API_KEY": "n"}


class ModelSelectionLogicTests(unittest.TestCase):
    def test_catalogue_is_the_managers_own_list_in_its_order(self):
        self.assertEqual(available_models("gemini"), MultiLLMManager.GEMINI_MODELS)
        self.assertEqual(available_models("groq"), MultiLLMManager.GROQ_MODELS)
        self.assertEqual(available_models("cohere"), MultiLLMManager.COHERE_MODELS)
        self.assertEqual(available_models("nvidia"), MultiLLMManager.NVIDIA_MODELS)

    def test_missing_choice_means_all_models(self):
        self.assertEqual(validate_models(None, ["gemini", "groq"]),
                         {"gemini": available_models("gemini"), "groq": available_models("groq")})

    def test_subset_keeps_catalogue_order_and_bad_input_is_rejected(self):
        models = available_models("gemini")
        chosen = validate_models({"gemini": [models[2], models[0]]}, ["gemini"])
        self.assertEqual(chosen, {"gemini": [models[0], models[2]]})
        for bad in ({"gemini": []}, {"gemini": ["nope"]}):
            with self.assertRaises(ValueError):
                validate_models(bad, ["gemini"])
        # an empty list for a provider that is not enabled is ignored (its models are unused)
        self.assertEqual(validate_models({"cohere": []}, ["gemini"]).keys(), {"gemini"})

    def test_model_env_only_for_enabled_providers(self):
        models = available_models("groq")
        env = model_env(["groq"], {"groq": [models[1]]})
        self.assertEqual(env, {"LLM_ALLOWED_PROVIDERS": "groq", MODEL_ENV["groq"]: models[1]})

    def test_save_and_load_models_round_trip(self):
        models = available_models("gemini")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sel.json"
            save_selection(["gemini", "groq"], path, {"gemini": [models[1]]})
            self.assertEqual(load_models(path)["gemini"], [models[1]])
            self.assertEqual(load_models(path)["groq"], available_models("groq"))
            with self.assertRaises(ValueError):
                save_selection(["gemini"], path, {"gemini": []})
            self.assertEqual(load_models(path)["gemini"], [models[1]])  # rejected save changed nothing
            path.write_text(json.dumps({"providers": ["gemini"], "models": {"gemini": ["bogus"]}}), encoding="utf-8")
            self.assertEqual(load_models(path)["gemini"], available_models("gemini"))  # invalid file -> all models

    def test_manager_uses_only_the_selected_models(self):
        models = available_models("gemini")
        with patch.dict(os.environ, KEYS):
            applied = model_env(["gemini", "groq"], {"gemini": [models[1]], "groq": [available_models("groq")[0]]})
            with patch.dict(os.environ, applied), patch("core.llm_manager.genai.Client"), \
                    patch("core.llm_manager.Groq", create=True):
                manager = MultiLLMManager()
        self.assertEqual(manager.providers, ["gemini", "groq"])
        self.assertEqual(manager.gemini_models, [models[1]])
        self.assertEqual(manager.groq_models, [available_models("groq")[0]])

    def test_apply_sets_model_env_and_catalogue_survives_filtering(self):
        models = available_models("gemini")
        with patch.dict(os.environ, {}):
            apply_selection(["gemini"], {"gemini": [models[0]]})
            self.assertEqual(os.environ[MODEL_ENV["gemini"]], models[0])
            self.assertEqual(available_models("gemini"), models)  # catalogue is not shrunk by the applied env


if __name__ == "__main__":
    unittest.main()
