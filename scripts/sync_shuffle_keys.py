"""
Knowley Firestore Soru Shuffle Key Senkronizasyonu
Tüm mevcut soruları tarar ve 'shuffle_key' (0.0 - 1.0 arası rastgele float)
alanı bulunmayan sorulara bu anahtarı batch update ile ekler.
"""

import sys
import random
import logging

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db_manager import init_firebase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def sync_questions_shuffle_keys(force_regenerate: bool = False):
    """
    Firestore'daki 'questions' koleksiyonundaki tüm sorulara
    rastgele sorgulama ve istemci karıştırması için 'shuffle_key' atar.
    
    :param force_regenerate: True ise var olan shuffle_key'leri de yeniden üretir.
    """
    logging.info("🔄 Firestore Soru Shuffle Key Senkronizasyonu Başlatılıyor...")
    
    db = init_firebase()
    questions_ref = db.collection("questions")
    docs = questions_ref.stream()

    batch = db.batch()
    updated_count = 0
    already_has_count = 0
    total_scanned = 0
    BATCH_LIMIT = 450  # Firestore batch limiti 500'dür

    for doc in docs:
        total_scanned += 1
        data = doc.to_dict()
        doc_id = doc.id
        
        current_shuffle_key = data.get("shuffle_key")
        
        needs_update = False
        if force_regenerate or current_shuffle_key is None:
            needs_update = True
        elif not isinstance(current_shuffle_key, (int, float)):
            needs_update = True

        if needs_update:
            new_shuffle_key = round(random.uniform(0.000001, 0.999999), 6)
            doc_ref = questions_ref.document(doc_id)
            batch.update(doc_ref, {"shuffle_key": new_shuffle_key})
            updated_count += 1
            
            if updated_count % BATCH_LIMIT == 0:
                batch.commit()
                logging.info(f"💾 {updated_count} adet soruya shuffle_key yazıldı...")
                batch = db.batch()
        else:
            already_has_count += 1

    # Kalan batch'i kaydet
    if updated_count % BATCH_LIMIT != 0:
        batch.commit()

    logging.info("=" * 50)
    logging.info("🎉 SHUFFLE KEY SENKRONİZASYONU TAMAMLANDI!")
    logging.info(f"📊 Toplam Taranan Soru : {total_scanned}")
    logging.info(f"✨ Güncellenen Soru Sayısı: {updated_count}")
    logging.info(f"✅ Zaten Anahtarı Olan   : {already_has_count}")
    logging.info("=" * 50)


def verify_shuffle_keys():
    """Tüm soruların shuffle_key'e sahip olduğunu doğrular ve dağılım istatistiği çıkarır."""
    logging.info("🔍 Firestore 'shuffle_key' Alanları Doğrulanıyor...")
    db = init_firebase()
    docs = db.collection("questions").stream()

    total = 0
    with_key = 0
    missing_key = 0
    sample_keys = []

    for doc in docs:
        total += 1
        sk = doc.to_dict().get("shuffle_key")
        if sk is not None and isinstance(sk, (int, float)):
            with_key += 1
            if len(sample_keys) < 5:
                sample_keys.append(sk)
        else:
            missing_key += 1

    print("\n" + "=" * 50)
    print(" 🎲 SHUFFLE KEY KONTROL RAPORU")
    print("=" * 50)
    print(f"Toplam Soru       : {total}")
    print(f"Anahtarı Olan     : {with_key} (%{(with_key/total*100) if total else 0:.2f})")
    print(f"Eksik Olan        : {missing_key}")
    print(f"Örnek Anahtarlar  : {sample_keys}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    sync_questions_shuffle_keys()
    verify_shuffle_keys()
