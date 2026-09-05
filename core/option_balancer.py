import logging
import random
import threading
from collections import Counter
from typing import Dict, List, Optional, Any

logger = logging.getLogger("OptionBalancer")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [OptionBalancer] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

VALID_OPTIONS = ["A", "B", "C", "D"]


class OptionBalancer:
    """
    Soru üretiminde şık dağılımını matematiksel olarak dengeleyen mekanizma.
    
    1. Başlangıç Taraması: Firestore'daki mevcut 'questions' koleksiyonundan A, B, C, D sayılarını tespit eder.
    2. Catch-up (Dengeleme) Modu: Şıklar eşitlenene kadar (örneğin A öndeyken), sayısı en az olan şıkları seçer.
    3. 4-Cycle (Döngü) Modu: Şıklar eşitlendiğinde, her 4 soruluk blokta ['A', 'B', 'C', 'D'] listesini karıştırıp
       her şıktan tam olarak 1'er kez doğru cevap üretecek bir döngü havuzu kullanır.
    4. Çok Dilli Eşleme: Doğru cevabı ve 3 çeldiriciyi tüm dillerdeki (tr, en, es, pt, de) çevirilere senkronize dağıtır.
    """

    _instance: Optional["OptionBalancer"] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        db: Optional[Any] = None,
        auto_scan: bool = True,
        initial_counts: Optional[Dict[str, int]] = None
    ):
        self.lock = threading.Lock()
        self.cycle_pool: List[str] = []
        
        if initial_counts is not None:
            self.counts: Dict[str, int] = {
                opt: int(initial_counts.get(opt, 0)) for opt in VALID_OPTIONS
            }
        else:
            self.counts: Dict[str, int] = {opt: 0 for opt in VALID_OPTIONS}
            if auto_scan:
                self.scan_firestore(db=db)

        self._log_initial_status()

    @classmethod
    def get_instance(cls, db: Optional[Any] = None, force_refresh: bool = False) -> "OptionBalancer":
        """Thread-safe singleton instance döndürür."""
        with cls._lock:
            if cls._instance is None or force_refresh:
                cls._instance = cls(db=db, auto_scan=True)
            return cls._instance

    def scan_firestore(self, db: Optional[Any] = None) -> None:
        """Firestore'daki soruları tarayarak mevcut doğru cevap şıklarının dağılımını okur."""
        try:
            if db is None:
                from database.db_manager import init_firebase
                db = init_firebase()

            logger.info("🔍 Firestore 'questions' koleksiyonu taranıyor...")
            docs = db.collection("questions").stream()

            new_counts = Counter({opt: 0 for opt in VALID_OPTIONS})
            total = 0

            for doc in docs:
                data = doc.to_dict()
                correct_opt = (
                    data.get("correct_option") or 
                    data.get("correct_answer") or 
                    data.get("answer")
                )
                if correct_opt is not None:
                    key = str(correct_opt).strip().upper()
                    if key in VALID_OPTIONS:
                        new_counts[key] += 1
                        total += 1

            with self.lock:
                for opt in VALID_OPTIONS:
                    self.counts[opt] = new_counts[opt]

            logger.info(
                f"📊 Firestore taraması tamamlandı. Toplam Soru: {total} | "
                f"A: {self.counts['A']}, B: {self.counts['B']}, C: {self.counts['C']}, D: {self.counts['D']}"
            )
        except Exception as e:
            logger.warning(f"⚠️ Firestore taranırken hata veya kimlik bulunamadı, sayaçlar 0'dan başlıyor: {e}")

    def is_equalized(self) -> bool:
        """Tüm şıkların sayıları birbirine eşit mi?"""
        c = self.counts
        return c["A"] == c["B"] == c["C"] == c["D"]

    def _log_initial_status(self) -> None:
        """Başlangıç durumunu loglar."""
        c = self.counts
        if self.is_equalized():
            logger.info(f"⚖️ Dağılım tam dengede! (A={c['A']}, B={c['B']}, C={c['C']}, D={c['D']}) -> 4'lü Döngü Modu aktif.")
        else:
            min_val = min(c.values())
            min_opts = [opt for opt in VALID_OPTIONS if c[opt] == min_val]
            logger.info(
                f"🎯 Dengeleme Modu (Catch-up) aktif. Mevcut: A={c['A']}, B={c['B']}, C={c['C']}, D={c['D']} "
                f"| Öncelikli Şıklar (en az = {min_val}): {min_opts}"
            )

    def get_next_target_option(self) -> str:
        """
        Dengeleme kurallarına göre sıradaki sorunun doğru cevap şıkkı harfini belirler ve sayacı günceller.
        
        - Döngü Havuzunda eleman varsa: Mevcut 4'lü bloktan sıradaki şıkkı çeker.
        - Eşitlenmişse: Yeni 4'lü rastgele döngü havuzu oluşturur (her 4 soruda 1'er kez A, B, C, D).
        - Eşitlenmemişse (Catch-up): Sayısı en az olan şık(lar) arasından rastgele birini seçer (A öndeyse A asla seçilmez).
        """
        with self.lock:
            # 1. Durum: Aktif bir 4'lü döngü bloğu varsa veya eşitlenmişse döngü modunu işlet
            if self.cycle_pool or self.is_equalized():
                if not self.cycle_pool:
                    self.cycle_pool = list(VALID_OPTIONS)
                    random.shuffle(self.cycle_pool)
                    logger.info(f"🔄 [4-Cycle] Yeni dengeli döngü havuzu karıştırıldı: {self.cycle_pool}")

                target = self.cycle_pool.pop(0)
                self.counts[target] += 1
                logger.info(
                    f"🎯 [4-Cycle] Atanan Şık: {target} | Kalan Döngü: {self.cycle_pool} | "
                    f"Güncel Sayaç: A={self.counts['A']}, B={self.counts['B']}, C={self.counts['C']}, D={self.counts['D']}"
                )
                return target

            # 2. Durum: Dengeleme / Yakalama Modu (Catch-up Mode)
            # Sayısı en az olan şıkları bul
            min_count = min(self.counts.values())
            min_candidates = [opt for opt in VALID_OPTIONS if self.counts[opt] == min_count]

            target = random.choice(min_candidates)
            self.counts[target] += 1

            logger.info(
                f"🎯 [Catch-up] Atanan Şık: {target} (Adaylar: {min_candidates}, En Az: {min_count}) | "
                f"Güncel Sayaç: A={self.counts['A']}, B={self.counts['B']}, C={self.counts['C']}, D={self.counts['D']}"
            )

            # Eğer bu atama ile tüm şıklar eşitlendiyse bildir
            if self.is_equalized():
                logger.info(
                    f"🎉 TEBRİKLER! Tüm şıklar eşitlendi (A={self.counts['A']}, B={self.counts['B']}, "
                    f"C={self.counts['C']}, D={self.counts['D']})! Bundan sonra 4'lü Dengeli Döngü Modu kullanılacak."
                )

            return target

    def balance_question(self, question: Any, target_option: Optional[str] = None) -> Any:
        """
        Üretilen sorunun şıklarını ve çoklu dil çevirilerini belirlenen hedef şıkkına göre yeniden eşler.
        
        - question: GeneratedQuestion (Pydantic) veya dict
        - target_option: Belirtilmezse get_next_target_option() ile otomatik seçilir.
        """
        if target_option is None:
            target_option = self.get_next_target_option()

        # Orijinal doğru cevap harfini tespit et (varsayılan A)
        raw_correct = "A"
        if isinstance(question, dict):
            raw_correct = question.get("correct_answer") or question.get("correct_option") or "A"
        elif hasattr(question, "correct_answer"):
            raw_correct = getattr(question, "correct_answer", "A")

        raw_correct = str(raw_correct).strip().upper()
        if raw_correct not in VALID_OPTIONS:
            raw_correct = "A"

        # Şık eşleme permütasyonu (harf eşleşme haritası) oluştur
        # 1. Orijinal doğru cevap -> Hedef şık
        # 2. 3 çeldirici -> Kalan 3 hedef şık (rastgele karıştırılarak)
        orig_distractors = [opt for opt in VALID_OPTIONS if opt != raw_correct]
        target_distractors = [opt for opt in VALID_OPTIONS if opt != target_option]
        
        shuffled_target_distractors = list(target_distractors)
        random.shuffle(shuffled_target_distractors)

        letter_map = {raw_correct: target_option}
        for orig_k, dest_k in zip(orig_distractors, shuffled_target_distractors):
            letter_map[orig_k] = dest_k

        # Çoklu dilli çevirilerdeki seçenekleri bu haritaya göre yeniden konumlandır
        if isinstance(question, dict):
            translations = question.get("translations", {})
            for lang, content in translations.items():
                if isinstance(content, dict) and "options" in content:
                    orig_opts = content["options"]
                    if isinstance(orig_opts, dict):
                        new_opts = {
                            letter_map[orig]: orig_opts[orig]
                            for orig in VALID_OPTIONS
                            if orig in orig_opts
                        }
                        content["options"] = new_opts

            question["correct_answer"] = target_option
            question["correct_option"] = target_option

            # Kök düzeyde options varsa güncelle
            if "options" in question and isinstance(question["options"], dict):
                orig_opts = question["options"]
                question["options"] = {
                    letter_map[orig]: orig_opts[orig]
                    for orig in VALID_OPTIONS
                    if orig in orig_opts
                }

        else:
            # Pydantic GeneratedQuestion nesnesi
            translations = getattr(question, "translations", {})
            for lang, content in translations.items():
                orig_opts = getattr(content, "options", None)
                if orig_opts is not None:
                    if isinstance(orig_opts, dict):
                        orig_opts_dict = orig_opts
                    elif hasattr(orig_opts, "model_dump"):
                        orig_opts_dict = orig_opts.model_dump()
                    else:
                        orig_opts_dict = orig_opts.__dict__

                    new_opts_dict = {
                        letter_map[orig]: orig_opts_dict.get(orig, "")
                        for orig in VALID_OPTIONS
                    }

                    # Options modelini içe aktar veya yeniden oluştur
                    from core.generator import Options
                    content.options = Options(**new_opts_dict)

            setattr(question, "correct_answer", target_option)
            setattr(question, "correct_option", target_option)

        logger.info(
            f"🔀 Soru Şıkları Dengelendi: LLM '{raw_correct}' -> Hedef '{target_option}' "
            f"(Eşleme: {letter_map})"
        )
        return question

    def get_distribution_summary(self) -> Dict[str, Any]:
        """Güncel dağılım özetini döndürür."""
        with self.lock:
            total = sum(self.counts.values())
            percentages = {
                opt: (self.counts[opt] / total * 100) if total > 0 else 0.0
                for opt in VALID_OPTIONS
            }
            return {
                "counts": dict(self.counts),
                "percentages": percentages,
                "total": total,
                "is_equalized": self.is_equalized(),
                "cycle_pool": list(self.cycle_pool)
            }


def get_option_balancer(db: Optional[Any] = None) -> OptionBalancer:
    """Modüller için kolay erişim sağlayan fonksiyon."""
    return OptionBalancer.get_instance(db=db)
