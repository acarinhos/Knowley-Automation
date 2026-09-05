import sys
import re
import logging
from collections import Counter
from typing import Optional, Any
from db_manager import init_firebase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Konsol UTF-8 desteği (Windows cp1254 hatasını engeller)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def normalize_difficulty(val: Any) -> str:
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
        if label:
            parsed = normalize_difficulty(label)
            if parsed != "Bilinmiyor":
                return parsed
        if score is not None:
            return normalize_difficulty(score)
        return "Bilinmiyor"

    # 2. Eğer sayısal değerse (int / float)
    if isinstance(val, (int, float)):
        if val <= 4:
            return "Kolay"
        elif 5 <= val <= 8:
            return "Orta"
        elif val >= 9:
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

    match = re.search(r"\b(\d+)\b", val_str)
    if match:
        try:
            score_num = int(match.group(1))
            if score_num <= 4:
                return "Kolay"
            elif 5 <= score_num <= 8:
                return "Orta"
            elif score_num >= 9:
                return "Zor"
        except ValueError:
            pass

    return val.capitalize() if isinstance(val, str) else "Bilinmiyor"


def extract_score(val: Any) -> Optional[int]:
    """Herhangi bir değerden (int, dict, str) 1-10 sayısal puanı çıkarır."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, dict):
        sc = val.get("score") or val.get("value") or val.get("point")
        if sc is not None:
            return extract_score(sc)
    val_str = str(val)
    match = re.search(r"\b(\d+)\b", val_str)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


def extract_local_info(data: dict):
    """Yerel zorluk seviyesi ve puanını çıkarır."""
    level = None
    score = None

    # 1. Standart düz alanlar
    if "difficulty_local" in data and data["difficulty_local"] is not None:
        level = normalize_difficulty(data["difficulty_local"])
    if "difficulty_local_score" in data and data["difficulty_local_score"] is not None:
        score = extract_score(data["difficulty_local_score"])

    # 2. difficulty_profile
    dp = data.get("difficulty_profile")
    if isinstance(dp, dict):
        loc = dp.get("local")
        if isinstance(loc, dict):
            if not level and loc.get("label"):
                level = normalize_difficulty(loc.get("label"))
            if score is None and loc.get("score") is not None:
                score = extract_score(loc.get("score"))

    # 3. difficulty_meta
    dm = data.get("difficulty_meta")
    if isinstance(dm, dict):
        loc_meta = dm.get("local")
        if isinstance(loc_meta, dict):
            if not level and loc_meta.get("level"):
                level = normalize_difficulty(loc_meta.get("level"))
            if score is None and loc_meta.get("score") is not None:
                score = extract_score(loc_meta.get("score"))

    if not level and "difficulty" in data:
        level = normalize_difficulty(data["difficulty"])

    return level or "Bilinmiyor", score


def extract_global_info(data: dict):
    """Küresel zorluk seviyesi ve puanını çıkarır."""
    level = None
    score = None

    if "difficulty_global" in data and data["difficulty_global"] is not None:
        level = normalize_difficulty(data["difficulty_global"])
    if "difficulty_global_score" in data and data["difficulty_global_score"] is not None:
        score = extract_score(data["difficulty_global_score"])

    dp = data.get("difficulty_profile")
    if isinstance(dp, dict):
        gl = dp.get("global") or dp.get("global_scope")
        if isinstance(gl, dict):
            if not level and gl.get("label"):
                level = normalize_difficulty(gl.get("label"))
            if score is None and gl.get("score") is not None:
                score = extract_score(gl.get("score"))

    dm = data.get("difficulty_meta")
    if isinstance(dm, dict):
        gl_meta = dm.get("global")
        if isinstance(gl_meta, dict):
            if not level and gl_meta.get("level"):
                level = normalize_difficulty(gl_meta.get("level"))
            if score is None and gl_meta.get("score") is not None:
                score = extract_score(gl_meta.get("score"))

    return level or "Bilinmiyor", score


def analyze_difficulties():
    logging.info("🔍 Firestore soruları taranıyor...")
    db = init_firebase()
    questions_ref = db.collection("questions")
    docs = questions_ref.stream()

    local_level_counts = Counter()
    local_score_counts = Counter()
    global_level_counts = Counter()
    global_score_counts = Counter()
    total_questions = 0

    for doc in docs:
        total_questions += 1
        data = doc.to_dict()

        loc_lvl, loc_sc = extract_local_info(data)
        gl_lvl, gl_sc = extract_global_info(data)

        local_level_counts[loc_lvl] += 1
        if loc_sc is not None:
            local_score_counts[loc_sc] += 1

        global_level_counts[gl_lvl] += 1
        if gl_sc is not None:
            global_score_counts[gl_sc] += 1

    # Raporlama
    print("\n" + "=" * 65)
    print(" 🎯 ZORLUK SEVİYESİ VE 1-10 PUAN DAĞILIM RAPORU ")
    print("=" * 65)
    print(f"Toplam Veritabanı Soru Sayısı: {total_questions}\n")

    # 1. YEREL / LOCAL ZORLUK
    print("📍 [YEREL / LOCAL ZORLUK]")
    print("-" * 65)
    print(f"{'Seviye':<15} | {'Soru Sayısı':<15} | {'Oran (%)':<10}")
    print("-" * 65)
    for level in ["Kolay", "Orta", "Zor", "Bilinmiyor"]:
        count = local_level_counts.get(level, 0)
        pct = (count / total_questions * 100) if total_questions > 0 else 0
        if count > 0 or level != "Bilinmiyor":
            print(f"{level:<15} | {count:<15} | %{pct:.1f}")

    print("\n  📊 Yerel Puan Dağılımı (1 - 10):")
    print("  " + "-" * 55)
    print(f"  {'Grup':<12} | {'Puan':<8} | {'Soru Sayısı':<15} | {'Oran (%)':<10}")
    print("  " + "-" * 55)
    for grp, p_range in [("Kolay (1-4)", range(1, 5)), ("Orta (5-8)", range(5, 9)), ("Zor (9-10)", range(9, 11))]:
        for sc in p_range:
            sc_count = local_score_counts.get(sc, 0)
            sc_pct = (sc_count / total_questions * 100) if total_questions > 0 else 0
            print(f"  {grp:<12} | {sc:<8} | {sc_count:<15} | %{sc_pct:.1f}")
        print("  " + "-" * 55)

    # 2. KÜRESEL / GLOBAL ZORLUK
    print("\n🌍 [KÜRESEL / GLOBAL ZORLUK]")
    print("-" * 65)
    print(f"{'Seviye':<15} | {'Soru Sayısı':<15} | {'Oran (%)':<10}")
    print("-" * 65)
    for level in ["Kolay", "Orta", "Zor", "Bilinmiyor"]:
        count = global_level_counts.get(level, 0)
        pct = (count / total_questions * 100) if total_questions > 0 else 0
        if count > 0 or level != "Bilinmiyor":
            print(f"{level:<15} | {count:<15} | %{pct:.1f}")

    print("\n  📊 Global Puan Dağılımı (1 - 10):")
    print("  " + "-" * 55)
    print(f"  {'Grup':<12} | {'Puan':<8} | {'Soru Sayısı':<15} | {'Oran (%)':<10}")
    print("  " + "-" * 55)
    for grp, p_range in [("Kolay (1-4)", range(1, 5)), ("Orta (5-8)", range(5, 9)), ("Zor (9-10)", range(9, 11))]:
        for sc in p_range:
            sc_count = global_score_counts.get(sc, 0)
            sc_pct = (sc_count / total_questions * 100) if total_questions > 0 else 0
            print(f"  {grp:<12} | {sc:<8} | {sc_count:<15} | %{sc_pct:.1f}")
        print("  " + "-" * 55)

    print("=" * 65 + "\n")


if __name__ == "__main__":
    analyze_difficulties()