import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

def init_firebase():
    """Firebase uygulamasını başlatır."""
    if not firebase_admin._apps:
        cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH")
        if not cred_path or not os.path.exists(cred_path):
            raise FileNotFoundError(f"Firebase kimlik dosyası bulunamadı: {cred_path}")
        
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    return firestore.client()

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
        
        # Meta veriler ekle
        q_dict["question_hash"] = q_hash
        q_dict["created_at"] = datetime.now(timezone.utc).isoformat()
        q_dict["status"] = "published"
        
        doc_ref.set(q_dict)
        print(f"✅ Soru Firestore'a kaydedildi: {hash_seed[:45]}...")
        return True
    except Exception as e:
        print(f"❌ Firestore'a yazılırken hata: {e}")
        return False