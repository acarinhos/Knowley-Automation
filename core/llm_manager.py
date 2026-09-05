import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

import google.generativeai as genai
from groq import Groq
import cohere

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLMManager")


class MultiLLMManager:
    def __init__(self):
        self.providers = []

        # 1. Groq (Llama 3.3 70B - En Yüksek Hız ve Kota)
        self.groq_key = os.getenv("GROQ_API_KEY")
        if self.groq_key:
            try:
                self.groq_client = Groq(api_key=self.groq_key)
                self.providers.append("groq")
            except Exception as e:
                logger.warning(f"Groq başlatılamadı: {e}")

        # 2. Cohere (Trial Key - 1000 çağrı/ay, 20 RPM)
        self.cohere_key = os.getenv("COHERE_API_KEY")
        if self.cohere_key:
            try:
                self.cohere_client = cohere.ClientV2(api_key=self.cohere_key)
                self.providers.append("cohere")
            except Exception as e:
                logger.warning(f"Cohere başlatılamadı: {e}")

        # 3. Gemini (Flash 1.5)
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        if self.gemini_key:
            try:
                genai.configure(api_key=self.gemini_key)
                self.gemini_model = genai.GenerativeModel("gemini-1.5-flash")
                self.providers.append("gemini")
            except Exception as e:
                logger.warning(f"Gemini başlatılamadı: {e}")

        if not self.providers:
            raise ValueError("Hiçbir LLM API anahtarı bulunamadı!")

        self.current_idx = 0
        logger.info(f"Aktif LLM Sağlayıcıları: {self.providers}")

    def _call_groq(self, system_prompt: str, user_prompt: str) -> str:
        for model_name in [
            "llama-3.1-70b-versatile",
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile",
            "qwen/qwen3.8-27b",
        ]:
            try:
                completion = self.groq_client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"}
                )
                return completion.choices[0].message.content
            except Exception:
                continue
        raise RuntimeError("Groq modellerinin hiçbirine erişilemedi.")

    def _call_cohere(self, system_prompt: str, user_prompt: str) -> str:
        res = self.cohere_client.chat(
            model="command-r-plus-08-2024",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{user_prompt}\n\nNOT: Yalnızca ham JSON nesnesi döndür."}
            ],
            response_format={"type": "json_object"}
        )
        return res.message.content[0].text

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        full_prompt = f"{system_prompt}\n\n{user_prompt}\n\nNOT: Yalnızca geçerli bir JSON döndür."
        try:
            response = self.gemini_model.generate_content(
                full_prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            return response.text
        except Exception as e:
            err_msg = str(e).lower()
            if "not found" in err_msg or "404" in err_msg or "no longer available" in err_msg:
                # Google deprecated gemini-1.5-flash in favor of gemini-flash-latest / 2.5-flash
                fallback_model = genai.GenerativeModel("gemini-flash-latest")
                response = fallback_model.generate_content(
                    full_prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                return response.text
            raise e

    def generate_trivia(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        attempts = len(self.providers)

        for _ in range(attempts):
            provider = self.providers[self.current_idx]
            self.current_idx = (self.current_idx + 1) % len(self.providers)

            try:
                if provider == "groq":
                    raw = self._call_groq(system_prompt, user_prompt)
                elif provider == "cohere":
                    raw = self._call_cohere(system_prompt, user_prompt)
                elif provider == "gemini":
                    raw = self._call_gemini(system_prompt, user_prompt)

                clean = raw.strip()
                if clean.startswith("```"):
                    clean = clean.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                data = json.loads(clean)
                data["_provider"] = provider
                return data

            except Exception as e:
                logger.warning(f"⚠️ {provider.upper()} çağrısı başarısız oldu ({str(e)}). Sıradaki sağlayıcıya geçiliyor...")
                continue

        raise RuntimeError("Havuzdaki tüm LLM motorlarının kotası doldu! Tüm LLM sağlayıcıları hata verdi.")


_global_llm_manager: Optional[MultiLLMManager] = None

def get_llm_manager() -> MultiLLMManager:
    """Paylaşılan MultiLLMManager singleton örneğini döndürür."""
    global _global_llm_manager
    if _global_llm_manager is None:
        _global_llm_manager = MultiLLMManager()
    return _global_llm_manager
