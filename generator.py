import os
import json
import re
import logging
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
    version: str = Field(default="1.0.1", description="Soru veri şeması versiyonu")
    difficulty_local: str = Field(default="Orta", description="Yerel zorluk seviyesi (Kolay, Orta, Zor)")
    difficulty_local_score: int = Field(default=5, description="Yerel zorluk puanı (1-10)")
    difficulty_global: str = Field(default="Orta", description="Küresel zorluk seviyesi (Kolay, Orta, Zor)")
    difficulty_global_score: int = Field(default=5, description="Küresel zorluk puanı (1-10)")
    difficulty_meta: Optional[Dict[str, Any]] = Field(default=None, description="Yerel ve küresel zorluk meta sözlüğü")
    difficulty_profile: DifficultyProfile = Field(description="Zorluk profili (yerel ve küresel)")
    correct_answer: Literal["A", "B", "C", "D"] = Field(description="Doğru cevap şıkkı")
    correct_option: Optional[Literal["A", "B", "C", "D"]] = Field(default=None, description="Hedef doğru cevap şıkkı")
    duration_local: int = Field(default=15, description="Yerel soru süresi (saniye)")
    duration_global: int = Field(default=15, description="Küresel soru süresi (saniye)")
    duration_seconds: int = Field(default=15, description="Varsayılan soru süresi (saniye)")
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
    target_subcategory: Optional[str] = None,
    secondary_category: Optional[str] = None,
    target_country: Optional[str] = None,
    base_difficulty: Optional[str] = None
) -> str:
    is_combo = secondary_category is not None
    country_rule = target_country if target_country else "Global"

    primary_sub_cats = list(CATEGORIES_META.get(primary_category, {}).get("sub_categories", {}).keys())

    if target_subcategory:
        subcategory_instruction = (
            f"- Hedef Alt Kategori (ZORUNLU): {target_subcategory}\n"
            f"- Soru içeriği KESİNLİKLE ve DOĞRUDAN '{primary_category}' ana kategorisinin '{target_subcategory}' alt dalına ait olmalıdır.\n"
            f"- Çıktı JSON'ındaki 'sub_categories' alanında ilk ve ana eleman olarak tam olarak '[\"{target_subcategory}\"]' yer almalıdır."
        )
        sub_schema_val = f'"{target_subcategory}"'
    else:
        sub_cat_hint = f" (Önerilen alt dallar: {', '.join(primary_sub_cats)})" if primary_sub_cats else ""
        subcategory_instruction = f"- Alt Kategori Kılavuzu: Serbest{sub_cat_hint}"
        sub_schema_val = '"Alt Dal"'

    target_diff = base_difficulty if base_difficulty else "Orta"
    sample_score = 2 if target_diff == "Kolay" else (6 if target_diff == "Orta" else 9)

    difficulty_instruction = (
        f"ZORLUK SEVİYESİ VE PUANLAMA TALİMATI (ZORUNLU):\n"
        f"- Üretilecek sorunun Yerel Zorluk Seviyesi: {target_diff}.\n"
        f"- Lütfen yerel puanı ('difficulty_local_score') kesinlikle buna göre ver:\n"
        f"  * Kolay için: 1-4 puan arası (temel bilgi, genel bilinirlik)\n"
        f"  * Orta için: 5-8 puan arası (çeldirici güçlü, orta düzey bilgi)\n"
        f"  * Zor için: 9-10 puan arası (spesifik, derinlemesine uzmanlık bilgisi)\n"
        f"- Global zorluğu ('difficulty_global') ve puanını ('difficulty_global_score') ise sorunun evrensel zorluğuna göre bağımsız belirle (Kolay: 1-4, Orta: 5-8, Zor: 9-10)."
    )

    return f"""
Sen profesyonel ve çok dilli bir soru hazırlama uzmanısın.
Aşağıdaki kriterlere göre yüksek kaliteli, özgün tek bir çoktan seçmeli soru üret ve 5 dilde ('tr', 'en', 'es', 'pt', 'de') hazırla.

Kriterler:
- Birincil Kategori: {primary_category}
{subcategory_instruction}
{f"- İkincil Kategori (Combo): {secondary_category}" if is_combo else "- Tip: Standart (Tek Kategori)"}
- Odak Ülke/Bölge: {country_rule}
{difficulty_instruction}

ZORLUK DEĞERLENDİRME VE 4 DİNAMİK SENARYO KURALI:
Model olarak sorunun içeriğini ve hedef kitlesini analiz ederek aşağıdaki 4 senaryodan hangisine uyduğunu tespit et; hem `local` hem de `global` zorluk ve puanını dinamik olarak ata.
Hiçbir zorluk seviyesini varsayılan (default) kabul etme! `global` seviyesi de soruya göre "Kolay", "Orta" veya "Zor" olabilmeli ve skoru (1-10) gerçek küresel bilinirliğe göre atanmalıdır.

1. **Evrensel Kolay (Global: Kolay | Local: Kolay):**
   - Tüm dünyada ve yerelde hemen herkesin bildiği popüler kültür, temel coğrafya veya genel kültür soruları.
   - Örnekler: "Fransa'nın başkenti neresidir?", "Güneş sisteminin en büyük gezegeni hangisidir?", "Titanic filminin yönetmeni kimdir?"
   - Skor Aralığı: Local: 1-4 | Global: 1-4

2. **Küresel Orta / Popüler (Global: Orta | Local: Kolay veya Orta):**
   - Dünya çapında tanınan ama temel düzeyin bir tık üstündeki spor, sinema, tarih veya bilim soruları.
   - Örnekler: "Real Madrid'in Şampiyonlar Ligi şampiyonlukları", "Mitoz bölünme evreleri", "Newton'ın hareket yasaları".
   - Skor Aralığı: Local: 2-6 | Global: 5-8

3. **Yerel Niş / Küresel Zor (Global: Zor | Local: Kolay veya Orta):**
   - Sadece o ülkenin ({country_rule}) vatandaşlarının bilebileceği yerel ligler, yerel diziler, mahalli tarih veya yazarlar.
   - Örnekler: "Türkiye Süper Liginde 2010 şampiyonu kimdir?", "İspanya yerel bayramı La Tomatina hangi kasabada kutlanır?"
   - Skor Aralığı: Local: 1-6 | Global: 9-10

4. **Evrensel İleri Düzey (Global: Zor | Local: Zor):**
   - Dünyanın her yerinde uzmanlık, ileri bilim veya detaylı bilgi gerektiren sorular.
   - Örnekler: "Kuantum elektrodinamiğinde Feynman diyagramları", "17. yüzyıl Avrupa felsefesi epistemoloji detayları".
   - Skor Aralığı: Local: 9-10 | Global: 9-10

Etiket ve Puan Eşleşme Skalası:
- "Kolay": 1 - 4 puan
- "Orta": 5 - 8 puan
- "Zor": 9 - 10 puan
Seçilen etiket ile puan birbiriyle uyumlu tam sayılar olmalıdır.

Zorunlu JSON Şeması:
{{
  "categories": ["{primary_category}"{f', "{secondary_category}"' if is_combo else ''}],
  "sub_categories": [{sub_schema_val}],
  "is_combo": {str(is_combo).lower()},
  "countries": ["{country_rule}"],
  "version": "1.0.1",
  "difficulty_local": "{target_diff}",
  "difficulty_local_score": {sample_score},
  "difficulty_global": "Orta",
  "difficulty_global_score": 6,
  "difficulty_meta": {{
    "local": {{ "level": "{target_diff}", "score": {sample_score} }},
    "global": {{ "level": "Orta", "score": 6 }}
  }},
  "difficulty_profile": {{
    "local": {{ "label": "{target_diff}", "score": {sample_score} }},
    "global": {{ "label": "Orta", "score": 6 }}
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
    """Kategori rengi, alt kategori görselleri ve zorluk meta verilerini bağlar."""
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
    if not q.difficulty_meta:
        q.difficulty_meta = {
            "local": {
                "level": q.difficulty_local,
                "score": q.difficulty_local_score
            },
            "global": {
                "level": q.difficulty_global,
                "score": q.difficulty_global_score
            }
        }
    return q

def generate_question_with_fallback(
    primary_category: Optional[str] = None,
    secondary_category: Optional[str] = None,
    target_subcategory: Optional[str] = None,
    target_country: Optional[str] = None,
    base_difficulty: Optional[str] = None,
    balancer: Optional[Any] = None,
    balance_options: bool = True,
    is_combo: Optional[bool] = None,
    category_balancer: Optional[Any] = None,
    balance_categories: bool = True,
    difficulty_balancer: Optional[Any] = None,
    balance_difficulty: bool = True
) -> GeneratedQuestion:
    """
    Kategori, şık ve zorluk (4-4-2 kotası) dengeleme mekanizması destekli soru üretim fonksiyonu.
    """
    # 1. Kategori dengelemesi aktifse ve kategori veya alt kategori eksikse
    if balance_categories:
        try:
            from category_balancer import get_category_balancer
            cb = category_balancer or get_category_balancer()

            if primary_category is None:
                target = cb.get_next_target(is_combo=bool(is_combo or secondary_category))
                primary_category = target.primary_category
                target_subcategory = target.target_subcategory
                if is_combo or secondary_category:
                    secondary_category = target.secondary_category
            elif target_subcategory is None:
                subs = cb.subcategory_counts.get(primary_category, {})
                if subs:
                    min_s = min(subs.values())
                    candidates = [s for s, sc in subs.items() if sc == min_s]
                    import random
                    target_subcategory = random.choice(candidates)
        except Exception:
            pass

    # Fallback varsayılan
    if not primary_category:
        primary_category = "Bilim"

    # 2. Zorluk dengelemesi (4-4-2 kuralı)
    from difficulty_balancer import get_difficulty_balancer, DifficultyBalancer
    diff_balancer = difficulty_balancer or get_difficulty_balancer()

    target_diff = base_difficulty
    if balance_difficulty and not target_diff:
        target_diff = diff_balancer.get_next_target()
    if not target_diff:
        target_diff = "Orta"

    prompt = build_prompt(
        primary_category=primary_category,
        target_subcategory=target_subcategory,
        secondary_category=secondary_category,
        target_country=target_country,
        base_difficulty=target_diff
    )
    
    last_error: Optional[Exception] = None
    for item in MODEL_CASCADE:
        provider = item["provider"]
        model = item["model"]
        
        try:
            q: Optional[GeneratedQuestion] = None
            if provider == "gemini" and gemini_client:
                q = generate_with_gemini(gemini_client, model, prompt)
            elif provider == "groq" and groq_client:
                q = generate_with_groq(groq_client, model, prompt)

            if q:
                # Hedef alt kategori varsa ve model döndürmediyse başa ekle
                if target_subcategory and (not q.sub_categories or target_subcategory not in q.sub_categories):
                    q.sub_categories = [target_subcategory] + [s for s in q.sub_categories if s != target_subcategory]

                # Zorluk doğrulaması ve sınırlandırma (Clamping)
                local_level = target_diff
                raw_local_score = getattr(q, "difficulty_local_score", None)
                if (not raw_local_score or raw_local_score == 5) and q.difficulty_profile and q.difficulty_profile.local:
                    raw_local_score = q.difficulty_profile.local.score
                clamped_local_score = DifficultyBalancer.validate_and_clamp_score(local_level, raw_local_score)

                raw_global_level = getattr(q, "difficulty_global", None)
                if not raw_global_level and q.difficulty_profile and q.difficulty_profile.global_scope:
                    raw_global_level = q.difficulty_profile.global_scope.label
                if not raw_global_level:
                    raw_global_level = "Orta"

                raw_global_score = getattr(q, "difficulty_global_score", None)
                if (not raw_global_score or raw_global_score == 5) and q.difficulty_profile and q.difficulty_profile.global_scope:
                    raw_global_score = q.difficulty_profile.global_scope.score
                clamped_global_score = DifficultyBalancer.validate_and_clamp_score(raw_global_level, raw_global_score)
                final_global_level = DifficultyBalancer.score_to_level(clamped_global_score)

                q.version = "1.0.1"
                q.difficulty_local = local_level
                q.difficulty_local_score = clamped_local_score
                q.difficulty_global = final_global_level
                q.difficulty_global_score = clamped_global_score
                q.difficulty_meta = {
                    "local": {
                        "level": local_level,
                        "score": clamped_local_score
                    },
                    "global": {
                        "level": final_global_level,
                        "score": clamped_global_score
                    }
                }
                q.difficulty_profile = DifficultyProfile(
                    local=DifficultyScope(label=local_level, score=clamped_local_score),
                    global_scope=DifficultyScope(label=final_global_level, score=clamped_global_score)
                )

                q = populate_question_meta(q, primary_category)
                if balance_options:
                    from option_balancer import get_option_balancer
                    b = balancer or get_option_balancer()
                    q = b.balance_question(q)
                
                from timer_calculator import calculate_durations_from_obj
                durations = calculate_durations_from_obj(q)
                q.duration_local = durations["duration_local"]
                q.duration_global = durations["duration_global"]
                q.duration_seconds = durations["duration_seconds"]

                # Zorluk kotası logu
                remaining_quota = diff_balancer.get_remaining_summary()
                logging.info(
                    f"🎯 [Zorluk] Yerel: {q.difficulty_local} ({q.difficulty_local_score}/10) | "
                    f"Global: {q.difficulty_global} ({q.difficulty_global_score}/10) | "
                    f"Kalan Blok Kotası: {remaining_quota}"
                )
                return q
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"Tüm model zinciri tükendi! Son hata: {last_error}")

def create_gemini_client() -> Optional[genai.Client]:
    """Geriye dönük uyumluluk için Gemini istemcisi döndürür."""
    return gemini_client

def generate_single_question(
    client: Any = None,
    category: Optional[str] = "Bilim",
    sub_category: Optional[str] = None,
    filter_tag: Optional[str] = None,
    difficulty: str = "Orta",
    balancer: Optional[Any] = None,
    balance_options: bool = True,
    category_balancer: Optional[Any] = None,
    balance_categories: bool = True
) -> GeneratedQuestion:
    """Geriye dönük uyumluluk fonksiyonu."""
    return generate_question_with_fallback(
        primary_category=category,
        secondary_category=None,
        target_subcategory=sub_category,
        target_country=None,
        base_difficulty=difficulty,
        balancer=balancer,
        balance_options=balance_options,
        category_balancer=category_balancer,
        balance_categories=balance_categories
    )