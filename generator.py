import os
import json
import re
from pathlib import Path
from typing import List, Literal, Optional, Dict, Any
from dotenv import load_dotenv
from google import genai
from google.genai import types
from groq import Groq
from pydantic import BaseModel, Field, ConfigDict, field_validator

from categories_config import (
    CATEGORIES_META,
    get_category_color,
    get_subcategory_image,
)

env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# --- Pydantic Şemaları ---

class Options(BaseModel):
    A: str = Field(description="A şıkkı metni")
    B: str = Field(description="B şıkkı metni")
    C: str = Field(description="C şıkkı metni")
    D: str = Field(description="D şıkkı metni")

class LocalizedContent(BaseModel):
    question: str = Field(description="Soru metni")
    options: Options = Field(description="4 seçenek")
    explanation: str = Field(description="Çözüm açıklaması")
    filter_tag: str = Field(description="Konu etiketi")

class CategoryMeta(BaseModel):
    name: str = Field(description="Kategori adı")
    color: str = Field(description="Kategori HEX renk kodu")

class SubCategoryMeta(BaseModel):
    name: str = Field(description="Alt kategori adı")
    image_url: str = Field(description="Alt kategori görsel URL'si")

class DifficultyScope(BaseModel):
    label: Literal["Kolay", "Orta", "Zor"] = Field(description="Zorluk seviyesi etiketi")
    score: int = Field(description="1-10 arası hassas zorluk puanı", ge=1, le=10)

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, v: Any) -> str:
        if isinstance(v, str):
            v_clean = v.strip().capitalize()
            mapping = {
                "Kolay": "Kolay",
                "Easy": "Kolay",
                "Orta": "Orta",
                "Medium": "Orta",
                "Zor": "Zor",
                "Hard": "Zor",
                "Difficult": "Zor",
            }
            if v_clean in mapping:
                return mapping[v_clean]
            return v_clean
        return str(v)

    @field_validator("score", mode="before")
    @classmethod
    def normalize_score(cls, v: Any) -> int:
        if isinstance(v, str):
            try:
                v = int(float(v))
            except ValueError:
                pass
        if isinstance(v, (int, float)):
            return max(1, min(10, int(v)))
        return 5

class DifficultyProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    local: DifficultyScope = Field(description="İlgili ülkedeki kullanıcılara göre zorluk")
    global_scope: DifficultyScope = Field(alias="global", description="Dünya geneli kullanıcılara göre zorluk")

class GeneratedQuestion(BaseModel):
    categories: List[str] = Field(description="Ana kategori listesi")
    categories_meta: List[CategoryMeta] = Field(default_factory=list, description="Kategori meta bilgileri (isim ve renk)")
    sub_categories: List[str] = Field(default_factory=list, description="Alt dallar")
    sub_categories_meta: List[SubCategoryMeta] = Field(default_factory=list, description="Alt kategori meta bilgileri (isim ve görsel URL)")
    is_combo: bool = Field(default=False, description="Hibrit soru mu")
    countries: List[str] = Field(default_factory=lambda: ["Global"], description="İlgili ülkeler")
    difficulty_profile: DifficultyProfile = Field(description="Zorluk profili (yerel ve küresel)")
    correct_answer: Literal["A", "B", "C", "D"] = Field(description="Doğru cevap şıkkı")
    supported_languages: List[str] = Field(default=["tr", "en", "es", "pt", "de"], description="Desteklenen diller")
    translations: Dict[str, LocalizedContent] = Field(description="5 dilde çeviriler")

# İstemcileri Başlat
gemini_client: Optional[genai.Client] = (
    genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    if os.getenv("GEMINI_API_KEY")
    else None
)
groq_client: Optional[Groq] = (
    Groq(api_key=os.getenv("GROQ_API_KEY"))
    if os.getenv("GROQ_API_KEY")
    else None
)

MODEL_CASCADE = [
    {"provider": "gemini", "model": "gemini-2.5-flash"},
    {"provider": "gemini", "model": "gemini-3.6-flash"},
    {"provider": "groq", "model": "openai/gpt-oss-120b"},
    {"provider": "groq", "model": "openai/gpt-oss-20b"},
]

