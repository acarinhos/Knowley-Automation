"""JobManager provider selection: all-or-nothing updates, no side effects while a job runs, env restoration."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import llm_manager as llm_module
from core import llm_settings
import ui_server
from ui_server import JobManager, mock_env_if

KEYS = {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "r", "COHERE_API_KEY": "", "NVIDIA_API_KEY": ""}


class ProviderTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = Path(tmp.name) / "providers.json"
        stack = [
            patch.dict(os.environ, {**KEYS}),
            patch("core.llm_manager.genai.Client"),
            patch("core.llm_manager.Groq", create=True),
            patch("ui_server.load_selection", lambda: ["gemini", "groq"]),
            patch("ui_server.save_selection", lambda sel: llm_settings.save_selection(sel, self.settings)),
        ]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)
        os.environ.pop("LLM_ALLOWED_PROVIDERS", None)
        self.addCleanup(lambda: setattr(llm_module, "_global_llm_manager", None))

    def manager(self):
        return JobManager()


class SetProvidersTests(ProviderTestCase):
    def test_valid_selection_is_built_saved_and_applied(self):
        jm = self.manager()
        self.assertEqual(jm.set_providers(["groq"]), ["groq"])
        self.assertEqual(jm.llm_manager.providers, ["groq"])
        self.assertEqual(os.environ["LLM_ALLOWED_PROVIDERS"], "groq")
        self.assertEqual(llm_settings.load_selection(self.settings), ["groq"])
        self.assertEqual(jm.selection, ["groq"])

    def test_invalid_input_changes_neither_file_nor_env_nor_manager(self):
        jm = self.manager()
        before = jm.llm_manager
        for bad in ([], ["openai"]):
            with self.assertRaises(ValueError):
                jm.set_providers(bad)
        with self.assertRaises(ValueError):  # selected provider without an API key
            jm.set_providers(["cohere"])
        self.assertFalse(self.settings.exists())
        self.assertNotIn("LLM_ALLOWED_PROVIDERS", os.environ)
        self.assertIs(jm.llm_manager, before)
        self.assertEqual(jm.selection, ["gemini", "groq"])

    def test_change_is_rejected_while_a_job_is_active(self):
        jm = self.manager()
        jm.jobs["j1"] = {"status": "running"}
        with self.assertRaises(RuntimeError) as ctx:
            jm.set_providers(["groq"])
        self.assertIn("iş bitince uygulanır", str(ctx.exception))
        self.assertFalse(self.settings.exists())
        jm.jobs["j1"]["status"] = "completed"
        self.assertEqual(jm.set_providers(["groq"]), ["groq"])

    def test_without_any_key_manager_is_none_with_a_reason(self):
        with patch.dict(os.environ, {k: "" for k in KEYS}):
            jm = self.manager()
        self.assertIsNone(jm.llm_manager)
        self.assertTrue(jm.llm_error)
        self.assertNotIn("LLM_ALLOWED_PROVIDERS", os.environ)

    def test_active_job_blocks_start_without_touching_env_or_shared_manager(self):
        jm = self.manager()
        jm.jobs["j1"] = {"status": "starting"}
        shared = object()
        llm_module._global_llm_manager = shared
        with self.assertRaises(RuntimeError):
            jm.start({})
        self.assertNotIn("LLM_ALLOWED_PROVIDERS", os.environ)
        self.assertIs(llm_module._global_llm_manager, shared)

    def test_building_the_manager_does_not_leak_the_env_var(self):
        self.manager()
        self.assertNotIn("LLM_ALLOWED_PROVIDERS", os.environ)


class MockEnvTests(unittest.TestCase):
    def test_restores_existing_and_missing_keys(self):
        with patch.dict(os.environ, {"KEEP_ME": "old"}):
            os.environ.pop("NEW_KEY", None)
            with mock_env_if(True, {"KEEP_ME": "temp", "NEW_KEY": "temp"}):
                self.assertEqual((os.environ["KEEP_ME"], os.environ["NEW_KEY"]), ("temp", "temp"))
            self.assertEqual(os.environ["KEEP_ME"], "old")
            self.assertNotIn("NEW_KEY", os.environ)

    def test_restores_after_an_exception_and_noop_when_inactive(self):
        with patch.dict(os.environ, {"KEEP_ME": "old"}):
            with self.assertRaises(RuntimeError):
                with mock_env_if(True, {"KEEP_ME": "temp"}):
                    raise RuntimeError("boom")
            self.assertEqual(os.environ["KEEP_ME"], "old")
            with mock_env_if(False, {"KEEP_ME": "temp"}):
                self.assertEqual(os.environ["KEEP_ME"], "old")


class AtomicSaveTests(unittest.TestCase):
    def test_failed_replace_keeps_old_file_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sel.json"
            llm_settings.save_selection(["gemini"], path)
            with patch("core.llm_settings.os.replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    llm_settings.save_selection(["groq"], path)
            self.assertEqual(llm_settings.load_selection(path), ["gemini"])
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["sel.json"])


if __name__ == "__main__":
    unittest.main()
