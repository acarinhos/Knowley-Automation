CATEGORIES_META = {
    "Bilim": {
        "color": "#2563EB",
        "sub_categories": {
            "Fizik": "https://storage.googleapis.com/knowley-1-categories/categories/Bilim/Fizik.jpg",
            "Kimya": "https://storage.googleapis.com/knowley-1-categories/categories/Bilim/Kimya.jpg",
            "Biyoloji": "https://storage.googleapis.com/knowley-1-categories/categories/Bilim/Biyoloji.jpg",
            "Astronomi": "https://storage.googleapis.com/knowley-1-categories/categories/Bilim/Astronomi.jpg",
            "Genetik": "https://storage.googleapis.com/knowley-1-categories/categories/Bilim/Genetik.jpg",
        }
    },
    "Tarih": {
        "color": "#D97706",
        "sub_categories": {
            "Antik Çağ": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/Antik%20%C3%87a%C4%9F.jpg",
            "Orta Çağ": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/Orta%20%C3%87a%C4%9F.jpg",
            "Dünya Savaşları": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/D%C3%BCnya%20Sava%C5%9Flar%C4%B1.jpg",
            "Osmanlı Tarihi": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/Osmanl%C4%B1%20Tarihi.jpg",
            "Modern Tarih": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/Modern%20Tarih.jpg",
        }
    },
    "Coğrafya": {
        "color": "#059669",
        "sub_categories": {
            "Başkentler ve Ülkeler": "https://storage.googleapis.com/knowley-1-categories/categories/Co%C4%9Frafya/Ba%C5%9Fkentler%20ve%20%C3%9Clkeler.jpg",
            "Fiziki Coğrafya": "https://storage.googleapis.com/knowley-1-categories/categories/Co%C4%9Frafya/Fiziki%20Co%C4%9Frafya.jpg",
            "Harita ve Bayraklar": "https://storage.googleapis.com/knowley-1-categories/categories/Co%C4%9Frafya/Harita%20ve%20Bayraklar.jpg",
            "İklim ve Doğa Olayları": "https://storage.googleapis.com/knowley-1-categories/categories/Co%C4%9Frafya/%C4%B0klim%20ve%20Do%C4%9Fa%20Olaylar%C4%B1.jpg",
        }
    },
    "Sanat ve Edebiyat": {
        "color": "#9333EA",
        "sub_categories": {
            "Resim ve Heykel": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/Resim%20ve%20Heykel.jpg",
            "Dünya Edebiyatı": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/D%C3%BCnya%20Edebiyat%C4%B1.jpg",
            "Tiyatro ve Sahne": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/Tiyatro%20ve%20Sahne.jpg",
            "Mimari": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/Mimari.jpg",
        }
    },
    "Spor": {
        "color": "#DC2626",
        "sub_categories": {
            "Futbol": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Futbol.jpg",
            "Basketbol": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Basketbol.jpg",
            "Olimpiyatlar": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Olimpiyatlar.jpg",
            "Motor Sporları": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Motor%20Sporlar%C4%B1.jpg",
            "Tenis": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Tenis.jpg",
        }
    },
    "Popüler Kültür": {
        "color": "#EC4899",
        "sub_categories": {
            "Sinema": "https://storage.googleapis.com/knowley-1-categories/categories/Pop%C3%BCler%20K%C3%BClt%C3%BCr/Sinema.jpg",
            "Müzik": "https://storage.googleapis.com/knowley-1-categories/categories/Pop%C3%BCler%20K%C3%BClt%C3%BCr/M%C3%BCzik.jpg",
            "Video Oyunları": "https://storage.googleapis.com/knowley-1-categories/categories/Pop%C3%BCler%20K%C3%BClt%C3%BCr/Video%20Oyunlar%C4%B1.jpg",
            "Diziler ve TV": "https://storage.googleapis.com/knowley-1-categories/categories/Pop%C3%BCler%20K%C3%BClt%C3%BCr/Diziler%20ve%20TV.jpg",
        }
    },
    "Felsefe ve Mantık": {
        "color": "#475569",
        "sub_categories": {
            "Ahlak Felsefesi": "https://storage.googleapis.com/knowley-1-categories/categories/Felsefe%20ve%20Mant%C4%B1k/Ahlak%20Felsefesi.jpg",
            "Mantık ve Akıl Yürütme": "https://storage.googleapis.com/knowley-1-categories/categories/Felsefe%20ve%20Mant%C4%B1k/Mant%C4%B1k%20ve%20Ak%C4%B1l%20Y%C3%BCr%C3%BCtme.jpg",
            "Epistemoloji": "https://storage.googleapis.com/knowley-1-categories/categories/Felsefe%20ve%20Mant%C4%B1k/Epistemoloji.jpg",
        }
    },
}

DEFAULT_FALLBACK_IMAGE = "https://storage.googleapis.com/knowley-1-categories/categories/Genel/default_fallback.jpg"
DEFAULT_FALLBACK_COLOR = "#6366F1"

