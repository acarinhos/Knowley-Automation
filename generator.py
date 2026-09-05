import os
import sys
import json
import re
import random
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional, Dict, Any
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
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
    global_scope: Optional[DifficultyScope] = Field(default=None, alias="global", description="Dünya geneli kullanıcılara göre zorluk")

class GeneratedQuestion(BaseModel):
    categories: List[str] = Field(description="Ana kategori listesi")
    categories_meta: List[CategoryMeta] = Field(default_factory=list, description="Kategori meta bilgileri (isim ve renk)")
    sub_categories: List[str] = Field(default_factory=list, description="Alt dallar")
    sub_categories_meta: List[SubCategoryMeta] = Field(default_factory=list, description="Alt kategori meta bilgileri (isim ve görsel URL)")
    is_combo: bool = Field(default=False, description="Hibrit soru mu")
    scope: Literal["global", "local"] = Field(default="global", description="Kapsam: global veya local")
    target_country: Optional[str] = Field(default=None, description="Hedef ülke")
    target_country_credit: Optional[int] = Field(default=None, description="Hedef ülke kredi puanı")
    is_global_eligible: bool = Field(default=True, description="Küresel elit ülke mi (çift zorluk mu)")
    countries: List[str] = Field(default_factory=lambda: ["Global"], description="İlgili ülkeler")
    version: str = Field(default="1.0.2", description="Soru veri şeması versiyonu")
    difficulty_local: str = Field(default="Orta", description="Yerel zorluk seviyesi (Kolay, Orta, Zor)")
    difficulty_local_score: int = Field(default=5, description="Yerel zorluk puanı (1-10)")
    difficulty_global: Optional[str] = Field(default=None, description="Küresel zorluk seviyesi (Kolay, Orta, Zor veya None)")
    difficulty_global_score: Optional[int] = Field(default=None, description="Küresel zorluk puanı (1-10 veya None)")
    difficulty_meta: Optional[Dict[str, Any]] = Field(default=None, description="Yerel ve küresel zorluk meta sözlüğü")
    difficulty_profile: DifficultyProfile = Field(description="Zorluk profili (yerel ve küresel)")
    correct_answer: Literal["A", "B", "C", "D"] = Field(description="Doğru cevap şıkkı")
    correct_option: Optional[Literal["A", "B", "C", "D"]] = Field(default=None, description="Hedef doğru cevap şıkkı")
    duration_local: int = Field(default=15, description="Yerel soru süresi (saniye)")
    duration_global: int = Field(default=15, description="Küresel soru süresi (saniye)")
    duration_seconds: int = Field(default=15, description="Varsayılan soru süresi (saniye)")
    correct_count: int = Field(default=0, description="Doğru cevaplanma sayısı")
    wrong_count: int = Field(default=0, description="Yanlış cevaplanma sayısı")
    created_at: Optional[str] = Field(default=None, description="ISO-8601 UTC oluşturulma zaman damgası")
    shuffle_key: float = Field(default_factory=lambda: round(random.random(), 6), description="Rastgele sorgulama ve çekim anahtarı (0.0 - 1.0)")
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

# ==============================================================================
# EVRENSEL "KÜLTÜREL TANINIRLIK & TRIVIA" STANDARTI (ANTİ-BÜROKRASİ MOTORU)
# ==============================================================================

BUREAUCRACY_BLACKLIST = [
    "strateji belgesi",
    "eylem planı",
    "kalkınma raporu",
    "kanun maddesi",
    "kanun fıkrası",
    "yönetmelik",
    "resmi bülten",
    "bakanlık kararı",
    "yıllık bütçe yüzdesi",
    "ulusal yol haritası",
    "genelge",
]

SELF_VERIFICATION_RULE = (
    "Bu soru Kim Milyoner Olmak İster, Jeopardy veya Trivial Pursuit gibi uluslararası bir yarışma programında "
    "oyuncuya sorulduğunda adil, heyecan verici ve genel kültüre dayalı bir soru mudur? Yoksa bir devlet dairesinin "
    "resmi evrakı mıdır? Eğer resmi evraksa soruyu anında sil ve ülkenin/konunun dünya çapında bilinen ikonik unsurlarıyla yeniden üret."
)

