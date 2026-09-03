import random
import threading
import logging
from typing import Optional, Union, Dict, Any

logger = logging.getLogger("DifficultyBalancer")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [DifficultyBalancer] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# 1-10 Dinamik Puanlama Aralıkları
SCORE_RANGES = {
    "Kolay": (1, 4),
    "Orta": (5, 8),
    "Zor": (9, 10),
}

# 10'luk Blok Kotası (4-4-2 Kuralı)
BASE_QUOTA = ["Kolay", "Kolay", "Kolay", "Kolay", "Orta", "Orta", "Orta", "Orta", "Zor", "Zor"]


class DifficultyBalancer:
    """
    Her 10 soruluk blokta katı 4 Kolay - 4 Orta - 2 Zor dağılımı uygulayan
    ve 1-10 puanlama aralıklarını doğrulayan thread-safe dengeleyici sınıfı.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.quota_pool = []
        self._refill_quota_pool()

    def _refill_quota_pool(self) -> None:
        """Yeni 10'luk havuz oluşturur ve karıştırır (4K - 4O - 2Z)."""
        self.quota_pool = list(BASE_QUOTA)
        random.shuffle(self.quota_pool)
        logger.info(f"🔄 [Zorluk Kotası] Yeni 10'luk blok havuzu hazırlandı: {self.get_remaining_summary()}")

    def get_next_target(self) -> str:
        """
        Havuzdan sıradaki hedef zorluk seviyesini çeker.
        Havuz boşalırsa otomatik olarak yeni 4-4-2 bloğu başlatır.
        """
        with self.lock:
            if not self.quota_pool:
                self._refill_quota_pool()
            target = self.quota_pool.pop(0)
            return target

    def get_remaining_summary(self) -> str:
        """
        Mevcut blokta kalan kota durumunu 'K:X O:Y Z:Z' formatında döndürür.
        """
        k_count = self.quota_pool.count("Kolay")
        o_count = self.quota_pool.count("Orta")
        z_count = self.quota_pool.count("Zor")
        return f"K:{k_count} O:{o_count} Z:{z_count}"

    @staticmethod
    def validate_and_clamp_score(level: str, score: Optional[Union[int, float, str]] = None) -> int:
        """
        Verilen zorluk seviyesine göre puanı sınırlar içine çeker (clamp/validate):
        - Kolay -> 1..4 (Varsayılan: 2)
        - Orta  -> 5..8 (Varsayılan: 6)
        - Zor   -> 9..10 (Varsayılan: 9)
        """
        clean_level = str(level).strip().capitalize()
        if clean_level not in SCORE_RANGES:
            # Seviye bilinmiyorsa puan üzerinden seviye tespit edilir
            clean_level = "Orta"

        min_score, max_score = SCORE_RANGES[clean_level]
        default_score = (min_score + max_score) // 2

        if score is None:
            return default_score

        try:
            if isinstance(score, str):
                import re
                match = re.search(r"\b(\d+)\b", score)
                if match:
                    numeric_score = int(match.group(1))
                else:
                    return default_score
            else:
                numeric_score = int(score)
        except (ValueError, TypeError):
            return default_score

        return max(min_score, min(max_score, numeric_score))

    @staticmethod
    def score_to_level(score: Union[int, float, str]) -> str:
        """Sayısal puana (1-10) göre zorluk seviyesi döndürür."""
        try:
            s = int(float(score))
        except (ValueError, TypeError):
            s = 5

        if s <= 4:
            return "Kolay"
        elif 5 <= s <= 8:
            return "Orta"
        else:
            return "Zor"


_global_difficulty_balancer: Optional[DifficultyBalancer] = None
_init_lock = threading.Lock()


def get_difficulty_balancer() -> DifficultyBalancer:
    """DifficultyBalancer singleton nesnesini döndürür."""
    global _global_difficulty_balancer
    if _global_difficulty_balancer is None:
        with _init_lock:
            if _global_difficulty_balancer is None:
                _global_difficulty_balancer = DifficultyBalancer()
    return _global_difficulty_balancer
