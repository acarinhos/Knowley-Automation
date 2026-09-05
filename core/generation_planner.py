"""
Knowley Soru Üretim Motoru v1.0.3
Generation Planner: Dinamik Kapsam (Global/Local) ve Ülke Kredi Ağırlıklı Planlayıcı
"""

import random
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, Literal

from config.country_credit_config import (
    CATEGORY_COUNTRY_MATRIX,
    get_category_matrix_config,
    normalize_category_name,
    is_country_global_eligible,
    get_country_credit
)
from core.production_balancer import get_production_balancer

logger = logging.getLogger("GenerationPlanner")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [GenerationPlanner] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


@dataclass
class GenerationPlan:
    """Tek bir soru üretimi için kapsam, ülke, kredi ve küresel uygunluk planı."""
    scope: Literal["global", "local"]
    ratio_percent: int
    target_country: str
    target_country_credit: int
    is_global_eligible: bool
    category: str
    subcategory: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"GenerationPlan(scope='{self.scope}', ratio={self.ratio_percent}%, "
            f"country='{self.target_country}', credit={self.target_country_credit}, "
            f"global_eligible={self.is_global_eligible}, "
            f"category='{self.category}', subcategory='{self.subcategory}')"
        )


class GenerationPlanner:
    """
    Kategori bazlı Global/Local üretim kotalarını işleten,
    Globalde yalnızca >= 5 kredi puanlı elit ülkeleri seçen,
    Localde soruları yalnızca tek bir ülkeye hapsetmeyip yüksek kredili ve yerel ülkeler
    arasından dengeli seçim yapan planlayıcı sınıf.
    """

    def __init__(self, matrix: Optional[Dict[str, Dict[str, Any]]] = None):
        self.matrix = matrix or CATEGORY_COUNTRY_MATRIX
        self.prod_balancer = get_production_balancer()

    def plan_scope(self, category: str, force_scope: Optional[str] = None) -> Tuple[Literal["global", "local"], int]:
        """
        Kategori kotasına göre sıradaki hedef kapsamı ('global' veya 'local') ve yüzdesini belirler.
        """
        norm_cat = normalize_category_name(category)
        cfg = get_category_matrix_config(norm_cat)
        ratio_dict = cfg.get("ratio", {"global": 0.50, "local": 0.50})
        g_ratio = float(ratio_dict.get("global", 0.50))
        l_ratio = float(ratio_dict.get("local", 0.50))

        if force_scope:
            clean_scope = str(force_scope).strip().lower()
            if clean_scope in ("global", "local"):
                percent = int(round(g_ratio * 100)) if clean_scope == "global" else int(round(l_ratio * 100))
                return clean_scope, percent  # type: ignore

        # ProductionBalancer ile deterministik kota takibi
        chosen_scope = self.prod_balancer.get_next_scope(norm_cat)
        percent = int(round(g_ratio * 100)) if chosen_scope == "global" else int(round(l_ratio * 100))
        return chosen_scope, percent  # type: ignore

    def plan_country(
        self,
        category: str,
        scope: Literal["global", "local"],
        force_country: Optional[str] = None
    ) -> Tuple[str, int, bool]:
        """
        Kapsama göre hedef ülke, kredi puanı ve küresel uygunluk bayrağını (is_global_eligible) seçer.
        - Global: Yalnızca >= 5 kredili Elit Ülkeler arasından kredi ağırlığıyla seçilir (is_global_eligible=True).
        - Local: Kategori bağlamındaki yüksek kredili elit ülkeler ile yerel/closed ülkeler arasından dengeli seçim yapılır.
        """
        norm_cat = normalize_category_name(category)
        cfg = get_category_matrix_config(norm_cat)
        elite = cfg.get("elite", {})
        closed = cfg.get("closed", [])

        # Zorunlu ülke verilmişse
        if force_country:
            fc = str(force_country).strip()
            credit = get_country_credit(norm_cat, fc)
            eligible = is_country_global_eligible(norm_cat, fc)
            return fc, credit, eligible

        if scope == "global":
            # Global için sadece >= 5 kredili ve closed içinde OLMAYAN ülkeler
            valid_candidates = {
                country: credit
                for country, credit in elite.items()
                if credit >= 5 and country not in closed
            }

            if not valid_candidates:
                valid_candidates = {"ABD": 10}

            countries = list(valid_candidates.keys())
            weights = [valid_candidates[c] for c in countries]
            chosen_country = random.choices(countries, weights=weights, k=1)[0]
            return chosen_country, valid_candidates[chosen_country], True

        else:
            # Local Kapsam:
            # Hem küresel elit ülkeler (ör. ABD, Birleşik Krallık, Fransa) hem de
            # sadece yerel/closed ülkeler (ör. Portekiz, Brezilya, Türkiye, vb.) seçilebilir.
            local_candidates: Dict[str, int] = {}

            # 1. Elit ülkeler (Kredileriyle)
            for country, credit in elite.items():
                local_candidates[country] = credit

            # 2. Closed / yerel ülkeler (Dengeli yerel ağırlık e.g. 4)
            for country in closed:
                if country not in local_candidates:
                    local_candidates[country] = 4

            # 3. Türkiye güvencesi
            if "Türkiye" not in local_candidates:
                local_candidates["Türkiye"] = 8

            countries = list(local_candidates.keys())
            weights = [local_candidates[c] for c in countries]
            chosen_country = random.choices(countries, weights=weights, k=1)[0]
            credit = local_candidates[chosen_country]
            eligible = is_country_global_eligible(norm_cat, chosen_country)
            return chosen_country, credit, eligible

    def create_plan(
        self,
        category: str,
        subcategory: Optional[str] = None,
        force_scope: Optional[str] = None,
        force_country: Optional[str] = None
    ) -> GenerationPlan:
        """Kategori ve opsiyonel parametrelere göre tam bir GenerationPlan oluşturur."""
        norm_cat = normalize_category_name(category)
        scope, ratio_percent = self.plan_scope(norm_cat, force_scope=force_scope)
        country, credit, eligible = self.plan_country(norm_cat, scope=scope, force_country=force_country)

        plan = GenerationPlan(
            scope=scope,
            ratio_percent=ratio_percent,
            target_country=country,
            target_country_credit=credit,
            is_global_eligible=eligible,
            category=norm_cat,
            subcategory=subcategory
        )
        logger.debug(f"Plan oluşturuldu: {plan}")
        return plan


_global_generation_planner: Optional[GenerationPlanner] = None


def get_generation_planner() -> GenerationPlanner:
    """GenerationPlanner singleton nesnesini döndürür."""
    global _global_generation_planner
    if _global_generation_planner is None:
        _global_generation_planner = GenerationPlanner()
    return _global_generation_planner
