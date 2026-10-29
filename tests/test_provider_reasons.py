import os
import unittest
from unittest.mock import MagicMock, patch

from core.llm_manager import AllProvidersCoolingDown, MultiLLMManager
from worker import provider_reasons


class ProviderReasonTests(unittest.TestCase):
    def make(self):
        keys = {"GROQ_API_KEY": "secret-groq-key-123", "COHERE_API_KEY": "c"}
        env = patch.dict(os.environ, {**keys, "LLM_ALLOWED_PROVIDERS": "groq,cohere"})
        env.start()  # stays active: redaction reads the key from the environment at call time
        self.addCleanup(env.stop)
        with patch("core.llm_manager.Groq"), patch("core.llm_manager.cohere.ClientV2"):
            return MultiLLMManager()

    def test_provider_errors_are_redacted_and_survive_cooldown_retry(self):
        m = self.make()
        m._call_groq = MagicMock(side_effect=RuntimeError("HTTP 500 boom secret-groq-key-123"))
        m._call_cohere = MagicMock(side_effect=RuntimeError("HTTP 500 cohere down"))
        for _ in range(2):  # second round: both providers are cooling down, reasons must remain
            with self.assertRaises(AllProvidersCoolingDown) as ctx:
                m.generate_trivia("s", "u")
            text = provider_reasons(ctx.exception)
            self.assertIn("groq:", text)
            self.assertIn("cohere:", text)
            self.assertNotIn("secret-groq-key-123", text)

    def test_groq_413_tries_next_model(self):
        m = self.make()
        m.groq_models = ["a", "b"]
        err = RuntimeError("413 too large")
        ok = MagicMock()
        ok.choices = [MagicMock(message=MagicMock(content="{}"))]
        m.groq_client = MagicMock()
        m.groq_client.chat.completions.with_raw_response.create.side_effect = AttributeError
        m.groq_client.chat.completions.create.side_effect = [err, ok]
        self.assertEqual(m._call_groq("s", "u"), "{}")

    def _groq_calls(self, first_error):
        m = self.make()
        m.groq_models = ["a", "b"]
        ok = MagicMock()
        ok.choices = [MagicMock(message=MagicMock(content="{}"))]
        m.groq_client = MagicMock()
        m.groq_client.chat.completions.with_raw_response.create.side_effect = AttributeError
        create = m.groq_client.chat.completions.create
        create.side_effect = [first_error, ok]
        try:
            m._call_groq("s", "u")
        except Exception:  # noqa: BLE001 - only call count matters
            pass
        return create.call_count

    def test_groq_413_body_numbers_do_not_break_loop(self):
        err = RuntimeError("Limit 8000, Requested 8403")
        err.status_code = 413
        self.assertEqual(self._groq_calls(err), 2)

    def test_groq_auth_and_rate_status_break_loop(self):
        for code in (401, 403, 429):
            err = RuntimeError("x")
            err.status_code = code
            self.assertEqual(self._groq_calls(err), 1, code)
        self.assertEqual(self._groq_calls(TimeoutError("timed out")), 1)

    def test_provider_reasons_total_is_capped(self):
        err = AllProvidersCoolingDown.__new__(AllProvidersCoolingDown)
        err.provider_errors = {p: "x" * 300 for p in ("a", "b", "c", "d")}
        self.assertLessEqual(len(provider_reasons(err)), 600)


if __name__ == "__main__":
    unittest.main()
