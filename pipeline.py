import time
import random
from config.categories_config import CATEGORIES_DATA, DIFFICULTIES
from core.generator import create_gemini_client, generate_single_question
from database.db_manager import init_firebase, save_question_to_firestore

def run_automation(total_questions: int = 5, target_category: str = None, target_difficulty: str = None):
    """
    Belirlenen kriterlerde döngüye girerek sorular üretir ve Firebase'e yazar.
    """
    print(f"🚀 Soru Üretim Otomasyonu Başlatıldı! (Hedef: {total_questions} soru)\n")
    
    gemini_client = create_gemini_client()
    db = init_firebase()
    
    success_count = 0
    categories_list = list(CATEGORIES_DATA.keys())
    
    for i in range(total_questions):
        # 1. Parametreleri belirle (Seçili değilse rastgele seç)
        category = target_category if target_category else random.choice(categories_list)
        cat_info = CATEGORIES_DATA[category]
        
        sub_category = random.choice(cat_info["sub_categories"])
        filter_tag = random.choice(cat_info["filters"])
        difficulty = target_difficulty if target_difficulty else random.choice(DIFFICULTIES)
        
        print(f"[{i+1}/{total_questions}] Üretiliyor: {category} > {sub_category} > {difficulty} ({filter_tag})...")
        
        try:
            # 2. Gemini'den soruyu üret
            question_data = generate_single_question(
                client=gemini_client,
                category=category,
                sub_category=sub_category,
                filter_tag=filter_tag,
                difficulty=difficulty
            )
            
            # 3. Firebase'e kaydet
            is_saved = save_question_to_firestore(db, question_data)
            if is_saved:
                success_count += 1
                
            # API rate limit için kısa bir bekleme
            time.sleep(1)
            
        except Exception as e:
            print(f"❌ Adım {i+1} başarısız oldu: {e}")
            continue

    print(f"\n🎉 İşlem Tamamlandı! Toplam {success_count}/{total_questions} soru Firebase Firestore'a yazıldı.")

if __name__ == "__main__":
    # Test: Rastgele kategorilerden 3 adet soru üret ve kaydet
    run_automation(total_questions=3)
    
    # İstersen belirli bir kategori ve zorluk da verebilirsin:
    # run_automation(total_questions=2, target_category="Tarih", target_difficulty="Zor")