TRIVIA_GUIDELINES = """20 KATEGORİNİN TAMAMI İÇİN DOĞRU / YANLIŞ TRIVIA KILAVUZU:

1. Tarih:
   - ❌ Yanlış: "1832 İngiltere Seçim Reformu Kanunu'nun 3. maddesi hangi bölgeyi kapsar?"
   - ✅ Doğru: "1215 yılında İngiltere Kralı Yurtsuz John'a imzalatılarak kralın yetkilerini tarihte ilk kez kısıtlayan belge hangisidir?" (Magna Carta)

2. Coğrafya:
   - ❌ Yanlış: "Brezilya Çevre Bakanlığı'nın 2021 Amazon koruma tebliğindeki hedef nedir?"
   - ✅ Doğru: "Dünyanın en büyük tatlı su debisine sahip olan ve Atlas Okyanusu'na dökülen Güney Amerika nehri hangisidir?" (Amazon Nehri)

3. Spor:
   - ❌ Yanlış: "Fransa Spor Federasyonu'nun 2018 antrenör lisans yönergesi neyi şart koşar?"
   - ✅ Doğru: "Brezilya formasıyla üç kez FIFA Dünya Kupası şampiyonluğu kazanan tek futbolcu kimdir?" (Pelé)

4. Fizik:
   - ❌ Yanlış: "Almanya Federal Fizik Enstitüsü'nün 2015 ölçüm hassasiyeti standartı nedir?"
   - ✅ Doğru: "Işığın parçacık özelliği gösterdiğini fotoelektrik olayla açıklayarak Nobel Ödülü kazanan kuramsal fizikçi kimdir?" (Albert Einstein)

5. Kimya:
   - ❌ Yanlış: "Fransız Kimya Kurumu'nun tehlikeli atık sınıflandırma yönetmeliği nasıldır?"
   - ✅ Doğru: "Radyoaktivite alanındaki çığır açan çalışmalarıyla iki farklı bilim dalında Nobel kazanan tek bilim insanı kimdir?" (Marie Curie)

6. Biyoloji:
   - ❌ Yanlış: "ABD Tarım Bakanlığı'nın 2020 bitki tohumu ihracat kriteri nedir?"
   - ✅ Doğru: "Galapagos Adaları'ndaki ispinoz kuşlarını gözlemleyerek doğal seçilim yoluyla evrim kuramını geliştiren doğa bilimci kimdir?" (Charles Darwin)

7. Ekonomi & Finans:
   - ❌ Yanlış: "Almanya Maliye Bakanlığı'nın 2022 vergi uyum kılavuzu fıkrası nedir?"
   - ✅ Doğru: "1923 yılında Almanya Weimar Cumhuriyeti'nde paranın sobalarda yakılacak kadar değersizleşmesine yol açan ekonomik kriz fenomeni hangisidir?" (Hiperenflasyon)

8. Edebiyat:
   - ❌ Yanlış: "Rusya Eğitim Bakanlığı'nın 2016 lise zorunlu okuma müfredatı yönergesi nedir?"
   - ✅ Doğru: "Napolyon'un Rusya Seferi'ni arka planına alarak Rus aristokrasisini anlatan, Lev Tolstoy imzalı anıtsal roman hangisidir?" (Savaş ve Barış)

9. Felsefe & Mantık:
   - ❌ Yanlış: "Yunanistan Felsefe Vakfı'nın 2019 sempozyum bildirisinin 4. tezi nedir?"
   - ✅ Doğru: "Sorgulanmamış hayatın yaşanmaya değer olmadığını savunan ve 'Bildiğim tek şey hiçbir şey bilmediğimdir' diyen Antik Yunan filozofu kimdir?" (Sokrates)

10. Sinema & Dizi:
    - ❌ Yanlış: "ABD Film Denetim Kurulu'nun 1930 Hays Kodunun 2. fıkra yasağı nedir?"
    - ✅ Doğru: "Sinema tarihinin ilk büyük gişe rekortmeni (blockbuster) kabul edilen ve Steven Spielberg tarafından yönetilen 1975 yapımı gerilim filmi hangisidir?" (Jaws)

11. Müzik:
    - ❌ Yanlış: "Almanya Müzik Eseri Sahipleri Birliği (GEMA) 2014 lisans harç tarifesi nedir?"
    - ✅ Doğru: "İşitme duyusunu neredeyse tamamen kaybetmişken 'Kaderin Kapıyı Çalması' olarak bilinen ünlü 5. Senfoni'yi besteleyen müzisyen kimdir?" (Ludwig van Beethoven)

12. Genel Kültür & Mitoloji:
    - ❌ Yanlış: "Atina Arkeoloji Müdürlüğü'nün 2017 Akropolis kazı protokolü nedir?"
    - ✅ Doğru: "İskandinav mitolojisinde 'Kıyamet Günü' olarak adlandırılan ve tanrıların devlerle savaşarak dünyanın yok oluşunu simgeleyen olay hangisidir?" (Ragnarök)

13. Bilgisayar & Yazılım:
    - ❌ Yanlış: "2019 İsveç Ulusal Yapay Zeka Stratejisi belgesinde hangi eylem planı öne çıkar?"
    - ✅ Doğru: "Mojang Studios tarafından Stockholm'de geliştirilen ve dünya genelinde en çok satan video oyunu unvanını alan sandbox yapım hangisidir?" (Minecraft)

14. Tıp & Sağlık:
    - ❌ Yanlış: "İngiltere Sağlık Bakanlığı'nın 2015 hastane sterilizasyon tebliği neyi zorunlu tutar?"
    - ✅ Doğru: "Laboratuvarında küf mantarını tesadüfen fark ederek ilk antibiyotik olan penisilini keşfeden İskoç bilim insanı kimdir?" (Alexander Fleming)

15. Sosyoloji & Psikoloji:
    - ❌ Yanlış: "Fransa Sosyoloji Derneği'nin 2018 saha araştırma etik tüzüğü nasıldır?"
    - ✅ Doğru: "İntihar olgusunu bireysel bir kriz değil, toplumsal dayanışma eksikliği üzerinden inceleyerek modern sosyolojinin kurucularından kabul edilen düşünür kimdir?" (Émile Durkheim)

16. Astronomi & Uzay:
    - ❌ Yanlış: "NASA'nın 2021 tedarik zinciri lojistik alt planında hangi madde vardır?"
    - ✅ Doğru: "1969 yılında Apollo 11 göreviyle Ay yüzeyine ayak basan ilk insan kimdir?" (Neil Armstrong)

17. Hukuk & Siyaset:
    - ❌ Yanlış: "Fransız Medeni Kanunu'nun 1101. maddesinde sözleşme nasıl tanımlanır?"
    - ✅ Doğru: "Napolyon'un 'Benim asıl zaferim Waterloo değil, bu kanundur' dediği ve modern Avrupa özel hukukunun temelini atan kanun külliyatı hangisidir?" (Code Civil / Napolyon Kanunları)

18. Oyun & Espor:
    - ❌ Yanlış: "Japonya Espor Birliği'nin 2019 turnuva vergilendirme yönetmeliği nedir?"
    - ✅ Doğru: "1985 yılında Shigeru Miyamoto tarafından tasarlanarak oyun sektörünü batmaktan kurtaran ve Nintendo'nun maskotu olan efsanevi platform oyunu hangisidir?" (Super Mario Bros.)

19. Gastronomi & Mutfak:
    - ❌ Yanlış: "İtalya Tarım Bakanlığı'nın pizza unu nem oranı standardı genelgesi nedir?"
    - ✅ Doğru: "Geleneksel olarak dana incik, sebzeler ve beyaz şarapla pişirilip üzeri gremolata ile servis edilen ünlü Milano kökenli et yemeği hangisidir?" (Ossobuco)

20. Mimarlık & Sanat:
    - ❌ Yanlış: "İtalya Kültür Bakanlığı'nın 2018 tarihi eser restorasyon hibesi şartnamesi nedir?"
    - ✅ Doğru: "Floransa Katedrali'nin devasa kubbesini iç iskele kurmadan inşa ederek Rönesans mimarisinde çığır açan dahi mimar kimdir?" (Filippo Brunelleschi)"""