def build_prompt(
    primary_category: str,
    secondary_category: Optional[str] = None,
    target_country: Optional[str] = None,
    base_difficulty: Optional[str] = None
) -> str:
    is_combo = secondary_category is not None
    country_rule = target_country if target_country else "Global"

    primary_sub_cats = list(CATEGORIES_META.get(primary_category, {}).get("sub_categories", {}).keys())
    sub_cat_hint = f" (Önerilen alt dallar: {', '.join(primary_sub_cats)})" if primary_sub_cats else ""
    difficulty_hint = f"- Genel Zorluk Kılavuzu: {base_difficulty}" if base_difficulty else "- Genel Zorluk Kılavuzu: Serbest (Konuya göre sen belirle)"

    return f"""
Sen profesyonel ve çok dilli bir soru hazırlama uzmanısın.
Aşağıdaki kriterlere göre yüksek kaliteli, özgün tek bir çoktan seçmeli soru üret ve 5 dilde ('tr', 'en', 'es', 'pt', 'de') hazırla.

Kriterler:
- Birincil Kategori: {primary_category}{sub_cat_hint}
{f"- İkincil Kategori (Combo): {secondary_category}" if is_combo else "- Tip: Standart (Tek Kategori)"}
- Odak Ülke/Bölge: {country_rule}
{difficulty_hint}

ZORLUK DEĞERLENDİRME VE 4 DİNAMİK SENARYO KURALI:
Model olarak sorunun içeriğini ve hedef kitlesini analiz ederek aşağıdaki 4 senaryodan hangisine uyduğunu tespit et; hem `local` hem de `global` zorluk ve puanını dinamik olarak ata.
Hiçbir zorluk seviyesini varsayılan (default) kabul etme! `global` seviyesi de soruya göre "Kolay", "Orta" veya "Zor" olabilmeli ve skoru (1-10) gerçek küresel bilinirliğe göre atanmalıdır.

1. **Evrensel Kolay (Global: Kolay | Local: Kolay):**
   - Tüm dünyada ve yerelde hemen herkesin bildiği popüler kültür, temel coğrafya veya genel kültür soruları.
   - Örnekler: "Fransa'nın başkenti neresidir?", "Güneş sisteminin en büyük gezegeni hangisidir?", "Titanic filminin yönetmeni kimdir?"
   - Skor Aralığı: Local: 1-3 | Global: 1-3

2. **Küresel Orta / Popüler (Global: Orta | Local: Kolay veya Orta):**
   - Dünya çapında tanınan ama temel düzeyin bir tık üstündeki spor, sinema, tarih veya bilim soruları.
   - Örnekler: "Real Madrid'in Şampiyonlar Ligi şampiyonlukları", "Mitoz bölünme evreleri", "Newton'ın hareket yasaları".
   - Skor Aralığı: Local: 2-5 | Global: 4-6

3. **Yerel Niş / Küresel Zor (Global: Zor | Local: Kolay veya Orta):**
   - Sadece o ülkenin ({country_rule}) vatandaşlarının bilebileceği yerel ligler, yerel diziler, mahalli tarih veya yazarlar.
   - Örnekler: "Türkiye Süper Liginde 2010 şampiyonu kimdir?", "İspanya yerel bayramı La Tomatina hangi kasabada kutlanır?"
   - Skor Aralığı: Local: 1-4 | Global: 8-10

4. **Evrensel İleri Düzey (Global: Zor | Local: Zor):**
   - Dünyanın her yerinde uzmanlık, ileri bilim veya detaylı bilgi gerektiren sorular.
   - Örnekler: "Kuantum elektrodinamiğinde Feynman diyagramları", "17. yüzyıl Avrupa felsefesi epistemoloji detayları".
   - Skor Aralığı: Local: 8-10 | Global: 8-10

Etiket ve Puan Eşleşme Skalası:
- "Kolay": 1 - 3 puan
- "Orta": 4 - 7 puan
- "Zor": 8 - 10 puan
Seçilen etiket (`label`) ile puan (`score`) birbiriyle uyumlu tam sayılar olmalıdır.

Zorunlu JSON Şeması:
{{
  "categories": ["{primary_category}"{f', "{secondary_category}"' if is_combo else ''}],
  "sub_categories": ["Alt Dal"],
  "is_combo": {str(is_combo).lower()},
  "countries": ["{country_rule}"],
  "difficulty_profile": {{
    "local": {{ "label": "Kolay", "score": 2 }},
    "global": {{ "label": "Orta", "score": 5 }}
  }},
  "correct_answer": "A",
  "supported_languages": ["tr", "en", "es", "pt", "de"],
  "translations": {{
    "tr": {{ "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "explanation": "...", "filter_tag": "..." }},
    "en": {{ "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "explanation": "...", "filter_tag": "..." }},
    "es": {{ "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "explanation": "...", "filter_tag": "..." }},
    "pt": {{ "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "explanation": "...", "filter_tag": "..." }},
    "de": {{ "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "explanation": "...", "filter_tag": "..." }}
  }}
}}
"""

