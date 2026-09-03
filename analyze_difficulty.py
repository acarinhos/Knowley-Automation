import re
import logging
from collections import Counter
from db_manager import init_firebase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def normalize_difficulty(val) -> str:
    """
    Farklı tiplerdeki (str, int, float, dict) zorluk değerini 
    standart 'Kolay', 'Orta' veya 'Zor' etiketine dönüştürür.
    """
    if val is None:
        return "Bilinmiyor"
    
    # 1. Eğer veri dict tipindeyse (örn: {'label': 'Kolay', 'score': 2} veya {'level': 'Orta'})
    if isinstance(val, dict):
        label = val.get("label") or val.get("level") or val.get("difficulty") or val.get("name")
        score = val.get("score") or val.get("value") or val.get("point")
        
        # Öncelik metin etiketinde
        if label:
            parsed_label = normalize_difficulty(label)
            if parsed_label != "Bilinmiyor":
                return parsed_label
        # Metin yoksa veya çözülemediyse puanı dene
        if score is not None:
            return normalize_difficulty(score)
        return "Bilinmiyor"

    # 2. Eğer sayısal değerse (int / float)
    if isinstance(val, (int, float)):
        if val <= 3:
            return "Kolay"
        elif 4 <= val <= 7:
            return "Orta"
        elif val >= 8:
            return "Zor"
        return "Bilinmiyor"

    # 3. Eğer string ise
    val_str = str(val).strip().lower()
    if not val_str:
        return "Bilinmiyor"

    if "kolay" in val_str or "easy" in val_str:
        return "Kolay"
    elif "orta" in val_str or "medium" in val_str:
        return "Orta"
    elif "zor" in val_str or "hard" in val_str:
        return "Zor"

    # Karmaşık string içindeki sayısal skoru yakala (örn: '2/10', 'Seviye: 5')
    match = re.search(r"\b(\d+)\b", val_str)
    if match:
        try:
            score_num = int(match.group(1))
            if score_num <= 3:
                return "Kolay"
            elif 4 <= score_num <= 7:
                return "Orta"
            elif score_num >= 8:
                return "Zor"
        except ValueError:
            pass

    return val.capitalize() if isinstance(val, str) else "Bilinmiyor"

def extract_local_difficulty(data: dict):
    """Dokümandan Yerel (Local) zorluk alanını aday anahtarlardan çıkarır."""
    for key in ["difficulty_local", "local_difficulty", "difficulty"]:
        if key in data and data[key] is not None:
            return data[key]
    
    dp = data.get("difficulty_profile")
    if isinstance(dp, dict):
        for sub_key in ["local", "local_scope"]:
            if sub_key in dp and dp[sub_key] is not None:
                return dp[sub_key]
                
    return None

def extract_global_difficulty(data: dict):
    """Dokümandan Küresel (Global) zorluk alanını aday anahtarlardan çıkarır."""
    for key in ["difficulty_global", "global_difficulty", "global_score", "difficulty"]:
        if key in data and data[key] is not None:
            return data[key]
            
    dp = data.get("difficulty_profile")
    if isinstance(dp, dict):
        for sub_key in ["global", "global_scope"]:
            if sub_key in dp and dp[sub_key] is not None:
                return dp[sub_key]
                
    return None

def analyze_difficulties():
    logging.info("🔍 Firestore soruları taranıyor...")
    
    db = init_firebase()
    questions_ref = db.collection("questions")
    docs = questions_ref.stream()

    local_counts = Counter()
    global_counts = Counter()
    total_questions = 0

    for doc in docs:
        total_questions += 1
        data = doc.to_dict()
        doc_id = doc.id

        # İlk 3 soru için tek satırlık Debug çıktısı
        if total_questions <= 3:
            diff_debug = {
                k: v for k, v in data.items()
                if any(w in k.lower() for w in ["diff", "score", "level", "profile"])
            }
            logging.info(f"🔎 [DEBUG Soru #{total_questions} | ID: {doc_id[:8]}]: {diff_debug}")

        # Local ve Global değerleri çıkar
        diff_local = extract_local_difficulty(data)
        diff_global = extract_global_difficulty(data)

        local_counts[normalize_difficulty(diff_local)] += 1
        global_counts[normalize_difficulty(diff_global)] += 1

    # Raporlama
    print("\n" + "=" * 60)
    print(" 🎯 ZORLUK SEVİYESİ DAĞILIM RAPORU ")
    print("=" * 60)
    print(f"Toplam Soru Sayısı: {total_questions}\n")

    print("📍 [YEREL / LOCAL ZORLUK]")
    print("-" * 60)
    print(f"{'Seviye':<15} | {'Soru Sayısı':<15} | {'Oran (%)':<10}")
    print("-" * 60)
    for level in ["Kolay", "Orta", "Zor", "Bilinmiyor"]:
        count = local_counts.get(level, 0)
        pct = (count / total_questions * 100) if total_questions > 0 else 0
        if count > 0 or level != "Bilinmiyor":
            print(f"{level:<15} | {count:<15} | %{pct:.1f}")

    print("\n🌍 [KÜRESEL / GLOBAL ZORLUK]")
    print("-" * 60)
    print(f"{'Seviye':<15} | {'Soru Sayısı':<15} | {'Oran (%)':<10}")
    print("-" * 60)
    for level in ["Kolay", "Orta", "Zor", "Bilinmiyor"]:
        count = global_counts.get(level, 0)
        pct = (count / total_questions * 100) if total_questions > 0 else 0
        if count > 0 or level != "Bilinmiyor":
            print(f"{level:<15} | {count:<15} | %{pct:.1f}")

    print("=" * 60 + "\n")

if __name__ == "__main__":
    analyze_difficulties()