TRIVIA_SYSTEM_PROMPT = f"""Sen profesyonel, çok dilli ve uluslararası düzeyde tanınan bir Trivia & Yarışma Programı Soru Uzmanısın.
Görevin, Kim Milyoner Olmak İster, Jeopardy veya Trivial Pursuit standartlarında; adil, heyecan verici, akıl yürütmeye dayalı, merak uyandırıcı ve genel dünya literatürüne mal olmuş ikonik sorular üretmektir.

================================================================================
EVRENSEL "KÜLTÜREL TANINIRLIK & TRIVIA" STANDARTI (ANTİ-BÜROKRASİ KURALI)
================================================================================
20 KATEGORİNİN HİÇBİRİNDE resmi rapor maddeleri, kanun numaraları/fıkraları, tebliğler, bakanlık strateji metinleri veya kimsenin bilmediği bürokratik evraklar üzerinden soru ÜRETİLMEYECEKTİR.

1. KESİN YASAKLI KALIPLAR VE EVRAK KELİMELERİ (KARA LİSTE):
- ❌ Yasaklı Sözcükler: "Strateji belgesi", "eylem planı", "kalkınma raporu", "kanun maddesi", "kanun fıkrası", "yönetmelik", "resmi bülten", "bakanlık kararı", "yıllık bütçe yüzdesi", "ulusal yol haritası", "genelge".
- ❌ Yasaklı Mantık: Bir ülkenin yalnızca bürokratlarının veya kamu kurumu çalışanlarının bildiği iç idari detaylar soru yapılamaz.

2. ZORUNLU KENDİ KENDİNİ DOĞRULAMA (SELF-VERIFICATION):
"{SELF_VERIFICATION_RULE}"

{TRIVIA_GUIDELINES}

Çıktıyı YALNIZCA şemaya tam uyumlu geçerli JSON formatında döndür. Markdown backtick (```) kullanma.
"""