def generate_with_gemini(client: genai.Client, model_name: str, prompt: str) -> GeneratedQuestion:
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GeneratedQuestion,
            temperature=0.7,
        ),
    )
    if isinstance(response.parsed, GeneratedQuestion):
        return response.parsed
    elif isinstance(response.parsed, dict):
        return GeneratedQuestion.model_validate(response.parsed)
    elif hasattr(response, "text") and response.text:
        cleaned = re.sub(r"^```(?:json)?\s*", "", response.text.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return GeneratedQuestion.model_validate_json(cleaned)
    raise ValueError("Gemini geçerli bir soru modeli döndüremedi.")

def generate_with_groq(client: Groq, model_name: str, prompt: str) -> GeneratedQuestion:
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": "You are a database seeding bot. Output ONLY valid, raw JSON adhering strictly to the schema. No markdown backticks."
            },
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    raw_text = response.choices[0].message.content or "{}"
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return GeneratedQuestion.model_validate(json.loads(cleaned))

def populate_question_meta(q: GeneratedQuestion, primary_cat: str) -> GeneratedQuestion:
    """Kategori rengi ve alt kategori görsellerini bağlar."""
    if not q.categories_meta:
        q.categories_meta = [
            CategoryMeta(name=cat, color=get_category_color(cat))
            for cat in q.categories
        ]
    if not q.sub_categories_meta:
        q.sub_categories_meta = [
            SubCategoryMeta(name=sub, image_url=get_subcategory_image(primary_cat, sub))
            for sub in q.sub_categories
        ]
    return q

def generate_question_with_fallback(
    primary_category: str,
    secondary_category: Optional[str] = None,
    target_country: Optional[str] = None,
    base_difficulty: Optional[str] = None
) -> GeneratedQuestion:
    prompt = build_prompt(
        primary_category=primary_category,
        secondary_category=secondary_category,
        target_country=target_country,
        base_difficulty=base_difficulty
    )
    
    last_error: Optional[Exception] = None
    for item in MODEL_CASCADE:
        provider = item["provider"]
        model = item["model"]
        
        try:
            if provider == "gemini" and gemini_client:
                q = generate_with_gemini(gemini_client, model, prompt)
                return populate_question_meta(q, primary_category)
            elif provider == "groq" and groq_client:
                q = generate_with_groq(groq_client, model, prompt)
                return populate_question_meta(q, primary_category)
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"Tüm model zinciri tükendi! Son hata: {last_error}")

def create_gemini_client() -> Optional[genai.Client]:
    """Geriye dönük uyumluluk için Gemini istemcisi döndürür."""
    return gemini_client

def generate_single_question(
    client: Any = None,
    category: str = "Bilim",
    sub_category: Optional[str] = None,
    filter_tag: Optional[str] = None,
    difficulty: str = "Orta"
) -> GeneratedQuestion:
    """Geriye dönük uyumluluk fonksiyonu."""
    return generate_question_with_fallback(
        primary_category=category,
        secondary_category=None,
        target_country=None,
        base_difficulty=difficulty
    )