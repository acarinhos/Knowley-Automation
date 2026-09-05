import logging
import random
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Union, Tuple

from categories_config import (
    CATEGORIES_META,
    POSSIBLE_COMBO_MATCHES,
)

logger = logging.getLogger("CategoryBalancer")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [CategoryBalancer] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Eski veya alternatif kategori / alt kategori isimlerini standart 20 forma eşleme
CATEGORY_ALIASES: Dict[str, str] = {
    "Felsefe ve Mantık": "Felsefe & Mantık",
    "Felsefe": "Felsefe & Mantık",
    "Astronomi": "Astronomi & Uzay",
    "Uzay": "Astronomi & Uzay",
    "Sinema": "Sinema & Dizi",
    "Diziler": "Sinema & Dizi",
    "Video Oyunları": "Oyun & Espor",
    "Oyun": "Oyun & Espor",
    "Espor": "Oyun & Espor",
    "Mimarlık": "Mimarlık & Sanat",
    "Mimari": "Mimarlık & Sanat",
    "Sanat": "Mimarlık & Sanat",
    "Mitoloji": "Genel Kültür & Mitoloji",
    "Genel Kültür": "Genel Kültür & Mitoloji",
    "Genel": "Genel Kültür & Mitoloji",
    "Teknoloji": "Bilgisayar & Yazılım",
    "Yazılım": "Bilgisayar & Yazılım",
    "Bilgisayar": "Bilgisayar & Yazılım",
    "Tıp": "Tıp & Sağlık",
    "Sağlık": "Tıp & Sağlık",
    "Sosyoloji": "Sosyoloji & Psikoloji",
    "Psikoloji": "Sosyoloji & Psikoloji",
    "Hukuk": "Hukuk & Siyaset",
    "Siyaset": "Hukuk & Siyaset",
    "Gastronomi": "Gastronomi & Mutfak",
    "Mutfak": "Gastronomi & Mutfak",
    "Ekonomi": "Ekonomi & Finans",
    "Finans": "Ekonomi & Finans",
}

SUBCATEGORY_ALIASES: Dict[str, str] = {
    "Antik Çağ": "Arkeoloji",
    "Orta Çağ": "Kültürel Tarih",
    "Dünya Savaşları": "Askeri Tarih",
    "Osmanlı Tarihi": "Siyasi Tarih",
    "Modern Tarih": "Siyasi Tarih",
    "Başkentler ve Ülkeler": "Beşeri",
    "Fiziki Coğrafya": "Fiziki",
    "Harita ve Bayraklar": "Ekonomik Coğrafya",
    "İklim ve Doğa Olayları": "Fiziki",
    "Motor Sporları": "Formula 1",
    "Olimpiyatlar": "Atletizm",
    "Klasik Mekanik": "Mekanik",
    "Kuantum ve Atom": "Kuantum",
    "Elektrik ve Manyetizma": "Elektromanyetizma",
    "Termodinamik ve Enerji": "Termodinamik",
    "Optik ve Dalgalar": "Optik",
    "Periyodik Tablo ve Elementler": "Anorganik",
    "Kimya": "Anorganik",
    "Organik Kimya": "Organik",
    "Kimyasal Tepkimeler": "Fizikokimya",
    "Biyoloji": "Hücre",
    "İnsan Anatomisi ve Tıp": "İnsan Fizyolojisi",
    "Hücre ve Mikrobiyoloji": "Hücre",
    "Hayvanlar Alemi": "Ekoloji",
    "Şiir ve Şairler": "Şiir",
    "Tiyatro ve Sahne": "Tiyatro & Drama",
    "Ahlak Felsefesi": "Etik",
    "Mantık ve Akıl Yürütme": "Klasik Mantık",
    "Sinema": "Yönetmen Sineması",
    "Diziler ve TV": "Kült Diziler",
    "Diziler": "Kült Diziler",
    "Müzik": "Pop & Hip-Hop",
    "Rock ve Metal": "Rock & Metal",
    "Caz ve Blues": "Caz & Blues",
    "Popüler Müzik": "Pop & Hip-Hop",
    "Video Oyunları": "Oyun Tarihi",
    "Mimari": "Mimari Akımlar",
    "Resim ve Heykel": "Heykel",
    "Yunan ve Roma Mitolojisi": "Yunan",
    "İskandinav Mitolojisi": "İskandinav",
    "Mısır Mitolojisi": "Mısır/Doğu Mitolojisi",
    "Türk ve Doğu Mitolojileri": "Mısır/Doğu Mitolojisi",
    "Yapay Zeka ve Gelecek": "Yapay Zeka",
    "Yazılım ve Kodlama": "Web Geliştirme",
    "Akıllı Cihazlar ve Donanım": "Veritabanları",
    "Astronomi": "Astrofizik",
    "Güneş Sistemi ve Gezegenler": "Güneş Sistemi",
    "Yıldızlar ve Galaksiler": "Yıldızlar & Karadelikler",
    "Uzay Keşifleri ve Roketler": "Uzay Görevleri",
    "Astrofizik ve Kara Delikler": "Astrofizik",
}