def normalize_text_for_search(text: str) -> str:
    """Türkçe karakterleri ve küçük/büyük harf farklarını arama için normalize eder."""
    mapping = str.maketrans({
        'ı': 'i', 'İ': 'i', 'ğ': 'g', 'Ğ': 'g',
        'ü': 'u', 'Ü': 'u', 'ş': 's', 'Ş': 's',
        'ö': 'o', 'Ö': 'o', 'ç': 'c', 'Ç': 'c'
    })
    return text.lower().translate(mapping)

def validate_trivia_compliance(q: GeneratedQuestion) -> None:
    """
    Üretilen sorunun bürokratik kara liste terimleri içerip içermediğini denetler.
    Yasaklı terim tespit edilirse ValueError fırlatır ve yedek model kademesini tetikler.
    """
    if not q or not q.translations:
        return

    normalized_blacklist = [normalize_text_for_search(w) for w in BUREAUCRACY_BLACKLIST]

    texts_to_check = []
    for lang, content in q.translations.items():
        if hasattr(content, "question") and content.question:
            texts_to_check.append((lang, "question", content.question))
        if hasattr(content, "explanation") and content.explanation:
            texts_to_check.append((lang, "explanation", content.explanation))
        if hasattr(content, "options") and content.options:
            for opt_key in ["A", "B", "C", "D"]:
                opt_val = getattr(content.options, opt_key, "")
                if opt_val:
                    texts_to_check.append((lang, f"options.{opt_key}", opt_val))

    for lang, field_name, raw_text in texts_to_check:
        norm_text = normalize_text_for_search(raw_text)
        for orig_kw, norm_kw in zip(BUREAUCRACY_BLACKLIST, normalized_blacklist):
            if norm_kw in norm_text:
                raise ValueError(
                    f"Anti-Bürokrasi Kural İhlali: Soru [{lang}][{field_name}] içinde "
                    f"yasaklı bürokratik terim tespit edildi: '{orig_kw}' (Metin: {raw_text[:60]}...)"
                )

