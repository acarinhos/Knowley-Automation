"""
Knowley Soru Üretim Motoru v1.0.2
Production Balancer: Kategori Bazlı Global / Local Üretim Kotası Takipçisi
"""

import random
import threading
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Any

from country_credit_config import (
    CATEGORY_COUNTRY_MATRIX,
    get_category_matrix_config,
    normalize_category_name
)

logger = logging.getLogger("ProductionBalancer")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [ProductionBalancer] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Standart kota pencere boyutu (Örn: 20 veya 100 soru)
WINDOW_SIZE = 20


class ProductionBalancer:
    """
    Her kategori için tanımlı Global/Local yüzdelerini (ör. Tarih: %25 Global / %75 Local)
    dinamik ve deterministik kota havuzları (discrete window pool) ile yöneten sayaç sınıfı.
    
    Örneğin 100 soru üretildiğinde tam olarak 25 Global ve 75 Local soru üretilmesini garanti eder.
    """

    def __init__(self, window_size: int = WINDOW_SIZE):
        self.lock = threading.Lock()
        self.window_size = window_size
        self.pools: Dict[str, List[str]] = {}
        self.produced_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"global": 0, "local": 0})

    def _refill_pool(self, category: str) -> None:
        """Kategoriye ait kota havuzunu oranlarına göre doldurur ve karıştırır."""
        cfg = get_category_matrix_config(category)
        ratio = cfg.get("ratio", {"global": 0.50, "local": 0.50})
        g_ratio = float(ratio.get("global", 0.50))

        # Pencere boyutuna göre tam sayı adetleri hesapla
        g_count = int(round(self.window_size * g_ratio))
        l_count = self.window_size - g_count

        pool = (["global"] * g_count) + (["local"] * l_count)
        random.shuffle(pool)
        self.pools[category] = pool

        logger.info(
            f"🔄 [Kota Havuzu] '{category}' için yeni {self.window_size}'lik kota havuzu oluşturuldu: "
            f"Global: {g_count} (%{int(g_ratio*100)}), Local: {l_count} (%{int((1-g_ratio)*100)})"
        )

    def get_next_scope(self, category: str, force_scope: Optional[str] = None) -> str:
        """
        Kategori kotasından sıradaki hedef kapsamı ('global' veya 'local') çeker.
        - force_scope verilmişse doğrudan onu döndürür.
        - Verilmemişse pencere havuzundan sıradakini pop eder (havuz bittiğinde yeniden doldurur).
        """
        if force_scope:
            clean = str(force_scope).strip().lower()
            if clean in ("global", "local"):
                return clean

        norm_cat = normalize_category_name(category)
        with self.lock:
            if not self.pools.get(norm_cat):
                self._refill_pool(norm_cat)

            chosen_scope = self.pools[norm_cat].pop(0)
            return chosen_scope

    def record_success(self, category: str, scope: str) -> None:
        """Başarıyla üretilen ve kaydedilen soruyu sayaca işler."""
        norm_cat = normalize_category_name(category)
        clean_scope = str(scope).strip().lower()
        if clean_scope not in ("global", "local"):
            clean_scope = "global"

        with self.lock:
            self.produced_counts[norm_cat][clean_scope] += 1
            tot = self.produced_counts[norm_cat]["global"] + self.produced_counts[norm_cat]["local"]
            g_cnt = self.produced_counts[norm_cat]["global"]
            l_cnt = self.produced_counts[norm_cat]["local"]
            logger.debug(f"📊 [Kota Sayaç] {norm_cat} -> Toplam: {tot} (Global: {g_cnt}, Local: {l_cnt})")

    def get_category_stats(self, category: str) -> Dict[str, Any]:
        """Kategorinin gerçekleşen üretim istatistiklerini ve kalan havuz durumunu döner."""
        norm_cat = normalize_category_name(category)
        with self.lock:
            counts = dict(self.produced_counts[norm_cat])
            total = counts["global"] + counts["local"]
            remaining_pool = list(self.pools.get(norm_cat, []))
            return {
                "category": norm_cat,
                "produced_global": counts["global"],
                "produced_local": counts["local"],
                "total_produced": total,
                "actual_global_ratio": (counts["global"] / total) if total > 0 else 0.0,
                "actual_local_ratio": (counts["local"] / total) if total > 0 else 0.0,
                "remaining_in_pool": len(remaining_pool),
                "remaining_pool": remaining_pool
            }

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Tüm kategorilerin istatistiklerini döner."""
        return {cat: self.get_category_stats(cat) for cat in CATEGORY_COUNTRY_MATRIX}


_global_production_balancer: Optional[ProductionBalancer] = None
_prod_lock = threading.Lock()


def get_production_balancer() -> ProductionBalancer:
    """ProductionBalancer singleton instance'ını döndürür."""
    global _global_production_balancer
    if _global_production_balancer is None:
        with _prod_lock:
            if _global_production_balancer is None:
                _global_production_balancer = ProductionBalancer()
    return _global_production_balancer
