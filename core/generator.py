import os
import sys
import json
import re
import random
import logging
import time
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

QUESTION_SCHEMA_VERSION = "1.1.0"

from pydantic import BaseModel, Field, ConfigDict, field_validator
try:
    from firebase_admin import firestore
    SERVER_TIMESTAMP = firestore.SERVER_TIMESTAMP
except Exception:
    SERVER_TIMESTAMP = None
from core.llm_manager import AllProvidersCoolingDown, MultiLLMManager
from core.translation_service import (
    COUNTRY_LANGUAGE_CODES,
    LANGUAGE_CODE_TO_NAME,
    SUPPORTED_LANGUAGE_CODES,
    TranslationIncompleteError,
    translate_localized_content,
)

from config.categories_config import (
    CATEGORIES_META,
    get_category_color,
    get_subcategory_image,
)

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# --- Pydantic Şemaları ---

class Options(BaseModel):
    A: str = Field(description="A şıkkı metni")
    B: str = Field(description="B şıkkı metni")
    C: str = Field(description="C şıkkı metni")
    D: str = Field(description="D şıkkı metni")


class BinaryFallback(BaseModel):
    statement: str = Field(description="Doğru/Yanlış modunda gösterilecek kısa önerme")
    is_true: bool = Field(description="Önermenin doğruluğu")