def build_prompt(
    primary_category: str,
    target_subcategory: Optional[str] = None,
    secondary_category: Optional[str] = None,
    target_country: Optional[str] = None,
    target_country_credit: Optional[int] = None,
    scope: Optional[str] = "global",
    is_global_eligible: bool = True,
    base_difficulty: Optional[str] = None,
    target_score: Optional[int] = None
) -> str:
    is_combo = secondary_category is not None
    country_rule = target_country if target_country else "Global"
    credit_rule = target_country_credit if target_country_credit is not None else 10
    scope_clean = "local" if scope and scope.lower() == "local" else "global"

    primary_sub_cats = list(CATEGORIES_META.get(primary_category, {}).get("sub_categories", {}).keys())
    category_filters = CATEGORIES_META.get(primary_category, {}).get("filters", [])
    filters_hint = f"\n- Odak / Tema İpuçları (Filters): {', '.join(category_filters)}" if category_filters else ""

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

    from difficulty_balancer import DifficultyBalancer
    target_diff = DifficultyBalancer.normalize_level(base_difficulty) if base_difficulty else "Orta"
    if target_score is None:
        target_score = DifficultyBalancer.validate_and_clamp_score(target_diff)

    if scope_clean == "global":
        scope_instruction = (
            f"- Kapsam (Scope): Global (Küresel)\n"
            f"- Hedef Odak Ülke: {country_rule} (Kredi: {credit_rule}/10)\n"
            f"- Bu soru küresel perspektiften hazırlanmalıdır. Sorunun konusu ve cevabı dünya çapındaki genel entelektüel kitlece bilinmeli ve anlaşılabilir olmalıdır. Seçilen odak ülkenin ({country_rule}) evrensel başarıları veya küresel yansımaları işlenmelidir."
        )
    else:
        scope_instruction = (
            f"- Kapsam (Scope): Local (Yerel)\n"
            f"- Hedef Odak Ülke: {country_rule} (Kredi: {credit_rule}/10)\n"
            f"- Bu soru yerel (Local) bağlamda hazırlanmalıdır. Soruların tamamı varsayılan olarak Türkiye yereline hapsedilmemelidir. Odak ülke olarak '{country_rule}' belirlenmiştir; o ülkenin kendi iç dinamikleri, yerel tarihi, bölgesel kültürü veya iç yapısı doğrudan işlenmelidir."
        )

    if is_global_eligible:
        dual_difficulty_instruction = (
            f"ASİMETRİK ÇİFT ZORLUK PUANLAMA TALİMATI (KÜRESEL ELİT ÜLKE):\n"
            f"- Hedef Odak Ülke: {country_rule} (Kredi: {credit_rule}/10 - Küresel Elit Ülke)\n"
            f"- Bu ülke küresel sahnede tanınan elit bir merkezdir. Soruya İKİ AYRI PUAN atanmalıdır:\n"
            f"  1. 'difficulty_local_score' (1-10): {country_rule} vatandaşının/kültürünün bu konuyu bilme zorluğu. Hedef Yerel Puan: {target_score}/10 ({target_diff}).\n"
            f"  2. 'difficulty_global_score' (1-10): Dünyanın geri kalanındaki ortalama bir insanın bu ülkeyle ilgili konuyu bilme zorluğu (Kolay: 1-4, Orta: 5-8, Zor: 9-10).\n"
            f"- Bu iki puanı birbirinden bağımsız, ASİMETRİK olarak değerlendir. Örneğin ABD iç dinamikleri bir Amerikalı için Kolay (1-4) veya Orta (5-8) olabilirken, dünyanın geri kalanı için Zor (9-10) olabilir.\n"
            f"- Çıktı JSON'ında 'is_global_eligible': true, hem local hem global puan ve seviyeleri eksiksiz doldurulmalıdır.\n"
            f"- 'difficulty_meta': {{'local': {{'level': '{target_diff}', 'score': {target_score}, 'context_country': '{country_rule}'}}, 'global': {{'level': '...', 'score': ...}}}} olmalıdır."
        )
        schema_global_level = '"Orta"'
        schema_global_score = "6"
        schema_meta_global = '{"level": "Orta", "score": 6}'
        schema_profile_global = '{"label": "Orta", "score": 6}'
    else:
        dual_difficulty_instruction = (
            f"TEK YEREL ZORLUK PUANLAMA TALİMATI (SADECE YEREL KAPSAMLI ÜLKE):\n"
            f"- Hedef Odak Ülke: {country_rule} (Kredi: {credit_rule}/10 - Yerel Kapsamlı Ülke)\n"
            f"- Bu soru sadece yerel kapsamdadır. Konu küresel bir standart taşımadığı için küresel puanlama YAPILMAYACAKTIR.\n"
            f"- Yalnızca {country_rule} yerel perspektifine göre yerel zorluk puanı ata: 'difficulty_local_score' = {target_score}, 'difficulty_local' = '{target_diff}'.\n"
            f"- 'difficulty_global' ve 'difficulty_global_score' alanlarını KESİNLİKLE null bırak.\n"
            f"- Çıktı JSON'ında 'is_global_eligible': false olmalıdır.\n"
            f"- 'difficulty_meta': {{'local': {{'level': '{target_diff}', 'score': {target_score}, 'context_country': '{country_rule}'}}, 'global': null}} olmalıdır."
        )
        schema_global_level = "null"
        schema_global_score = "null"
        schema_meta_global = "null"
        schema_profile_global = "null"

    difficulty_instruction = (
        f"ZORLUK SEVİYESİ VE HEDEF PUAN TALİMATI (ZORUNLU):\n"
        f"- Hedef Yerel Zorluk Seviyesi: {target_diff}\n"
        f"- Hedef Yerel Zorluk Puanı: {target_score}/10 (KOLAY: 1, 2, 3, 4 | ORTA: 5, 6, 7, 8 | ZOR: 9, 10)\n"
        f"- Çıktı JSON'ındaki 'difficulty_local_score' alanını TAM OLARAK {target_score} yap.\n"
        f"- Puanlama kurallarına kesinlikle uy: Kolay için 1-4, Orta için 5-8, Zor için 9-10 aralığında sayısal puan ata.\n"
        f"{dual_difficulty_instruction}"
    )

    return f"""
Sen profesyonel ve çok dilli bir soru hazırlama uzmanısın.
Aşağıdaki kriterlere göre yüksek kaliteli, özgün tek bir çoktan seçmeli soru üret ve 5 dilde ('tr', 'en', 'es', 'pt', 'de') hazırla.

Kriterler:
- Birincil Kategori: {primary_category}
{subcategory_instruction}
{f"- İkincil Kategori (Combo): {secondary_category}" if is_combo else "- Tip: Standart (Tek Kategori)"}
{scope_instruction}{filters_hint}
{difficulty_instruction}

AŞIRI ZORLUK (ULTRA-HARD) ENGELİ VE GENEL BİLİNİRLİK KURALI:
- Aşırı niş veya cevabı imkansız ultra-zor akademik detaylardan kaçın. Zor sorular da genel entelektüel düzeyde çözülebilir olmalıdır.
- Zor (9-10) sorular dahi ilgili konunun/ülkenin literatüründe veya popüler kültüründe saygın, genel entelektüel bilinirliği olan nitelikli dönüm noktalarından seçilmelidir.
- Akademisyen düzeyinde ezoterik veya kimsenin bilmediği aşırı niş detaylardan kesinlikle kaçın.

EVRENSEL "KÜLTÜREL TANINIRLIK & TRIVIA" STANDARTI (ANTİ-BÜROKRASİ PROMPT KURALI):
- 20 kategorinin hiçbirinde resmi rapor maddeleri, kanun numaraları/fıkraları, tebliğler, bakanlık strateji metinleri veya kimsenin bilmediği bürokratik evraklar üzerinden soru ÜRETİLMEYECEKTİR.
- Üretilen tüm sorular genel dünya literatürüne mal olmuş, ikonik, akıl yürütmeye dayalı, merak uyandırıcı ve uluslararası popüler yarışma (Trivia) formatında (Kim Milyoner Olmak İster, Jeopardy, Trivial Pursuit) olmalıdır.

KESİN YASAKLI KALIPLAR VE EVRAK KELİMELERİ (KARA LİSTE):
- ❌ Yasaklı Sözcükler: "Strateji belgesi", "eylem planı", "kalkınma raporu", "kanun maddesi", "kanun fıkrası", "yönetmelik", "resmi bülten", "bakanlık kararı", "yıllık bütçe yüzdesi", "ulusal yol haritası", "genelge".
- ❌ Yasaklı Mantık: Bir ülkenin yalnızca bürokratlarının veya kamu kurumu çalışanlarının bildiği iç idari detaylar soru yapılamaz.

ZORUNLU KENDİ KENDİNİ DOĞRULAMA (SELF-VERIFICATION KURALI):
"{SELF_VERIFICATION_RULE}"

{TRIVIA_GUIDELINES}

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
   - Dünyanın her yerinde uzmanlık, saygın genel entelektüel derinlik gerektiren sorular.
   - Örnekler: "Kuantum elektrodinamiğinde Feynman diyagramları", "17. yüzyıl Avrupa felsefesi epistemoloji detayları".
   - Skor Aralığı: Local: 9-10 | Global: 9-10

Etiket ve Puan Eşleşme Skalası:
- "Kolay": 1 - 4 puan
- "Orta": 5 - 8 puan
- "Zor": 9 - 10 puan
Seçilen etiket ile puan birbiriyle kesinlikle uyumlu tam sayılar olmalıdır.

Zorunlu JSON Şeması:
{{
  "categories": ["{primary_category}"{f', "{secondary_category}"' if is_combo else ''}],
  "sub_categories": [{sub_schema_val}],
  "is_combo": {str(is_combo).lower()},
  "scope": "{scope_clean}",
  "target_country": "{country_rule}",
  "target_country_credit": {credit_rule},
  "is_global_eligible": {str(is_global_eligible).lower()},
  "countries": ["{country_rule}"],
  "version": "1.0.2",
  "difficulty_local": "{target_diff}",
  "difficulty_local_score": {target_score},
  "difficulty_global": {schema_global_level},
  "difficulty_global_score": {schema_global_score},
  "difficulty_meta": {{
    "local": {{ "level": "{target_diff}", "score": {target_score}, "context_country": "{country_rule}" }},
    "global": {schema_meta_global}
  }},
  "difficulty_profile": {{
    "local": {{ "label": "{target_diff}", "score": {target_score} }},
    "global": {schema_profile_global}
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
            system_instruction=TRIVIA_SYSTEM_PROMPT,
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
                "content": TRIVIA_SYSTEM_PROMPT
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
        context_country = q.target_country or (q.countries[0] if q.countries else "Global")
        local_meta = {
            "level": q.difficulty_local,
            "score": q.difficulty_local_score,
            "context_country": context_country
        }
        global_meta = None
        if getattr(q, "is_global_eligible", True) and q.difficulty_global is not None:
            global_meta = {
                "level": q.difficulty_global,
                "score": q.difficulty_global_score
            }
        q.difficulty_meta = {
            "local": local_meta,
            "global": global_meta
        }
    return q

def generate_question_with_fallback(
    primary_category: Optional[str] = None,
    secondary_category: Optional[str] = None,
    target_subcategory: Optional[str] = None,
    target_country: Optional[str] = None,
    target_country_credit: Optional[int] = None,
    scope: Optional[str] = None,
    base_difficulty: Optional[str] = None,
    balancer: Optional[Any] = None,
    balance_options: bool = True,
    is_combo: Optional[bool] = None,
    category_balancer: Optional[Any] = None,
    balance_categories: bool = True,
    difficulty_balancer: Optional[Any] = None,
    balance_difficulty: bool = True,
    generation_planner: Optional[Any] = None
) -> GeneratedQuestion:
    """
    Kategori, şık, kapsam (Global/Local), ülke kredisi ve zorluk (4-4-2 kotası)
    dengeleme mekanizması destekli soru üretim fonksiyonu (v1.0.2).
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

    # 2. Dinamik Kapsam (Global/Local) ve Hedef Ülke Kredi Planlaması
    from generation_planner import get_generation_planner
    planner = generation_planner or get_generation_planner()

    plan = planner.create_plan(
        category=primary_category,
        subcategory=target_subcategory,
        force_scope=scope,
        force_country=target_country
    )
    final_scope = plan.scope
    ratio_percent = plan.ratio_percent
    final_country = target_country or plan.target_country
    final_country_credit = target_country_credit if target_country_credit is not None else plan.target_country_credit
    final_is_global_eligible = plan.is_global_eligible

    # 3. Zorluk dengelemesi (4-4-2 kuralı ve 1-10 puan dengesi)
    from difficulty_balancer import get_difficulty_balancer, DifficultyBalancer
    diff_balancer = difficulty_balancer or get_difficulty_balancer()

    target_diff = base_difficulty
    target_score = None
    if balance_difficulty and not target_diff:
        diff_target = diff_balancer.get_next_target()
        target_diff = diff_target.level
        target_score = diff_target.score
    elif target_diff:
        target_diff = DifficultyBalancer.normalize_level(target_diff)
        target_score = diff_balancer.get_balanced_score_for_level(target_diff)

    if not target_diff:
        target_diff = "Orta"
    if not target_score:
        target_score = diff_balancer.get_balanced_score_for_level(str(target_diff))

    prompt = build_prompt(
        primary_category=primary_category,
        target_subcategory=target_subcategory,
        secondary_category=secondary_category,
        target_country=final_country,
        target_country_credit=final_country_credit,
        scope=final_scope,
        is_global_eligible=final_is_global_eligible,
        base_difficulty=target_diff,
        target_score=target_score
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
                # Anti-Bürokrasi ve Trivia uyumluluk doğrulaması (Python Guard)
                validate_trivia_compliance(q)

                # Hedef alt kategori varsa sorunun alt kategorisini kesin olarak hedef alt kategoriye sabitle
                if target_subcategory:
                    q.sub_categories = [target_subcategory]

                # Zorluk doğrulaması ve katı 1-10 puan dengesi
                local_level = DifficultyBalancer.normalize_level(target_diff)
                raw_local_score = getattr(q, "difficulty_local_score", None) or target_score
                clamped_local_score = DifficultyBalancer.validate_and_clamp_score(local_level, raw_local_score)
                final_local_level = DifficultyBalancer.score_to_level(clamped_local_score)

                if final_is_global_eligible:
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
                else:
                    final_global_level = None
                    clamped_global_score = None

                now_iso = datetime.now(timezone.utc).isoformat()

                q.version = "1.0.2"
                q.scope = final_scope
                q.target_country = final_country
                q.target_country_credit = final_country_credit
                q.is_global_eligible = final_is_global_eligible
                q.countries = [final_country]
                q.created_at = now_iso
                q.shuffle_key = getattr(q, "shuffle_key", None) or round(random.random(), 6)
                q.correct_count = getattr(q, "correct_count", 0) or 0
                q.wrong_count = getattr(q, "wrong_count", 0) or 0
                q.difficulty_local = final_local_level
                q.difficulty_local_score = clamped_local_score
                q.difficulty_global = final_global_level
                q.difficulty_global_score = clamped_global_score
                
                local_meta = {
                    "level": final_local_level,
                    "score": clamped_local_score,
                    "context_country": final_country
                }
                global_meta = {
                    "level": final_global_level,
                    "score": clamped_global_score
                } if final_is_global_eligible and final_global_level else None

                q.difficulty_meta = {
                    "local": local_meta,
                    "global": global_meta
                }
                q.difficulty_profile = DifficultyProfile(
                    local=DifficultyScope(label=final_local_level, score=clamped_local_score),
                    global_scope=DifficultyScope(label=final_global_level, score=clamped_global_score) if final_is_global_eligible and final_global_level else None
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

                # Kota gerçekleşmesini ProductionBalancer'a kaydet
                try:
                    from production_balancer import get_production_balancer
                    get_production_balancer().record_success(primary_category, final_scope)
                except Exception as b_err:
                    logging.warning(f"ProductionBalancer record_success hatası: {b_err}")

                # Terminal Log Formatı:
                # [v1.0.2] 🌍 Kapsam: Global (%85) | Odak: ABD (Kredi: 10) | Global Uygun: Evet | Local: Kolay (Puan: 3/10) | Global: Zor (Puan: 9/10) | Tarih: {created_at}
                # veya
                # [v1.0.2] 🌍 Kapsam: Local (%75) | Odak: Portekiz (Kredi: 4) | Global Uygun: Hayır | Local: Orta (Puan: 6/10) | Global: Yok | Tarih: {created_at}
                global_str = f"{final_global_level} (Puan: {clamped_global_score}/10)" if final_is_global_eligible else "Yok"
                terminal_msg = (
                    f"[v1.0.2] 🌍 Kapsam: {final_scope.capitalize()} (%{ratio_percent}) | "
                    f"Odak: {final_country} (Kredi: {final_country_credit}) | "
                    f"Global Uygun: {'Evet' if final_is_global_eligible else 'Hayır'} | "
                    f"Local: {q.difficulty_local} (Puan: {q.difficulty_local_score}/10) | "
                    f"Global: {global_str} | "
                    f"Tarih: {q.created_at}"
                )
                logging.info(terminal_msg)
                try:
                    print(terminal_msg)
                except UnicodeEncodeError:
                    print(terminal_msg.encode("ascii", "replace").decode("ascii"))
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
    balance_categories: bool = True,
    scope: Optional[str] = None,
    target_country: Optional[str] = None,
    target_country_credit: Optional[int] = None
) -> GeneratedQuestion:
    """Geriye dönük uyumluluk fonksiyonu."""
    return generate_question_with_fallback(
        primary_category=category,
        secondary_category=None,
        target_subcategory=sub_category,
        target_country=target_country,
        target_country_credit=target_country_credit,
        scope=scope,
        base_difficulty=difficulty,
        balancer=balancer,
        balance_options=balance_options,
        category_balancer=category_balancer,
        balance_categories=balance_categories
    )