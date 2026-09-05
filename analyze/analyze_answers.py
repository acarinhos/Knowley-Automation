import sys
from collections import Counter
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

def analyze_correct_answers():
    logging.info("🔍 Firestore'dan sorular çekiliyor ve doğru cevaplar analiz ediliyor...")
    
    db = init_firebase()
    questions_ref = db.collection("questions")
    docs = questions_ref.stream()

    answer_counts = Counter()
    total_questions = 0
    missing_answer_count = 0

    for doc in docs:
        total_questions += 1
        data = doc.to_dict()
        
        # Doğru cevabı belirten olası alan adlarını kontrol et
        correct_opt = (
            data.get("correct_option") or 
            data.get("correct_answer") or 
            data.get("answer")
        )

        if correct_opt is not None:
            # Şık harfi (A, B, C, D) veya indeks (0, 1, 2, 3) olarak kaydet
            formatted_key = str(correct_opt).strip().upper()
            answer_counts[formatted_key] += 1
        else:
            missing_answer_count += 1

    print("\n" + "="*50)
    print(" 📊 DOĞRU CEVAP ŞIKKI DAĞILIM RAPORU ")
    print("="*50)
    print(f"Toplam Taranan Soru : {total_questions}")
    print(f"Cevabı Tanımsız Soru: {missing_answer_count}\n")
    print(f"{'Şık / İndeks':<15} | {'Adet':<10} | {'Yüzde (%)':<10}")
    print("-" * 50)

    # Sıralı ve yüzdeli olarak listele
    for opt in sorted(answer_counts.keys()):
        count = answer_counts[opt]
        percentage = (count / total_questions * 100) if total_questions > 0 else 0
        print(f"{opt:<15} | {count:<10} | %{percentage:.2f}")

    print("="*50 + "\n")

if __name__ == "__main__":
    analyze_correct_answers()