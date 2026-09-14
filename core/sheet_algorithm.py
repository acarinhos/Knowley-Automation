"""Google Sheets tabanlı Knowley kategorizasyon algoritması.

Uygulama, Drive'daki çalışma kitabından alınan doğrulanmış JSON snapshot'ını
okur. ``scripts/sync_algorithm_sheet.py`` aynı dosyayı Google Sheets API ile
yenileyebilir. Üretim sırasında ağ bağımlılığı olmaması için snapshot kasıtlı
olarak repoda tutulur.
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = ROOT_DIR / "data" / "knowley_algorithm_snapshot.json"

POOL_COLUMNS = {
    "154": "154 Kredi Havuzu",
    "64": "64 Kredi Havuzu",
}

COUNTRY_SHEET_ALIASES = {
    "Türkiye": "Türkiye Kategorizasyon",
    "ABD": "ABD Kategorizasyon",
    "Çin": "Çin Kategorizasyon",
    "Japonya": "Japonya Kategorizasyon",
    "Almanya": "Almanya Kategorizasyon",
}

GENERATION_MODES = {"154", "64", "basic"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean(value)).casefold()
    return re.sub(r"\s+", " ", text)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(_clean(value)))
    except (TypeError, ValueError):
        return default


def _strip_country_number(value: str) -> str:
    return re.sub(r"^\s*\d+\.\s*", "", _clean(value)).strip()


def normalize_category(category: str) -> str:
    """Sheet'teki kategori adını değiştirmeden yalnızca boşlukları temizler."""
    return _clean(category)


def parse_scope_ratio(value: Any) -> Tuple[float, float]:
    """``%60 GL / %40 LO`` ve ``Öge`` hücrelerini sayısal orana çevirir."""
    text = _clean(value)
    if not text:
        return 0.5, 0.5
    if _key(text) in {_key("Öge"), _key("Oge")}:
        return 1.0, 0.0
    match = re.search(r"%?\s*(\d+)\s*GL\s*/\s*%?\s*(\d+)\s*LO", text, re.I)
    if not match:
        return 0.5, 0.5
    global_pct, local_pct = int(match.group(1)), int(match.group(2))
    total = global_pct + local_pct
    if total <= 0:
        return 0.5, 0.5
    return global_pct / total, local_pct / total


@dataclass(frozen=True)
class AlgorithmTarget:
    category: str
    subcategory: str
    category_weight: int
    subcategory_weight: int
    global_ratio: float
    local_ratio: float
    guide: str
    source_sheet: str
    category_focus: str = ""
    category_drafts: str = ""


