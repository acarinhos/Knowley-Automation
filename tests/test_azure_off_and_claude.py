"""Mock-only tests: Azure on/off for translation and the optional Claude CLI provider (no real calls)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import core.llm_manager as lm
import core.translation_service as ts
from core.llm_manager import MultiLLMManager
from core.llm_settings import apply_translation_mode, key_status, load_use_azure, save_selection

SOURCE = {"question": "Q", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}}
LANGS = ["en"] + [f"l{i:02d}" for i in range(20)]


def _llm_fake(seen):
    def fake(source, source_language, langs, manager):
        seen.extend(langs)
        return {lang: {"question": lang} for lang in langs}, "P", []

    return fake


class AzureSwitchTests(unittest.TestCase):
    def test_env_parsing(self):
        for value, expected in [("1", True), ("true", True), ("", True), ("0", False), ("false", False),
                                ("OFF", False)]:
            with patch.dict(os.environ, {"TRANSLATION_USE_AZURE": value}):
                self.assertIs(ts.translation_use_azure(), expected, value)
        with patch.dict(os.environ, {"TRANSLATION_USE_AZURE": "auto", "AZURE_TRANSLATOR_KEY": ""}):
            self.assertFalse(ts.translation_use_azure())
        with patch.dict(os.environ, {"TRANSLATION_USE_AZURE": "auto", "AZURE_TRANSLATOR_KEY": "k"}):
            self.assertTrue(ts.translation_use_azure())
        with patch.dict(os.environ):
            os.environ.pop("TRANSLATION_USE_AZURE", None)
            self.assertTrue(ts.translation_use_azure())

    def test_azure_off_makes_no_azure_calls_and_uses_llm_for_all(self):
        seen = []
        with patch.dict(os.environ, {"TRANSLATION_USE_AZURE": "0"}), \
             patch.object(ts, "_azure_translate", side_effect=AssertionError("azure called")) as azure, \
             patch.object(ts, "_wait_for_azure_character_budget", side_effect=AssertionError("budget wait")) as wait, \
             patch.object(ts, "_llm_translate_with_cooldown", side_effect=_llm_fake(seen)):
            result, provider = ts.translate_localized_content(SOURCE, "en", LANGS, llm_manager=Mock())
        azure.assert_not_called()
        wait.assert_not_called()
        self.assertEqual(sorted(seen), sorted(LANGS[1:]))
        self.assertEqual(set(result), set(LANGS))
        self.assertNotIn("AZURE", provider)

    def test_azure_on_unchanged(self):
        azure_result = {lang: {"question": lang} for lang in LANGS[1:]}
        with patch.dict(os.environ, {"TRANSLATION_USE_AZURE": "1"}), \
             patch.object(ts, "_azure_translate", return_value=(azure_result, [], "")) as azure, \
             patch.object(ts, "_llm_translate_with_cooldown", side_effect=AssertionError("llm called")):
            result, provider = ts.translate_localized_content(SOURCE, "en", LANGS, llm_manager=Mock())
        azure.assert_called_once()
        self.assertEqual(provider, "AZURE_TRANSLATOR")
        self.assertEqual(set(result), set(LANGS))

    def test_workers_cap_and_batch_env(self):
        with patch.dict(os.environ, {"TRANSLATION_LLM_WORKERS": "20", "TRANSLATION_LLM_BATCH_SIZE": "5"}):
            self.assertEqual(ts._llm_workers(), 6)
            self.assertEqual(ts._llm_batch_size(), 5)
        with patch.dict(os.environ):
            os.environ.pop("TRANSLATION_LLM_WORKERS", None)
            self.assertEqual(ts._llm_workers(), 3)

    def test_settings_persist_and_survive_save_selection(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            path = Path(tmp) / "s.json"
            os.environ.pop("TRANSLATION_USE_AZURE", None)
            self.assertTrue(load_use_azure(path))
            apply_translation_mode(False, path)
            self.assertEqual(os.environ["TRANSLATION_USE_AZURE"], "0")
            self.assertFalse(load_use_azure(path))
            save_selection(["groq"], path)
            self.assertFalse(load_use_azure(path))
            apply_translation_mode(True, path)
            self.assertTrue(load_use_azure(path))


class ClaudeProviderTests(unittest.TestCase):
    def setUp(self):
        lm.claude_ready.cache_clear()
        self.addCleanup(lm.claude_ready.cache_clear)

    def manager(self, allowed, ready):
        env = {"GROQ_API_KEY": "g", "LLM_ALLOWED_PROVIDERS": allowed}
        with patch.dict(os.environ, env), patch.object(lm, "claude_ready", return_value=ready), \
             patch("core.llm_manager.Groq"):
            return MultiLLMManager()

    def test_not_selected_unless_explicit_and_ready(self):
        self.assertNotIn("claude", self.manager("groq", True).providers)
        self.assertNotIn("claude", self.manager("groq,claude", False).providers)
        self.assertIn("claude", self.manager("groq,claude", True).providers)
        with patch.dict(os.environ, {"GROQ_API_KEY": "g"}):
            os.environ.pop("LLM_ALLOWED_PROVIDERS", None)
            with patch.object(lm, "claude_ready", return_value=True), patch("core.llm_manager.Groq"):
                self.assertNotIn("claude", MultiLLMManager().providers)

    def test_ready_false_without_cli_or_login(self):
        with patch.object(lm, "claude_cmd", return_value=None):
            self.assertFalse(lm.claude_ready())
        lm.claude_ready.cache_clear()
        with patch.object(lm, "claude_cmd", return_value="claude"), \
             patch.object(lm.subprocess, "run", return_value=Mock(stdout='{"loggedIn": false}')):
            self.assertFalse(lm.claude_ready())
        lm.claude_ready.cache_clear()
        with patch.object(lm, "claude_cmd", return_value="claude"), \
             patch.object(lm.subprocess, "run", side_effect=OSError("boom")):
            self.assertFalse(lm.claude_ready())
        lm.claude_ready.cache_clear()
        with patch.object(lm, "claude_cmd", return_value="claude"), \
             patch.object(lm.subprocess, "run", return_value=Mock(stdout='{"loggedIn": true}')):
            self.assertTrue(lm.claude_ready())

    def test_key_status_includes_claude(self):
        with patch.object(lm, "claude_ready", return_value=False):
            self.assertFalse(key_status()["claude"])
        self.assertNotIn("claude", key_status({"GEMINI_API_KEY": "x"}))

    def test_subprocess_safe_argv_env_and_json_output(self):
        proc = MagicMock(returncode=0)
        proc.communicate.return_value = ('{"answer": 1}', "")
        env = {"ANTHROPIC_API_KEY": "secret", "GROQ_API_KEY": "secret", "PATH": "p", "FIREBASE_X": "y"}
        with patch.dict(os.environ, env), patch.object(lm, "claude_cmd", return_value="claude"), \
             patch.object(lm.subprocess, "Popen", return_value=proc) as popen:
            out = lm.run_claude_cli("SYS", "USER", "sonnet", 60)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[:8], ["claude", "-p", "--model", "sonnet", "--tools", "", "--no-session-persistence",
                                    "--disable-slash-commands"])
        self.assertEqual(argv[-2:], ["--system-prompt", "SYS"])
        child_env = popen.call_args.kwargs["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", child_env)
        self.assertNotIn("GROQ_API_KEY", child_env)
        self.assertNotIn("FIREBASE_X", child_env)
        self.assertEqual(child_env["PATH"], "p")
        self.assertNotEqual(Path(popen.call_args.kwargs["cwd"]).resolve(), Path.cwd().resolve())
        self.assertTrue(proc.communicate.call_args.args[0].startswith("USER"))  # prompt via stdin
        self.assertEqual(json.loads(out), {"answer": 1})

    def test_timeout_kills_process_tree(self):
        proc = MagicMock(returncode=None, pid=123)
        proc.communicate.side_effect = [subprocess.TimeoutExpired("claude", 1), ("", "")]
        with patch.object(lm, "claude_cmd", return_value="claude"), \
             patch.object(lm.subprocess, "Popen", return_value=proc), \
             patch.object(lm, "_kill_process_tree") as kill:
            with self.assertRaises(TimeoutError):
                lm.run_claude_cli("", "u", "sonnet", 1)
        kill.assert_called_once_with(proc)

    def test_generate_trivia_routes_to_claude_and_parses_json(self):
        manager = self.manager("claude", True)
        manager.providers = ["claude"]
        with patch.object(lm, "run_claude_cli", return_value='```json\n{"ok": true}\n```') as run:
            data = manager.generate_trivia("s", "u")
        self.assertTrue(data["ok"])
        self.assertEqual(data["_provider"], "claude")
        self.assertEqual(run.call_args.args[2], "sonnet")


if __name__ == "__main__":
    unittest.main()
