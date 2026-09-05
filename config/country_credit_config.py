"""
Knowley Soru Üretim Motoru v1.0.3
Kategori - Ülke Kredi Matrisi ve Dağılım Oranları Konfigürasyonu
"""

from typing import Dict, Any

CATEGORY_COUNTRY_MATRIX: Dict[str, Dict[str, Any]] = {
    "Tarih": {
        "ratio": {"global": 0.25, "local": 0.75},
        "elite": {"Birleşik Krallık": 9, "Fransa": 8, "İtalya": 9, "Yunanistan": 7, "Rusya": 7, "Çin": 7, "İspanya": 5},
        "closed": ["ABD", "Almanya", "Türkiye", "Portekiz", "Hindistan", "Japonya", "İskandinav Bölgesi", "Brezilya"]
    },
    "Coğrafya": {
        "ratio": {"global": 0.30, "local": 0.70},
        "elite": {"Rusya": 10, "Brezilya": 10, "ABD": 9, "Çin": 9, "Türkiye": 6, "Hindistan": 5},
        "closed": ["Birleşik Krallık", "Portekiz", "İspanya", "İskandinav Bölgesi", "Fransa", "Almanya", "İtalya", "Japonya", "Yunanistan"]
    },
    "Spor": {
        "ratio": {"global": 0.70, "local": 0.30},
        "elite": {"Birleşik Krallık": 10, "ABD": 6, "İspanya": 9, "Almanya": 8, "İtalya": 7, "Fransa": 7},
        "closed": ["Brezilya", "Portekiz", "Japonya", "Rusya", "Çin", "İskandinav Bölgesi", "Türkiye", "Yunanistan", "Hindistan"]
    },
    "Fizik": {
        "ratio": {"global": 0.85, "local": 0.15},
        "elite": {"Almanya": 10, "Birleşik Krallık": 10, "ABD": 10, "Fransa": 8},
        "closed": ["İtalya", "Rusya", "İskandinav Bölgesi", "Japonya", "Yunanistan", "Çin", "Hindistan", "İspanya", "Türkiye", "Brezilya", "Portekiz"]
    },
    "Kimya": {
        "ratio": {"global": 0.90, "local": 0.10},
        "elite": {"Almanya": 10, "Birleşik Krallık": 10, "Fransa": 9, "ABD": 9},
        "closed": ["İskandinav Bölgesi", "Rusya", "İtalya", "Japonya", "Çin", "Hindistan", "İspanya", "Türkiye", "Brezilya", "Yunanistan", "Portekiz"]
    },
    "Biyoloji": {
        "ratio": {"global": 0.90, "local": 0.10},
        "elite": {"Birleşik Krallık": 10, "ABD": 10, "Almanya": 8, "Fransa": 8},
        "closed": ["İskandinav Bölgesi", "Rusya", "Brezilya", "Japonya", "İtalya", "Çin", "Hindistan", "İspanya", "Yunanistan", "Türkiye", "Portekiz"]
    },
    "Ekonomi & Finans": {
        "ratio": {"global": 0.80, "local": 0.20},
        "elite": {"ABD": 10, "Birleşik Krallık": 10, "Almanya": 8, "Çin": 8},
        "closed": ["Japonya", "Fransa", "İskandinav Bölgesi", "İtalya", "Hindistan", "Rusya", "İspanya", "Brezilya", "Türkiye", "Portekiz", "Yunanistan"]
    },
    "Edebiyat": {
        "ratio": {"global": 0.50, "local": 0.50},
        "elite": {"Birleşik Krallık": 10, "Rusya": 10, "Fransa": 10, "ABD": 8, "İtalya": 7},
        "closed": ["Almanya", "İspanya", "Yunanistan", "İskandinav Bölgesi", "Japonya", "Portekiz", "Çin", "Hindistan", "Türkiye", "Brezilya"]
    },
    "Felsefe & Mantık": {
        "ratio": {"global": 0.85, "local": 0.15},
        "elite": {"Yunanistan": 10, "Almanya": 10, "Fransa": 9, "Birleşik Krallık": 8, "Çin": 7},
        "closed": ["İtalya", "Hindistan", "ABD", "Rusya", "İskandinav Bölgesi", "İspanya", "Japonya", "Türkiye", "Portekiz", "Brezilya"]
    },
    "Sinema & Dizi": {
        "ratio": {"global": 0.70, "local": 0.30},
        "elite": {"ABD": 10, "Birleşik Krallık": 8, "Fransa": 7, "Japonya": 5, "İtalya": 6},
        "closed": ["Hindistan", "Almanya", "İspanya", "İskandinav Bölgesi", "Rusya", "Türkiye", "Çin", "Brezilya", "Yunanistan", "Portekiz"]
    },
    "Müzik": {
        "ratio": {"global": 0.60, "local": 0.40},
        "elite": {"ABD": 10, "Birleşik Krallık": 10, "Almanya": 9, "İtalya": 8, "Fransa": 6},
        "closed": ["Rusya", "Brezilya", "İskandinav Bölgesi", "İspanya", "Japonya", "Portekiz", "Hindistan", "Yunanistan", "Türkiye", "Çin"]
    },
    "Genel Kültür & Mitoloji": {
        "ratio": {"global": 0.60, "local": 0.40},
        "elite": {"Yunanistan": 10, "İskandinav Bölgesi": 10, "İtalya": 8, "Çin": 8},
        "closed": ["Birleşik Krallık", "Hindistan", "Fransa", "ABD", "Rusya", "Japonya", "Türkiye", "Almanya", "İspanya", "Portekiz", "Brezilya"]
    },
    "Bilgisayar & Yazılım": {
        "ratio": {"global": 0.85, "local": 0.15},
        "elite": {"ABD": 10, "Birleşik Krallık": 8, "İskandinav Bölgesi": 7, "Çin": 5, "Japonya": 5},
        "closed": ["Hindistan", "Almanya", "Rusya", "Fransa", "İtalya", "İspanya", "Türkiye", "Brezilya", "Portekiz", "Yunanistan"]
    },
    "Tıp & Sağlık": {
        "ratio": {"global": 0.85, "local": 0.15},
        "elite": {"ABD": 10, "Birleşik Krallık": 9, "Almanya": 8, "Fransa": 8},
        "closed": ["İskandinav Bölgesi", "İtalya", "Japonya", "Yunanistan", "Çin", "Rusya", "Hindistan", "İspanya", "Brezilya", "Türkiye", "Portekiz"]
    },
    "Sosyoloji & Psikoloji": {
        "ratio": {"global": 0.75, "local": 0.25},
        "elite": {"Fransa": 10, "Almanya": 10, "Birleşik Krallık": 8, "ABD": 8},
        "closed": ["Rusya", "İskandinav Bölgesi", "İtalya", "Çin", "Yunanistan", "Hindistan", "İspanya", "Japonya", "Brezilya", "Türkiye", "Portekiz"]
    },
    "Astronomi & Uzay": {
        "ratio": {"global": 0.95, "local": 0.05},
        "elite": {"ABD": 10, "Rusya": 10, "Çin": 8},
        "closed": ["Fransa", "Almanya", "Birleşik Krallık", "Japonya", "Hindistan", "İtalya", "İskandinav Bölgesi", "Yunanistan", "İspanya", "Brezilya", "Türkiye", "Portekiz"]
    },
    "Hukuk & Siyaset": {
        "ratio": {"global": 0.50, "local": 0.50},
        "elite": {"Fransa": 10, "Birleşik Krallık": 10, "ABD": 9, "İtalya": 8},
        "closed": ["Almanya", "Yunanistan", "Rusya", "Çin", "İskandinav Bölgesi", "İspanya", "Hindistan", "Türkiye", "Portekiz", "Brezilya", "Japonya"]
    },
    "Oyun & Espor": {
        "ratio": {"global": 0.85, "local": 0.15},
        "elite": {"Japonya": 10, "ABD": 10, "İskandinav Bölgesi": 8},
        "closed": ["Çin", "Birleşik Krallık", "Fransa", "Almanya", "Rusya", "Brezilya", "Türkiye", "İspanya", "İtalya", "Hindistan", "Portekiz", "Yunanistan"]
    },
    "Gastronomi & Mutfak": {
        "ratio": {"global": 0.50, "local": 0.50},
        "elite": {"İtalya": 10, "Fransa": 10, "Japonya": 9, "Türkiye": 8, "İspanya": 8},
        "closed": ["Çin", "Hindistan", "Yunanistan", "ABD", "Brezilya", "Portekiz", "Birleşik Krallık", "Almanya", "Rusya", "İskandinav Bölgesi"]
    },
    "Mimarlık & Sanat": {
        "ratio": {"global": 0.60, "local": 0.40},
        "elite": {"İtalya": 10, "Fransa": 10, "Yunanistan": 9, "İspanya": 8},
        "closed": ["Birleşik Krallık", "Almanya", "Rusya", "ABD", "Japonya", "Türkiye", "Çin", "Hindistan", "İskandinav Bölgesi", "Portekiz", "Brezilya"]
    }
}