class SheetAlgorithmRepository:
    """Sheets snapshot'ını seçim, UI ve prompt bağlamı için indeksler."""

    def __init__(self, snapshot_path: Optional[Path | str] = None):
        self.snapshot_path = Path(snapshot_path or DEFAULT_SNAPSHOT_PATH)
        with self.snapshot_path.open("r", encoding="utf-8") as handle:
            self.payload: Dict[str, Any] = json.load(handle)
        self.source: Dict[str, Any] = dict(self.payload.get("source", {}))
        self._sheets: Dict[str, Dict[str, Any]] = {
            sheet["title"]: sheet for sheet in self.payload.get("sheets", [])
        }
        self._category_meta = self._build_category_meta()
        self._category_weights = self._build_category_weights()
        self._generic_records = self._build_generic_records()
        self._localized_records = self._build_localized_records()
        self._country_records = self._build_country_records()

    @property
    def version(self) -> str:
        return _clean(self.source.get("version")) or "unknown"

    def sheet_rows(self, title: str) -> List[Dict[str, Any]]:
        return list(self._sheets.get(title, {}).get("rows", []))

    def _build_category_meta(self) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for row in self.sheet_rows("Kategorizasyon v1.0.5"):
            raw_category = _clean(row.get("Ana Kategori"))
            if not raw_category:
                continue
            category = normalize_category(raw_category)
            entry = result.setdefault(
                category,
                {"name": category, "raw_names": [], "colors": [], "focus": [], "drafts": []},
            )
            entry["raw_names"].append(raw_category)
            color = _clean(row.get("Renk Kodu (HEX)"))
            focus = _clean(row.get("Odak Noktaları (Ülke - Bölge / Öge)"))
            drafts = _clean(row.get("Sub Kategoriler (Lokalizasyon Taslağı / Parametreleri)"))
            if color:
                entry["colors"].append(color)
            if focus and focus != '"':
                entry["focus"].append(focus)
            if drafts:
                entry["drafts"].append(drafts)
        return result

    def _build_category_weights(self) -> Dict[str, Dict[str, int]]:
        result: Dict[str, Dict[str, int]] = {"154": {}, "64": {}}
        for row in self.sheet_rows("Kategorilerin Kredi Dereceleri"):
            raw_category = _clean(row.get("Ana Kategori"))
            if not raw_category or "kategori" in _key(raw_category) and raw_category[0].isdigit():
                continue
            category = normalize_category(raw_category)
            if category not in self._category_meta:
                continue
            for pool, column in POOL_COLUMNS.items():
                weight = _as_int(row.get(column))
                if weight > 0:
                    result[pool][category] = result[pool].get(category, 0) + weight
        return result

    def _guidance_index(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        result: Dict[Tuple[str, str], Dict[str, Any]] = {}
        current_category = ""
        for row in self.sheet_rows("Sub Kategorilerin GL-LO Dengesi"):
            raw_category = _clean(row.get("Ana Kategori"))
            if raw_category:
                if raw_category.startswith("BÖLÜM"):
                    current_category = ""
                    continue
                current_category = normalize_category(raw_category)
            subcategory = _clean(row.get("Alt Kategori (Sub)"))
            if not current_category or not subcategory:
                continue
            result[(_key(current_category), _key(subcategory))] = {
                "ratio_text": _clean(row.get("GL - LO Dengesi / Öge")),
                "guide": _clean(row.get("Odak & Kapsam Kılavuzu")),
            }
        return result

    def _rows_to_records(self, rows: Iterable[Mapping[str, Any]], source_sheet: str) -> List[Dict[str, Any]]:
        guidance = self._guidance_index()
        records: List[Dict[str, Any]] = []
        current_category = ""
        for row in rows:
            raw_category = _clean(row.get("Ana Kategori"))
            if raw_category:
                if raw_category.upper() in {"TOPLAM", "TOTAL"}:
                    current_category = ""
                    continue
                current_category = normalize_category(raw_category)
            subcategory = _clean(row.get("Alt Kategori (Sub)"))
            if not current_category or not subcategory or "ana kategori" in _key(subcategory):
                continue
            guide_data = guidance.get((_key(current_category), _key(subcategory)), {})
            ratio_text = _clean(row.get("GL - LO Dengesi / Öge")) or guide_data.get("ratio_text", "")
            guide = _clean(row.get("Odak & Kapsam Kılavuzu")) or guide_data.get("guide", "")
            global_ratio, local_ratio = parse_scope_ratio(ratio_text)
            records.append(
                {
                    "category": current_category,
                    "subcategory": subcategory,
                    "weight_154": max(0, _as_int(row.get("154 Kredi Havuzu Payı"))),
                    "weight_64": max(0, _as_int(row.get("64 Kredi Havuzu Payı"))),
                    "is_base": bool(_as_int(row.get("Temel Kategoriler"))),
                    "ratio_text": ratio_text,
                    "global_ratio": global_ratio,
                    "local_ratio": local_ratio,
                    "guide": guide,
                    "source_sheet": source_sheet,
                }
            )
        return records

    def _build_generic_records(self) -> List[Dict[str, Any]]:
        return self._rows_to_records(
            self.sheet_rows("Sub Kredi Dereceleri"), "Sub Kredi Dereceleri"
        )

    def _build_localized_records(self) -> Dict[str, List[Dict[str, Any]]]:
        result: Dict[str, List[Dict[str, Any]]] = {}
        current_country = ""
        current_category = ""
        rows = self.sheet_rows("Localize Sub Kredi Dereceleri")
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            raw_country = _clean(row.get("Ülke / Bölge"))
            if raw_country:
                current_country = _strip_country_number(raw_country)
            raw_category = _clean(row.get("Ana Kategori"))
            if raw_category:
                current_category = normalize_category(raw_category)
            if not current_country or not current_category:
                continue
            copy = dict(row)
            copy["Ana Kategori"] = current_category
            grouped.setdefault(current_country, []).append(copy)
        for country, country_rows in grouped.items():
            result[country] = self._rows_to_records(country_rows, "Localize Sub Kredi Dereceleri")
        return result

    def _build_country_records(self) -> Dict[str, List[Dict[str, Any]]]:
        result: Dict[str, List[Dict[str, Any]]] = {}
        for country, sheet in COUNTRY_SHEET_ALIASES.items():
            result[country] = self._rows_to_records(self.sheet_rows(sheet), sheet)
        return result

    def categories(self) -> List[str]:
        weighted = self._category_weights.get("154", {})
        return sorted(self._category_meta, key=lambda item: (-weighted.get(item, 0), item))

    def category_metadata(self) -> List[Dict[str, Any]]:
        return [
            {
                **self._category_meta[category],
                "weight_154": self._category_weights["154"].get(category, 0),
                "weight_64": self._category_weights["64"].get(category, 0),
            }
            for category in self.categories()
        ]

    def countries(self) -> List[Dict[str, str]]:
        result = []
        for row in self.sheet_rows("Ülke/Bölge Listesi"):
            country = _clean(row.get("Ülke / Bölge"))
            if country:
                result.append(
                    {
                        "name": country,
                        "description": _clean(row.get("Kapsam / Açıklama")),
                    }
                )
        return result

    def country_profiles(self) -> List[Dict[str, str]]:
        """UI'da seçilebilen, kendine ait kategorizasyon sayfası olan profiller."""
        descriptions = {item["name"]: item["description"] for item in self.countries()}
        return [
            {"name": country, "description": descriptions.get(country, "")}
            for country in COUNTRY_SHEET_ALIASES
        ]

    def category_weights(self, pool: str = "154") -> Dict[str, int]:
        return dict(self._category_weights.get(str(pool), self._category_weights["154"]))

    def basic_category_weights(self, country: Optional[str] = None) -> Dict[str, int]:
        """Yalnızca ``Temel Kategoriler`` işaretli alt hedeflerin ağırlıkları."""
        weights: Dict[str, int] = {}
        for record in self.records_for_country(country):
            if record.get("is_base"):
                category = record["category"]
                weights[category] = weights.get(category, 0) + max(
                    1, int(record.get("weight_154", 0))
                )
        return weights

    def records_for_country(self, country: Optional[str] = None) -> List[Dict[str, Any]]:
        if country and country in self._country_records:
            return [dict(record) for record in self._country_records[country]]
        records = [dict(record) for record in self._generic_records]
        localized = self._localized_records.get(_clean(country), []) if country else []
        if not localized:
            return records
        localized_categories = {record["category"] for record in localized}
        return [r for r in records if r["category"] not in localized_categories] + [
            dict(record) for record in localized
        ]

    def subcategories(self, category: str, country: Optional[str] = None) -> List[Dict[str, Any]]:
        normalized = normalize_category(category)
        return [r for r in self.records_for_country(country) if r["category"] == normalized]

    def find_record(
        self, category: str, subcategory: str, country: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        normalized = normalize_category(category)
        sub_key = _key(subcategory)
        for record in self.subcategories(normalized, country):
            if _key(record["subcategory"]) == sub_key:
                return dict(record)
        return None

    @staticmethod
    def _weighted_deficit_choice(
        counts: Mapping[str, int], weights: Mapping[str, int], allowed: Optional[Sequence[str]] = None
    ) -> str:
        candidates = list(allowed) if allowed else list(weights)
        candidates = [item for item in candidates if weights.get(item, 0) > 0]
        if not candidates:
            candidates = list(allowed or counts.keys())
        if not candidates:
            raise ValueError("Seçim için kullanılabilir hedef bulunamadı.")
        total_count = sum(max(0, int(counts.get(item, 0))) for item in candidates)
        total_weight = sum(max(0, int(weights.get(item, 1))) for item in candidates) or len(candidates)
        scores = {
            item: ((total_count + 1) * max(1, int(weights.get(item, 1))) / total_weight)
            - max(0, int(counts.get(item, 0)))
            for item in candidates
        }
        top_score = max(scores.values())
        top = [item for item, score in scores.items() if abs(score - top_score) < 1e-9]
        return random.choice(top)

    def choose_category(
        self,
        counts: Mapping[str, int],
        pool: str = "154",
        allowed: Optional[Sequence[str]] = None,
        basic_only: bool = False,
        country: Optional[str] = None,
    ) -> str:
        normalized_allowed = [normalize_category(item) for item in allowed] if allowed else None
        weights = self.basic_category_weights(country) if basic_only else self.category_weights(pool)
        return self._weighted_deficit_choice(counts, weights, normalized_allowed)

    def choose_subcategory(
        self,
        category: str,
        counts: Mapping[str, int],
        country: Optional[str] = None,
        pool: str = "154",
        scope: Optional[str] = None,
        basic_only: bool = False,
    ) -> str:
        records = self.subcategories(category, country)
        if basic_only:
            records = [record for record in records if record.get("is_base")]
            if not records:
                raise ValueError(f"{category} için temel alt kategori bulunamadı.")
        if scope in {"global", "local"}:
            ratio_key = f"{scope}_ratio"
            scoped = [record for record in records if float(record.get(ratio_key, 0)) > 0]
            records = scoped or records
        weight_key = "weight_64" if str(pool) == "64" else "weight_154"
        weights = {
            record["subcategory"]: int(record.get(weight_key, 0))
            for record in records
            if int(record.get(weight_key, 0)) > 0
        }
        if not weights:
            weights = {record["subcategory"]: 1 for record in records}
        return self._weighted_deficit_choice(counts, weights)

    def target(
        self,
        category: str,
        subcategory: str,
        country: Optional[str] = None,
        pool: str = "154",
    ) -> AlgorithmTarget:
        record = self.find_record(category, subcategory, country)
        if not record:
            record = {
                "global_ratio": 0.5,
                "local_ratio": 0.5,
                "guide": "",
                "source_sheet": "fallback",
                "weight_154": 1,
                "weight_64": 1,
            }
        meta = self._category_meta.get(normalize_category(category), {})
        focus_list = [f for f in meta.get("focus", []) if f and f != '"']
        drafts_list = [d for d in meta.get("drafts", []) if d]
        category_focus = ", ".join(focus_list)
        category_drafts = ", ".join(drafts_list)

        pool_key = "64" if str(pool) == "64" else "154"
        return AlgorithmTarget(
            category=normalize_category(category),
            subcategory=subcategory,
            category_weight=self.category_weights(pool_key).get(normalize_category(category), 1),
            subcategory_weight=max(1, int(record.get(f"weight_{pool_key}", 1))),
            global_ratio=float(record.get("global_ratio", 0.5)),
            local_ratio=float(record.get("local_ratio", 0.5)),
            guide=_clean(record.get("guide")),
            source_sheet=_clean(record.get("source_sheet")),
            category_focus=category_focus,
            category_drafts=category_drafts,
        )

    def build_credit_queue(
        self,
        pool: str = "154",
        country: Optional[str] = None,
        basic_only: bool = False,
        total_count: Optional[int] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
    ) -> List[AlgorithmTarget]:
        """Yalnızca Sheet kredi ağırlıklarından AlgorithmTarget kuyruğu oluşturur.

        - pool="154": 90 alt kategorinin weight_154 kredisi kadar (tam 154 soru)
        - pool="64": weight_64 kredisi kadar (tam 64 soru)
        - basic_only=True: Temel (is_base) alt kategorilerden tam 21 soru
        - total_count verilirse krediler bu toplama en büyük kalan yöntemiyle paylaştırılır

        Slotlar kategoriler arasında dengeli ve dönüşümlü (interleaved) sıralanır.
        """
        records = self.records_for_country(country)
        if category:
            normalized_category = normalize_category(category)
            records = [record for record in records if record["category"] == normalized_category]
        if subcategory:
            sub_key = _key(subcategory)
            records = [record for record in records if _key(record["subcategory"]) == sub_key]
        pool_key = "64" if str(pool) == "64" else "154"
        weight_key = f"weight_{pool_key}"

        weighted_records: List[tuple[Dict[str, Any], int]] = []
        for record in records:
            if basic_only and not record.get("is_base"):
                continue
            weight = 1 if basic_only else max(0, int(record.get(weight_key, 0)))
            if weight > 0:
                weighted_records.append((record, weight))

        if not weighted_records:
            raise ValueError("Sheet kredi havuzunda seçime uygun kategori/alt kategori bulunamadı.")

        natural_total = sum(weight for _, weight in weighted_records)
        requested_total = natural_total if total_count is None else int(total_count)
        if requested_total < 1:
            return []

        if requested_total == natural_total:
            allocations = [weight for _, weight in weighted_records]
        else:
            exact = [requested_total * weight / natural_total for _, weight in weighted_records]
            allocations = [int(value) for value in exact]
            remaining = requested_total - sum(allocations)
            remainder_order = list(range(len(weighted_records)))
            random.shuffle(remainder_order)
            remainder_order.sort(key=lambda index: exact[index] - allocations[index], reverse=True)
            for index in remainder_order[:remaining]:
                allocations[index] += 1

        cat_slots: Dict[str, List[AlgorithmTarget]] = {}
        for (record, _weight), slots_count in zip(weighted_records, allocations):
            if slots_count <= 0:
                continue

            target_obj = self.target(
                record["category"],
                record["subcategory"],
                country=country,
                pool=pool_key,
            )
            cat_name = target_obj.category
            cat_slots.setdefault(cat_name, []).extend([target_obj] * slots_count)

        # Kategori içi hafif karıştırma
        for cat_name, targets in cat_slots.items():
            random.shuffle(targets)

        # Kategoriler arasında interleaved dağıtım (round-robin)
        queue: List[AlgorithmTarget] = []
        cat_keys = list(cat_slots.keys())
        random.shuffle(cat_keys)

        active = True
        while active:
            active = False
            for cat in cat_keys:
                if cat_slots[cat]:
                    queue.append(cat_slots[cat].pop(0))
                    active = True

        return queue

    def summary(self) -> Dict[str, Any]:
        return {
            "source": dict(self.source),
            "category_count": len(self.categories()),
            "country_count": len(self.countries()),
            "generic_subcategory_count": len(self._generic_records),
            "localized_country_count": len(self._localized_records),
            "country_specific_sheets": sorted(self._country_records),
            "pool_totals": {
                pool: sum(weights.values()) for pool, weights in self._category_weights.items()
            },
            "basic_category_count": len(self.basic_category_weights()),
            "basic_subcategory_count": sum(
                1 for record in self._generic_records if record.get("is_base")
            ),
        }


_repository: Optional[SheetAlgorithmRepository] = None


def get_sheet_algorithm_repository(force_refresh: bool = False) -> SheetAlgorithmRepository:
    global _repository
    if _repository is None or force_refresh:
        _repository = SheetAlgorithmRepository()
    return _repository