class LocalizedContent(BaseModel):
    question: str = Field(description="Soru metni")
    options: Options = Field(description="4 seçenek")
    explanation: str = Field(description="Çözüm açıklaması")
    filter_tag: str = Field(description="Konu etiketi")
    hint: Optional[str] = Field(default=None, description="Yetenek ipucu kartında gösterilecek yerelleştirilmiş metin")
    distractor_trap_note: Optional[str] = Field(default=None, description="En güçlü yanlış seçeneğin neden yanlış olduğunu anlatan yerelleştirilmiş not")
    keyword: Optional[str] = Field(default=None, description="Soru içindeki vurgulanacak yerelleştirilmiş anahtar ifade")
    binary_fallback: Optional[BinaryFallback] = Field(default=None, description="Doğru/Yanlış biçimine geçiş için yerelleştirilmiş önerme")
    simplified_question: str = Field(description="Soru kökünün aynı gerçeği ve cevabı koruyan sade yerelleştirilmiş sürümü")
    clarified_options: Options = Field(description="A-D anahtarlarını ve doğru cevabı koruyan daha açık yerelleştirilmiş seçenekler")

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
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True, extra="allow")
    categories: List[str] = Field(description="Ana kategori listesi")
    categories_meta: List[CategoryMeta] = Field(default_factory=list, description="Kategori meta bilgileri (isim ve renk)")
    sub_categories: List[str] = Field(default_factory=list, description="Alt dallar")
    sub_categories_meta: List[SubCategoryMeta] = Field(default_factory=list, description="Alt kategori meta bilgileri (isim ve görsel URL)")
    question_image_url: Optional[str] = Field(default=None, description="Soruya özel görsel URL'si; görsel yoksa null")
    is_visual_question: bool = Field(default=False, description="Soru cevaplanırken görsel zorunlu mu")
    question_image_search_query: Optional[str] = Field(
        default=None,
        description="Sunucu tarafında görsel bulmak için kesin İngilizce konu sorgusu",
    )
    question_image_alt_text: Optional[str] = Field(
        default=None,
        description="Cevabı söylemeden görseldeki ayırt edici özellikleri betimler",
    )
    is_combo: bool = Field(default=False, description="Hibrit soru mu")
    scope: Literal["global", "local"] = Field(default="global", description="Kapsam: global veya local")
    target_country: Optional[str] = Field(default=None, description="Hedef ülke")
    target_country_code: str = Field(default="GL", description="Hedef ülke/bölge kodu veya GL")
    target_country_credit: Optional[int] = Field(default=None, description="Hedef ülke kredi puanı")
    is_global_eligible: bool = Field(default=True, description="Küresel elit ülke mi (çift zorluk mu)")
    countries: List[str] = Field(default_factory=lambda: ["Global"], description="İlgili ülkeler")
    version: str = Field(default=QUESTION_SCHEMA_VERSION, description="Soru veri şeması versiyonu")
    difficulty_local: str = Field(default="Orta", description="Yerel zorluk seviyesi (Kolay, Orta, Zor)")
    difficulty_local_score: int = Field(default=5, description="Yerel zorluk puanı (1-10)")
    difficulty_global: Optional[str] = Field(default=None, description="Küresel zorluk seviyesi (Kolay, Orta, Zor veya None)")
    difficulty_global_score: Optional[int] = Field(default=None, description="Küresel zorluk puanı (1-10 veya None)")
    difficulty_meta: Optional[Dict[str, Any]] = Field(default=None, description="Yerel ve küresel zorluk meta sözlüğü")
    difficulty_profile: DifficultyProfile = Field(description="Zorluk profili (yerel ve küresel)")
    correct_answer: Literal["A", "B", "C", "D"] = Field(description="Doğru cevap şıkkı")
    correct_option: Optional[Literal["A", "B", "C", "D"]] = Field(default=None, description="Hedef doğru cevap şıkkı")
    weakest_distractor_index: Optional[int] = Field(default=None, ge=0, le=3, description="A-D sırasındaki en zayıf yanlış şıkkın 0 tabanlı indeksi")
    strongest_distractor_index: Optional[int] = Field(default=None, ge=0, le=3, description="A-D sırasındaki en güçlü yanlış şıkkın 0 tabanlı indeksi")
    hint: Optional[str] = Field(default=None, description="Seçili kaynak dilde yetenek ipucu")
    distractor_trap_note: Optional[str] = Field(default=None, description="Seçili kaynak dilde güçlü çeldirici açıklaması")
    keyword: Optional[str] = Field(default=None, description="Seçili kaynak dilde vurgulanacak anahtar kelime")
    logical_order: Optional[List[int]] = Field(default=None, description="A-D seçenek indekslerinin anlamlı kronolojik/mantıksal sırası; anlamlı değilse null")
    option_difficulty_order: Optional[List[int]] = Field(default=None, description="A-D indekslerinin hedef kitle için en kolay tanınandan en zor tanınana sıralaması; sıralama güvenilir değilse null")
    binary_fallback: Optional[BinaryFallback] = Field(default=None, description="Seçili kaynak dilde Doğru/Yanlış biçimi")
    community_distribution: Optional[List[int]] = Field(default=None, description="Modelden istenmez; kayıt sırasında simüle edilir (community_distribution_meta.source=simulated), gerçek oylar gelince değişir")
    base_points: int = Field(default=100, ge=0, description="Yetenek puan çarpanlarının uygulandığı taban soru puanı")
    difficulty: Optional[Literal["Kolay", "Orta", "Zor"]] = Field(default=None, description="Soru kapsamı için yetenek koşullarında kullanılan zorluk etiketi")
    duration_local: int = Field(default=15, description="Yerel soru süresi (saniye)")
    duration_global: int = Field(default=15, description="Küresel soru süresi (saniye)")
    duration_seconds: int = Field(default=15, description="Varsayılan soru süresi (saniye)")
    correct_count: int = Field(default=0, description="Doğru cevaplanma sayısı")
    wrong_count: int = Field(default=0, description="Yanlış cevaplanma sayısı")
    created_at: Optional[Any] = Field(default=None, description="ISO-8601 UTC veya firestore.SERVER_TIMESTAMP")
    shuffle_key: float = Field(default_factory=lambda: round(random.random(), 6), description="Rastgele sorgulama ve çekim anahtarı (0.0 - 1.0)")
    supported_languages: List[str] = Field(
        default_factory=lambda: list(SUPPORTED_LANGUAGE_CODES),
        description="Bu soru için üretilen dil kodları",
    )
    translations: Dict[str, LocalizedContent] = Field(description="Desteklenen dillerde soru içerikleri")
    generation_provider: Optional[str] = Field(default="GEMINI", description="Üretimi gerçekleştiren LLM sağlayıcısı")
    translation_provider: Optional[str] = Field(default=None, description="Çeviri sağlayıcısı")

    @field_validator("logical_order")
    @classmethod
    def validate_logical_order(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if value is not None and sorted(value) != [0, 1, 2, 3]:
            raise ValueError("logical_order must be a permutation of option indices 0, 1, 2, and 3")
        return value

    @field_validator("option_difficulty_order")
    @classmethod
    def validate_option_difficulty_order(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if value is not None and sorted(value) != [0, 1, 2, 3]:
            raise ValueError("option_difficulty_order must be a permutation of option indices 0, 1, 2, and 3")
        return value


def normalize_generated_payload(data: Dict[str, Any], fallback_filter_tag: str) -> Dict[str, Any]:
    """Harmless omissions from otherwise usable JSON should not trigger another API call."""
    translations = data.get("translations")
    correct = str(data.get("correct_answer") or "A")
    if not isinstance(translations, dict):
        return data
    for content in translations.values():
        if not isinstance(content, dict):
            continue
        options = content.get("options")
        answer = options.get(correct) if isinstance(options, dict) else None
        question_text = str(content.get("question") or "").strip()
        if not str(content.get("simplified_question") or "").strip() and question_text:
            # Older/short model responses may omit the ability-facing variant.
            # Reuse the source question rather than inventing a paraphrase.
            content["simplified_question"] = question_text
        if isinstance(options, dict):
            clarified = content.get("clarified_options")
            clarified = clarified if isinstance(clarified, dict) else {}
            normalized_clarified = {}
            for letter in "ABCD":
                value = clarified.get(letter)
                if not str(value or "").strip():
                    value = options.get(letter)
                if value is not None:
                    normalized_clarified[letter] = value
            if normalized_clarified:
                # Keep provided clarifications and safely fill omissions with
                # their matching original option; never alter the answer map.
                content["clarified_options"] = normalized_clarified
        if not str(content.get("explanation") or "").strip() and answer:
            content["explanation"] = f"Doğru cevap: {answer}."
        if not str(content.get("filter_tag") or "").strip():
            content["filter_tag"] = fallback_filter_tag
    return data

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

TRIVIA_GUIDELINES = """20 KATEGORİ İÇİN KISA & TEMEL TRIVIA REFERANS KILAVUZU:

1. Tarih:
   - ❌ Yanlış: "1832 yılında İngiliz parlamentosunda kabul edilen ve sanayi şehirlerine temsil hakkı tanıyan reform kanunu nedir?"
   - ✅ Doğru: "1215'te kralın yetkilerini tarihte ilk kez kısıtlayan İngiliz belgesi hangisidir?" (Magna Carta)

2. Coğrafya:
   - ❌ Yanlış: "Brezilya Ulusal Coğrafya Enstitüsü'nün hidrolojik havza raporunda debisi en yüksek ölçülen nehir hangisidir?"
   - ✅ Doğru: "Dünyanın en çok su taşıyan ve Atlas Okyanusu'na dökülen nehri hangisidir?" (Amazon)

3. Spor:
   - ❌ Yanlış: "Fransa Futbol Federasyonu tarafından 1998 yılında onur madalyası verilen dönemin milli takım kaptanı kimdir?"
   - ✅ Doğru: "FIFA Dünya Kupası'nı futbolcu olarak 3 kez kazanan tek isim kimdir?" (Pelé)

4. Fizik:
   - ❌ Yanlış: "Almanya Fizik Derneği'nin 1905 bülteninde kuantum teorisinin temeli sayılan fotoelektrik makalesini kim yazmıştır?"
   - ✅ Doğru: "Fotoelektrik etkiyi açıklayarak Nobel Fizik Ödülü'nü kazanan bilim insanı kimdir?" (Albert Einstein)

5. Kimya:
   - ❌ Yanlış: "Fransız Bilimler Akademisi'nce 1911'de saf metal olarak izole edildiği tescillenen radyoaktif element hangisidir?"
   - ✅ Doğru: "İki farklı bilim dalında Nobel Ödülü kazanan tek bilim insanı kimdir?" (Marie Curie)

6. Biyoloji:
   - ❌ Yanlış: "İngiliz Doğa Tarihi Müzesi arşivlerine göre 1835'te adalardaki kuş gagalarını çizen doğa bilimci kimdir?"
   - ✅ Doğru: "Doğal seçilim yoluyla evrim kuramını ortaya atan İngiliz doğa bilimci kimdir?" (Charles Darwin)

7. Ekonomi & Finans:
   - ❌ Yanlış: "1923 Weimar Almanyası'nda Reichsbank tarafından basılan paranın değer kaybı oranını belirten ekonomik terim nedir?"
   - ✅ Doğru: "Paranın aşırı değer kaybedip fiyatların kontrolden çıktığı duruma ne ad verilir?" (Hiperenflasyon)

8. Edebiyat:
   - ❌ Yanlış: "Rusya Eğitim Bakanlığı klasik listesinde yer alan, 1812 seferini aristokrat aileler üzerinden anlatan dev roman hangisidir?"
   - ✅ Doğru: "Tolstoy'un Napolyon'un Rusya seferini anlattığı anıtsal eseri hangisidir?" (Savaş ve Barış)

9. Felsefe & Mantık:
   - ❌ Yanlış: "Atina mahkemesi kayıtlarına göre gençlerin ahlakını bozmakla suçlanan ve sorgulanmamış hayatı reddeden düşünür kimdir?"
   - ✅ Doğru: "'Bildiğim tek şey hiçbir şey bilmediğimdir' sözüyle bilinen filozof kimdir?" (Sokrates)

10. Sinema & Dizi:
    - ❌ Yanlış: "Amerikan Film Enstitüsü'nün gişe rekoru kıran ilk modern yaz filmi olarak nitelendirdiği 1975 Spielberg filmi nedir?"
    - ✅ Doğru: "Steven Spielberg'in yönettiği, dev bir köpekbalığını konu alan 1975 yapımı gerilim filmi hangisidir?" (Jaws)

11. Müzik:
    - ❌ Yanlış: "Fransız Hükümeti tarafından Sanat ve Edebiyat Nişanı ile ödüllendirilen ve neyi Batı'ya tanıtan Türk sanatçı kimdir?"
    - ✅ Doğru: "Hababam Sınıfı filmlerinin unutulmaz tema müziğini besteleyen ünlü müzisyen kimdir?" (Melih Kibar)

12. Genel Kültür & Mitoloji:
    - ❌ Yanlış: "İskandinav Edda metinlerinde geçen ve tanrıların devlerle savaşarak evrenin yok olmasına yol açan mitolojik savaş nedir?"
    - ✅ Doğru: "İskandinav mitolojisinde dünyanın sonu ve tanrıların savaşı anlamına gelen olay nedir?" (Ragnarök)

13. Bilgisayar & Yazılım:
    - ❌ Yanlış: "İsveç Yapay Zeka ve Bilişim Enstitüsü'nün 2011 raporunda en çok hasılat yapan blok kırma oyunu hangisidir?"
    - ✅ Doğru: "Stockholm'de geliştirilen, bloklarla inşa yapılan dünyanın en çok satan oyunu hangisidir?" (Minecraft)

14. Tıp & Sağlık:
    - ❌ Yanlış: "Londra St. Mary Hastanesi laboratuvar tebliğinde 1928'de küf mantarından izole edildiği duyurulan ilk antibiyotik nedir?"
    - ✅ Doğru: "Alexander Fleming'in küf mantarından kazara keşfettiği ilk antibiyotik hangisidir?" (Penisilin)

15. Sosyoloji & Psikoloji:
    - ❌ Yanlış: "Fransız Sosyoloji Ekolü'nün anomi kavramını açıklamak için 1897'de yayımladığı vaka çalışmasının konusu nedir?"
    - ✅ Doğru: "Sosyolojinin kurucularından kabul edilen ve 'İntihar' adlı eseriyle bilinen Fransız düşünür kimdir?" (Émile Durkheim)

16. Astronomi & Uzay:
    - ❌ Yanlış: "NASA'nın 1969 Apollo uçuş günlüğünde ay modülünden ilk iniş protokolünü gerçekleştiren astronot kimdir?"
    - ✅ Doğru: "1969 yılında Ay yüzeyine ayak basan ilk insan kimdir?" (Neil Armstrong)

17. Hukuk & Siyaset:
    - ❌ Yanlış: "Fransız Konsüllük Kararnamesi ile 1804'te yürürlüğe giren ve özel hukuku birleştiren kanunun resmi adı nedir?"
    - ✅ Doğru: "Modern Avrupa özel hukukunun temelini oluşturan Napolyon Kanunları'nın diğer adı nedir?" (Code Civil)

18. Oyun & Espor:
    - ❌ Yanlış: "Japon Eğlence Makineleri Birliği'nin 1985 bülteninde tesisatı konu alan platform oyunu hangisidir?"
    - ✅ Doğru: "Nintendo'nun ikonik bıyıklı musluk tamircisi maskotu hangi oyuna aittir?" (Super Mario)

19. Gastronomi & Mutfak:
    - ❌ Yanlış: "Milano Ticaret Odası tescil belgesinde dana incik ve gremolata ile pişirildiği belirtilen yerel yemek nedir?"
    - ✅ Doğru: "Geleneksel olarak dana incikle pişirilen ünlü İtalyan yemeği hangisidir?" (Ossobuco)

20. Mimarlık & Sanat:
    - ❌ Yanlış: "Floransa Loncası şartnamesine göre 1436'da katedralin kubbesini iskele kullanmadan kapatan mimar kimdir?"
    - ✅ Doğru: "Floransa Katedrali'nin devasa kubbesini tasarlayan ünlü Rönesans mimarı kimdir?" (Filippo Brunelleschi)"""

TRIVIA_SYSTEM_PROMPT = f"""Sen profesyonel, çok dilli ve uluslararası düzeyde tanınan bir Trivia & Yarışma Programı Soru Uzmanısın.
Görevin, Kim Milyoner Olmak İster, Jeopardy veya Trivial Pursuit standartlarında; adil, heyecan verici, akıl yürütmeye dayalı, merak uyandırıcı ve genel dünya literatürüne mal olmuş ikonik sorular üretmektir.

================================================================================
[ZORUNLU FORMAT VE UZUNLUK KURALI - MOBILE TRIVIA STANDARTI]
================================================================================
- Soru kökü EN FAZLA 20-25 kelime (150 karakter) olmalıdır.
- Paragraf yazmak, araya kurum adı, madalya/nişan ismi, kanun numarası veya ansiklopedik detay eklemek KESİNLİKLE YASAKTIR.
- Soruyu herkesin anlayabileceği, konunun en bilinen ve temel anahtarı üzerinden tek nefeste sor.
- Doğrudan Anlatım: "Uzun yıllar boyunca...", "...tarafından verilen nişanla ödüllendirilen...", "...yılında yayımlanan karara göre..." gibi gereksiz dolgu cümleleri kurulamaz.
- Temel Bilgi İlkesi: Sorular ansiklopedik dipnotları değil; ilgili konunun dünya/ülke çapında en çok bilinen, en ayırt edici ana özelliğini hedefler.

================================================================================
[TEMELDEN BAŞLAMA VE HALKIN BİLGİSİNE UYGUNLUK İLKESİ - FOUNDATIONAL TRIVIA]
================================================================================
- Sorular daima TEMELDEN başlamalıdır! Her kategoride halkın gündelik genel kültürüne ve genel bilgisine uygun temel yapı taşları sorulmalıdır.
- Temel ve Sağlam Sorular Kesinlikle Teşvik Edilir:
  * Bir ülkenin başkentini sormak (ör. "Avustralya'nın başkenti neresidir?", "Japonya'nın başkenti hangisidir?"),
  * Bir ülkenin bayrağını, renklerini ve sembollerini sormak (ör. "Bayrağında akçaağaç yaprağı bulunan ülke hangisidir?"),
  * Coğrafyada en bilinen dağ, nehir, göl, deniz ve boğazları sormak,
  * Tarihte en bilinen kurucu liderleri, başkentleri ve dönüm noktası zaferleri sormak,
  * Bilimde temel formülleri (H2O, yerçekimi, fotosentez, gezegenler, altın, demir vb.) sormak,
  * Sporda temel kuralları, efsaneleşmiş şampiyonlukları ve efsane sporcuları sormak,
  * Sanatta Mona Lisa, Eyfel Kulesi, Kolezyum gibi dünyaca bilinen eserleri sormak.
- Soruların halkın genel kültür seviyesine uygun, anlaşılır, bilindiğinde oyuncuya tatmin veren sorulardan oluşması esastır. Kimsenin bilmediği aşırı niş akademik detaylar KESİNLİKLE yasaktır.

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

SYSTEM_PROMPT = TRIVIA_SYSTEM_PROMPT

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
    Üretilen sorunun:
    1. Bürokratik kara liste terimleri içerip içermediğini denetler.
    2. Soru metninin mobil uzunluk standardını (maksimum 150-160 karakter / 25 kelime) denetler.
    Kural ihlali tespit edilirse ValueError fırlatır ve yedek model kademesini tetikler.
    """
    if not q or not q.translations:
        return

    normalized_blacklist = [normalize_text_for_search(w) for w in BUREAUCRACY_BLACKLIST]

    texts_to_check = []
    for lang, content in q.translations.items():
        if hasattr(content, "question") and content.question:
            q_text = content.question.strip()
            # Karakter ve kelime sayısı kontrolü (Mobile Trivia Standartı)
            max_chars = 180 if lang in ("de", "es", "pt") else 160
            max_words = 28 if lang in ("de", "es", "pt") else 25
            if len(q_text) > max_chars or len(q_text.split()) > max_words:
                raise ValueError("Soru metni belirlenen 150 karakterlik mobil sınırını aştı!")
            texts_to_check.append((lang, "question", q_text))
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

def validate_skill_support_fields(q: GeneratedQuestion, source_language: str) -> None:
    """Yetenekler (ipucu, anahtar kelime, tuzak notu, ikili format, eleme) için gereken alanlar eksikse yeniden üretimi tetikler."""
    content = q.translations.get(source_language)
    if content is None:
        return
    missing = [
        name for name in ("hint", "keyword", "distractor_trap_note")
        if not str(getattr(content, name, "") or "").strip()
    ]
    fallback = content.binary_fallback
    if fallback is None or not str(fallback.statement or "").strip():
        missing.append("binary_fallback")
    weakest, strongest = q.weakest_distractor_index, q.strongest_distractor_index
    correct = "ABCD".index(q.correct_answer)
    if weakest is None or strongest is None or len({weakest, strongest, correct}) != 3:
        missing.append("weakest/strongest_distractor_index")
    if missing:
        raise ValueError(f"Yetenek destek alanları eksik veya geçersiz: {', '.join(missing)}")


def build_prompt(
    primary_category: str,
    target_subcategory: Optional[str] = None,
    secondary_category: Optional[str] = None,
    target_country: Optional[str] = None,
    target_country_code: Optional[str] = None,
    target_country_credit: Optional[int] = None,
    scope: Optional[str] = "global",
    is_global_eligible: bool = True,
    base_difficulty: Optional[str] = None,
    target_score: Optional[int] = None,
    languages: Optional[List[str]] = None,
    algorithm_context: Optional[str] = None,
    visual_question: bool = False,
    target_global_difficulty: Optional[str] = None,
    target_global_score: Optional[int] = None,
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

    from core.difficulty_balancer import DifficultyBalancer
    target_diff = DifficultyBalancer.normalize_level(base_difficulty) if base_difficulty else "Orta"
    if target_score is None:
        target_score = DifficultyBalancer.validate_and_clamp_score(target_diff)

    allowed_languages = COUNTRY_LANGUAGE_CODES
    selected_languages = [
        lang for lang in (languages or SUPPORTED_LANGUAGE_CODES)
        if lang in allowed_languages
    ]
    if not selected_languages:
        selected_languages = list(SUPPORTED_LANGUAGE_CODES)
    language_json = json.dumps(selected_languages, ensure_ascii=False)
    translation_schema = ",\n".join(
        f'    "{lang}": {{ "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "explanation": "...", "filter_tag": "...", "hint": "...", "distractor_trap_note": "...", "keyword": "...", "binary_fallback": {{"statement": "...", "is_true": true}}, "simplified_question": "...", "clarified_options": {{"A": "...", "B": "...", "C": "...", "D": "..."}} }}'
        for lang in selected_languages
    )
    language_instruction = ", ".join(
        f"{lang} ({LANGUAGE_CODE_TO_NAME.get(lang, lang)})" for lang in selected_languages
    )

    algorithm_instruction = (
        f"\n[ZORUNLU ODAK NOKTASI VE KAPSAM KILAVUZU]:\n"
        f"{algorithm_context.strip()}\n"
        f"- Kural: Sorunun konusu doğrudan yukarıdaki Odak Noktası ve Kapsam Kılavuzu çerçevesinde olmalıdır. Kılavuz dışına çıkılamaz.\n"
        if algorithm_context and algorithm_context.strip()
        else ""
    )

    if visual_question:
        from core.visual_questions import visual_focus_for_category
        visual_focus = visual_focus_for_category(primary_category, target_subcategory or "")
        if not visual_focus:
            raise ValueError(f"{primary_category} kategorisi görsel eşleştirme sorusuna uygun değil.")
        visual_instruction = f"""
[ZORUNLU GÖRSEL SORU KURALI]:
- Bu soru bir GÖRSEL TANIMA sorusu olmalı ve görsel olmadan doğrudan cevaplanamamalıdır.
- Bu kategori için izin verilen tek görsel odağı: {visual_focus}.
- Soyut kavram, formül, kimyasal denklem, fizik diyagramı, felsefi şema, yazı ağırlı belge veya dekoratif stok görsel kullanma.
- Kişi sorulacaksa yalnızca geniş halk kitlelerince hemen tanınabilecek kadar çok ünlü biri olmalıdır. Niş akademisyen, yerel uzman veya az bilinen yazar seçme.
- Tüm dillerde soru kökü "Bu görseldeki..." mantığıyla görsele açıkça bağlı olmalıdır; kişinin/nesnenin adı soru kökünde geçmemelidir.
- `is_visual_question` true olmalıdır.
- `question_image_search_query`, doğru cevabı gösteren telifsiz bir Wikimedia Commons görseli bulmaya uygun, kesin ve kısa bir İNGİLİZCE sorgu olmalıdır. Yalnızca konu ve görsel türünü yaz.
- Sorgunun başına görselde gerçekten bulunması gereken kişi/nesne/yapının İngilizce özel adını yaz; şehir, müze, "Wikimedia Commons" gibi aramayı genişleten ek sözcükler kullanma. Gerçek kişi için fotoğraf iste: örneğin "Michael Jackson photograph"; yalnızca "moonwalk" yazma.
- Görseldeki kişi/nesnenin kimliğini sordur; görselden kanıtlanamayan "icat eden", "ilk yapan" veya "ünlü yapan" gibi tarihsel ilişki soruları kurma.
- `question_image_alt_text`, cevabın özel adını söylemeden görselde görülebilen ayırt edici özellikleri Türkçe betimlemelidir.
- `question_image_url` null kalmalıdır; sunucu görseli bulup yükleyecektir.
"""
        visual_schema = (
            '  "question_image_url": null,\n'
            '  "is_visual_question": true,\n'
            '  "question_image_search_query": "precise subject Wikimedia Commons image",\n'
            '  "question_image_alt_text": "Cevabı açıklamayan görsel betimi",'
        )
    else:
        visual_instruction = """
[NORMAL METİN SORUSU KURALI]:
- Bu soru görsele bağlı olmamalıdır.
- `is_visual_question` false; `question_image_url`, `question_image_search_query` ve `question_image_alt_text` null olmalıdır.
"""
        visual_schema = (
            '  "question_image_url": null,\n'
            '  "is_visual_question": false,\n'
            '  "question_image_search_query": null,\n'
            '  "question_image_alt_text": null,'
        )

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
        global_target_instruction = (
            f"- Üretim kotası için küresel zorluk hedefi kesin: 'difficulty_global' = '{target_global_difficulty}', "
            f"'difficulty_global_score' = {target_global_score}. Küresel etiketi ve puanı aynen kullan.\n"
            if target_global_difficulty and target_global_score is not None
            else "- Küresel zorluğu dünya genelindeki ortalama bilgi düzeyine göre bağımsız değerlendir.\n"
        )
        dual_difficulty_instruction = (
            f"ASİMETRİK ÇİFT ZORLUK PUANLAMA TALİMATI (KÜRESEL ELİT ÜLKE):\n"
            f"- Hedef Odak Ülke: {country_rule} (Kredi: {credit_rule}/10 - Küresel Elit Ülke)\n"
            f"- Bu ülke küresel sahnede tanınan elit bir merkezdir. Soruya İKİ AYRI PUAN atanmalıdır:\n"
            f"  1. 'difficulty_local_score' (1-10): {country_rule} vatandaşının/kültürünün bu konuyu bilme zorluğu. Hedef Yerel Puan: {target_score}/10 ({target_diff}).\n"
            f"  2. 'difficulty_global_score' (1-10): Dünyanın geri kalanındaki ortalama bir insanın bu ülkeyle ilgili konuyu bilme zorluğu (Kolay: 1-4, Orta: 5-8, Zor: 9-10).\n"
            f"- Yerel zorluğu ülke bağlamında değerlendir; küresel puanı ayrı ver. Yerel puanı küresel kota hedefine uydurma. Örneğin ABD iç dinamikleri ABD'de Kolay/Orta iken küresel ölçekte Zor olabilir.\n"
            f"{global_target_instruction}"
            f"- Çıktı JSON'ında 'is_global_eligible': true, hem local hem global puan ve seviyeleri eksiksiz doldurulmalıdır.\n"
            f"- 'difficulty_meta': {{'local': {{'level': '{target_diff}', 'score': {target_score}, 'context_country': '{country_rule}'}}, 'global': {{'level': '...', 'score': ...}}}} olmalıdır."
        )
        schema_global_level = json.dumps(target_global_difficulty, ensure_ascii=False) if target_global_difficulty else '"Orta"'
        schema_global_score = str(target_global_score) if target_global_score is not None else "6"
        schema_meta_global = (
            json.dumps({"level": target_global_difficulty, "score": target_global_score}, ensure_ascii=False)
            if target_global_difficulty and target_global_score is not None
            else '{"level": "Orta", "score": 6}'
        )
        schema_profile_global = (
            json.dumps({"label": target_global_difficulty, "score": target_global_score}, ensure_ascii=False)
            if target_global_difficulty and target_global_score is not None
            else '{"label": "Orta", "score": 6}'
        )
    else:
        dual_difficulty_instruction = (
            f"TEK YEREL ZORLUK PUANLAMA TALİMATI (SADECE YEREL KAPSAMLI ÜLKE):\n"
            f"- Hedef Odak Ülke: {country_rule} (Kredi: {credit_rule}/10 - Yerel Kapsamlı Ülke)\n"
            f"- Bu soru sadece yerel kapsamdadır. Konu küresel bir standart taşımadığı için küresel puanlama YAPILMAYACAKTIR.\n"
            f"- Yalnızca {country_rule} yerel perspektifine göre yerel zorluk puanı ata: 'difficulty_local_score' = {target_score}, 'difficulty_local' = '{target_diff}'.\n"
            f"- 'difficulty_global' and 'difficulty_global_score' alanlarını KESİNLİKLE null bırak.\n"
            f"- Çıktı JSON'ında 'is_global_eligible': false olmalıdır.\n"
            f"- 'difficulty_meta': {{'local': {{'level': '{target_diff}', 'score': {target_score}, 'context_country': '{country_rule}'}}, 'global': null}} olmalıdır."
        )
        schema_global_level = "null"
        schema_global_score = "null"
        schema_meta_global = "null"
        schema_profile_global = "null"

    difficulty_schema_value = (
        schema_global_level
        if scope_clean == "global" and schema_global_level != "null"
        else json.dumps(target_diff, ensure_ascii=False)
    )

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
Aşağıdaki kriterlere göre yüksek kaliteli, özgün tek bir çoktan seçmeli soru üret ve şu dillerde hazırla: {language_instruction}.

Kriterler:
- Birincil Kategori: {primary_category}
{subcategory_instruction}
{f"- İkincil Kategori (Combo): {secondary_category}" if is_combo else "- Tip: Standart (Tek Kategori)"}
{scope_instruction}{filters_hint}{algorithm_instruction}
{difficulty_instruction}
{visual_instruction}

[ZORUNLU FORMAT VE UZUNLUK KURALI - MOBILE TRIVIA STANDARTI]:
- Soru kökü EN FAZLA 20-25 kelime (150 karakter) olmalıdır. Paragraf yazmak KESİNLİKLE YASAKTIR.
- Doğrudan Anlatım: "Uzun yıllar boyunca...", "...tarafından verilen nişanla ödüllendirilen...", "...yılında yayımlanan karara göre..." gibi gereksiz dolgu cümleleri kurulamaz.
- Temel Bilgi İlkesi: Sorular ansiklopedik dipnotları değil; ilgili konunun en çok bilinen, en ayırt edici ana özelliğini hedefler.
- Soruyu herkesin anlayabileceği, konunun en bilinen ve temel anahtarı üzerinden tek nefeste sor. Mobil ekranda tek bakışta anlaşılmalıdır.

[TEMELDEN BAŞLAMA VE HALKIN BİLGİSİNE UYGUNLUK KURALI]:
- Sorular daima TEMELDEN başlamalıdır! Her kategoride halkın gündelik genel kültürüne ve genel bilgisine uygun temel yapı taşları sorulmalıdır.
- Temel ve Sağlam Sorular Kesinlikle Teşvik Edilir:
  * Bir ülkenin başkentini sormak (ör. "Avustralya'nın başkenti neresidir?", "Japonya'nın başkenti hangisidir?"),
  * Bir ülkenin bayrağını, renklerini ve sembollerini sormak (ör. "Bayrağında akçaağaç yaprağı olan ülke hangisidir?"),
  * Coğrafyada en bilinen dağ, nehir, göl, kıta ve boğazları sormak,
  * Tarihte kurucu liderleri, başkentleri ve dönüm noktası zaferleri/olayları sormak,
  * Bilimde temel formülleri (H2O, yerçekimi, fotosentez, gezegenler) sormak,
  * Sporda temel kuralları, efsane şampiyonlukları ve dünyaca ünlü sporcuları sormak,
  * Sanatta Mona Lisa, Eyfel Kulesi, Kolezyum gibi dünyaca bilinen eserleri sormak.
- Sorular halkın genel kültür seviyesine uygun, anlaşılır, bilindiğinde oyuncuya keyif ve tatmin veren sorulardan oluşmalıdır. Kimsenin bilmediği aşırı niş akademik dipnotlardan KESİNLİKLE kaçınılmalıdır.

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

[KARAKTER YETENEKLERİ İÇİN SORU YARDIMCI ALANLARI]:
- `weakest_distractor_index` ve `strongest_distractor_index` 0 tabanlı seçenek indeksidir: A=0, B=1, C=2, D=3. İkisi de doğru cevaptan farklı yanlış seçenekleri göstermelidir ve `weakest_distractor_index` ile `strongest_distractor_index` birbirinden FARKLI olmalıdır (üçü de farklı indeks).
- `hint` cevabı doğrudan söylemeyen kısa, yararlı bir ipucu; `keyword` soru kökündeki gerçek kilit ifade; `distractor_trap_note` en güçlü yanlış seçeneğin neden yanlış olduğunu açıklar. Bunları HER hedef dilin çevirisinde doğal biçimde yaz.
- `binary_fallback` her hedef dilde doğru/yanlış biçimine çevrilebilen tek bir önerme ve bu önermenin doğruluğunu gösteren `is_true` içerir.
- `logical_order`, yalnızca seçeneklerin gerçekten sıralanabileceği sorularda A-D'nin 0 tabanlı indekslerinden oluşan tam bir permütasyondur; uygun değilse null.
- `option_difficulty_order`, seçeneklerin hedef kitle için en kolay tanınandan en zor tanınana doğru A-D indeks permütasyonudur; güvenilir biçimde sıralanamıyorsa null bırak.
- Her dil için `simplified_question` alanında soru kökünü aynı olguları, koşulları ve cevap eşleşmesini koruyarak daha sade yaz. `clarified_options` içinde A-D seçeneklerini daha açık anlat; seçenek anahtarlarını ve doğru cevabı değiştirme.
- `simplified_question` ve `clarified_options` bütün istenen dillerde doldurulmalıdır; belirsiz soruda sadeleştirme yaparken yeni ipucu veya cevap ekleme.
- Bu yardımcı metinler her dilin `translations` nesnesinde yer almalı; kök düzeyindeki aynı alanlar seçilmiş kaynak dilde tutulmalı.
- `base_points` 100 olmalı. `difficulty`, soru global ise `difficulty_global`, yerel ise `difficulty_local` ile eşleşmeli.
- `community_distribution` statik üretilmez; canlı oyuncu oylarından sunucu tarafında hesaplandığı için null bırakılır.

Kategoriye özgü ayrıntılı trivia örnekleri ve anti-bürokrasi kuralları sistem
talimatında zaten verilmiştir; aynı kuralları eksiksiz uygula.

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
{visual_schema}
  "is_combo": {str(is_combo).lower()},
  "scope": "{scope_clean}",
  "target_country": "{country_rule}",
  "target_country_code": "{target_country_code or 'GL'}",
  "target_country_credit": {credit_rule},
  "is_global_eligible": {str(is_global_eligible).lower()},
  "countries": ["{target_country_code or 'GL'}"],
  "version": "{QUESTION_SCHEMA_VERSION}",
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
  "weakest_distractor_index": 1,
  "strongest_distractor_index": 2,
  "hint": "Cevabı açıklamayan kısa ipucu",
  "distractor_trap_note": "En güçlü yanlış seçeneğin neden yanlış olduğu",
  "keyword": "Soru kökündeki anahtar ifade",
  "logical_order": null,
  "option_difficulty_order": [0, 1, 2, 3],
  "binary_fallback": {{"statement": "Kısa ve doğrulanabilir önerme.", "is_true": true}},
  "base_points": 100,
  "difficulty": {difficulty_schema_value},
  "community_distribution": null,
  "supported_languages": {language_json},
  "translations": {{
{translation_schema}
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
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
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

def _translation_stage_attempts() -> int:
    """Translation-only retries per validated source (env TRANSLATION_STAGE_ATTEMPTS, default 2)."""
    try:
        return max(1, int(os.getenv("TRANSLATION_STAGE_ATTEMPTS", "") or 2))
    except ValueError:
        return 2


def generate_question_with_fallback(
    primary_category: Optional[str] = None,
    secondary_category: Optional[str] = None,
    target_subcategory: Optional[str] = None,
    target_country: Optional[str] = None,
    target_country_credit: Optional[int] = None,
    preferred_local_country: Optional[str] = None,
    scope: Optional[str] = None,
    base_difficulty: Optional[str] = None,
    balancer: Optional[Any] = None,
    balance_options: bool = True,
    is_combo: Optional[bool] = None,
    category_balancer: Optional[Any] = None,
    balance_categories: bool = False,
    difficulty_balancer: Optional[Any] = None,
    balance_difficulty: bool = True,
    generation_planner: Optional[Any] = None,
    languages: Optional[List[str]] = None,
    duration_seconds: Optional[int] = None,
    algorithm_context: Optional[str] = None,
    algorithm_version: Optional[str] = None,
    visual_question: bool = False,
    llm_manager: Optional[Any] = None,
    target_score: Optional[int] = None,
    target_global_difficulty: Optional[str] = None,
    target_global_score: Optional[int] = None,
    **kwargs: Any,
) -> GeneratedQuestion:
    """
    Kategori, şık, kapsam (Global/Local), ülke kredisi ve zorluk kotası
    dengeleme mekanizması destekli soru üretim fonksiyonu (v1.0.3).
    """
    # 1. Kategori dengelemesi aktifse ve kategori veya alt kategori eksikse
    if balance_categories:
        try:
            from core.category_balancer import get_category_balancer
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
                    target_subcategory = random.choice(candidates)
        except Exception:
            pass

    # Fallback varsayılan
    if not primary_category:
        primary_category = "Bilim"

    # 2. Dinamik Kapsam (Global/Local) ve Hedef Ülke Kredi Planlaması
    from core.generation_planner import get_generation_planner
    planner = generation_planner or get_generation_planner()

    plan = planner.create_plan(
        category=primary_category,
        subcategory=target_subcategory,
        force_scope=scope,
        force_country=target_country,
        preferred_local_country=preferred_local_country,
    )
    final_scope = plan.scope
    final_country = target_country or plan.target_country
    final_country_credit = target_country_credit if target_country_credit is not None else plan.target_country_credit
    final_is_global_eligible = plan.is_global_eligible
    from core.sheet_algorithm import get_sheet_algorithm_repository
    repository = getattr(planner, "sheet_repository", None) or get_sheet_algorithm_repository()
    final_country_code = repository.country_code(final_country, final_scope)

    requested_languages = list(dict.fromkeys(
        lang for lang in (languages or SUPPORTED_LANGUAGE_CODES)
        if lang in COUNTRY_LANGUAGE_CODES
    ))
    if final_scope == "local":
        # Country languages (Sheets) + en, regardless of the caller's selection; en-only when the country is unknown.
        requested_languages = repository.question_languages(final_country) if final_country else ["en"]
    elif not requested_languages:
        requested_languages = list(SUPPORTED_LANGUAGE_CODES)
    source_language = "en" if "en" in requested_languages else requested_languages[0]

    # 3. Zorluk dengelemesi. Worker ayrılmış bir hedefi verdiyse aynı hedef,
    # yeniden denemelerde korunur; diğer çağrılar balancer'dan alır.
    from core.difficulty_balancer import get_difficulty_balancer, DifficultyBalancer
    diff_balancer = difficulty_balancer or get_difficulty_balancer()

    target_diff = base_difficulty
    target_score = None
    if balance_difficulty and not target_diff:
        diff_target = diff_balancer.get_next_target()
        target_diff = diff_target.level
        target_score = diff_target.score
    elif target_diff:
        target_diff = DifficultyBalancer.normalize_level(target_diff)
        if target_score is None:
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
        target_country_code=final_country_code,
        target_country_credit=final_country_credit,
        scope=final_scope,
        is_global_eligible=final_is_global_eligible,
        base_difficulty=target_diff,
        target_score=target_score,
        languages=[source_language],
        algorithm_context=algorithm_context,
        visual_question=visual_question,
        target_global_difficulty=target_global_difficulty,
        target_global_score=target_global_score,
    )
    
    active_llm_manager = llm_manager or MultiLLMManager()
    
    # 4. MultiLLMManager üzerinden soru üretimi (Gemini -> Groq -> Cohere Fallback & Round-Robin)
    max_retries = 3
    q: Optional[GeneratedQuestion] = None
    provider_name = "GEMINI"
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            data = active_llm_manager.generate_trivia(SYSTEM_PROMPT, prompt)
            provider_name = str(data.get("_provider", "GEMINI")).upper()
            data = normalize_generated_payload(
                data,
                str(target_subcategory or primary_category),
            )
            candidate_q = GeneratedQuestion.model_validate(data)
            if visual_question and not str(candidate_q.question_image_search_query or "").strip():
                raise ValueError("Görsel soru için question_image_search_query üretilmedi.")
            # Anti-Bürokrasi ve Trivia uyumluluk doğrulaması (Python Guard)
            validate_trivia_compliance(candidate_q)
            validate_skill_support_fields(candidate_q, source_language)
            source_content = candidate_q.translations[source_language].model_dump()
            # The source passed validation: retry only translation (bounded) so a
            # translation hiccup does not re-spend source-generation LLM calls.
            translation_attempts = _translation_stage_attempts()
            azure_cache: Dict[str, Any] = {}  # reused so retries never re-run Azure
            for translation_attempt in range(1, translation_attempts + 1):
                try:
                    translated_content, translation_provider = translate_localized_content(
                        source_content,
                        source_language,
                        requested_languages,
                        llm_manager=active_llm_manager,
                        azure_cache=azure_cache,
                    )
                    break
                except (AllProvidersCoolingDown, TranslationIncompleteError):
                    raise
                except Exception as translation_error:
                    if translation_attempt >= translation_attempts:
                        raise
                    logging.warning(
                        f"Çeviri denemesi ({translation_attempt}/{translation_attempts}) başarısız; "
                        f"kaynak soru korunuyor: {translation_error}"
                    )
                    time.sleep(random.uniform(0.5, 1.0))
            candidate_q.translations = {
                language: LocalizedContent.model_validate(content)
                for language, content in translated_content.items()
            }
            candidate_q.supported_languages = list(translated_content)
            candidate_q.translation_provider = translation_provider
            q = candidate_q
            break
        except AllProvidersCoolingDown:
            raise
        except TranslationIncompleteError:
            # Regenerating the source question cannot repair omitted locales and
            # needlessly spends generation quota.
            raise
        except Exception as err:
            last_error = err
            logging.warning(f"Soru üretim/doğrulama denemesi ({attempt}/{max_retries}) başarısız: {err}")
            continue

    if not q:
        raise RuntimeError(f"Soru üretimi başarısız oldu! Son hata: {last_error}")

    # Hedef alt kategori varsa sorunun alt kategorisini kesin olarak hedef alt kategoriye sabitle
    if target_subcategory:
        q.sub_categories = [target_subcategory]
    # LLM'nin kategori adını yeniden yazmasına izin verme; Sheet'teki adlar birebir korunur.
    q.categories = [primary_category]
    if secondary_category and secondary_category != primary_category:
        q.categories.append(secondary_category)

    # Zorluk doğrulaması ve katı 1-10 puan dengesi
    local_level = DifficultyBalancer.normalize_level(target_diff)
    raw_local_score = getattr(q, "difficulty_local_score", None) or target_score
    clamped_local_score = DifficultyBalancer.validate_and_clamp_score(local_level, raw_local_score)
    final_local_level = DifficultyBalancer.score_to_level(clamped_local_score)

    if final_is_global_eligible:
        raw_global_level = target_global_difficulty or getattr(q, "difficulty_global", None)
        if not raw_global_level and q.difficulty_profile and q.difficulty_profile.global_scope:
            raw_global_level = q.difficulty_profile.global_scope.label
        if not raw_global_level:
            raw_global_level = "Orta"

        raw_global_score = (
            target_global_score
            if target_global_score is not None
            else getattr(q, "difficulty_global_score", None)
        )
        if (not raw_global_score or raw_global_score == 5) and q.difficulty_profile and q.difficulty_profile.global_scope:
            raw_global_score = q.difficulty_profile.global_scope.score
        clamped_global_score = DifficultyBalancer.validate_and_clamp_score(raw_global_level, raw_global_score)
        final_global_level = DifficultyBalancer.score_to_level(clamped_global_score)
    else:
        final_global_level = None
        clamped_global_score = None

    server_ts = SERVER_TIMESTAMP if SERVER_TIMESTAMP is not None else datetime.now(timezone.utc).isoformat()

    q.version = QUESTION_SCHEMA_VERSION
    q.scope = final_scope
    q.target_country = final_country
    q.target_country_code = final_country_code
    q.target_country_credit = final_country_credit
    q.is_global_eligible = final_is_global_eligible
    q.generation_provider = provider_name
    q.countries = [final_country_code]
    q.created_at = server_ts
    q.shuffle_key = random.random()
    q.correct_count = 0
    q.wrong_count = 0
    q.is_visual_question = bool(visual_question)
    if not visual_question:
        q.question_image_url = None
        q.question_image_search_query = None
        q.question_image_alt_text = None
    q.difficulty_local = final_local_level
    q.difficulty_local_score = clamped_local_score
    q.difficulty_global = final_global_level
    q.difficulty_global_score = clamped_global_score
    q.difficulty = (
        final_global_level
        if final_scope == "global" and final_global_level
        else final_local_level
    )
    q.base_points = max(0, int(q.base_points or 100))
    # Poll statistics are derived from live votes and must never be model-generated.
    q.community_distribution = None
    
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
        from core.option_balancer import get_option_balancer
        b = balancer or get_option_balancer()
        q = b.balance_question(q)
    
    from core.timer_calculator import calculate_durations_from_obj
    durations = calculate_durations_from_obj(q)
    q.duration_local = durations["duration_local"]
    q.duration_global = durations["duration_global"]
    q.duration_seconds = durations["duration_seconds"]

    # Kota gerçekleşmesini ProductionBalancer'a kaydet
    try:
        from core.production_balancer import get_production_balancer
        get_production_balancer().record_success(primary_category, final_scope)
    except Exception as b_err:
        logging.warning(f"ProductionBalancer record_success hatası: {b_err}")

    # Payload hazırlığı
    payload = q.model_dump(by_alias=True)
    payload["version"] = QUESTION_SCHEMA_VERSION
    payload["shuffle_key"] = random.random()
    payload["correct_count"] = 0
    payload["wrong_count"] = 0
    payload["created_at"] = server_ts
    q.payload = payload

    # Terminal Log Formatı:
    # [v1.0.3] ⚡ [Motor: COHERE/GROQ/GEMINI] Kapsam: Global | Ülke: İtalya (Kredi: 9) | Zorluk: Orta (6/10)
    terminal_msg = (
        f"[v1.0.3] ⚡ [Motor: {provider_name}] "
        f"Kapsam: {final_scope.capitalize()} | "
        f"Ülke: {final_country} (Kredi: {final_country_credit}) | "
        f"Zorluk: {q.difficulty_local} ({q.difficulty_local_score}/10)"
    )
    logging.info(terminal_msg)
    try:
        print(terminal_msg)
    except UnicodeEncodeError:
        print(terminal_msg.encode("ascii", "replace").decode("ascii"))
    return q

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
    balance_categories: bool = False,
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
