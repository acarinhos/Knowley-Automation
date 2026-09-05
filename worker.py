import time
import random
import re
import logging
from typing import List, Any

from config.categories_config import (
    get_category_color,
    get_subcategory_image,
)
from core.category_balancer import get_category_balancer
from core.generator import (
    generate_question_with_fallback,
    GeneratedQuestion,
    CategoryMeta,
    SubCategoryMeta,
)
from database.db_manager import init_firebase, generate_question_hash
from firebase_admin import firestore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("worker.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

SLEEP_BETWEEN_REQUESTS = 4.0
MAX_BLOCK_RETRIES = 5

# 35+ Ülke ve Global Havuzu
TARGET_COUNTRIES: List[str] = [
    "Global",
    "TR", "GB", "DE", "FR", "IT", "ES", "PT", "NL", "GR", "SE", "NO", "PL", "RU",
    "US", "CA", "BR", "AR", "MX", "CL", "CO",
    "JP", "KR", "CN", "IN", "AU", "NZ", "ID", "SA", "AE",
    "EG", "ZA", "NG", "MA", "KE", "IE", "BE", "CH", "AT", "AZ"
]

def save_worker_question(db: Any, question_obj: GeneratedQuestion) -> bool:
    try:
        if isinstance(question_obj, dict):
            q_dict = question_obj
        else:
            q_dict = question_obj.model_dump(by_alias=True)
        
        # Firestore'a 'global' anahtarıyla kaydedilmesini garanti et
        if "difficulty_profile" in q_dict and isinstance(q_dict["difficulty_profile"], dict):
            prof = q_dict["difficulty_profile"]
            if "global_scope" in prof and "global" not in prof:
                prof["global"] = prof.pop("global_scope")

        hash_seed = (
            q_dict.get("translations", {}).get("en", {}).get("question")
            or q_dict.get("translations", {}).get("tr", {}).get("question")
            or str(time.time())
        )
        q_hash = generate_question_hash(hash_seed)
        doc_ref = db.collection("questions").document(q_hash)
        
        if doc_ref.get().exists:
            logging.warning(f"Aynı soru veritabanında mevcut, atlandı: {hash_seed[:40]}...")
            return False

        q_dict["question_hash"] = q_hash
        q_dict["created_at"] = firestore.SERVER_TIMESTAMP
        q_dict["status"] = "published"
        q_dict["times_served"] = 0
        q_dict["is_active"] = True
        q_dict["version"] = "1.0.3"
        q_dict.setdefault("scope", "global")
        q_dict.setdefault("target_country", (q_dict.get("countries") or ["Global"])[0])
        q_dict.setdefault("target_country_credit", 10)
        q_dict.setdefault("is_global_eligible", True)
        q_dict["correct_count"] = 0
        q_dict["wrong_count"] = 0
        q_dict["shuffle_key"] = random.random()
        if "correct_option" not in q_dict or not q_dict["correct_option"]:
            q_dict["correct_option"] = q_dict.get("correct_answer")
        if "correct_answer" not in q_dict or not q_dict["correct_answer"]:
            q_dict["correct_answer"] = q_dict.get("correct_option")
        if "duration_local" not in q_dict or "duration_global" not in q_dict or "duration_seconds" not in q_dict:
            from core.timer_calculator import calculate_durations_from_obj
            durations = calculate_durations_from_obj(q_dict)
            q_dict.setdefault("duration_local", durations["duration_local"])
            q_dict.setdefault("duration_global", durations["duration_global"])
            q_dict.setdefault("duration_seconds", durations["duration_seconds"])

        doc_ref.set(q_dict)
        
        prof = q_dict.get("difficulty_profile", {})
        local_scope = prof.get("local", {}) if isinstance(prof.get("local"), dict) else {}
        global_scope = prof.get("global", {}) if isinstance(prof.get("global"), dict) else {}
        local_label = local_scope.get("label", "Orta")
        local_score = local_scope.get("score", 5)
        
        is_glob_elig = q_dict.get("is_global_eligible", True)
        if is_glob_elig and global_scope:
            global_label = global_scope.get("label", "Orta")
            global_score = global_scope.get("score", 5)
            global_str = f"{global_label} ({global_score}/10)"
        else:
            global_str = "Yok"
        
        scope_val = str(q_dict.get("scope", "global")).capitalize()
        focus_country = q_dict.get("target_country", "Global")
        focus_credit = q_dict.get("target_country_credit", 10)

        logging.info(
            f"✅ [v1.0.3] [{'COMBO' if q_dict.get('is_combo') else 'TEK'}] "
            f"🌍 Kapsam: {scope_val} | Odak: {focus_country} (Kredi: {focus_credit}) | "
            f"Global Uygun: {'Evet' if is_glob_elig else 'Hayır'} | "
            f"Local: {local_label} ({local_score}/10) | Global: {global_str}"
        )
        return True
    except Exception as e:
        logging.error(f"Firestore yazma hatası: {e}")
        return False

def extract_retry_delay(err_str: str) -> int:
    """API hata mesajındaki bekleme süresini regex ile okur."""
    match = re.search(r"retry in (\d+\.?\d*)s", err_str)
    return int(float(match.group(1))) + 2 if match else 45

def start_infinite_worker() -> None:
    logging.info("🚀 Soru Üretim Motoru v1.0.3 Başlatıldı!")
    
    db = init_firebase()
    cat_balancer = get_category_balancer(db=db)
    from core.difficulty_balancer import get_difficulty_balancer
    diff_balancer = get_difficulty_balancer()
    from core.generation_planner import get_generation_planner
    gen_planner = get_generation_planner()
    produced_count = 0

    while True:
        block_pattern = [False, False, False, True, True]
        random.shuffle(block_pattern)

        for is_combo in block_pattern:
            target = cat_balancer.get_next_target(is_combo=is_combo)
            primary_cat = target.primary_category
            target_sub = target.target_subcategory
            secondary_cat = target.secondary_category

            for attempt in range(1, MAX_BLOCK_RETRIES + 1):
                try:
                    q_data = generate_question_with_fallback(
                        primary_category=primary_cat,
                        target_subcategory=target_sub,
                        secondary_category=secondary_cat,
                        category_balancer=cat_balancer,
                        difficulty_balancer=diff_balancer,
                        generation_planner=gen_planner
                    )
                    
                    # 1. Ana kategoriler için get_category_color(cat) ile HEX renklerini ekle
                    q_data.categories_meta = [
                        CategoryMeta(name=cat, color=get_category_color(cat))
                        for cat in q_data.categories
                    ]
                    
                    # 2. Alt kategoriler için get_subcategory_image(primary_cat, sub) ile CDN görsel linklerini ekle
                    q_data.sub_categories_meta = [
                        SubCategoryMeta(name=sub, image_url=get_subcategory_image(primary_cat, sub))
                        for sub in q_data.sub_categories
                    ]
                    
                    # 3. Firestore'a meta verileriyle birlikte kaydet
                    if save_worker_question(db, q_data):
                        cat_balancer.record_success(
                            primary_category=primary_cat,
                            subcategory=target_sub,
                            secondary_category=secondary_cat
                        )
                        produced_count += 1
                        logging.info(f"📊 Toplam Başarılı Soru: {produced_count}")
                    break

                except Exception as err:
                    err_str = str(err)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        wait_sec = extract_retry_delay(err_str)
                        logging.warning(f"⏳ Kota aşımı tespit edildi. {wait_sec} saniye bekleniyor...")
                        time.sleep(wait_sec)
                    else:
                        wait_sec = attempt * 5
                        logging.error(f"Hata oluştu ({attempt}/{MAX_BLOCK_RETRIES}): {err}")
                        time.sleep(wait_sec)

            time.sleep(SLEEP_BETWEEN_REQUESTS)

if __name__ == "__main__":
    try:
        start_infinite_worker()
    except KeyboardInterrupt:
        logging.info("🛑 Worker durduruldu.")