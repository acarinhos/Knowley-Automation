import logging
from db_manager import init_firebase
from categories_config import get_category_color, get_subcategory_image

# Loglama yapılandırması
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def sync_existing_questions_metadata():
    """
    Firestore'daki tüm mevcut soruları tarar ve categories_config.py dosyasındaki 
    güncel renk ve görsel URL'leri ile senkronize eder (Batch update).
    """
    logging.info("🔄 Firestore Soru Meta Veri Senkronizasyonu Başlatılıyor...")
    
    db = init_firebase()
    questions_ref = db.collection("questions")
    docs = questions_ref.stream()

    batch = db.batch()
    updated_count = 0
    total_scanned = 0
    BATCH_LIMIT = 450  # Firestore batch limiti 500'dür, güvenli limit 450

    for doc in docs:
        total_scanned += 1
        data = doc.to_dict()
        doc_id = doc.id
        
        categories = data.get("categories", [])
        sub_categories = data.get("sub_categories", [])
        
        # 1. Güncel Kategori Renklerini Oluştur
        updated_categories_meta = []
        for cat in categories:
            updated_categories_meta.append({
                "name": cat,
                "color": get_category_color(cat)
            })
            
        # 2. Güncel Alt Kategori Görsellerini Oluştur
        updated_subcategories_meta = []
        primary_cat = categories[0] if categories else "Genel"
        for sub in sub_categories:
            updated_subcategories_meta.append({
                "name": sub,
                "image_url": get_subcategory_image(primary_cat, sub)
            })
            
        # Mevcut verilerle fark var mı kontrol et (Gereksiz yazmayı önler)
        current_cats_meta = data.get("categories_meta", [])
        current_subs_meta = data.get("sub_categories_meta", [])
        
        if (current_cats_meta != updated_categories_meta) or (current_subs_meta != updated_subcategories_meta):
            doc_ref = questions_ref.document(doc_id)
            batch.update(doc_ref, {
                "categories_meta": updated_categories_meta,
                "sub_categories_meta": updated_subcategories_meta
            })
            updated_count += 1
            
            # Firestore batch sınırına ulaşıldığında yaz ve yeni batch aç
            if updated_count % BATCH_LIMIT == 0:
                batch.commit()
                logging.info(f"💾 {updated_count} adet soru güncellendi ve veritabanına yazıldı...")
                batch = db.batch()

    # Kalan son batch'i kaydet
    if updated_count % BATCH_LIMIT != 0:
        batch.commit()

    logging.info(f"🎯 Senkronizasyon Tamamlandı!")
    logging.info(f"📊 Toplam Taranan Soru: {total_scanned}")
    logging.info(f"✨ Güncellenen Soru Sayısı: {updated_count}")

if __name__ == "__main__":
    sync_existing_questions_metadata()