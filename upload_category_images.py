import io
import os
import re
import sys
import logging
import urllib.parse
import urllib.request
from typing import Dict, Tuple

from db_manager import get_storage_bucket
import categories_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# 404 veya erişim hatası veren eski Unsplash linkleri için garantili yüksek çözünürlüklü yedekler
URL_CORRECTIONS: Dict[Tuple[str, str], str] = {
    ("Tarih", "Dünya Savaşları"): "https://images.unsplash.com/photo-1579208575657-c595a05383b7?auto=format&fit=crop&w=800&q=80",
    ("Sanat ve Edebiyat", "Resim ve Heykel"): "https://images.unsplash.com/photo-1544967082-d9d25d867d66?auto=format&fit=crop&w=800&q=80",
    ("Spor", "Futbol"): "https://images.unsplash.com/photo-1579952363873-27f3bade9f55?auto=format&fit=crop&w=800&q=80"
}

def download_image_to_ram(source_url: str, fallback_seed: str) -> bytes:
    """
    Görseli yerel diske kaydetmeden doğrudan RAM'e (memory) indirir.
    İlk deneme başarısız olursa telifsiz Picsum endpoint'ini fallback olarak kullanır.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # 1. Öncelikli URL denemesi
    try:
        req = urllib.request.Request(source_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                data = resp.read()
                if len(data) > 1000:
                    return data
    except Exception as e:
        logging.warning(f"⚠️ Asıl linkten indirme başarısız ({source_url[:60]}...): {e}")

    # 2. Picsum Fallback denemesi
    safe_seed = urllib.parse.quote(fallback_seed)
    picsum_url = f"https://picsum.photos/seed/{safe_seed}/800/600"
    logging.info(f"🔄 Picsum fallback deneniyor: {picsum_url}")
    try:
        req = urllib.request.Request(picsum_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return resp.read()
    except Exception as e:
        logging.error(f"❌ Picsum indirmesi de başarısız: {e}")

    raise RuntimeError(f"Görsel hiçbir kaynaktan indirilemedi: {source_url}")

def upload_to_firebase_storage(bucket, blob_path: str, image_bytes: bytes) -> str:
    """
    RAM'deki görsel verisini Firebase Admin SDK Storage kullanarak belirtilen yola yükler
    ve blob.make_public() çağrısı ile kalıcı public URL üretir.
    """
    blob = bucket.blob(blob_path)
    blob.upload_from_string(image_bytes, content_type="image/jpeg")
    blob.make_public()
    return blob.public_url

def update_categories_config_file(new_meta: dict, new_fallback_image: str):
    """
    categories_config.py dosyasındaki CATEGORIES_META ve DEFAULT_FALLBACK_IMAGE değerlerini
    yeni Firebase Storage URL'leri ile günceller.
    """
    config_path = os.path.join(os.path.dirname(__file__), "categories_config.py")
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    # CATEGORIES_META bloğunu formatla
    meta_lines = ["CATEGORIES_META = {"]
    for cat, data in new_meta.items():
        color = data.get("color", "#6366F1")
        meta_lines.append(f'    "{cat}": {{')
        meta_lines.append(f'        "color": "{color}",')
        meta_lines.append('        "sub_categories": {')
        for sub, url in data.get("sub_categories", {}).items():
            meta_lines.append(f'            "{sub}": "{url}",')
        meta_lines.append('        }')
        meta_lines.append('    },')
    meta_lines.append('}')
    formatted_meta = "\n".join(meta_lines)

    # Regex ile CATEGORIES_META = { ... } bloğunu değiştir
    pattern = r"CATEGORIES_META\s*=\s*\{.*?\n\}"
    content = re.sub(pattern, formatted_meta, content, flags=re.DOTALL)

    # DEFAULT_FALLBACK_IMAGE değerini güncelle
    fallback_pattern = r'DEFAULT_FALLBACK_IMAGE\s*=\s*".*?"'
    content = re.sub(fallback_pattern, f'DEFAULT_FALLBACK_IMAGE = "{new_fallback_image}"', content)

    # CATEGORY_METADATA alias'ı ekle veya güncelle
    if "CATEGORY_METADATA = CATEGORIES_META" not in content:
        content = content.replace(
            'DEFAULT_FALLBACK_COLOR = "#6366F1"',
            'DEFAULT_FALLBACK_COLOR = "#6366F1"\nCATEGORY_METADATA = CATEGORIES_META'
        )

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)

    logging.info("✅ categories_config.py başarıyla güncellendi!")

def main():
    logging.info("🚀 Kategori ve Alt Kategori Görsellerinin Firebase Storage'a Yükleme İşlemi Başlıyor...")
    
    bucket = get_storage_bucket()
    logging.info(f"📦 Hedef Firebase Storage Bucket: {bucket.name}")

    categories_meta = categories_config.CATEGORIES_META
    updated_meta = {}
    total_images = sum(len(d.get("sub_categories", {})) for d in categories_meta.values()) + 1
    current_index = 0

    # 1. Tüm Kategorileri ve Alt Kategorileri İşle
    for cat_name, cat_data in categories_meta.items():
        color = cat_data.get("color", "#6366F1")
        sub_categories = cat_data.get("sub_categories", {})
        new_subs = {}

        for sub_name, original_url in sub_categories.items():
            current_index += 1
            # 404 veren linkler için düzeltilmiş URL'yi seç
            use_url = URL_CORRECTIONS.get((cat_name, sub_name), original_url)
            blob_path = f"categories/{cat_name}/{sub_name}.jpg"

            logging.info(f"[{current_index}/{total_images}] 📥 İndiriliyor: {cat_name} -> {sub_name}")
            img_bytes = download_image_to_ram(use_url, fallback_seed=f"{cat_name}_{sub_name}")

            logging.info(f"    ☁️ Storage'a yükleniyor: {blob_path} ({len(img_bytes)} bytes)")
            public_url = upload_to_firebase_storage(bucket, blob_path, img_bytes)
            new_subs[sub_name] = public_url
            logging.info(f"    ✨ Public URL: {public_url}")

        updated_meta[cat_name] = {
            "color": color,
            "sub_categories": new_subs
        }

    # 2. Varsayılan Fallback Görselini Yükle
    current_index += 1
    fallback_blob_path = "categories/Genel/default_fallback.jpg"
    logging.info(f"[{current_index}/{total_images}] 📥 Fallback görseli indiriliyor...")
    fallback_bytes = download_image_to_ram(categories_config.DEFAULT_FALLBACK_IMAGE, fallback_seed="default_quiz_fallback")
    new_fallback_url = upload_to_firebase_storage(bucket, fallback_blob_path, fallback_bytes)
    logging.info(f"    ✨ Fallback Public URL: {new_fallback_url}")

    # 3. categories_config.py Dosyasını Güncelle
    logging.info("📝 categories_config.py dosyasına yeni linkler yazılıyor...")
    update_categories_config_file(updated_meta, new_fallback_url)

    logging.info("🎉 TÜM KATEGORİ GÖRSELLERİ FIREBASE STORAGE'A BAŞARIYLA YÜKLENDİ VE KONFİGÜRASYON GÜNCELLENDİ!")

if __name__ == "__main__":
    main()
