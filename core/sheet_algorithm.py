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

from core.translation_service import language_codes_from_sheet


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = ROOT_DIR / "data" / "knowley_algorithm_snapshot.json"

POOL_COLUMNS = {
    "154": "154 Kredi Havuzu",
    "64": "64 Kredi Havuzu",
}

LEGACY_COUNTRY_SHEET_ALIASES = {
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
    section: str = ""
    target_country: Optional[str] = None
    target_country_code: str = "GL"


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
        self._country_profile_sheets: Dict[str, str] = {}
        self._load_country_sidecars()
        self._category_meta = self._build_category_meta()
        self._category_weights = self._build_category_weights()
        self._generic_records = self._build_generic_records()
        self._localized_records = self._build_localized_records()
        self._country_records = self._build_country_records()

    def _load_country_sidecars(self) -> None:
        """Merge connector-exported country data until the regular sheet sync runs."""
        data_dir = ROOT_DIR / "data"
        metadata_path = data_dir / "knowley_country_region_metadata.json"
        if metadata_path.exists():
            with metadata_path.open("r", encoding="utf-8") as handle:
                metadata = json.load(handle)
            self.source.update(metadata.get("source", {}))
            self._sheets["Ülke/Bölge Listesi"] = {
                "title": "Ülke/Bölge Listesi",
                "rows": metadata.get("rows", []),
            }

        localize_path = data_dir / "knowley_localize_sub_snapshot.json"
        if localize_path.exists():
            with localize_path.open("r", encoding="utf-8") as handle:
                localized_sheet = json.load(handle)
            self._sheets["Localize Sub Kredi Dereceleri"] = localized_sheet

        profile_paths = sorted(data_dir.glob("knowley_country_profiles_*.json"))
        for path in profile_paths:
            with path.open("r", encoding="utf-8") as handle:
                profiles = json.load(handle)
            for profile in profiles:
                country = _clean(profile.get("name"))
                title = _clean(profile.get("title"))
                if not country or not title:
                    continue
                self._country_profile_sheets[country] = title
                self._sheets[title] = {
                    "title": title,
                    "sheet_id": profile.get("sheet_id"),
                    "columns": profile.get("columns", []),
                    "rows": profile.get("rows", []),
                }

        if not self._country_profile_sheets:
            known_countries = {
                _clean(row.get("Ülke / Bölge"))
                for row in self.sheet_rows("Ülke/Bölge Listesi")
                if _clean(row.get("Ülke / Bölge"))
            }
            for title in self._sheets:
                if title == "Birleşik Kralllık Kategorizasyon":
                    country = "Birleşik Krallık"
                else:
                    country = re.sub(
                        r"\s+Kategorizasyon(?:u)?$", "", title, flags=re.I
                    )
                if country in known_countries:
                    self._country_profile_sheets[country] = title
            for country, title in LEGACY_COUNTRY_SHEET_ALIASES.items():
                if country not in self._country_profile_sheets and title in self._sheets:
                    self._country_profile_sheets[country] = title

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
        current_section = ""
        for row in self.sheet_rows("Sub Kategorilerin GL-LO Dengesi"):
            raw_category = _clean(row.get("Ana Kategori"))
            if raw_category:
                if raw_category.startswith("BÖLÜM"):
                    section_match = re.match(r"BÖLÜM\s+(\d+)", raw_category, re.I)
                    current_section = f"section_{section_match.group(1)}" if section_match else ""
                    current_category = ""
                    continue
                current_category = normalize_category(raw_category)
            subcategory = _clean(row.get("Alt Kategori (Sub)"))
            if not current_category or not subcategory:
                continue
            result[(_key(current_category), _key(subcategory))] = {
                "ratio_text": _clean(row.get("GL - LO Dengesi / Öge")),
                "guide": _clean(row.get("Odak & Kapsam Kılavuzu")),
                "section": current_section,
            }
        return result

    def _guidance_by_category(self) -> Dict[str, List[Dict[str, Any]]]:
        """GL/LO sayfasındaki odakları kategori içindeki sıralarıyla döndürür.

        Bölüm 3'ün ülkeye özel alt kategori adları, kılavuzdaki genel adlarla
        birebir aynı değildir. Satır sırası bu iki tablo arasındaki resmi eşlemedir.
        """
        result: Dict[str, List[Dict[str, Any]]] = {}
        current_category = ""
        current_section = ""
        for row in self.sheet_rows("Sub Kategorilerin GL-LO Dengesi"):
            raw_category = _clean(row.get("Ana Kategori"))
            if raw_category:
                if raw_category.startswith("BÖLÜM"):
                    section_match = re.match(r"BÖLÜM\s+(\d+)", raw_category, re.I)
                    current_section = f"section_{section_match.group(1)}" if section_match else ""
                    current_category = ""
                    continue
                current_category = normalize_category(raw_category)
            subcategory = _clean(row.get("Alt Kategori (Sub)"))
            if not current_category or not subcategory:
                continue
            result.setdefault(_key(current_category), []).append(
                {
                    "subcategory": subcategory,
                    "ratio_text": _clean(row.get("GL - LO Dengesi / Öge")),
                    "guide": _clean(row.get("Odak & Kapsam Kılavuzu")),
                    "section": current_section,
                }
            )
        return result

    def _rows_to_records(self, rows: Iterable[Mapping[str, Any]], source_sheet: str) -> List[Dict[str, Any]]:
        guidance = self._guidance_index()
        ordered_guidance = self._guidance_by_category()
        records: List[Dict[str, Any]] = []
        current_category = ""
        category_position = -1
        for row in rows:
            raw_category = _clean(row.get("Ana Kategori"))
            if raw_category:
                if raw_category.upper() in {"TOPLAM", "TOTAL"}:
                    current_category = ""
                    continue
                current_category = normalize_category(raw_category)
                category_position = 0
            elif current_category:
                category_position += 1
            subcategory = _clean(row.get("Alt Kategori (Sub)"))
            if not current_category or not subcategory or "ana kategori" in _key(subcategory):
                continue
            guide_data = guidance.get((_key(current_category), _key(subcategory)), {})
            if not guide_data:
                category_guidance = ordered_guidance.get(_key(current_category), [])
                if category_guidance and category_position >= 0:
                    # Bazı ülke profilleri genel kılavuzdan bir alt hedef daha
                    # ayrıntılıdır. Fazla satırlar son odak kapsamını devralır.
                    guide_data = category_guidance[min(category_position, len(category_guidance) - 1)]
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
                    "section": guide_data.get("section", ""),
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
            # Boş kategori hücrelerini burada doldurmak, _rows_to_records içindeki
            # kategori-içi sıra sayacını her satırda sıfırlar. Orijinal boşlukları
            # koruyarak Bölüm 3 odak kılavuzuyla satır sırasından eşleştiriyoruz.
            copy["Ana Kategori"] = normalize_category(raw_category) if raw_category else ""
            grouped.setdefault(current_country, []).append(copy)
        for country, country_rows in grouped.items():
            result[country] = self._rows_to_records(country_rows, "Localize Sub Kredi Dereceleri")
        return result

    def _build_country_records(self) -> Dict[str, List[Dict[str, Any]]]:
        result: Dict[str, List[Dict[str, Any]]] = {}
        for country, sheet in self._country_profile_sheets.items():
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

    def countries(self) -> List[Dict[str, Any]]:
        result = []
        for row in self.sheet_rows("Ülke/Bölge Listesi"):
            country = _clean(row.get("Ülke / Bölge"))
            if country:
                language_names = [
                    _clean(value)
                    for value in re.split(r"\s*,\s*", _clean(row.get("Eşleşen Dil(ler)")))
                    if _clean(value)
                ]
                result.append(
                    {
                        "name": country,
                        "description": _clean(row.get("Kapsam / Açıklama")),
                        "code": _clean(row.get("Kod")),
                        "language_names": language_names,
                        "languages": language_codes_from_sheet(
                            row.get("Eşleşen Dil(ler)"), row.get("Kod")
                        ),
                        "profile_sheet": self._country_profile_sheets.get(country)
                        or _clean(row.get("profile_sheet")),
                    }
                )
        return result

    def country_profiles(self) -> List[Dict[str, Any]]:
        """UI'da seçilebilen, kendine ait kategorizasyon sayfası olan profiller."""
        return [
            country for country in self.countries() if country.get("profile_sheet")
        ]

    def country_code(self, country: Optional[str], scope: Optional[str] = None) -> str:
        """Return the configured country/region code, or GL for global questions."""
        if str(scope or "").strip().lower() != "local":
            return "GL"
        normalized = _clean(country)
        for item in self.countries():
            if item["name"] == normalized:
                return item.get("code") or "GL"
        return "GL"

    def languages_for_country(self, country: Optional[str]) -> List[str]:
        for item in self.countries():
            if item["name"] == _clean(country):
                return list(item.get("languages") or [])
        return []

    def question_languages(self, country: Optional[str]) -> List[str]:
        """Languages every LOCAL question of `country` must carry: its Sheets languages plus English."""
        languages = list(dict.fromkeys(self.languages_for_country(country)))
        if "en" not in languages:
            languages.append("en")
        return languages

    def category_weights(
        self, pool: str = "154", country: Optional[str] = None
    ) -> Dict[str, int]:
        pool_key = "64" if str(pool) == "64" else "154"
        if country and country in self._country_records:
            weights: Dict[str, int] = {}
            for record in self._country_records[country]:
                weight = max(0, int(record.get(f"weight_{pool_key}", 0)))
                if weight:
                    category = record["category"]
                    weights[category] = weights.get(category, 0) + weight
            return weights
        return dict(self._category_weights.get(pool_key, self._category_weights["154"]))

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
        country_name = _clean(country)
        if country_name in self._country_records:
            return [dict(record) for record in self._country_records[country_name]]

        records = [dict(record) for record in self._generic_records]
        localized = self._localized_records.get(country_name, []) if country_name else []
        if not localized:
            return records
        # Ülke profili yalnızca Bölüm 3 (Spor/Müzik/Edebiyat)
        # yer tutucularını gerçek yerel alt kategorilerle değiştirir.
        # Bölüm 1 oranları ve Bölüm 2 öge hedefleri genel tablodan kalır.
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
        weights = (
            self.basic_category_weights(country)
            if basic_only
            else self.category_weights(pool, country)
        )
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
        target_country: Optional[str] = None,
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
        category_weights = self.category_weights(pool_key, country)
        return AlgorithmTarget(
            category=normalize_category(category),
            subcategory=subcategory,
            category_weight=category_weights.get(normalize_category(category), 1),
            subcategory_weight=max(1, int(record.get(f"weight_{pool_key}", 1))),
            global_ratio=float(record.get("global_ratio", 0.5)),
            local_ratio=float(record.get("local_ratio", 0.5)),
            guide=_clean(record.get("guide")),
            source_sheet=_clean(record.get("source_sheet")),
            category_focus=category_focus,
            category_drafts=category_drafts,
            section=_clean(record.get("section")),
            target_country=target_country,
            target_country_code=self.country_code(target_country, "local"),
        )

    def _expand_localized_placeholders(
        self,
        records: List[Dict[str, Any]],
        *,
        basic_only: bool,
    ) -> List[Dict[str, Any]]:
        """Bölüm 3 yer tutucularını gerçek ülke/bölge alt hedeflerine açar.

        Her kategori için bir ülke/bölge seçilir. Seçilen profilin alt
        kategori kredi değerleri kullanıldığından kategori toplamı ve alt
        kategori dağılımı Sheets ile birebir korunur.
        """
        expanded: List[Dict[str, Any]] = []
        available_countries = list(self._localized_records)
        for record in records:
            if _key(record.get("subcategory")) != _key("Localize Sub"):
                expanded.append(record)
                continue

            category = record["category"]
            candidates = [
                country
                for country in available_countries
                if any(item["category"] == category for item in self._localized_records[country])
            ]
            if not candidates:
                expanded.append(record)
                continue
            selected_country = random.choice(candidates)
            localized = [
                dict(item)
                for item in self._localized_records[selected_country]
                if item["category"] == category
            ]
            if basic_only:
                localized = [item for item in localized if item.get("is_base")]
            for item in localized:
                item["target_country"] = selected_country
                expanded.append(item)
        return expanded

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
        pool_key = "64" if str(pool) == "64" else "154"
        if country:
            records = self.records_for_country(country)
            for record in records:
                record.setdefault("target_country", country)
        else:
            records = []
            for country_info in self.countries():
                country_name = country_info["name"]
                for source_record in self.records_for_country(country_name):
                    record = dict(source_record)
                    record["target_country"] = country_name
                    records.append(record)
            if not records:
                records = self._expand_localized_placeholders(
                    self.records_for_country(None),
                    basic_only=basic_only,
                )
        if category:
            normalized_category = normalize_category(category)
            records = [record for record in records if record["category"] == normalized_category]
        if subcategory:
            sub_key = _key(subcategory)
            # "Localize Sub" Bölüm 3'ün UI yer tutucusudur; genişletme
            # sonrasında seçilen ülkenin tüm gerçek alt hedeflerini kapsar.
            if sub_key != _key("Localize Sub"):
                records = [record for record in records if _key(record["subcategory"]) == sub_key]
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
                country=record.get("target_country") or country,
                pool=pool_key,
                target_country=record.get("target_country") or country,
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
