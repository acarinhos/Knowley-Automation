# ==============================================================================
# 20 ANA KATEGORİ VE ALT DALLAR KONFİGÜRASYONU
# Kullanıcının resmi şemasına (HEX renkleri, alt dallar, odak filtreleri) %100 uyumludur.
# ==============================================================================

CATEGORIES_META = {
    "Tarih": {
        "color": "#8B4513",
        "filters": ["İlk Çağ", "Orta Çağ", "Osmanlı Dönemi", "20. Yüzyıl", "Cumhuriyet", "Soğuk Savaş"],
        "sub_categories": {
            "Siyasi Tarih": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/Modern%20Tarih.jpg",
            "Askeri Tarih": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/D%C3%BCnya%20Sava%C5%9Flar%C4%B1.jpg",
            "Kültürel Tarih": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/Orta%20%C3%87a%C4%9F.jpg",
            "Antlaşmalar": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/Osmanl%C4%B1%20Tarihi.jpg",
            "Arkeoloji": "https://storage.googleapis.com/knowley-1-categories/categories/Tarih/Antik%20%C3%87a%C4%9F.jpg",
        }
    },
    "Coğrafya": {
        "color": "#2E8B57",
        "filters": ["Türkiye", "Akdeniz Havzası", "Dünya Coğrafyası", "Kıtalar & Okyanuslar"],
        "sub_categories": {
            "Fiziki": "https://storage.googleapis.com/knowley-1-categories/categories/Co%C4%9Frafya/Fiziki%20Co%C4%9Frafya.jpg",
            "Beşeri": "https://storage.googleapis.com/knowley-1-categories/categories/Co%C4%9Frafya/Ba%C5%9Fkentler%20ve%20%C3%9Clkeler.jpg",
            "Ekonomik Coğrafya": "https://storage.googleapis.com/knowley-1-categories/categories/Co%C4%9Frafya/Harita%20ve%20Bayraklar.jpg",
            "Jeopolitik": "https://storage.googleapis.com/knowley-1-categories/categories/Co%C4%9Frafya/%C4%B0klim%20ve%20Do%C4%9Fa%20Olaylar%C4%B1.jpg",
        }
    },
    "Spor": {
        "color": "#16A085",
        "filters": ["Şampiyonlar Ligi", "Dünya Kupası", "NBA", "EuroLeague", "Olimpiyatlar", "Grand Slam"],
        "sub_categories": {
            "Futbol": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Futbol.jpg",
            "Basketbol": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Basketbol.jpg",
            "Voleybol": "https://images.unsplash.com/photo-1612872087720-bb876e2e67d1?auto=format&fit=crop&w=800&q=80",
            "Formula 1": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Motor%20Sporlar%C4%B1.jpg",
            "Tenis": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Tenis.jpg",
            "Yüzme": "https://images.unsplash.com/photo-1530549387789-4c1017266635?auto=format&fit=crop&w=800&q=80",
            "Atletizm": "https://storage.googleapis.com/knowley-1-categories/categories/Spor/Olimpiyatlar.jpg",
        }
    },
    "Fizik": {
        "color": "#1E90FF",
        "filters": ["Temel Kavramlar", "Formül & Yasalar", "Uygulamalı Fizik"],
        "sub_categories": {
            "Mekanik": "https://storage.googleapis.com/knowley-1-categories/categories/Bilim/Fizik.jpg",
            "Termodinamik": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=800&q=80",
            "Optik": "https://images.unsplash.com/photo-1507499739999-097706ad8914?auto=format&fit=crop&w=800&q=80",
            "Elektromanyetizma": "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=800&q=80",
            "Kuantum": "https://images.unsplash.com/photo-1636466497217-26a8cbeaf0aa?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Kimya": {
        "color": "#00CED1",
        "filters": ["Periyodik Tablo", "Asit-Baz", "Kimyasal Bağlar", "Termokimya"],
        "sub_categories": {
            "Organik": "https://images.unsplash.com/photo-1532187863486-abf9dbad1b69?auto=format&fit=crop&w=800&q=80",
            "Anorganik": "https://storage.googleapis.com/knowley-1-categories/categories/Bilim/Kimya.jpg",
            "Fizikokimya": "https://images.unsplash.com/photo-1603126857599-f6e157fa2fe6?auto=format&fit=crop&w=800&q=80",
            "Biyokimya": "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?auto=format&fit=crop&w=800&q=80",
            "Analitik Kimya": "https://images.unsplash.com/photo-1579154204601-01588f351e67?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Biyoloji": {
        "color": "#27AE60",
        "filters": ["Sistemler", "Bitki Biyolojisi", "Moleküler Biyoloji"],
        "sub_categories": {
            "Genetik": "https://storage.googleapis.com/knowley-1-categories/categories/Bilim/Genetik.jpg",
            "Hücre": "https://images.unsplash.com/photo-1576086213369-97a306d36557?auto=format&fit=crop&w=800&q=80",
            "İnsan Fizyolojisi": "https://images.unsplash.com/photo-1530026405186-ed1f139313f8?auto=format&fit=crop&w=800&q=80",
            "Ekoloji": "https://images.unsplash.com/photo-1511497584788-87676104235f?auto=format&fit=crop&w=800&q=80",
            "Evrim": "https://images.unsplash.com/photo-1474511320723-9a56873867b5?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Ekonomi & Finans": {
        "color": "#2980B9",
        "filters": ["Para Politikası", "Piyasa Teorileri", "Finansal Okuryazarlık"],
        "sub_categories": {
            "Makroekonomi": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=800&q=80",
            "Mikroekonomi": "https://images.unsplash.com/photo-1526304640581-d334cdbbf45e?auto=format&fit=crop&w=800&q=80",
            "Borsa & Yatırım": "https://images.unsplash.com/photo-1590283603385-17ffb3a7f29f?auto=format&fit=crop&w=800&q=80",
            "Kripto & Fintech": "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Edebiyat": {
        "color": "#D35400",
        "filters": ["Tanzimat/Servet-i Fünun", "Cumhuriyet", "Rönesans", "Modern Edebiyat"],
        "sub_categories": {
            "Türk Edebiyatı": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/T%C3%BCrk%20Edebiyat%C4%B1.jpg",
            "Dünya Edebiyatı": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/D%C3%BCnya%20Edebiyat%C4%B1.jpg",
            "Şiir": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/%C5%9Eiir%20ve%20%C5%9Eairler.jpg",
            "Tiyatro & Drama": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/Tiyatro%20ve%20Sahne.jpg",
            "Mitolojik Metinler": "https://images.unsplash.com/photo-1461360370896-922624d12aa1?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Felsefe & Mantık": {
        "color": "#34495E",
        "filters": ["Antik Yunan", "Aydınlanma Dönemi", "Varoluşçuluk", "Mantıksal Çıkarım"],
        "sub_categories": {
            "Epistemoloji": "https://storage.googleapis.com/knowley-1-categories/categories/Felsefe/Epistemoloji.jpg",
            "Etik": "https://storage.googleapis.com/knowley-1-categories/categories/Felsefe/Ahlak%20Felsefesi.jpg",
            "Varlık Felsefesi": "https://images.unsplash.com/photo-1509062522246-3755977927d7?auto=format&fit=crop&w=800&q=80",
            "Klasik Mantık": "https://storage.googleapis.com/knowley-1-categories/categories/Felsefe/Mant%C4%B1k%20ve%20Ak%C4%B1l%20Y%C3%BCr%C3%BCtme.jpg",
            "Dil Felsefesi": "https://images.unsplash.com/photo-1544716278-ca5e3f4abd8c?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Sinema & Dizi": {
        "color": "#E74C3C",
        "filters": ["Oscar & Festivaller", "Bilim Kurgu & Fantastik", "Sinema İlkleri"],
        "sub_categories": {
            "Yönetmen Sineması": "https://storage.googleapis.com/knowley-1-categories/categories/Pop%C3%BCler%20K%C3%BClt%C3%BCr/Sinema.jpg",
            "Gişe": "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?auto=format&fit=crop&w=800&q=80",
            "Animasyon": "https://images.unsplash.com/photo-1534447677768-be436bb09401?auto=format&fit=crop&w=800&q=80",
            "Kült Diziler": "https://storage.googleapis.com/knowley-1-categories/categories/Pop%C3%BCler%20K%C3%BClt%C3%BCr/Diziler%20ve%20TV.jpg",
            "Belgesel": "https://images.unsplash.com/photo-1518173946687-a4c8a383392e?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Müzik": {
        "color": "#9B59B6",
        "filters": ["Barok & Romantik", "80'ler & 90'lar", "Müzik Teorisi & Enstrümanlar"],
        "sub_categories": {
            "Klasik": "https://images.unsplash.com/photo-1520523839898-50712825e3a7?auto=format&fit=crop&w=800&q=80",
            "Rock & Metal": "https://images.unsplash.com/photo-1498038432885-c6f3f1b912ee?auto=format&fit=crop&w=800&q=80",
            "Caz & Blues": "https://images.unsplash.com/photo-1511192336575-5a79af67a629?auto=format&fit=crop&w=800&q=80",
            "Türk Sanat/Halk Müziği": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?auto=format&fit=crop&w=800&q=80",
            "Pop & Hip-Hop": "https://storage.googleapis.com/knowley-1-categories/categories/Pop%C3%BCler%20K%C3%BClt%C3%BCr/M%C3%BCzik.jpg",
        }
    },
    "Genel Kültür & Mitoloji": {
        "color": "#8E44AD",
        "filters": ["Efsaneler & Kahramanlar", "Tanrılar", "Tarihi Keşifler"],
        "sub_categories": {
            "Yunan": "https://images.unsplash.com/photo-1564507592333-c60657eea523?auto=format&fit=crop&w=800&q=80",
            "İskandinav": "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=800&q=80",
            "Mısır/Doğu Mitolojisi": "https://images.unsplash.com/photo-1503152394-c571994fd383?auto=format&fit=crop&w=800&q=80",
            "Rekorlar": "https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?auto=format&fit=crop&w=800&q=80",
            "İlginç Bilgiler": "https://images.unsplash.com/photo-1456513080510-7bf3a84b82f8?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Bilgisayar & Yazılım": {
        "color": "#0055FF",
        "filters": ["Python/Backend", "SQL/NoSQL", "Ağ Protokolleri", "Yapay Zeka & ML"],
        "sub_categories": {
            "Algoritmalar": "https://images.unsplash.com/photo-1516116211227-bbc13c6041e0?auto=format&fit=crop&w=800&q=80",
            "Web Geliştirme": "https://images.unsplash.com/photo-1498050108023-c5249f4df085?auto=format&fit=crop&w=800&q=80",
            "Veritabanları": "https://images.unsplash.com/photo-1544383835-bda2bc66a55d?auto=format&fit=crop&w=800&q=80",
            "Siber Güvenlik": "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=800&q=80",
            "Yapay Zeka": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Tıp & Sağlık": {
        "color": "#E67E22",
        "filters": ["Halk Sağlığı", "İnsan Anatomisi", "Vitaminler & Hormonlar"],
        "sub_categories": {
            "İlk Yardım": "https://images.unsplash.com/photo-1584515979956-d9f6e5d09982?auto=format&fit=crop&w=800&q=80",
            "Beslenme & Diyet": "https://images.unsplash.com/photo-1498837167922-ddd27525d352?auto=format&fit=crop&w=800&q=80",
            "Farmakoloji": "https://images.unsplash.com/photo-1471864190281-a93a3070b6de?auto=format&fit=crop&w=800&q=80",
            "Tıp Tarihi": "https://images.unsplash.com/photo-1532938911079-1b06ac7ceec7?auto=format&fit=crop&w=800&q=80",
            "Hastalıklar": "https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Sosyoloji & Psikoloji": {
        "color": "#1ABC9C",
        "filters": ["Ünlü Psikoloji Deneyleri", "Toplumsal Kuramlar", "Kişilik Kuramları"],
        "sub_categories": {
            "Bilişsel": "https://images.unsplash.com/photo-1507679799987-c73779587ccf?auto=format&fit=crop&w=800&q=80",
            "Sosyal": "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?auto=format&fit=crop&w=800&q=80",
            "Gelişim Psikolojisi": "https://images.unsplash.com/photo-1485546246426-74dc88dec4d9?auto=format&fit=crop&w=800&q=80",
            "Toplumbilim": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=800&q=80",
            "Davranış Bilimleri": "https://images.unsplash.com/photo-1499209974431-9dddcece7f88?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Astronomi & Uzay": {
        "color": "#0B3C5D",
        "filters": ["NASA/ESA/SpaceX", "Gezegen Bilimi", "Derin Uzay"],
        "sub_categories": {
            "Güneş Sistemi": "https://images.unsplash.com/photo-1614728894747-a83421e2b9c9?auto=format&fit=crop&w=800&q=80",
            "Yıldızlar & Karadelikler": "https://images.unsplash.com/photo-1506703719100-a0f3a48c0f86?auto=format&fit=crop&w=800&q=80",
            "Uzay Görevleri": "https://images.unsplash.com/photo-1517976487502-86f376f9d936?auto=format&fit=crop&w=800&q=80",
            "Astrofizik": "https://storage.googleapis.com/knowley-1-categories/categories/Bilim/Astronomi.jpg",
        }
    },
    "Hukuk & Siyaset": {
        "color": "#C0392B",
        "filters": ["Temel Hak ve Hürriyetler", "Devlet Sistemleri", "Diplomasi"],
        "sub_categories": {
            "Anayasa": "https://images.unsplash.com/photo-1589829545856-d10d557cf95f?auto=format&fit=crop&w=800&q=80",
            "Ceza Hukuku": "https://images.unsplash.com/photo-1453733190028-57a68841e40c?auto=format&fit=crop&w=800&q=80",
            "İnsan Hakları": "https://images.unsplash.com/photo-1469571486292-0ba58a3f068b?auto=format&fit=crop&w=800&q=80",
            "Uluslararası İlişkiler": "https://images.unsplash.com/photo-1541872703-74c5e44368f9?auto=format&fit=crop&w=800&q=80",
            "Siyaset Bilimi": "https://images.unsplash.com/photo-1540910419892-4a36d2c3266c?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Oyun & Espor": {
        "color": "#7D3C98",
        "filters": ["Lore (Hikaye)", "Şampiyonlar & Finaller", "Oyun Mekanikleri"],
        "sub_categories": {
            "RPG & Macera": "https://images.unsplash.com/photo-1538481199705-c710c4e965fc?auto=format&fit=crop&w=800&q=80",
            "FPS & Rekabetçi": "https://images.unsplash.com/photo-1542751371-adc38448a05e?auto=format&fit=crop&w=800&q=80",
            "Strateji": "https://images.unsplash.com/photo-1612287233207-674338e55e09?auto=format&fit=crop&w=800&q=80",
            "Espor Turnuvaları": "https://images.unsplash.com/photo-1511512578047-dfb367046420?auto=format&fit=crop&w=800&q=80",
            "Oyun Tarihi": "https://storage.googleapis.com/knowley-1-categories/categories/Pop%C3%BCler%20K%C3%BClt%C3%BCr/Video%20Oyunlar%C4%B1.jpg",
        }
    },
    "Gastronomi & Mutfak": {
        "color": "#D9534F",
        "filters": ["İtalyan, Asya, Türk Mutfağı; Gıda Kimyası & Teknikler"],
        "sub_categories": {
            "Dünya Mutfakları": "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?auto=format&fit=crop&w=800&q=80",
            "Geleneksel Türk Mutfağı": "https://images.unsplash.com/photo-1541544741938-0af808871cc0?auto=format&fit=crop&w=800&q=80",
            "Pişirme Teknikleri": "https://images.unsplash.com/photo-1556910103-1c02745aae4d?auto=format&fit=crop&w=800&q=80",
            "İçecek Kültürü": "https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?auto=format&fit=crop&w=800&q=80",
        }
    },
    "Mimarlık & Sanat": {
        "color": "#BA4A00",
        "filters": ["İkonik Yapılar", "Ünlü Eserler/Ressamlar", "Sanat Akımları"],
        "sub_categories": {
            "Mimari Akımlar": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/Mimari.jpg",
            "Rönesans/Barok": "https://images.unsplash.com/photo-1579783902614-a3fb3927b675?auto=format&fit=crop&w=800&q=80",
            "Heykel": "https://storage.googleapis.com/knowley-1-categories/categories/Sanat%20ve%20Edebiyat/Resim%20ve%20Heykel.jpg",
            "Modern Sanat": "https://images.unsplash.com/photo-1547891654-e66ed7ebb968?auto=format&fit=crop&w=800&q=80",
            "Şehir Planlama": "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=800&q=80",
        }
    }
}

DEFAULT_FALLBACK_IMAGE = "https://storage.googleapis.com/knowley-1-categories/categories/Genel/default_fallback.jpg"
DEFAULT_FALLBACK_COLOR = "#6366F1"

# 20 Ana Kategori Arasında Olası Combo Eşleşmeleri Tablosu
POSSIBLE_COMBO_MATCHES = {
    "Tarih": ["Hukuk & Siyaset", "Coğrafya", "Edebiyat", "Mimarlık & Sanat", "Genel Kültür & Mitoloji", "Felsefe & Mantık", "Ekonomi & Finans"],
    "Coğrafya": ["Tarih", "Genel Kültür & Mitoloji", "Ekonomi & Finans", "Gastronomi & Mutfak", "Spor"],
    "Spor": ["Sinema & Dizi", "Tarih", "Genel Kültür & Mitoloji", "Coğrafya", "Oyun & Espor"],
    "Fizik": ["Astronomi & Uzay", "Kimya", "Bilgisayar & Yazılım", "Felsefe & Mantık"],
    "Kimya": ["Biyoloji", "Fizik", "Tıp & Sağlık", "Gastronomi & Mutfak"],
    "Biyoloji": ["Tıp & Sağlık", "Kimya", "Sosyoloji & Psikoloji", "Coğrafya"],
    "Ekonomi & Finans": ["Hukuk & Siyaset", "Tarih", "Bilgisayar & Yazılım", "Coğrafya", "Sosyoloji & Psikoloji"],
    "Edebiyat": ["Tarih", "Felsefe & Mantık", "Mimarlık & Sanat", "Genel Kültür & Mitoloji", "Sinema & Dizi"],
    "Felsefe & Mantık": ["Hukuk & Siyaset", "Sosyoloji & Psikoloji", "Tarih", "Edebiyat", "Fizik"],
    "Sinema & Dizi": ["Müzik", "Edebiyat", "Oyun & Espor", "Tarih", "Sosyoloji & Psikoloji"],
    "Müzik": ["Sinema & Dizi", "Mimarlık & Sanat", "Tarih", "Genel Kültür & Mitoloji"],
    "Genel Kültür & Mitoloji": ["Tarih", "Edebiyat", "Mimarlık & Sanat", "Coğrafya", "Gastronomi & Mutfak"],
    "Bilgisayar & Yazılım": ["Oyun & Espor", "Fizik", "Ekonomi & Finans", "Tıp & Sağlık"],
    "Tıp & Sağlık": ["Biyoloji", "Kimya", "Sosyoloji & Psikoloji", "Spor"],
    "Sosyoloji & Psikoloji": ["Felsefe & Mantık", "Hukuk & Siyaset", "Tarih", "Tıp & Sağlık", "Edebiyat"],
    "Astronomi & Uzay": ["Fizik", "Genel Kültür & Mitoloji", "Bilgisayar & Yazılım", "Coğrafya"],
    "Hukuk & Siyaset": ["Tarih", "Felsefe & Mantık", "Sosyoloji & Psikoloji", "Ekonomi & Finans"],
    "Oyun & Espor": ["Bilgisayar & Yazılım", "Sinema & Dizi", "Spor", "Genel Kültür & Mitoloji"],
    "Gastronomi & Mutfak": ["Coğrafya", "Tarih", "Kimya", "Genel Kültür & Mitoloji"],
    "Mimarlık & Sanat": ["Tarih", "Edebiyat", "Fizik", "Coğrafya", "Genel Kültür & Mitoloji"]
}
OLASI_COMBO_ESLESMELERI = POSSIBLE_COMBO_MATCHES

# Zorluk dereceleri
DIFFICULTIES = ["Kolay", "Orta", "Zor"]

# Geriye dönük uyumluluk için CATEGORIES_DATA
CATEGORIES_DATA = {
    cat: {
        "color": meta.get("color", DEFAULT_FALLBACK_COLOR),
        "sub_categories": list(meta.get("sub_categories", {}).keys()),
        "filters": list(meta.get("filters", []))
    }
    for cat, meta in CATEGORIES_META.items()
}

CATEGORY_METADATA = CATEGORIES_META

# Eski veya alternatif kategori isimlerini 20 standart forma eşleme
CATEGORY_ALIASES = {
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

def get_category_color(category_name: str) -> str:
    """Kategoriye ait HEX renk kodunu getirir."""
    if category_name in CATEGORIES_META:
        return CATEGORIES_META[category_name].get("color", DEFAULT_FALLBACK_COLOR)
    
    canonical = CATEGORY_ALIASES.get(category_name)
    if canonical and canonical in CATEGORIES_META:
        return CATEGORIES_META[canonical].get("color", DEFAULT_FALLBACK_COLOR)
        
    return DEFAULT_FALLBACK_COLOR

def get_category_filters(category_name: str) -> list:
    """Kategoriye ait odak filtrelerini (filters) getirir."""
    if category_name in CATEGORIES_META:
        return CATEGORIES_META[category_name].get("filters", [])
    
    canonical = CATEGORY_ALIASES.get(category_name)
    if canonical and canonical in CATEGORIES_META:
        return CATEGORIES_META[canonical].get("filters", [])
        
    return []

def get_subcategory_image(category_name: str, subcategory_name: str) -> str:
    """Alt kategoriye ait doğrudan görsel linkini getirir."""
    # 1. Doğrudan kategoride ara
    cat_data = CATEGORIES_META.get(category_name, {})
    sub_data = cat_data.get("sub_categories", {})
    if subcategory_name in sub_data:
        return sub_data[subcategory_name]

    # 2. Kategori alias kontrolü
    canonical_cat = CATEGORY_ALIASES.get(category_name)
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
