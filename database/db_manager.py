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
        if not cred_path or not os.path.exists(cred_path):
            raise FileNotFoundError(f"Firebase kimlik dosyası bulunamadı: {cred_path}")
        
        storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET", "knowley-1-categories")
        cred = credentials.Certificate(cred_path)
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

def save_question_to_firestore(db, question_obj) -> bool:
    """
    Pydantic GeneratedQuestion nesnesini Firestore'a kaydeder.
    Eğer soru daha önce eklenmişse kaydetmez.
    """
    try:
        if isinstance(question_obj, dict):
            q_dict = question_obj
        else:
            q_dict = question_obj.model_dump(by_alias=True)

        if "difficulty_profile" in q_dict and isinstance(q_dict["difficulty_profile"], dict):
            prof = q_dict["difficulty_profile"]
            if "global_scope" in prof and "global" not in prof:
                prof["global"] = prof.pop("global_scope")

        hash_seed = (
            q_dict.get("question")
            or q_dict.get("translations", {}).get("en", {}).get("question")
            or q_dict.get("translations", {}).get("tr", {}).get("question")
            or str(datetime.now(timezone.utc).timestamp())
        )
        q_hash = generate_question_hash(hash_seed)
        
        # Hash'i ID olarak kullanarak doküman çakışmasını/tekrarını önlüyoruz
        doc_ref = db.collection("questions").document(q_hash)
        doc = doc_ref.get()
        
        if doc.exists:
            print(f"⚠️ Bu soru zaten kayıtlı, atlandı: {hash_seed[:40]}...")
            return False
        
        # Meta veriler ve doğru şık/süre eşleşmesi
        q_dict["question_hash"] = q_hash
        q_dict["created_at"] = firestore.SERVER_TIMESTAMP
        q_dict["status"] = "published"
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
        print(f"✅ Soru Firestore'a kaydedildi: {hash_seed[:45]}...")
        return True
    except Exception as e:
        print(f"❌ Firestore'a yazılırken hata: {e}")
        return False