"""Her 10 soruda tam 3 görsel soru ve Firebase Storage görsel akışı."""

from __future__ import annotations

import hashlib
import random
import re
from pathlib import PurePosixPath
from typing import Any, Optional
from urllib.parse import quote

import requests


VISUAL_CATEGORY_FOCUS = {
    "Coğrafya": "bayrak, çok ünlü simge yapı, doğal oluşum veya ayırt edilebilir harita",
    "Gastronomi": "yemeğin kendisi, tatlı, içecek veya belirgin mutfak ürünü",
    "Sinema": "dünya çapında çok ünlü oyuncu veya yönetmenin portresi",
    "Spor": "çok ünlü sporcu, ikonik kupa, forma veya kolay tanınan spor ekipmanı",
    "Müzik": "dünya çapında çok ünlü müzisyen veya ayırt edilebilir enstrüman",
    "Edebiyat": "yalnızca dünya çapında çok ünlü yazar veya şair portresi",
    "Tarih": "dünya çapında çok ünlü tarihi kişi, anıt veya ikonik eser",
    "Genel Kültür": "herkesçe tanınabilir bayrak, simge, ünlü kişi, nesne veya yapı",
    "Mitoloji": "ikonik mitolojik figürün çok bilinen heykeli, mozaiği veya klasik tasviri",
    "Resim": "dünya çapında çok ünlü sanat eseri, ressam veya heykel",
    "Biyoloji": "kolay ayırt edilebilen hayvan, bitki veya doğal canlı",
    "Astronomi": "kolay tanınan gezegen, uydu, galaksi veya astronomik oluşum",
    "Siyaset": "yalnızca dünya çapında çok ünlü lider portresi veya ulusal sembol",
}


FAMOUS_SCIENTIST_EXCEPTIONS = {
    "Fizik": "yalnızca Einstein veya Newton düzeyinde dünya çapında çok ünlü bilim insanı portresi",
    "Kimya": "yalnızca Marie Curie veya Mendeleyev düzeyinde dünya çapında çok ünlü bilim insanı portresi",
}


def visual_focus_for_category(category: str, subcategory: str = "") -> Optional[str]:
    """Kategori görsel eşleştirme sorusuna uygunsa izin verilen odağı döndürür."""
    clean_category = str(category or "").strip()
    focus = VISUAL_CATEGORY_FOCUS.get(clean_category)
    if focus:
        return focus
    if str(subcategory or "").strip().casefold().startswith("temel "):
        return FAMOUS_SCIENTIST_EXCEPTIONS.get(clean_category)
    return None


def is_visual_target(target: Any) -> bool:
    category = target if isinstance(target, str) else getattr(target, "category", "")
    subcategory = "" if isinstance(target, str) else getattr(target, "subcategory", "")
    return visual_focus_for_category(str(category), str(subcategory)) is not None


def prioritize_visual_targets(queue: list[Any], block_size: int = 10, required: int = 3) -> None:
    """Tam blokta en az 3 uygun hedef bulunması için yalnızca sırayı değiştirir."""
    block_end = min(block_size, len(queue))
    if block_end < block_size:
        return
    eligible = [index for index in range(block_end) if is_visual_target(queue[index])]
    if len(eligible) >= required:
        return
    replaceable = [index for index in range(block_end - 1, -1, -1) if not is_visual_target(queue[index])]
    later = [index for index in range(block_end, len(queue)) if is_visual_target(queue[index])]
    needed = min(required - len(eligible), len(replaceable), len(later))
    for target_index, source_index in zip(replaceable[:needed], later[:needed]):
        queue[target_index], queue[source_index] = queue[source_index], queue[target_index]


class VisualQuestionSchedule:
    """Başarıyla kaydedilen her 10 sorunun rastgele 3'ünü görsel yapar."""

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self.rng = rng or random.Random()
        self._position = 0
        self._visual_positions: Optional[set[int]] = None

    @property
    def starts_block(self) -> bool:
        return self._position == 0 and self._visual_positions is None

    def _plan_block(self, targets: Optional[list[Any]] = None) -> None:
        candidates = (
            [index for index, target in enumerate((targets or [])[:10]) if is_visual_target(target)]
            if targets is not None
            else list(range(10))
        )
        selection_count = min(3, len(candidates))
        self._visual_positions = set(self.rng.sample(candidates, selection_count))

    def peek(self, targets: Optional[list[Any]] = None) -> bool:
        """Sıradaki başarılı kaydın görsel olup olmayacağını döndürür."""
        if self._visual_positions is None:
            self._plan_block(targets)
        return self._position in self._visual_positions

    def commit(self) -> None:
        self._position += 1
        if self._position == 10:
            self._position = 0
            self._visual_positions = None


