"""
Knowley Soru Üretim Motoru v1.0.3
MultiLLMManager Çoklu Sağlayıcı ve Fallback Birim Testleri
"""

import os
import unittest
from unittest.mock import Mock, patch
from core.llm_manager import AllProvidersCoolingDown, MultiLLMManager, ProviderCallError


class TestMultiLLMManager(unittest.TestCase):

    def setUp(self):
        # Birim testleri gerçek anahtar veya ağ erişimi gerektirmemeli.
        with patch.dict(
            "os.environ",
            {
                "NVIDIA_API_KEY": "test-only-placeholder",
                "LLM_ALLOWED_PROVIDERS": "nvidia",
            },
        ):
            self.manager = MultiLLMManager()

    def test_providers_initialized(self):
        """Sağlayıcı havuzunda en az bir sağlayıcı bulunmalıdır."""
        self.assertGreaterEqual(len(self.manager.providers), 1)
        for p in self.manager.providers:
            self.assertIn(p, ["gemini", "groq", "cohere", "nvidia"])

    def test_default_enables_each_configured_provider_as_fallback(self):
        keys = {
            "GEMINI_API_KEY": "test-gemini",
            "GROQ_API_KEY": "test-groq",
            "COHERE_API_KEY": "test-cohere",
            "NVIDIA_API_KEY": "test-nvidia",
        }
        with patch.dict("os.environ", keys):
            os.environ.pop("LLM_ALLOWED_PROVIDERS", None)
            manager = MultiLLMManager()

        self.assertEqual(manager.providers, ["gemini", "groq", "cohere", "nvidia"])

    @patch.object(MultiLLMManager, "_call_nvidia")
    def test_nvidia_provider_is_supported(self, mock_nvidia):
        mock_nvidia.return_value = '{"question": "NVIDIA Question"}'
        self.manager.providers = ["nvidia"]

        result = self.manager.generate_trivia("sys", "user")

        self.assertEqual(result["_provider"], "nvidia")
        self.assertEqual(result["question"], "NVIDIA Question")

    @patch.object(MultiLLMManager, "_call_gemini")
    @patch.object(MultiLLMManager, "_call_groq")
    @patch.object(MultiLLMManager, "_call_cohere")
    @patch.object(MultiLLMManager, "_call_nvidia")
    def test_primary_provider_rotates_across_configured_providers(
        self, mock_nvidia, mock_cohere, mock_groq, mock_gemini
    ):
        """Başarılı soru çağrıları etkin sağlayıcılar arasında sırayla dağıtılır."""
        mock_gemini.return_value = '{"question": "Gemini Question"}'
        mock_groq.return_value = '{"question": "Groq Question"}'
        mock_cohere.return_value = '{"question": "Cohere Question"}'
        mock_nvidia.return_value = '{"question": "NVIDIA Question"}'

        self.manager.providers = ["gemini", "groq", "cohere", "nvidia"]
        providers = [
            self.manager.generate_trivia("sys", "user")["_provider"]
            for _ in range(4)
        ]

        self.assertEqual(providers, ["gemini", "groq", "cohere", "nvidia"])
        for mock_provider in (mock_gemini, mock_groq, mock_cohere, mock_nvidia):
            self.assertEqual(mock_provider.call_count, 1)

    @patch.object(MultiLLMManager, "_call_gemini")
    @patch.object(MultiLLMManager, "_call_groq")
    def test_fallback_on_error(self, mock_groq, mock_gemini):
        """Bir sağlayıcı hata (örn. 429 Rate Limit) verdiğinde otomatik olarak sıradaki motora geçmelidir."""
        mock_gemini.side_effect = Exception("429 Too Many Requests")
        mock_groq.return_value = '{"question": "Groq Fallback Question"}'

        self.manager.providers = ["gemini", "groq"]

        res = self.manager.generate_trivia("sys", "user")
        self.assertEqual(res["_provider"], "groq")
        self.assertEqual(res["question"], "Groq Fallback Question")

    @patch.object(MultiLLMManager, "_call_gemini")
    @patch.object(MultiLLMManager, "_call_groq")
    @patch.object(MultiLLMManager, "_call_cohere")
    def test_all_providers_fail_raises_runtime_error(self, mock_cohere, mock_groq, mock_gemini):
        """Tüm sağlayıcılar hata verdiğinde RuntimeError fırlatılmalıdır."""
        mock_gemini.side_effect = Exception("Gemini Error")
        mock_groq.side_effect = Exception("Groq Error")
        mock_cohere.side_effect = Exception("Cohere Error")

        self.manager.providers = ["gemini", "groq", "cohere"]

        with self.assertRaises(AllProvidersCoolingDown) as ctx:
            self.manager.generate_trivia("sys", "user")
        self.assertIn("Tüm LLM sağlayıcıları hata verdi", str(ctx.exception))

    @patch.object(MultiLLMManager, "_call_cohere")
    def test_cooldown_provider_is_not_called_again(self, mock_cohere):
        mock_cohere.side_effect = Exception("429 Too Many Requests")
        self.manager.providers = ["cohere"]
        with self.assertRaises(AllProvidersCoolingDown):
            self.manager.generate_trivia("sys", "user")
        with self.assertRaises(AllProvidersCoolingDown):
            self.manager.generate_trivia("sys", "user")
        self.assertEqual(mock_cohere.call_count, 1)

    def test_cohere_stops_model_cascade_on_rate_limit(self):
        client = Mock()
        client.chat.side_effect = Exception("429 Too Many Requests")
        self.manager.cohere_client = client
        self.manager.cohere_models = ["one", "two", "three"]

        with self.assertRaises(RuntimeError):
            self.manager._call_cohere("sys", "user")

        self.assertEqual(client.chat.call_count, 1)

    def test_gemini_error_keeps_correct_provider_label(self):
        client = Mock()
        client.models.generate_content.side_effect = Exception("invalid model")
        self.manager.gemini_client = client
        self.manager.gemini_models = ["test-model"]
        self.manager.gemini_quota_guard = None

        with self.assertRaises(ProviderCallError) as ctx:
            self.manager._call_gemini("sys", "user")

        self.assertIn("Gemini provider request failed", str(ctx.exception))

    @patch.object(MultiLLMManager, "_call_cohere")
    def test_markdown_code_block_stripping(self, mock_cohere):
        """Markdown kod blokları (```json ... ```) temizlenip geçerli JSON olarak ayrıştırılmalıdır."""
        mock_cohere.return_value = '```json\n{"question": "Cleaned Question"}\n```'
        self.manager.providers = ["cohere"]

        res = self.manager.generate_trivia("sys", "user")
        self.assertEqual(res["_provider"], "cohere")
        self.assertEqual(res["question"], "Cleaned Question")


if __name__ == "__main__":
    unittest.main()
