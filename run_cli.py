import argparse
import time
import random
from categories_config import CATEGORIES_DATA, DIFFICULTIES
from generator import create_gemini_client, generate_single_question
from db_manager import init_firebase, save_question_to_firestore

def generate_with_retry(client, category, sub_category, filter_tag, difficulty, max_retries=3):
    """API hatalarına karşı kademeli bekleme (exponential backoff) ile güvenli üretim."""
    delay = 2
    for attempt in range(1, max_retries + 1):
        try:
            return generate_single_question(
                client=client,
                category=category,
                sub_category=sub_category,
                filter_tag=filter_tag,
                difficulty=difficulty
            )
        except Exception as e:
            print(f"  ⚠️ Hata oluştu (Deneme {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
            else:
                raise e

def run_targeted_generation(count: int, category: str = None, sub_category: str = None, difficulty: str = None):
    """Belirli veya rastgele filtrelerle soru üretir."""
    client = create_gemini_client()
    db = init_firebase()
    categories_list = list(CATEGORIES_DATA.keys())
    
    saved_count = 0
    print(f"\n🚀 Soru Üretimi Başlıyor | Hedef: {count} Soru\n" + "="*50)

    for i in range(count):
        # Parametre seçimi
        selected_cat = category if category else random.choice(categories_list)
        cat_info = CATEGORIES_DATA[selected_cat]
        
        selected_sub = sub_category if sub_category else random.choice(cat_info["sub_categories"])
        selected_filter = random.choice(cat_info["filters"])
        selected_diff = difficulty if difficulty else random.choice(DIFFICULTIES)

        print(f"[{i+1}/{count}] Üretiliyor: {selected_cat} -> {selected_sub} [{selected_diff}] ({selected_filter})")

        try:
            q_data = generate_with_retry(
                client=client,
                category=selected_cat,
                sub_category=selected_sub,
                filter_tag=selected_filter,
                difficulty=selected_diff
            )
            
            if save_question_to_firestore(db, q_data):
                saved_count += 1
            
            # API kota koruması
            time.sleep(1.2)
            
        except Exception as e:
            print(f"  ❌ Bu soru üretilemedi, atlanıyor...")
            continue

    print("="*50 + f"\n🎉 Tamamlandı! {saved_count}/{count} soru başarıyla Firestore'a kaydedildi.\n")

def run_matrix_generation(questions_per_category: int = 3):
    """20 kategorinin tamamı için Kolay, Orta ve Zor dengeli soru havuzu üretir."""
    client = create_gemini_client()
    db = init_firebase()
    
    total_target = len(CATEGORIES_DATA) * questions_per_category * len(DIFFICULTIES)
    print(f"\n🌍 Matris Havuz Üretimi Başlıyor!")
    print(f"Toplam 20 Kategori x 3 Zorluk x {questions_per_category} Soru = {total_target} Soru Hedefleniyor.\n" + "="*50)

    saved_count = 0
    for cat_name, cat_data in CATEGORIES_DATA.items():
        print(f"\n📂 Kategori İşleniyor: {cat_name}")
        for diff in DIFFICULTIES:
            for _ in range(questions_per_category):
                sub = random.choice(cat_data["sub_categories"])
                filter_tag = random.choice(cat_data["filters"])
                
                try:
                    q_data = generate_with_retry(
                        client=client,
                        category=cat_name,
                        sub_category=sub,
                        filter_tag=filter_tag,
                        difficulty=diff
                    )
                    if save_question_to_firestore(db, q_data):
                        saved_count += 1
                    time.sleep(1.2)
                except Exception:
                    continue

    print("="*50 + f"\n🎉 Matris Tamamlandı! Toplam {saved_count} yeni soru veritabanına eklendi.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quiz Question Generator CLI")
    parser.add_argument("--count", type=int, default=5, help="Üretilecek soru sayısı")
    parser.add_argument("--category", type=str, default=None, help="Belirli bir ana kategori")
    parser.add_argument("--subcategory", type=str, default=None, help="Belirli bir alt kategori")
    parser.add_argument("--difficulty", type=str, choices=["Kolay", "Orta", "Zor"], default=None, help="Zorluk seviyesi")
    parser.add_argument("--matrix", action="store_true", help="Tüm kategorilerden dengeli havuz üretir")
    parser.add_argument("--matrix_count", type=int, default=2, help="Matris modunda her zorluk için üretilecek adet")

    args = parser.parse_args()

    if args.matrix:
        run_matrix_generation(questions_per_category=args.matrix_count)
    else:
        run_targeted_generation(
            count=args.count,
            category=args.category,
            sub_category=args.subcategory,
            difficulty=args.difficulty
        )