# Standartlaştırma ve Alias Eşleme
CATEGORY_ALIASES: Dict[str, str] = {
    "Bilim": "Fizik",
    "Felsefe": "Felsefe & Mantık",
    "Felsefe ve Mantık": "Felsefe & Mantık",
    "Astronomi": "Astronomi & Uzay",
    "Uzay": "Astronomi & Uzay",
    "Sinema": "Sinema & Dizi",
    "Dizi": "Sinema & Dizi",
    "Diziler": "Sinema & Dizi",
    "Video Oyunları": "Oyun & Espor",
    "Oyun": "Oyun & Espor",
    "Espor": "Oyun & Espor",
    "Mimarlık": "Mimarlık & Sanat",
    "Sanat": "Mimarlık & Sanat",
    "Mimari": "Mimarlık & Sanat",
    "Mitoloji": "Genel Kültür & Mitoloji",
    "Genel Kültür": "Genel Kültür & Mitoloji",
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


def normalize_category_name(category: str) -> str:
    """Kategori adını matristeki resmi ada eşler."""
    c = str(category).strip()
    if c in CATEGORY_COUNTRY_MATRIX:
        return c
    return CATEGORY_ALIASES.get(c, c)


def get_category_matrix_config(category: str) -> Dict[str, Any]:
    """Kategoriye ait matris konfigürasyonunu döndürür. Bulunamazsa varsayılan Tarih döner."""
    norm = normalize_category_name(category)
    if norm in CATEGORY_COUNTRY_MATRIX:
        return CATEGORY_COUNTRY_MATRIX[norm]
    return CATEGORY_COUNTRY_MATRIX.get("Tarih", {
        "ratio": {"global": 0.50, "local": 0.50},
        "elite": {"Birleşik Krallık": 10, "ABD": 10},
        "closed": []
    })


def is_country_global_eligible(category: str, country: str) -> bool:
    """
    Belirli bir kategoride verilen ülkenin 'Küresel Elit Ülke' (Global Eligible) olup olmadığını denetler:
    - Kategori elit listesinde yer almalı ve kredi puanı >= 5 olmalıdır.
    - Kategori closed (kapalı) listesinde YER ALMAMALIDIR.
    """
    cfg = get_category_matrix_config(category)
    elite = cfg.get("elite", {})
    closed = cfg.get("closed", [])

    c_clean = str(country).strip()
    if c_clean in closed:
        return False

    return elite.get(c_clean, 0) >= 5


def get_country_credit(category: str, country: str) -> int:
    """Ülkenin kategorideki kredi puanını döndürür. Elit değilse veya kapalıysa varsayılan 4 döner."""
    cfg = get_category_matrix_config(category)
    elite = cfg.get("elite", {})
    c_clean = str(country).strip()
    if c_clean in elite:
        return elite[c_clean]
    return 4


def get_category_ratio(category: str) -> Dict[str, int]:
    """Kategoriye ait Global ve Local yüzdelik oranlarını (0-100 tam sayı) döndürür."""
    cfg = get_category_matrix_config(category)
    ratio = cfg.get("ratio", {"global": 0.50, "local": 0.50})
    return {
        "global": int(round(ratio.get("global", 0.50) * 100)),
        "local": int(round(ratio.get("local", 0.50) * 100))
    }


