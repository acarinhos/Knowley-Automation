import os
import random
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore, storage

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

def init_firebase():
    """Firebase uygulamasını başlatır."""
    if not firebase_admin._apps:
        cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH")
        if cred_path:
            if not os.path.exists(cred_path):
                raise FileNotFoundError(f"Firebase kimlik dosyası bulunamadı: {cred_path}")
            cred = credentials.Certificate(cred_path)
        else:
            # Cloud Run/Functions service identities provide Application Default
            # Credentials; local desktop runs can keep using the JSON file above.
            cred = credentials.ApplicationDefault()

        storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET", "knowley-1-categories")
        firebase_admin.initialize_app(cred, {
            "storageBucket": storage_bucket
        })
    return firestore.client()

def get_storage_bucket(bucket_name: str = None):
    """Firebase Storage bucket nesnesini döndürür."""
    init_firebase()
    target_bucket = bucket_name or os.getenv("FIREBASE_STORAGE_BUCKET", "knowley-1-categories")
    return storage.bucket(target_bucket)

def generate_question_hash(question_text: str) -> str:
    """Soru metninden benzersiz bir hash üretir (Tekrar kontrolü için)."""
    clean_text = question_text.strip().lower()
    return hashlib.sha256(clean_text.encode('utf-8')).hexdigest()

def save_question_to_firestore(db, question_obj, collection_name: str = "questions") -> bool:
    """
    Pydantic GeneratedQuestion nesnesini belirtilen Firestore koleksiyonuna kaydeder.
    Eğer soru daha önce eklenmişse kaydetmez.
    """
    try:
        collection_name = str(collection_name or "questions").strip()
        if not collection_name or "/" in collection_name:
            raise ValueError("Geçersiz Firestore koleksiyon adı.")
        if isinstance(question_obj, dict):
            q_dict = dict(question_obj)
        else:
            q_dict = question_obj.model_dump(by_alias=True)
        if q_dict.get("is_visual_question") and not str(q_dict.get("question_image_url") or "").strip():
            raise ValueError("Görsel soru question_image_url olmadan Firestore'a kaydedilemez.")
        if q_dict.get("is_visual_question") and isinstance(q_dict.get("payload"), dict):
            q_dict["payload"] = dict(q_dict["payload"])
            q_dict["payload"]["question_image_url"] = q_dict["question_image_url"]
            q_dict["payload"]["question_image_search_query"] = None

        # Yazma sınırında rengi yeniden hesapla: LLM veya eski içe aktarma
        # payload'ındaki categories_meta.color değeri güvenilir kaynak değildir.
        from config.categories_config import get_category_color
        categories = q_dict.get("categories") or q_dict.get("category") or []
        if isinstance(categories, str):
            categories = [categories]
        if not categories:
            categories = [item.get("name") for item in q_dict.get("categories_meta", []) if isinstance(item, dict)]
        if categories:
            q_dict["categories_meta"] = [
                {"name": str(category).strip(), "color": get_category_color(category)}
                for category in categories if str(category).strip()
            ]

        if "difficulty_profile" in q_dict and isinstance(q_dict["difficulty_profile"], dict):
            prof = q_dict["difficulty_profile"]
            if "global_scope" in prof and "global" not in prof:
                prof["global"] = prof.pop("global_scope")

        q_dict.setdefault("base_points", 100)
        q_dict.setdefault("community_distribution", None)
        if not q_dict.get("difficulty"):
            q_dict["difficulty"] = (
                q_dict.get("difficulty_global") or q_dict.get("difficulty_local")
                if str(q_dict.get("scope") or "global").lower() == "global"
                else q_dict.get("difficulty_local")
            )

        hash_seed = (
            q_dict.get("question")
            or q_dict.get("translations", {}).get("en", {}).get("question")
            or q_dict.get("translations", {}).get("tr", {}).get("question")
            or str(datetime.now(timezone.utc).timestamp())
        )
        q_hash = generate_question_hash(hash_seed)
        
        # Hash'i ID olarak kullanarak doküman çakışmasını/tekrarını önlüyoruz
        doc_ref = db.collection(collection_name).document(q_hash)
        doc = doc_ref.get()
        
        if doc.exists:
            print(f"⚠️ Bu soru zaten kayıtlı, atlandı: {hash_seed[:40]}...")
            return False
        
        # Meta veriler ve doğru şık/süre eşleşmesi
        q_dict["question_hash"] = q_hash
        from core.community_distribution import apply_simulated_distribution

        apply_simulated_distribution(q_hash, q_dict)  # simulated until real votes exist
        q_dict["created_at"] = firestore.SERVER_TIMESTAMP
        q_dict["status"] = "published"
        # Firestore sözleşmesi: eski ``version`` alanı artık kalıcı şemada yok.
        # Üretim modeli geriye dönük olarak version taşısa bile yazma sınırında
        # tek kanonik alan olan dbVersion'a dönüştürülür.
        q_dict["dbVersion"] = str(q_dict.pop("version", None) or q_dict.get("dbVersion") or "1.1.0")
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
        print(f"✅ Soru Firestore '{collection_name}' koleksiyonuna kaydedildi: {hash_seed[:45]}...")
        return True
    except Exception as e:
        print(f"❌ Firestore'a yazılırken hata: {e}")
        return False
