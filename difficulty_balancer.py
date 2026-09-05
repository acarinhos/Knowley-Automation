import random
import threading
import logging
from dataclasses import dataclass
from typing import Optional, Union, Dict, Any, List, Tuple

logger = logging.getLogger("DifficultyBalancer")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [DifficultyBalancer] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# 1-10 Dinamik Puanlama Aralıkları
SCORE_RANGES: Dict[str, Tuple[int, int]] = {
    "Kolay": (1, 4),
    "Orta": (5, 8),
    "Zor": (9, 10),
}

# 10'luk Blok Kotası: 4 Kolay (1, 2, 3, 4), 4 Orta (5, 6, 7, 8), 2 Zor (9, 10)
# Her 10 soruda 1'den 10'a tüm puanlar tam olarak 1'er kez yer alır.
BASE_DIFFICULTY_QUOTA: List[Tuple[str, int]] = [
    ("Kolay", 1), ("Kolay", 2), ("Kolay", 3), ("Kolay", 4),
    ("Orta", 5),  ("Orta", 6),  ("Orta", 7),  ("Orta", 8),
    ("Zor", 9),   ("Zor", 10),
]


@dataclass
class TargetDifficulty:
    """
    Belirlenen hedef zorluk seviyesi ve 1-10 puanını tutan veri modeli.
    Geriye dönük uyumluluk:
      - str(target) -> 'Kolay' / 'Orta' / 'Zor'
      - target == 'Kolay' -> True
      - level, score = target (unpack)
    """
    level: str
    score: int

    def __str__(self) -> str:
        return self.level

    def __repr__(self) -> str:
        return f"TargetDifficulty(level='{self.level}', score={self.score})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.level.lower() == other.lower()
        if isinstance(other, TargetDifficulty):
            return self.level == other.level and self.score == other.score
        return False

    def __iter__(self):
        yield self.level
        yield self.score


class DifficultyBalancer:
    """
    Her 10 soruluk blokta katı 4 Kolay (1-4), 4 Orta (5-8), 2 Zor (9-10) dağılımı uygulayan
    ve 1-10 puanlama aralıklarını homojen/eşit dağıtan thread-safe dengeleyici sınıfı.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.quota_pool: List[Tuple[str, int]] = []
        self.level_pools: Dict[str, List[int]] = {
            "Kolay": [],
            "Orta": [],
            "Zor": []
        }
        self._refill_quota_pool()

    def _refill_quota_pool(self) -> None:
        """Yeni 10'luk havuz oluşturur ve karıştırır (4K [1,2,3,4] - 4O [5,6,7,8] - 2Z [9,10])."""
        self.quota_pool = list(BASE_DIFFICULTY_QUOTA)
        random.shuffle(self.quota_pool)
        logger.info(f"🔄 [Zorluk Kotası] Yeni 10'luk blok havuzu hazırlandı: {self.get_remaining_summary()}")

    def _refill_level_pool(self, level: str) -> None:
        """Belirli bir seviye için puan havuzunu doldurur ve karıştırır."""
        min_s, max_s = SCORE_RANGES[level]
        pool = list(range(min_s, max_s + 1))
        random.shuffle(pool)
        self.level_pools[level] = pool

    def get_next_target(self, target_level: Optional[str] = None) -> TargetDifficulty:
        """
        Havuzdan sıradaki hedef zorluk seviyesini ve puanını çeker.
        - target_level belirtilmemişse: 10'luk bloktan sıradaki (seviye, puan) çifti döner.
        - target_level belirtilmişse: O seviyeye ait eşit dağılımlı puan havuzundan puan seçilir.
        """
        with self.lock:
            if target_level:
                clean_level = str(target_level).strip().capitalize()
                if clean_level not in SCORE_RANGES:
                    clean_level = "Orta"

                if not self.level_pools[clean_level]:
                    self._refill_level_pool(clean_level)
                score = self.level_pools[clean_level].pop(0)
                return TargetDifficulty(level=clean_level, score=score)

            if not self.quota_pool:
                self._refill_quota_pool()

            lvl, sc = self.quota_pool.pop(0)
            return TargetDifficulty(level=lvl, score=sc)

    def get_balanced_score_for_level(self, level: str) -> int:
        """Verilen seviye için eşit dağılımlı sıradaki puanı döndürür."""
        with self.lock:
            clean_level = str(level).strip().capitalize()
            if clean_level not in SCORE_RANGES:
                clean_level = "Orta"
            if not self.level_pools[clean_level]:
                self._refill_level_pool(clean_level)
            return self.level_pools[clean_level].pop(0)

    def get_remaining_summary(self) -> str:
        """Mevcut blokta kalan kota durumunu 'K:X O:Y Z:Z' formatında döndürür."""
        levels = [item[0] for item in self.quota_pool]
        k_count = levels.count("Kolay")
        o_count = levels.count("Orta")
        z_count = levels.count("Zor")
        return f"K:{k_count} O:{o_count} Z:{z_count}"

    @staticmethod
    def normalize_level(level: Any) -> str:
        """Zorluk seviyesi adını standart 'Kolay', 'Orta', 'Zor' haline getirir."""
        if not level:
            return "Orta"
        clean = str(level).strip().capitalize()
        mapping = {
            "Kolay": "Kolay",
            "Easy": "Kolay",
            "Orta": "Orta",
            "Medium": "Orta",
            "Zor": "Zor",
            "Hard": "Zor",
            "Difficult": "Zor",
        }
        return mapping.get(clean, "Orta" if clean not in SCORE_RANGES else clean)

    @staticmethod
    def validate_and_clamp_score(level: str, score: Optional[Union[int, float, str]] = None) -> int:
        """
        Verilen zorluk seviyesine göre puanı sınırlar içine çeker (clamp/validate):
        - Kolay -> 1..4 (Varsayılan rastgele 1..4)
        - Orta  -> 5..8 (Varsayılan rastgele 5..8)
        - Zor   -> 9..10 (Varsayılan rastgele 9..10)
        """
        clean_level = DifficultyBalancer.normalize_level(level)
        min_score, max_score = SCORE_RANGES[clean_level]

        if score is None:
            return random.randint(min_score, max_score)

        try:
            if isinstance(score, str):
                import re
                match = re.search(r"\b(\d+)\b", score)
                if match:
                    numeric_score = int(match.group(1))
                else:
                    return random.randint(min_score, max_score)
            else:
                numeric_score = int(score)
        except (ValueError, TypeError):
            return random.randint(min_score, max_score)

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