class WikimediaQuestionImageResolver:
    """LLM'nin kesin konu sorgusuna Commons görseli bulur ve Storage'a kopyalar."""

    API_URL = "https://commons.wikimedia.org/w/api.php"
    ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/svg+xml"}

    def __init__(self, *, bucket: Any = None, session: Any = None, timeout: float = 20.0) -> None:
        self.bucket = bucket
        self.session = session or requests.Session()
        if hasattr(self.session, "headers"):
            self.session.headers.update(
                {"User-Agent": "KnowleyTrivia/1.1.0 (question-image-resolver)"}
            )
        self.timeout = timeout

    @staticmethod
    def _value(meta: dict[str, Any], key: str) -> str:
        value = meta.get(key, {})
        return str(value.get("value", "") if isinstance(value, dict) else value).strip()

    def _search(self, query: str) -> dict[str, Any]:
        response = self.session.get(
            self.API_URL,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": 10,
                "prop": "imageinfo",
                "iiprop": "url|mime|extmetadata",
                "iiurlwidth": 1400,
                "format": "json",
                "formatversion": 2,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", [])
        # Güncel bayrak yerine tarihli/eski varyantın seçilmesini engelle;
        # diğer sorgularda Commons'un kendi arama sırası korunur.
        if "flag" in query.casefold():
            def flag_rank(page: dict[str, Any]) -> tuple[int, int]:
                title = str(page.get("title", ""))
                historical = bool(re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b|old|former|histor", title, re.I))
                return (1 if historical else 0, len(title))
            pages = sorted(pages, key=flag_rank)
        for page in pages:
            info_list = page.get("imageinfo") or []
            if not info_list:
                continue
            info = info_list[0]
            mime = str(info.get("mime", "")).lower()
            meta = info.get("extmetadata") or {}
            license_name = self._value(meta, "LicenseShortName")
            image_url = info.get("thumburl") or info.get("url")
            if mime in self.ALLOWED_MIME and image_url and license_name:
                return {
                    "url": image_url,
                    "mime": mime,
                    "source_url": info.get("descriptionurl") or image_url,
                    "license": license_name,
                    "artist": self._value(meta, "Artist"),
                }
        raise ValueError(f"Wikimedia Commons'ta lisanslı ve uygun görsel bulunamadı: {query}")

    def resolve(self, question: Any) -> str:
        query = str(getattr(question, "question_image_search_query", "") or "").strip()
        if not query:
            raise ValueError("Görsel soru için question_image_search_query eksik.")
        result = self._search(query)
        download = self.session.get(result["url"], timeout=self.timeout)
        download.raise_for_status()
        image_bytes = download.content
        if not image_bytes or len(image_bytes) > 12 * 1024 * 1024:
            raise ValueError("Soru görseli boş veya 12 MB sınırından büyük.")

        response_mime = str(download.headers.get("Content-Type", "")).split(";", 1)[0].lower()
        if response_mime in self.ALLOWED_MIME:
            result["mime"] = response_mime

        if self.bucket is None:
            question.question_image_url = result["url"]
        else:
            mime_ext = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/svg+xml": ".svg",
            }
            suffix = mime_ext[result["mime"]]
            digest = hashlib.sha256(image_bytes).hexdigest()
            blob_path = str(PurePosixPath("questions") / "images" / f"{digest}{suffix}")
            blob = self.bucket.blob(blob_path)
            if not blob.exists():
                blob.metadata = {
                    "source": result["source_url"],
                    "license": result["license"],
                    "artist": result["artist"],
                    "search_query": query,
                }
                blob.upload_from_string(image_bytes, content_type=result["mime"])
            question.question_image_url = (
                f"https://storage.googleapis.com/{self.bucket.name}/{quote(blob_path, safe='/')}"
            )

        # Doğru cevabı ele verebilecek arama sorgusu Firestore'a yazılmaz.
        question.question_image_search_query = None
        return str(question.question_image_url)
