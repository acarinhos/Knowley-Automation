CATEGORIES_META = {
    "Bilim": {
        "color": "#2563EB",  # Canlı Mavi
        "sub_categories": {
            "Fizik": "https://images.unsplash.com/photo-1636466497217-26a8cbeaf0aa?auto=format&fit=crop&w=800&q=80",
            "Kimya": "https://images.unsplash.com/photo-1532187863486-abf9dbad1b69?auto=format&fit=crop&w=800&q=80",
            "Biyoloji": "https://images.unsplash.com/photo-1530026405186-ed1f139313f8?auto=format&fit=crop&w=800&q=80",
            "Astronomi": "https://images.unsplash.com/photo-1506703719100-a0f3a48c0f86?auto=format&fit=crop&w=800&q=80",
            "Genetik": "https://images.unsplash.com/photo-1576086213369-97a306d36557?auto=format&fit=crop&w=800&q=80"
        }
    },
    "Tarih": {
        "color": "#D97706",  # Kehribar Sarısı
        "sub_categories": {
            "Antik Çağ": "https://images.unsplash.com/photo-1568605117036-5fe5e7bab0b7?auto=format&fit=crop&w=800&q=80",
            "Orta Çağ": "https://images.unsplash.com/photo-1599707367072-cd6ada2bc375?auto=format&fit=crop&w=800&q=80",
            "Dünya Savaşları": "https://images.unsplash.com/photo-1579965342577-19349479b122?auto=format&fit=crop&w=800&q=80",
            "Osmanlı Tarihi": "https://images.unsplash.com/photo-1541432901042-2d8bd64b4a9b?auto=format&fit=crop&w=800&q=80",
            "Modern Tarih": "https://images.unsplash.com/photo-1461360370896-922624d12aa1?auto=format&fit=crop&w=800&q=80"
        }
    },
    "Coğrafya": {
        "color": "#059669",  # Zümrüt Yeşili
        "sub_categories": {
            "Başkentler ve Ülkeler": "https://images.unsplash.com/photo-1526778548025-fa2f459cd5c1?auto=format&fit=crop&w=800&q=80",
            "Fiziki Coğrafya": "https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?auto=format&fit=crop&w=800&q=80",
            "Harita ve Bayraklar": "https://images.unsplash.com/photo-1527866959252-deab85ef7d1b?auto=format&fit=crop&w=800&q=80",
            "İklim ve Doğa Olayları": "https://images.unsplash.com/photo-1534088568595-a066f410bcda?auto=format&fit=crop&w=800&q=80"
        }
    },
    "Sanat ve Edebiyat": {
        "color": "#9333EA",  # Canlı Mor
        "sub_categories": {
            "Resim ve Heykel": "https://images.unsplash.com/photo-1579783902614-a3fb3927b675?auto=format&fit=crop&w=800&q=80",
            "Dünya Edebiyatı": "https://images.unsplash.com/photo-1495446815901-a7297e633e8d?auto=format&fit=crop&w=800&q=80",
            "Tiyatro ve Sahne": "https://images.unsplash.com/photo-1507676184212-d03ab07a01bf?auto=format&fit=crop&w=800&q=80",
            "Mimari": "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=800&q=80"
        }
    },
    "Spor": {
        "color": "#DC2626",  # Dinamik Kırmızı
        "sub_categories": {
            "Futbol": "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?auto=format&fit=crop&w=800&q=80",
            "Basketbol": "https://images.unsplash.com/photo-1546519638-68e109498ffc?auto=format&fit=crop&w=800&q=80",
            "Olimpiyatlar": "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?auto=format&fit=crop&w=800&q=80",
            "Motor Sporları": "https://images.unsplash.com/photo-1568605117036-5fe5e7bab0b7?auto=format&fit=crop&w=800&q=80",
            "Tenis": "https://images.unsplash.com/photo-1595435934249-5df7ed86e1c0?auto=format&fit=crop&w=800&q=80"
        }
    },
    "Popüler Kültür": {
        "color": "#EC4899",  # Pembe / Fuşya
        "sub_categories": {
            "Sinema": "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?auto=format&fit=crop&w=800&q=80",
            "Müzik": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?auto=format&fit=crop&w=800&q=80",
            "Video Oyunları": "https://images.unsplash.com/photo-1538481199705-c710c4e965fc?auto=format&fit=crop&w=800&q=80",
            "Diziler ve TV": "https://images.unsplash.com/photo-1522869635100-9f4c5e86aa37?auto=format&fit=crop&w=800&q=80"
        }
    },
    "Felsefe ve Mantık": {
        "color": "#475569",  # Gri / Arduvaz
        "sub_categories": {
            "Ahlak Felsefesi": "https://images.unsplash.com/photo-1505664194779-8beaceb93744?auto=format&fit=crop&w=800&q=80",
            "Mantık ve Akıl Yürütme": "https://images.unsplash.com/photo-1529699211952-734e80c4d42b?auto=format&fit=crop&w=800&q=80",
            "Epistemoloji": "https://images.unsplash.com/photo-1457369804613-52c61a468e7d?auto=format&fit=crop&w=800&q=80"
        }
    }
}

DEFAULT_FALLBACK_IMAGE = "https://images.unsplash.com/photo-1606326608606-aa0b62935f2b?auto=format&fit=crop&w=800&q=80"
DEFAULT_FALLBACK_COLOR = "#6366F1"

def get_category_color(category_name: str) -> str:
    """Kategoriye ait HEX renk kodunu getirir."""
    return CATEGORIES_META.get(category_name, {}).get("color", DEFAULT_FALLBACK_COLOR)

def get_subcategory_image(category_name: str, subcategory_name: str) -> str:
    """Alt kategoriye ait doğrudan görsel linkini getirir."""
    cat_data = CATEGORIES_META.get(category_name, {})
    sub_data = cat_data.get("sub_categories", {})
    return sub_data.get(subcategory_name, DEFAULT_FALLBACK_IMAGE)