# Olası Combo Eşleşmeleri Tablosu (Possible Combo Pairings)
POSSIBLE_COMBO_MATCHES = {
    "Bilim": ["Tarih", "Felsefe ve Mantık", "Coğrafya", "Sanat ve Edebiyat", "Popüler Kültür"],
    "Tarih": ["Sanat ve Edebiyat", "Coğrafya", "Felsefe ve Mantık", "Bilim", "Spor", "Popüler Kültür"],
    "Coğrafya": ["Tarih", "Bilim", "Popüler Kültür", "Spor", "Sanat ve Edebiyat"],
    "Sanat ve Edebiyat": ["Tarih", "Felsefe ve Mantık", "Popüler Kültür", "Bilim", "Coğrafya"],
    "Spor": ["Tarih", "Popüler Kültür", "Coğrafya", "Bilim"],
    "Popüler Kültür": ["Sanat ve Edebiyat", "Spor", "Tarih", "Bilim", "Felsefe ve Mantık"],
    "Felsefe ve Mantık": ["Bilim", "Tarih", "Sanat ve Edebiyat", "Popüler Kültür"]
}
OLASI_COMBO_ESLESMELERI = POSSIBLE_COMBO_MATCHES

# Zorluk dereceleri
DIFFICULTIES = ["Kolay", "Orta", "Zor"]

# Geriye dönük uyumluluk için CATEGORIES_DATA
CATEGORIES_DATA = {
    cat: {
        "color": meta.get("color", DEFAULT_FALLBACK_COLOR),
        "sub_categories": list(meta.get("sub_categories", {}).keys()),
        "filters": list(meta.get("sub_categories", {}).keys())
    }
    for cat, meta in CATEGORIES_META.items()
}

CATEGORY_METADATA = CATEGORIES_META

def get_category_color(category_name: str) -> str:
    """Kategoriye ait HEX renk kodunu getirir."""
    if category_name in CATEGORIES_META:
        return CATEGORIES_META[category_name].get("color", DEFAULT_FALLBACK_COLOR)
    
    # Bilinen varyant ve takma adlar
    alias_map = {
        "Felsefe & Mantık": "Felsefe ve Mantık",
        "Fizik": "Bilim",
        "Kimya": "Bilim",
        "Biyoloji": "Bilim",
        "Astronomi & Uzay": "Bilim",
        "Astronomi": "Bilim",
        "Sinema & Dizi": "Popüler Kültür",
        "Sinema": "Popüler Kültür",
        "Müzik": "Popüler Kültür",
        "Video Oyunları": "Popüler Kültür",
        "Oyun & Espor": "Popüler Kültür",
        "Edebiyat": "Sanat ve Edebiyat",
        "Mimarlık & Sanat": "Sanat ve Edebiyat",
        "Mimari": "Sanat ve Edebiyat",
        "Sanat": "Sanat ve Edebiyat",
    }
    canonical = alias_map.get(category_name)
    if canonical and canonical in CATEGORIES_META:
        return CATEGORIES_META[canonical].get("color", DEFAULT_FALLBACK_COLOR)
        
    return DEFAULT_FALLBACK_COLOR

def get_subcategory_image(category_name: str, subcategory_name: str) -> str:
    """Alt kategoriye ait doğrudan görsel linkini getirir."""
    # 1. Doğrudan kategoride ara
    cat_data = CATEGORIES_META.get(category_name, {})
    sub_data = cat_data.get("sub_categories", {})
    if subcategory_name in sub_data:
        return sub_data[subcategory_name]

    # 2. Kategori alias kontrolü
    alias_map = {
        "Felsefe & Mantık": "Felsefe ve Mantık",
        "Fizik": "Bilim",
        "Kimya": "Bilim",
        "Biyoloji": "Bilim",
        "Astronomi & Uzay": "Bilim",
        "Astronomi": "Bilim",
        "Sinema & Dizi": "Popüler Kültür",
        "Sinema": "Popüler Kültür",
        "Müzik": "Popüler Kültür",
        "Video Oyunları": "Popüler Kültür",
        "Oyun & Espor": "Popüler Kültür",
        "Edebiyat": "Sanat ve Edebiyat",
        "Mimarlık & Sanat": "Sanat ve Edebiyat",
        "Mimari": "Sanat ve Edebiyat",
        "Sanat": "Sanat ve Edebiyat",
    }
    canonical_cat = alias_map.get(category_name)
    if canonical_cat and canonical_cat in CATEGORIES_META:
        sub_match = CATEGORIES_META[canonical_cat].get("sub_categories", {}).get(subcategory_name)
        if sub_match:
            return sub_match

    # 3. Tüm kategorilerde alt kategori adını ara
    for c_name, c_meta in CATEGORIES_META.items():
        if subcategory_name in c_meta.get("sub_categories", {}):
            return c_meta["sub_categories"][subcategory_name]

    # 4. Bulunamazsa varsayılan fallback görseli döndür
    return DEFAULT_FALLBACK_IMAGE

