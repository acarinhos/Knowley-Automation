"""Mock-only tests for the normal-generation speed-ups (no real LLM/Azure calls)."""
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import requests

import core.generator as generator
from core.llm_manager import MultiLLMManager
from core.translation_service import translate_localized_content

SOURCE = {"question": "Q", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}}
LANGS = ["en"] + [f"l{i:02d}" for i in range(25)]


def _fake_translate(seen, fail_on=None):
    def fake(source, source_language, langs, manager):
        seen.append(list(langs))
        if fail_on and fail_on(langs):
            raise ValueError("Unterminated JSON string (truncated)")
        return {lang: {"question": lang} for lang in langs}, "P", []

    return fake


class TranslationBatchingTests(unittest.TestCase):
    def run_translate(self, fake, env=None):
        with patch.dict(os.environ, env or {}), patch(
            "core.translation_service._llm_translate_with_cooldown", side_effect=fake
        ):
            return translate_localized_content(
                SOURCE, "en", LANGS, llm_manager=Mock(), prefer_azure=False
            )

    def test_default_batch_size_10_and_deterministic_order(self):
        seen = []

        def fake(source, sl, langs, manager):
            # Earlier batches are slower, so completion order != language order.
            time.sleep(0.05 if langs[0] == "l00" else 0.0)
            seen.append(list(langs))
            return {lang: {"question": lang} for lang in langs}, f"P{langs[0]}", []

        result, provider = self.run_translate(fake)
        self.assertEqual(sorted(len(b) for b in seen), [5, 10, 10])
        self.assertEqual(list(result), ["en"] + LANGS[1:])
        self.assertEqual(provider, "Pl00+Pl10+Pl20")

    def test_batches_run_concurrently_and_workers_env(self):
        active = {"now": 0, "max": 0}
        lock = threading.Lock()

        def fake(source, sl, langs, manager):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1
            return {lang: {"question": lang} for lang in langs}, "P", []

        self.run_translate(fake)
        self.assertEqual(active["max"], 3)
        active["max"] = 0
        self.run_translate(fake, {"TRANSLATION_LLM_WORKERS": "1"})
        self.assertEqual(active["max"], 1)

    def test_batch_size_env(self):
        seen = []
        self.run_translate(_fake_translate(seen), {"TRANSLATION_LLM_BATCH_SIZE": "25"})
        self.assertEqual([len(b) for b in seen], [25])

    def test_truncated_json_splits_in_half_before_single_calls(self):
        seen = []
        fake = _fake_translate(seen, fail_on=lambda langs: len(langs) > 2)
        result, _ = self.run_translate(
            fake, {"TRANSLATION_LLM_BATCH_SIZE": "8", "TRANSLATION_LLM_WORKERS": "1"}
        )
        first = [b for b in seen if set(b) <= set(LANGS[1:9])]
        # 8 fails -> 4+4 fail -> 2+2 ok each; no single-language call is needed.
        self.assertEqual([len(b) for b in first], [8, 4, 2, 2, 4, 2, 2])
        self.assertEqual(set(result), set(LANGS))


