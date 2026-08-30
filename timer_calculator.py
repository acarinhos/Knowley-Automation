from typing import Any, Dict, List, Optional, Union

# Temel Parametreler
BASE_TIME: int = 10           # Taban süre: 10 saniye
CHARS_PER_SECOND: int = 25    # Okuma hızı katsayısı: ~25 karakter başına +1 saniye
MIN_TIME: int = 10            # Minimum süre alt sınırı
MAX_TIME: int = 30            # Maksimum süre üst tavanı

# Zorluk Seviyesi Bonus Süreleri
DIFFICULTY_BONUS: Dict[str, int] = {
    "kolay": 0,
    "easy": 0,
    "orta": 2,
    "medium": 2,
    "zor": 5,
    "hard": 5,
    "difficult": 5,
}


def calculate_question_durations(
    question_text: str,
    options: Union[List[str], Dict[str, str], Any],
    difficulty_local: Optional[str] = "Orta",
    difficulty_global: Optional[str] = "Orta"
) -> Dict[str, int]:
    """
    Soru metni, şıklar ve hem yerel (local) hem küresel (global) zorluk seviyelerine göre
    ayrık süreleri hesaplar.
    
    Döndürülen Değerler:
    - duration_local: Yerel zorluk bonuslu süre
    - duration_global: Küresel zorluk bonuslu süre
    - duration_seconds: Varsayılan (duration_local) süre (Geriye dönük uyumluluk için)
    """
    q_clean = str(question_text or "").strip()
    q_len = len(q_clean)

    # Şık uzunluklarını topla
    opt_lens = 0
    if isinstance(options, dict):
        for opt_val in options.values():
            opt_lens += len(str(opt_val or "").strip())
    elif isinstance(options, (list, tuple, set)):
        for opt_val in options:
            opt_lens += len(str(opt_val or "").strip())
    elif hasattr(options, "model_dump"):
        opts_dict = options.model_dump()
        for opt_val in opts_dict.values():
            opt_lens += len(str(opt_val or "").strip())
    elif hasattr(options, "__dict__"):
        for opt_val in options.__dict__.values():
            opt_lens += len(str(opt_val or "").strip())

    total_chars = q_len + opt_lens

    # Okuma ek süresi
    reading_extra = round(total_chars / CHARS_PER_SECOND)

    # Yerel ve Küresel bonusları hesapla
    local_key = str(difficulty_local or "orta").strip().lower()
    global_key = str(difficulty_global or "orta").strip().lower()

    local_bonus = DIFFICULTY_BONUS.get(local_key, 2)
    global_bonus = DIFFICULTY_BONUS.get(global_key, 2)

    raw_local = BASE_TIME + reading_extra + local_bonus
    raw_global = BASE_TIME + reading_extra + global_bonus

    dur_local = min(MAX_TIME, max(MIN_TIME, int(raw_local)))
    dur_global = min(MAX_TIME, max(MIN_TIME, int(raw_global)))

    return {
        "duration_local": dur_local,
        "duration_global": dur_global,
        "duration_seconds": dur_local,
    }


def calculate_question_duration(
    question_text: str,
    options: Union[List[str], Dict[str, str], Any],
    difficulty: Optional[str] = "Orta"
) -> int:
    """Tek bir zorluk seviyesi için süre hesaplar (Geriye dönük uyumluluk)."""
    res = calculate_question_durations(
        question_text=question_text,
        options=options,
        difficulty_local=difficulty,
        difficulty_global=difficulty
    )
    return res["duration_local"]


def calculate_durations_from_obj(question_obj: Any) -> Dict[str, int]:
    """
    Pydantic GeneratedQuestion veya Firestore dict nesnesinden hem yerel hem küresel
    zorluk verilerini okuyarak ayrık süre sözlüğünü döndürür.
    """
    question_text = ""
    options_data = {}
    diff_local = "Orta"
    diff_global = "Orta"

    if isinstance(question_obj, dict):
        translations = question_obj.get("translations", {})
        tr_or_en = translations.get("tr") or translations.get("en") or {}
        if isinstance(tr_or_en, dict):
            question_text = tr_or_en.get("question") or question_obj.get("question") or ""
            options_data = tr_or_en.get("options") or question_obj.get("options") or {}
        else:
            question_text = getattr(tr_or_en, "question", "") or question_obj.get("question") or ""
            options_data = getattr(tr_or_en, "options", {}) or question_obj.get("options") or {}

        # Zorluk bul
        diff_prof = question_obj.get("difficulty_profile")
        if isinstance(diff_prof, dict):
            local_scope = diff_prof.get("local")
            global_scope = diff_prof.get("global") or diff_prof.get("global_scope")

            if isinstance(local_scope, dict):
                diff_local = local_scope.get("label", "Orta")
            elif hasattr(local_scope, "label"):
                diff_local = getattr(local_scope, "label", "Orta")

            if isinstance(global_scope, dict):
                diff_global = global_scope.get("label", "Orta")
            elif hasattr(global_scope, "label"):
                diff_global = getattr(global_scope, "label", "Orta")

        elif "difficulty_local" in question_obj or "local_difficulty" in question_obj:
            diff_local = question_obj.get("difficulty_local") or question_obj.get("local_difficulty", "Orta")
            diff_global = question_obj.get("difficulty_global") or question_obj.get("global_difficulty", "Orta")
        elif "difficulty" in question_obj:
            diff_local = question_obj.get("difficulty", "Orta")
            diff_global = diff_local
        elif "level" in question_obj:
            diff_local = question_obj.get("level", "Orta")
            diff_global = diff_local

    else:
        # Pydantic GeneratedQuestion nesnesi
        translations = getattr(question_obj, "translations", {})
        tr_or_en = translations.get("tr") or translations.get("en")
        if tr_or_en:
            question_text = getattr(tr_or_en, "question", "")
            options_data = getattr(tr_or_en, "options", {})

        diff_prof = getattr(question_obj, "difficulty_profile", None)
        if diff_prof:
            local_scope = getattr(diff_prof, "local", None)
            global_scope = getattr(diff_prof, "global_scope", None) or getattr(diff_prof, "global", None)

            if local_scope:
                diff_local = getattr(local_scope, "label", "Orta")
            if global_scope:
                diff_global = getattr(global_scope, "label", "Orta")

    return calculate_question_durations(
        question_text=question_text,
        options=options_data,
        difficulty_local=diff_local,
        difficulty_global=diff_global
    )


def calculate_duration_from_obj(question_obj: Any) -> int:
    """Geriye dönük uyumluluk için varsayılan süreyi (duration_seconds) int olarak döndürür."""
    durations = calculate_durations_from_obj(question_obj)
    return durations["duration_seconds"]
