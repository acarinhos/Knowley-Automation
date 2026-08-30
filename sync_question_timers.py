import sys
import logging
from collections import Counter
from typing import Dict, Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from db_manager import init_firebase
from timer_calculator import calculate_durations_from_obj

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def sync_question_timers() -> None:
    """
    Firestore'daki tüm mevcut soruları tarar ve metin uzunluğu ile yerel/küresel zorluk
    seviyelerine göre hesaplanan dinamik süreleri (duration_local, duration_global, duration_seconds)
    dokümanlara yazar (Batch update).
    """
    logging.info("🔄 Firestore Soru Dinamik Süre (Local/Global Timer) Senkronizasyonu Başlatılıyor...")

    db = init_firebase()
    questions_ref = db.collection("questions")
    docs = questions_ref.stream()

    batch = db.batch()
    updated_count = 0
    total_scanned = 0
    local_stats = Counter()
    global_stats = Counter()
    diff_count = 0
    BATCH_LIMIT = 450  # Firestore batch sınırı 500'dür

    for doc in docs:
        total_scanned += 1
        data = doc.to_dict()
        doc_id = doc.id

        durations = calculate_durations_from_obj(data)
        dur_local = durations["duration_local"]
        dur_global = durations["duration_global"]
        dur_seconds = durations["duration_seconds"]

        local_stats[dur_local] += 1
        global_stats[dur_global] += 1

        if dur_local != dur_global:
            diff_count += 1

        current_local = data.get("duration_local")
        current_global = data.get("duration_global")
        current_seconds = data.get("duration_seconds")
        current_option = data.get("correct_option")
        correct_answer = data.get("correct_answer")

        # Eğer süreler veya correct_option henüz eklenmemişse veya farklıysa güncelle
        needs_update = (
            current_local != dur_local or 
            current_global != dur_global or 
            current_seconds != dur_seconds or
            (current_option is None and correct_answer is not None)
        )

        if needs_update:
            doc_ref = questions_ref.document(doc_id)
            update_payload = {
                "duration_local": dur_local,
                "duration_global": dur_global,
                "duration_seconds": dur_seconds,
            }
            if current_option is None and correct_answer is not None:
                update_payload["correct_option"] = correct_answer

            batch.update(doc_ref, update_payload)
            updated_count += 1

            if updated_count % BATCH_LIMIT == 0:
                batch.commit()
                logging.info(f"💾 {updated_count} adet soru güncellendi ve veritabanına yazıldı...")
                batch = db.batch()

    # Kalan son batch'i kaydet
    if updated_count % BATCH_LIMIT != 0:
        batch.commit()

    logging.info("=" * 60)
    logging.info("🎯 Yerel & Küresel Süre Senkronizasyonu Tamamlandı!")
    logging.info(f"📊 Toplam Taranan Soru  : {total_scanned}")
    logging.info(f"✨ Güncellenen Soru Sayısı: {updated_count}")
    logging.info(f"⚡ Yerel/Global Süresi Farklı Olan Soru Sayısı: {diff_count}")
    logging.info("-" * 60)
    logging.info("⏱️  Yerel Süre (duration_local) Dağılımı:")
    for dur in sorted(local_stats.keys()):
        count = local_stats[dur]
        pct = (count / total_scanned * 100) if total_scanned > 0 else 0
        logging.info(f"   - {dur:2d} saniye: {count:3d} soru (%{pct:.1f})")
    logging.info("-" * 60)
    logging.info("🌍  Küresel Süre (duration_global) Dağılımı:")
    for dur in sorted(global_stats.keys()):
        count = global_stats[dur]
        pct = (count / total_scanned * 100) if total_scanned > 0 else 0
        logging.info(f"   - {dur:2d} saniye: {count:3d} soru (%{pct:.1f})")
    logging.info("=" * 60)


if __name__ == "__main__":
    sync_question_timers()