@dataclass
class TargetCategory:
    """Belirlenen hedef kategori ve alt kategori bilgilerini tutan veri modeli."""
    primary_category: str
    target_subcategory: str
    secondary_category: Optional[str] = None
    is_combo: bool = False

    def __iter__(self):
        """Tuple unpack desteği: primary_cat, sub_cat, secondary_cat = target"""
        yield self.primary_category
        yield self.target_subcategory
        yield self.secondary_category


class CategoryBalancer:
    """
    Soru üretiminde ana ve alt kategori dağılımını dinamik ve homojen olarak dengeleyen mekanizma.

    1. Snapshot / Bootstrap:
       - Kod başladığında Firestore 'questions' koleksiyonunu tarar.
       - 'categories_config.py' içindeki tüm kategoriler ve alt kategoriler sayaçlara 0 olarak dahil edilir.
    2. İki Aşamalı Seçim:
       - Adım 1: Soru sayısı en az olan ana kategoriler tespit edilir, aralarından rastgele seçilir.
       - Adım 2: Seçilen ana kategori içindeki alt kategorilerden soru sayısı en az olan seçilir.
       - Döngü Modu: Tüm kategoriler eşitlendiğinde 'cycle_pool' üzerinden karıştırılarak tüketilir.
    3. Combo (Çoklu Kategori) Desteği:
       - İlk kategori en az soruya sahip olan, ikinci kategori ise 'categories_config.py' içindeki
         'POSSIBLE_COMBO_MATCHES' tablosundan soru sayısı en az olan aday seçilir.
    4. Sayaç Güncelleme:
       - Soru başarıyla kaydedildikten sonra sayaçlar anlık olarak (+1) artırılır.
    """

    _instance: Optional["CategoryBalancer"] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        db: Optional[Any] = None,
        auto_scan: bool = True,
        initial_category_counts: Optional[Dict[str, int]] = None,
        initial_subcategory_counts: Optional[Dict[str, Dict[str, int]]] = None,
    ):
        self.lock = threading.Lock()
        self.cycle_pool: List[str] = []
        self.sub_cycle_pools: Dict[str, List[str]] = {}

        # 1. categories_config.py içindeki tüm kategorileri ve alt kategorileri 0 ile başlat
        self.category_counts: Dict[str, int] = {}
        self.subcategory_counts: Dict[str, Dict[str, int]] = {}

        self._init_empty_counters()

        # Dışarıdan mock sayaç verilmişse yükle
        if initial_category_counts is not None:
            for cat, count in initial_category_counts.items():
                self.category_counts[cat] = int(count)

        if initial_subcategory_counts is not None:
            for cat, subs in initial_subcategory_counts.items():
                if cat not in self.subcategory_counts:
                    self.subcategory_counts[cat] = {}
                for sub, count in subs.items():
                    self.subcategory_counts[cat][sub] = int(count)

        # Firestore'dan tarama yapılacaksa
        if auto_scan and initial_category_counts is None:
            self.scan_firestore(db=db)

        self._log_initial_status()

    def _init_empty_counters(self) -> None:
        """Konfigürasyondaki tüm kategori ve alt kategorileri 0 ile hazırlar."""
        for cat_name, cat_meta in CATEGORIES_META.items():
            self.category_counts[cat_name] = 0
            self.subcategory_counts[cat_name] = {
                sub_name: 0
                for sub_name in cat_meta.get("sub_categories", {}).keys()
            }
            self.sub_cycle_pools[cat_name] = []

    @classmethod
    def get_instance(
        cls,
        db: Optional[Any] = None,
        force_refresh: bool = False,
        **kwargs: Any
    ) -> "CategoryBalancer":
        """Thread-safe singleton instance döndürür."""
        with cls._lock:
            if cls._instance is None or force_refresh:
                cls._instance = cls(db=db, **kwargs)
            return cls._instance

    def scan_firestore(self, db: Optional[Any] = None) -> None:
        """Firestore'daki soruları tarayarak ana ve alt kategori sayılarını tespit eder."""
        try:
            if db is None:
                from db_manager import init_firebase
                db = init_firebase()

            logger.info("🔍 Firestore 'questions' koleksiyonu kategoriler için taranıyor...")
            docs = db.collection("questions").stream()

            # Sıfırla ve yeniden say
            with self.lock:
                self._init_empty_counters()

            cat_counter = Counter()
            sub_counter: Dict[str, Counter] = {c: Counter() for c in CATEGORIES_META.keys()}
            total_questions = 0

            for doc in docs:
                total_questions += 1
                data = doc.to_dict()

                # Ana kategorileri oku
                cats = data.get("categories") or []
                if isinstance(cats, str):
                    cats = [cats]
                elif not cats and data.get("category"):
                    cats = [data.get("category")]

                # Alt kategorileri oku
                subs = data.get("sub_categories") or []
                if isinstance(subs, str):
                    subs = [subs]
                elif not subs and data.get("sub_category"):
                    subs = [data.get("sub_category")]

                first_sub = str(subs[0]).strip() if subs else ""
                first_sub = SUBCATEGORY_ALIASES.get(first_sub, first_sub)

                cleaned_cats: List[str] = []
                for c in cats:
                    clean_c = str(c).strip()
                    if clean_c == "Sanat ve Edebiyat":
                        if first_sub in ["Dünya Edebiyatı", "Türk Edebiyatı", "Şiir", "Tiyatro & Drama", "Mitolojik Metinler"]:
                            normalized_cat = "Edebiyat"
                        else:
                            normalized_cat = "Mimarlık & Sanat"
                    elif clean_c in ["Sanat", "Mimarlık", "Mimari"]:
                        normalized_cat = "Mimarlık & Sanat"
                    elif clean_c == "Popüler Kültür":
                        if first_sub in ["Sinema", "Diziler ve TV", "Diziler", "Yönetmen Sineması", "Gişe", "Animasyon", "Kült Diziler", "Belgesel"]:
                            normalized_cat = "Sinema & Dizi"
                        elif first_sub in ["Müzik", "Klasik", "Rock & Metal", "Caz & Blues", "Pop & Hip-Hop"]:
                            normalized_cat = "Müzik"
                        elif first_sub in ["Video Oyunları", "Oyun Tarihi", "RPG & Macera", "FPS & Rekabetçi", "Strateji", "Espor Turnuvaları"]:
                            normalized_cat = "Oyun & Espor"
                        else:
                            normalized_cat = "Genel Kültür & Mitoloji"
                    elif clean_c == "Bilim":
                        if first_sub in ["Mekanik", "Termodinamik", "Optik", "Elektromanyetizma", "Kuantum", "Fizik"]:
                            normalized_cat = "Fizik"
                        elif first_sub in ["Organik", "Anorganik", "Fizikokimya", "Biyokimya", "Analitik Kimya", "Kimya"]:
                            normalized_cat = "Kimya"
                        elif first_sub in ["Genetik", "Hücre", "İnsan Fizyolojisi", "Ekoloji", "Evrim", "Biyoloji"]:
                            normalized_cat = "Biyoloji"
                        elif first_sub in ["Güneş Sistemi", "Yıldızlar & Karadelikler", "Uzay Görevleri", "Astrofizik", "Astronomi"]:
                            normalized_cat = "Astronomi & Uzay"
                        else:
                            normalized_cat = "Bilgisayar & Yazılım"
                    elif clean_c in ["Felsefe ve Mantık", "Felsefe"]:
                        normalized_cat = "Felsefe & Mantık"
                    elif clean_c in ["Genel Kültür", "Mitoloji", "Genel"]:
                        normalized_cat = "Genel Kültür & Mitoloji"
                    elif clean_c in ["Teknoloji", "Yazılım", "Bilgisayar"]:
                        normalized_cat = "Bilgisayar & Yazılım"
                    else:
                        normalized_cat = CATEGORY_ALIASES.get(clean_c, clean_c)

                    if normalized_cat in self.category_counts:
                        cleaned_cats.append(normalized_cat)
                        cat_counter[normalized_cat] += 1

                # Birincil kategoriye alt kategoriyi bağla
                primary_cat = cleaned_cats[0] if cleaned_cats else None

                for s in subs:
                    clean_s = str(s).strip()
                    clean_s = SUBCATEGORY_ALIASES.get(clean_s, clean_s)

                    if primary_cat and primary_cat in self.subcategory_counts:
                        if clean_s in self.subcategory_counts[primary_cat]:
                            sub_counter[primary_cat][clean_s] += 1
                            continue

                    matched = False
                    for known_cat, sub_dict in self.subcategory_counts.items():
                        if clean_s in sub_dict:
                            sub_counter[known_cat][clean_s] += 1
                            matched = True
                            break

                    if not matched and primary_cat and primary_cat in self.subcategory_counts:
                        sub_keys = list(self.subcategory_counts[primary_cat].keys())
                        if sub_keys:
                            sub_counter[primary_cat][sub_keys[0]] += 1

            with self.lock:
                for cat in self.category_counts.keys():
                    self.category_counts[cat] = cat_counter[cat]
                    for sub in self.subcategory_counts[cat].keys():
                        self.subcategory_counts[cat][sub] = sub_counter[cat][sub]

            logger.info(
                f"📊 Firestore kategori taraması tamamlandı! Toplam Soru: {total_questions} | "
                f"Kategoriler: {dict(self.category_counts)}"
            )
        except Exception as e:
            logger.warning(f"⚠️ Firestore taranırken hata oluştu, sayaçlar varsayılan değerlerle devam ediyor: {e}")

    def is_equalized(self) -> bool:
        """Tüm ana kategorilerin soru sayıları birbirine eşit mi?"""
        vals = list(self.category_counts.values())
        return len(vals) > 0 and all(v == vals[0] for v in vals)

    def is_subcategory_equalized(self, category: str) -> bool:
        """Belirli bir ana kategorinin tüm alt kategorileri eşit sayıda mı?"""
        subs = self.subcategory_counts.get(category, {})
        vals = list(subs.values())
        return len(vals) > 0 and all(v == vals[0] for v in vals)

    def _log_initial_status(self) -> None:
        """Başlangıç durumunu loglar."""
        total = sum(self.category_counts.values())
        if self.is_equalized():
            logger.info(f"⚖️ Kategori dağılımı dengede! (Toplam {total} soru) -> Döngü Modu aktif.")
        else:
            min_val = min(self.category_counts.values()) if self.category_counts else 0
            min_cats = [c for c, count in self.category_counts.items() if count == min_val]
            logger.info(
                f"🎯 Kategori Dengeleme (Catch-up) Modu aktif. Toplam: {total} soru | "
                f"En Az Soruya Sahip ({min_val} soru): {min_cats}"
            )

    def get_next_target(
        self,
        is_combo: bool = False,
        auto_increment: bool = False
    ) -> TargetCategory:
        """
        Dengeleme kurallarına göre sıradaki üretilecek ana kategori, alt kategori ve (eğer combo ise) ikincil kategoriyi belirler.

        Adım 1 (Ana Kategori Seçimi):
          - Her zaman o anki sayaçlar üzerinden soru sayısı en az olan ana kategoriler tespit edilir.
          - En az soruya sahip kategoriler arasından rastgele seçim yapılır.
          - Tüm ana kategoriler eşit olduğunda hepsi aday olur ve aralarından rastgele seçilerek bir sonraki seviyeye dengeli ilerlenir.

        Adım 2 (Alt Kategori Seçimi):
          - Seçilen ana kategorinin alt dalları taranır; o ana kategori içinde soru sayısı en az olan alt dallar tespit edilir.
          - En az soruya sahip alt kategoriler arasından rastgele dağıtım yapılır.
          - Böylece bir ana kategorinin genel alt dalları da kendi arasında dengeli olur.

        Adım 3 (Combo Desteği):
          - is_combo=True ise 'POSSIBLE_COMBO_MATCHES' listesinde yer alan alternatifler arasından soru sayısı en az olan ikincil kategori seçilir.

        Args:
            is_combo: Üretilecek soru hibrit/combo soru mu?
            auto_increment: Seçim anında sayaçları hemen artırsın mı? (Varsayılan: False - kayıt sonrası güncellenir)
        """
        with self.lock:
            # --- Adım 1: Ana Kategori Seçimi (Dinamik Minimum + Rastgele Dağıtım) ---
            min_cat_count = min(self.category_counts.values()) if self.category_counts else 0
            cat_candidates = [c for c, count in self.category_counts.items() if count == min_cat_count]
            primary_cat = random.choice(cat_candidates)

            # Geriye dönük uyumluluk / özet için cycle_pool'u kalan adaylarla senkron tut
            self.cycle_pool = [c for c in cat_candidates if c != primary_cat]

            if self.is_equalized():
                logger.info(f"⚖️ [Category Equalized] Tüm ana kategoriler eşit ({min_cat_count} soru). Rastgele Seçilen: {primary_cat}")
            else:
                logger.info(f"🎯 [Category Catch-up] Atanan Ana Kategori: {primary_cat} (En Az Soru: {min_cat_count} | Adaylar: {cat_candidates})")

            # --- Adım 2: Alt Kategori Seçimi (Dinamik Minimum + Rastgele Dağıtım) ---
            subs = self.subcategory_counts.get(primary_cat, {})
            if not subs:
                cat_meta = CATEGORIES_META.get(primary_cat, {})
                available_subs = list(cat_meta.get("sub_categories", {}).keys())
                target_sub = random.choice(available_subs) if available_subs else "Genel"
            else:
                min_sub_count = min(subs.values())
                sub_candidates = [s for s, count in subs.items() if count == min_sub_count]
                target_sub = random.choice(sub_candidates)

                # Geriye dönük uyumluluk için sub_cycle_pools güncelle
                self.sub_cycle_pools[primary_cat] = [s for s in sub_candidates if s != target_sub]

                if self.is_subcategory_equalized(primary_cat):
                    logger.info(f"⚖️ [SubCategory Equalized] '{primary_cat}' alt dalları dengede ({min_sub_count} soru). Rastgele Seçilen: {target_sub}")
                else:
                    logger.info(
                        f"🎯 [SubCategory Catch-up] '{primary_cat}' için Alt Kategori: {target_sub} "
                        f"(En Az Soru: {min_sub_count} | Adaylar: {sub_candidates})"
                    )

            # --- Adım 3: Combo İkincil Kategori Seçimi ---
            secondary_cat: Optional[str] = None
            if is_combo:
                possible_secondaries = list(POSSIBLE_COMBO_MATCHES.get(primary_cat, []))
                # Sadece geçerli ve birincil kategoriden farklı olanları al
                valid_secondaries = [
                    c for c in possible_secondaries
                    if c in self.category_counts and c != primary_cat
                ]

                # Fallback: Eğer tabloda geçerli yoksa, birincil dışındaki tüm kategorileri al
                if not valid_secondaries:
                    valid_secondaries = [c for c in self.category_counts.keys() if c != primary_cat]

                if valid_secondaries:
                    min_sec_count = min(self.category_counts[c] for c in valid_secondaries)
                    sec_candidates = [c for c in valid_secondaries if self.category_counts[c] == min_sec_count]
                    secondary_cat = random.choice(sec_candidates)
                    logger.info(
                        f"🔗 [Combo Match] '{primary_cat}' için ikincil kategori '{secondary_cat}' seçildi. "
                        f"(Adaylar: {sec_candidates}, Min Soru: {min_sec_count})"
                    )

            target = TargetCategory(
                primary_category=primary_cat,
                target_subcategory=target_sub,
                secondary_category=secondary_cat,
                is_combo=is_combo
            )

            if auto_increment:
                self._unsafe_record_success(primary_cat, target_sub, secondary_cat)

            return target

    def record_success(
        self,
        primary_category: str,
        subcategory: Optional[str] = None,
        secondary_category: Optional[str] = None
    ) -> None:
        """
        Soru başarıyla üretilip Firestore'a kaydedildikten sonra sayaçları günceller (+1).
        Thread-safe işlem.
        """
        with self.lock:
            self._unsafe_record_success(primary_category, subcategory, secondary_category)

    def _unsafe_record_success(
        self,
        primary_category: str,
        subcategory: Optional[str] = None,
        secondary_category: Optional[str] = None
    ) -> None:
        """Lock altında çalışan iç sayaç artırma mantığı."""
        # Ana kategori sayacını artır
        if primary_category in self.category_counts:
            self.category_counts[primary_category] += 1
        else:
            self.category_counts[primary_category] = 1

        # Alt kategori sayacını artır
        if subcategory:
            if primary_category not in self.subcategory_counts:
                self.subcategory_counts[primary_category] = {}
            self.subcategory_counts[primary_category][subcategory] = (
                self.subcategory_counts[primary_category].get(subcategory, 0) + 1
            )

        # İkincil kategori varsa onun da sayacını artır (melez soru her iki kategoriyi besler)
        if secondary_category:
            if secondary_category in self.category_counts:
                self.category_counts[secondary_category] += 1
            else:
                self.category_counts[secondary_category] = 1

        sub_info = f" -> {subcategory}" if subcategory else ""
        combo_info = f" + {secondary_category}" if secondary_category else ""
        logger.info(
            f"✅ [Category Balancer] Sayaç Güncellendi: [{primary_category}{sub_info}{combo_info}] | "
            f"Mevcut {primary_category}: {self.category_counts[primary_category]}"
        )

        if self.is_equalized():
            logger.info(
                f"🎉 Tüm ana kategoriler eşitlendi! (Her biri: {self.category_counts[primary_category]} soru). "
                f"Sistem dinamik eşit dengeleme ile devam ediyor."
            )

    def record_question(self, question: Any) -> None:
        """
        Üretilen veya kaydedilen GeneratedQuestion/dict nesnesinden kategori bilgilerini okuyarak sayaçları günceller.
        """
        if isinstance(question, dict):
            categories = question.get("categories", [])
            sub_categories = question.get("sub_categories", [])
            is_combo = question.get("is_combo", False)
        else:
            categories = getattr(question, "categories", [])
            sub_categories = getattr(question, "sub_categories", [])
            is_combo = getattr(question, "is_combo", False)

        primary_cat = categories[0] if categories else None
        secondary_cat = categories[1] if (is_combo and len(categories) > 1) else None
        target_sub = sub_categories[0] if sub_categories else None

        if primary_cat:
            self.record_success(
                primary_category=primary_cat,
                subcategory=target_sub,
                secondary_category=secondary_cat
            )

    def get_distribution_summary(self) -> Dict[str, Any]:
        """Güncel kategori ve alt kategori dağılım durumunu özetler."""
        with self.lock:
            total = sum(self.category_counts.values())
            percentages = {
                cat: (self.category_counts[cat] / total * 100) if total > 0 else 0.0
                for cat in self.category_counts
            }
            return {
                "total_questions": total,
                "category_counts": dict(self.category_counts),
                "category_percentages": percentages,
                "subcategory_counts": {
                    cat: dict(subs) for cat, subs in self.subcategory_counts.items()
                },
                "is_equalized": self.is_equalized(),
                "cycle_pool": list(self.cycle_pool)
            }


def get_category_balancer(db: Optional[Any] = None) -> CategoryBalancer:
    """Modüller için kolay erişim sağlayan singleton fonksiyon."""
    return CategoryBalancer.get_instance(db=db)
