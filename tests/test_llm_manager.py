"""
Knowley Soru Üretim Motoru v1.0.3
MultiLLMManager Çoklu Sağlayıcı ve Fallback Birim Testleri
"""

import unittest
from unittest.mock import Mock, patch
from core.llm_manager import AllProvidersCoolingDown, MultiLLMManager


class TestMultiLLMManager(unittest.TestCase):

    def setUp(self):
        # Birim testleri gerçek anahtar veya ağ erişimi gerektirmemeli.
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "test-only-placeholder"}):
            self.manager = MultiLLMManager()

    def test_providers_initialized(self):
        """Sağlayıcı havuzunda en az bir sağlayıcı bulunmalıdır."""
        self.assertGreaterEqual(len(self.manager.providers), 1)
        for p in self.manager.providers:
            self.assertIn(p, ["gemini", "groq", "cohere", "nvidia"])

    @patch.object(MultiLLMManager, "_call_nvidia")
    def test_nvidia_provider_is_supported(self, mock_nvidia):
        mock_nvidia.return_value = '{"question": "NVIDIA Question"}'
        self.manager.providers = ["nvidia"]
        self.manager.current_idx = 0

        result = self.manager.generate_trivia("sys", "user")

        self.assertEqual(result["_provider"], "nvidia")
        self.assertEqual(result["question"], "NVIDIA Question")

    @patch.object(MultiLLMManager, "_call_gemini")
    @patch.object(MultiLLMManager, "_call_groq")
    @patch.object(MultiLLMManager, "_call_cohere")
    def test_round_robin_rotation(self, mock_cohere, mock_groq, mock_gemini):
        """Motorlar Round-Robin sıralamasıyla dönüşümlü olarak çağrılmalıdır."""
        mock_gemini.return_value = '{"question": "Gemini Question"}'
        mock_groq.return_value = '{"question": "Groq Question"}'
        mock_cohere.return_value = '{"question": "Cohere Question"}'

        self.manager.providers = ["gemini", "groq", "cohere"]
        self.manager.current_idx = 0

        res1 = self.manager.generate_trivia("sys", "user")
        self.assertEqual(res1["_provider"], "gemini")
        self.assertEqual(res1["question"], "Gemini Question")
        self.assertEqual(self.manager.current_idx, 1)

        res2 = self.manager.generate_trivia("sys", "user")
        self.assertEqual(res2["_provider"], "groq")
        self.assertEqual(res2["question"], "Groq Question")
        self.assertEqual(self.manager.current_idx, 2)

        res3 = self.manager.generate_trivia("sys", "user")
        self.assertEqual(res3["_provider"], "cohere")
        self.assertEqual(res3["question"], "Cohere Question")
        self.assertEqual(self.manager.current_idx, 0)

        # Başa dönme kontrolü
        res4 = self.manager.generate_trivia("sys", "user")
        self.assertEqual(res4["_provider"], "gemini")
        self.assertEqual(self.manager.current_idx, 1)

    @patch.object(MultiLLMManager, "_call_gemini")
    @patch.object(MultiLLMManager, "_call_groq")
    def test_fallback_on_error(self, mock_groq, mock_gemini):
        """Bir sağlayıcı hata (örn. 429 Rate Limit) verdiğinde otomatik olarak sıradaki motora geçmelidir."""
        mock_gemini.side_effect = Exception("429 Too Many Requests")
        mock_groq.return_value = '{"question": "Groq Fallback Question"}'

        self.manager.providers = ["gemini", "groq"]
        self.manager.current_idx = 0

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
        self.manager.current_idx = 0

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

    @patch.object(MultiLLMManager, "_call_cohere")
    def test_markdown_code_block_stripping(self, mock_cohere):
        """Markdown kod blokları (```json ... ```) temizlenip geçerli JSON olarak ayrıştırılmalıdır."""
        mock_cohere.return_value = '```json\n{"question": "Cleaned Question"}\n```'
        self.manager.providers = ["cohere"]
        self.manager.current_idx = 0

        res = self.manager.generate_trivia("sys", "user")
        self.assertEqual(res["_provider"], "cohere")
        self.assertEqual(res["question"], "Cleaned Question")


if __name__ == "__main__":
    unittest.main()