class SourceReuseTests(unittest.TestCase):
    def test_translation_failure_does_not_regenerate_source(self):
        manager = Mock()
        manager.generate_trivia.return_value = {"_provider": "gemini"}
        candidate = MagicMock()
        candidate.translations = {"en": MagicMock(model_dump=lambda: dict(SOURCE))}
        calls = {"n": 0}

        class Stop(Exception):
            pass

        def flaky_translate(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("translation hiccup")
            return {"en": SOURCE}, "P"

        planner = MagicMock()
        planner.create_plan.return_value = SimpleNamespace(
            scope="global", target_country=None, target_country_credit=1, is_global_eligible=True
        )
        balancer = MagicMock()
        balancer.get_balanced_score_for_level.return_value = 5
        repo = MagicMock()
        repo.country_code.return_value = "XX"
        repo.question_languages.return_value = ["en"]
        with patch.object(generator, "translate_localized_content", side_effect=flaky_translate), \
             patch.object(generator, "normalize_generated_payload", side_effect=lambda d, t: d), \
             patch.object(generator.GeneratedQuestion, "model_validate", return_value=candidate), \
             patch.object(generator.LocalizedContent, "model_validate", side_effect=lambda c: c), \
             patch.object(generator, "validate_trivia_compliance"), \
             patch.object(generator, "validate_skill_support_fields"), \
             patch.object(generator, "build_prompt", return_value="p"), \
             patch.object(generator, "populate_question_meta", side_effect=Stop), \
             patch("core.sheet_algorithm.get_sheet_algorithm_repository", return_value=repo):
            with self.assertRaises(Stop):  # reached the post-translation stage
                generator.generate_question_with_fallback(
                    primary_category="Bilim", languages=["en"], llm_manager=manager,
                    generation_planner=planner, difficulty_balancer=balancer,
                    balance_options=False, base_difficulty="Orta", target_score=5,
                )
        self.assertEqual(calls["n"], 3)
        self.assertEqual(manager.generate_trivia.call_count, 1)

    def test_stage_attempts_env(self):
        with patch.dict(os.environ, {"TRANSLATION_STAGE_ATTEMPTS": "5"}):
            self.assertEqual(generator._translation_stage_attempts(), 5)
        with patch.dict(os.environ, {"TRANSLATION_STAGE_ATTEMPTS": "x"}):
            self.assertEqual(generator._translation_stage_attempts(), 3)


class ProviderHealthTests(unittest.TestCase):
    def make(self, providers=("gemini", "groq", "cohere")):
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "t", "LLM_ALLOWED_PROVIDERS": "nvidia"}):
            m = MultiLLMManager()
        m.providers = list(providers)
        return m

    def test_equal_providers_rotate_deterministically(self):
        m = self.make()
        firsts = [m._health_ordered_providers()[0] for _ in range(4)]
        self.assertEqual(firsts, ["gemini", "groq", "cohere", "gemini"])

    def test_slow_and_failing_providers_sink(self):
        m = self.make()
        for _ in range(5):
            m._record_health("gemini", 12.0, "timeout")
            m._record_health("groq", 6.0, "ok")
            m._record_health("cohere", 0.5, "ok")
        for _ in range(3):
            self.assertEqual(m._health_ordered_providers(), ["cohere", "groq", "gemini"])

    def test_cooldown_and_disabled_go_last(self):
        m = self.make()
        m._provider_cooldown_until["gemini"] = time.time() + 100
        m._provider_disabled_reason["groq"] = "auth"
        self.assertEqual(m._health_ordered_providers(), ["cohere", "gemini", "groq"])

    def test_old_samples_expire(self):
        m = self.make()
        m._record_health("gemini", 12.0, "timeout")
        m._provider_health["gemini"][0] = (time.monotonic() - 1000, 12.0, "timeout")
        self.assertEqual(m._health_score("gemini", time.monotonic()), (0, 0))

    def test_failure_is_recorded_and_failing_provider_skipped_next(self):
        m = self.make(("gemini", "groq"))
        with patch.object(MultiLLMManager, "_call_gemini", side_effect=requests.Timeout("t")), \
             patch.object(MultiLLMManager, "_call_groq", return_value='{"q": 1}'):
            self.assertEqual(m.generate_trivia("s", "u")["_provider"], "groq")
            self.assertEqual(m._provider_health["gemini"][0][2], "timeout")
            self.assertEqual(m._health_ordered_providers()[0], "groq")

    def test_timeout_skips_remaining_groq_models(self):
        m = self.make(("groq",))
        m.groq_models = ["a", "b", "c"]
        m.groq_client = MagicMock()
        create = m.groq_client.chat.completions.with_raw_response.create
        create.side_effect = requests.Timeout("timed out")
        with self.assertRaises(Exception):
            m._call_groq("s", "u")
        self.assertEqual(create.call_count, 1)

    def test_timeout_skips_remaining_cohere_models(self):
        m = self.make(("cohere",))
        m.cohere_models = ["a", "b"]
        m.cohere_client = MagicMock()
        m.cohere_client.chat.side_effect = TimeoutError("read timeout")
        with self.assertRaises(Exception):
            m._call_cohere("s", "u")
        self.assertEqual(m.cohere_client.chat.call_count, 1)

    def test_timeout_env_defaults_and_overrides(self):
        keys = {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "r", "COHERE_API_KEY": "c", "NVIDIA_API_KEY": "n"}
        with patch.dict(os.environ, keys), patch("core.llm_manager.Groq") as groq, \
             patch("core.llm_manager.cohere.ClientV2") as co, patch("core.llm_manager.genai.Client") as ge:
            os.environ.pop("LLM_ALLOWED_PROVIDERS", None)
            os.environ.pop("GEMINI_TIMEOUT_MS", None)
            MultiLLMManager()
            self.assertEqual(groq.call_args.kwargs["timeout"], 15.0)
            self.assertEqual(co.call_args.kwargs["timeout"], 15.0)
            self.assertEqual(ge.call_args.kwargs["http_options"]["timeout"], 20000)
            with patch.dict(os.environ, {"GROQ_TIMEOUT_SECONDS": "7", "COHERE_TIMEOUT_SECONDS": "9", "GEMINI_TIMEOUT_MS": "5000"}):
                MultiLLMManager()
            self.assertEqual(groq.call_args.kwargs["timeout"], 7.0)
            self.assertEqual(co.call_args.kwargs["timeout"], 9.0)
            self.assertEqual(ge.call_args.kwargs["http_options"]["timeout"], 5000)


if __name__ == "__main__":
    unittest.main()
