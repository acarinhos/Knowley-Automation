import time
import random
import re
import logging
import os
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures
from dataclasses import dataclass, field
from typing import List, Any, Callable, Dict, Optional

from config.categories_config import (
    get_category_color,
    get_subcategory_image,
)
from core.generator import (
    generate_question_with_fallback,
    GeneratedQuestion,
    CategoryMeta,
    SubCategoryMeta,
    QUESTION_SCHEMA_VERSION,
)
from database.db_manager import init_firebase, generate_question_hash, get_storage_bucket
from firebase_admin import firestore
from core.llm_manager import MultiLLMManager
from core.llm_manager import AllProvidersCoolingDown
from core.sheet_algorithm import SheetAlgorithmRepository, get_sheet_algorithm_repository
from core.translation_service import COUNTRY_LANGUAGE_CODES, SUPPORTED_LANGUAGE_CODES, TranslationIncompleteError
from core.starting_difficulty import StartingDifficultyAssigner
from core.visual_questions import (
    VisualQuestionSchedule,
    WikimediaQuestionImageResolver,
    prioritize_visual_targets,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("worker.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

SLEEP_BETWEEN_REQUESTS = 1.5
MAX_BLOCK_RETRIES = 5
SLEEP_BETWEEN_REQUESTS_PARALLEL = 0.3
MAX_CONCURRENCY = 6


def default_concurrency() -> int:
    """GENERATION_CONCURRENCY env (1..6); 1 keeps the strictly sequential behaviour."""
    try:
        return max(1, min(MAX_CONCURRENCY, int(os.getenv("GENERATION_CONCURRENCY", "1"))))
    except ValueError:
        return 1

# 35+ Ülke ve Global Havuzu
TARGET_COUNTRIES: List[str] = [
    "Global",
    "TR", "GB", "DE", "FR", "IT", "ES", "PT", "NL", "GR", "SE", "NO", "PL", "RU",
    "US", "CA", "BR", "AR", "MX", "CL", "CO",
    "JP", "KR", "CN", "IN", "AU", "NZ", "ID", "SA", "AE",
    "EG", "ZA", "NG", "MA", "KE", "IE", "BE", "CH", "AT", "AZ"
]

SUPPORTED_LANGUAGES = list(SUPPORTED_LANGUAGE_CODES)
COUNTRY_PROFILES = [
    item["name"] for item in get_sheet_algorithm_repository().country_profiles()
]


@dataclass
class GenerationOptions:
    """CLI, sonsuz worker ve web UI tarafından paylaşılan üretim ayarları."""

    question_count: Optional[int] = 10
    category: Optional[str] = None
    subcategory: Optional[str] = None
    difficulty: Optional[str] = None
    scope: Optional[str] = None
    country: Optional[str] = None
    languages: List[str] = field(default_factory=lambda: list(SUPPORTED_LANGUAGES))
    duration_seconds: Optional[int] = None
    credit_pool: str = "154"
    generation_mode: Optional[str] = None
    custom_mode: Optional[str] = None
    difficulty_counts_per_10: Dict[str, int] = field(
        default_factory=lambda: {"Kolay": 4, "Orta": 4, "Zor": 2}
    )
    country_profile_enabled: bool = False
    combo_percent: int = 40
    sleep_between_requests: float = SLEEP_BETWEEN_REQUESTS
    max_retries: int = MAX_BLOCK_RETRIES
    write_to_firestore: bool = True
    visual_questions_enabled: bool = True
    concurrency: int = field(default_factory=default_concurrency)

    def validate(self) -> "GenerationOptions":
        self.concurrency = max(1, min(MAX_CONCURRENCY, int(self.concurrency or 1)))
        if self.concurrency > 1 and self.sleep_between_requests == SLEEP_BETWEEN_REQUESTS:
            self.sleep_between_requests = SLEEP_BETWEEN_REQUESTS_PARALLEL  # default only; explicit values are kept
        if self.question_count is not None:
            self.question_count = int(self.question_count)
        if self.question_count is not None and not 1 <= int(self.question_count) <= 10_000:
            raise ValueError("Soru adedi 1 ile 10.000 arasında olmalıdır.")
        if self.difficulty not in {None, "Kolay", "Orta", "Zor"}:
            raise ValueError("Zorluk Kolay, Orta veya Zor olmalıdır.")
        if self.scope not in {None, "global", "local"}:
            raise ValueError("Kapsam global, local veya otomatik olmalıdır.")
        mode = str(self.generation_mode or self.credit_pool).strip().lower()
        if mode not in {"154", "64", "basic", "custom", "country_local"}:
            raise ValueError("Üretim modu 154, 64, basic, custom veya country_local olmalıdır.")
        self.generation_mode = mode
        self.credit_pool = "64" if mode == "64" else "154"
        if mode == "custom":
            if self.question_count is None:
                raise ValueError("Özel üretim modunda sonlu bir soru adedi girilmelidir.")
            if self.custom_mode not in {"equal", "difficulty"}:
                raise ValueError("Özel mod türü eşit dağılım veya zorluk dağılımı olmalıdır.")
            if self.difficulty is not None:
                raise ValueError("Özel modda zorluk, seçilen özel dağılım tarafından belirlenir.")
            if self.custom_mode == "equal":
                if self.question_count < 160:
                    raise ValueError("Tüm kategori ve alt kategorileri eşit dağıtmak için en az 160 soru gerekir.")
                if self.category or self.subcategory:
                    raise ValueError("Eşit dağılım modu tüm kategori ve alt kategorileri kapsar; kategori filtresi kullanılamaz.")
            else:
                if self.question_count % 10 != 0:
                    raise ValueError("Özel zorluk modunda toplam soru sayısı 10'un katı olmalıdır.")
                if not isinstance(self.difficulty_counts_per_10, dict):
                    raise ValueError("Zorluk adetleri Kolay, Orta ve Zor için girilmelidir.")
                counts: Dict[str, int] = {}
                for level in ("Kolay", "Orta", "Zor"):
                    value = self.difficulty_counts_per_10.get(level)
                    if isinstance(value, bool):
                        raise ValueError("Zorluk adetleri 0 ile 10 arasında tam sayı olmalıdır.")
                    try:
                        count = int(value)
                    except (TypeError, ValueError):
                        raise ValueError("Zorluk adetleri 0 ile 10 arasında tam sayı olmalıdır.") from None
                    if count < 0 or count > 10 or str(value).strip() != str(count):
                        raise ValueError("Zorluk adetleri 0 ile 10 arasında tam sayı olmalıdır.")
                    counts[level] = count
                if sum(counts.values()) != 10:
                    raise ValueError("Her 10 soru için Kolay + Orta + Zor adetlerinin toplamı 10 olmalıdır.")
                self.difficulty_counts_per_10 = counts
        else:
            self.custom_mode = None
        local_target_countries: Optional[List[str]] = None
        if mode == "country_local":
            if self.question_count is None:
                raise ValueError("Ülke/bölge yerel hedef modunda soru adedi girilmelidir.")
            if not self.country:
                raise ValueError("Ülke/bölge yerel hedef modunda ülke/bölge seçmek zorunludur.")
            from core.sheet_algorithm import get_sheet_algorithm_repository

            repository = get_sheet_algorithm_repository()
            local_target_countries = repository.local_target_countries(pool="154")
            if self.country not in local_target_countries:
                raise ValueError("Seçilen ülke/bölge için yerel hedef bulunamadı.")
            minimum_count = repository.country_local_target_count(self.country, pool="154")
            if self.question_count < minimum_count:
                raise ValueError(
                    f"{self.country} için en az {minimum_count} soru girilmelidir; "
                    "her yerel alt hedefe en az bir soru ayrılır."
                )
            self.country_profile_enabled = True
            self.scope = "local"
        if self.country_profile_enabled:
            valid_countries = (
                local_target_countries
                if mode == "country_local"
                else COUNTRY_PROFILES
            )
            if self.country not in (valid_countries or []):
                raise ValueError("Ülke profili seçimi, kategorizasyon sayfası bulunan profillerden biri olmalıdır.")
        else:
            self.country = None
            # Profil kapalıyken Sheets GL/LO oranı ve GenerationPlanner ülke
            # seçimi doğal biçimde çalışır; global kapsam zorlanmaz.
            self.scope = None
        self.combo_percent = max(0, min(100, int(self.combo_percent)))
        self.languages = [
            lang for lang in self.languages if lang in COUNTRY_LANGUAGE_CODES
        ]
        if not self.languages:
            raise ValueError("En az bir dil seçilmelidir.")
        # v1.1.0 üretimlerinde süre yalnızca soru içeriğinden otomatik hesaplanır.
        self.duration_seconds = None
        return self

def save_worker_question(db: Any, question_obj: GeneratedQuestion) -> bool:
    try:
        if isinstance(question_obj, dict):
            q_dict = question_obj
        else:
            q_dict = question_obj.model_dump(by_alias=True)

        if q_dict.get("is_visual_question") and not str(q_dict.get("question_image_url") or "").strip():
            raise ValueError("Görsel soru question_image_url olmadan Firestore'a kaydedilemez.")
        if q_dict.get("is_visual_question") and isinstance(q_dict.get("payload"), dict):
            q_dict["payload"] = dict(q_dict["payload"])
            q_dict["payload"]["question_image_url"] = q_dict["question_image_url"]
            q_dict["payload"]["question_image_search_query"] = None

        question_scope = str(q_dict.get("scope") or "global").strip().lower()
        target_country = q_dict.get("target_country") or "Global"
        if question_scope == "global":
            target_country_code = "GL"
        else:
            target_country_code = str(q_dict.get("target_country_code") or "").strip()
            if not target_country_code or target_country_code == "GL":
                target_country_code = get_sheet_algorithm_repository().country_code(
                    target_country, "local"
                )
        q_dict["target_country_code"] = target_country_code
        q_dict["countries"] = [target_country_code]
        
        # Firestore'a 'global' anahtarıyla kaydedilmesini garanti et
        if "difficulty_profile" in q_dict and isinstance(q_dict["difficulty_profile"], dict):
            prof = q_dict["difficulty_profile"]
            if "global_scope" in prof and "global" not in prof:
                prof["global"] = prof.pop("global_scope")

        q_dict.setdefault("base_points", 100)
        q_dict.setdefault("community_distribution", None)
        if not q_dict.get("difficulty"):
            q_dict["difficulty"] = (
                q_dict.get("difficulty_global") or q_dict.get("difficulty_local")
                if question_scope == "global"
                else q_dict.get("difficulty_local")
            )

        translations = q_dict.get("translations", {})
        hash_seed = next(
            (
                translations.get(lang, {}).get("question")
                for lang in ("en", "tr")
                if isinstance(translations.get(lang), dict)
                and translations.get(lang, {}).get("question")
            ),
            None,
        ) or next(
            (
                content.get("question")
                for content in translations.values()
                if isinstance(content, dict) and content.get("question")
            ),
            str(time.time()),
        )
        q_hash = generate_question_hash(hash_seed)
        doc_ref = db.collection("questions").document(q_hash)
        
        if doc_ref.get().exists:
            logging.warning(f"Aynı soru veritabanında mevcut, atlandı: {hash_seed[:40]}...")
            return False

        q_dict["question_hash"] = q_hash
        from core.community_distribution import apply_simulated_distribution

        apply_simulated_distribution(q_hash, q_dict)  # simulated until real votes exist
        q_dict["created_at"] = firestore.SERVER_TIMESTAMP
        q_dict["status"] = "published"
        q_dict["times_served"] = 0
        q_dict["is_active"] = True
        q_dict["version"] = QUESTION_SCHEMA_VERSION
        # Alan Firestore belgelerinde her zaman bulunur; görsel üretimi eklenene kadar null'dır.
        q_dict.setdefault("question_image_url", None)
        q_dict.setdefault("scope", "global")
        q_dict.setdefault("target_country", (q_dict.get("countries") or ["Global"])[0])
        q_dict.setdefault("target_country_credit", 10)
        q_dict.setdefault("is_global_eligible", True)
        q_dict["correct_count"] = 0
        q_dict["wrong_count"] = 0
        q_dict["shuffle_key"] = random.random()
        if "correct_option" not in q_dict or not q_dict["correct_option"]:
            q_dict["correct_option"] = q_dict.get("correct_answer")
        if "correct_answer" not in q_dict or not q_dict["correct_answer"]:
            q_dict["correct_answer"] = q_dict.get("correct_option")
        if "duration_local" not in q_dict or "duration_global" not in q_dict or "duration_seconds" not in q_dict:
            from core.timer_calculator import calculate_durations_from_obj
            durations = calculate_durations_from_obj(q_dict)
            q_dict.setdefault("duration_local", durations["duration_local"])
            q_dict.setdefault("duration_global", durations["duration_global"])
            q_dict.setdefault("duration_seconds", durations["duration_seconds"])

        doc_ref.set(q_dict)
        
        prof = q_dict.get("difficulty_profile", {})
        local_scope = prof.get("local", {}) if isinstance(prof.get("local"), dict) else {}
        global_scope = prof.get("global", {}) if isinstance(prof.get("global"), dict) else {}
        local_label = local_scope.get("label", "Orta")
        local_score = local_scope.get("score", 5)
        
        is_glob_elig = q_dict.get("is_global_eligible", True)
        if is_glob_elig and global_scope:
            global_label = global_scope.get("label", "Orta")
            global_score = global_scope.get("score", 5)
            global_str = f"{global_label} ({global_score}/10)"
        else:
            global_str = "Yok"
        
        scope_val = str(q_dict.get("scope", "global")).capitalize()
        focus_country = q_dict.get("target_country", "Global")
        focus_credit = q_dict.get("target_country_credit", 10)

        logging.info(
            f"✅ [v{QUESTION_SCHEMA_VERSION}] [{'COMBO' if q_dict.get('is_combo') else 'TEK'}] "
            f"🌍 Kapsam: {scope_val} | Odak: {focus_country} (Kredi: {focus_credit}) | "
            f"Global Uygun: {'Evet' if is_glob_elig else 'Hayır'} | "
            f"Local: {local_label} ({local_score}/10) | Global: {global_str}"
        )
        return True
    except Exception as e:
        logging.error(f"Firestore yazma hatası: {e}")
        return False

def extract_retry_delay(err_str: str) -> int:
    """API hata mesajındaki bekleme süresini regex ile okur."""
    match = re.search(r"retry in (\d+\.?\d*)s", err_str)
    return int(float(match.group(1))) + 2 if match else 45

def _emit(callback: Optional[Callable[[Dict[str, Any]], None]], event: str, **payload: Any) -> None:
    if callback:
        callback({"event": event, "timestamp": time.time(), **payload})


def _choose_secondary(category: str, repository: SheetAlgorithmRepository) -> Optional[str]:
    from config.categories_config import CATEGORY_ALIASES, POSSIBLE_COMBO_MATCHES

    available = [item for item in repository.categories() if item != category]
    canonical_category = CATEGORY_ALIASES.get(category, category)
    canonical_matches = set(POSSIBLE_COMBO_MATCHES.get(canonical_category, []))
    matches = [
        item for item in available
        if CATEGORY_ALIASES.get(item, item) in canonical_matches
    ]
    return random.choice(matches or available) if available else None


def _choose_sheet_localization_country(repository: SheetAlgorithmRepository) -> str:
    """Sheets Ülke/Bölge Listesi'nden eşit olasılıkla bir yerelleştirme odağı seçer."""
    countries = [item["name"] for item in repository.countries() if item.get("name")]
    if not countries:
        raise ValueError("Sheets Ülke/Bölge Listesi boş; Localize Sub için ülke seçilemedi.")
    return random.choice(countries)


def run_generation(
    options: GenerationOptions,
    *,
    stop_event: Optional[threading.Event] = None,
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    repository: Optional[SheetAlgorithmRepository] = None,
    llm_manager: Optional[MultiLLMManager] = None,
    db: Any = None,
    image_resolver: Any = None,
) -> Dict[str, Any]:
    """Sheets hedefleme algoritmasıyla sonlu veya sonsuz üretim çalıştırır."""
    options.validate()
    repository = repository or get_sheet_algorithm_repository()
    manager = llm_manager or MultiLLMManager()
    stop_event = stop_event or threading.Event()
    if options.write_to_firestore and db is None:
        db = init_firebase()

    from core.difficulty_balancer import DifficultyBalancer
    from core.generation_planner import GenerationPlanner

    custom_difficulty_counts = (
        options.difficulty_counts_per_10
        if options.generation_mode == "custom" and options.custom_mode == "difficulty"
        else None
    )
    diff_balancer = DifficultyBalancer(custom_difficulty_counts)
    local_difficulty_balancer = DifficultyBalancer()
    gen_planner = GenerationPlanner(sheet_repository=repository)
    produced_count = 0
    processed_count = 0
    duplicate_count = 0
    failed_count = 0
    basic_only = options.generation_mode == "basic"
    starting_difficulty_assigner = StartingDifficultyAssigner()
    visual_schedule = VisualQuestionSchedule()
    resolved_image_service = image_resolver
    max_processed_attempts = (
        None
        if options.question_count is None
        else max(int(options.question_count) * 5, int(options.question_count) + 25)
    )

    credit_queue: List[Any] = repository.build_credit_queue(
        pool=options.credit_pool,
        country=options.country if options.country_profile_enabled else None,
        country_local_only=options.generation_mode == "country_local",
        basic_only=basic_only,
        equal_distribution=options.generation_mode == "custom" and options.custom_mode == "equal",
        total_count=options.question_count,
        category=options.category,
        subcategory=options.subcategory,
    )

    _emit(
        on_event,
        "started",
        target=options.question_count,
        algorithm_version=repository.version,
        firestore=options.write_to_firestore,
    )

    concurrency = max(1, int(options.concurrency or 1))
    lock = threading.RLock()
    reserved_targets: List[Any] = []


    def _plan_item():
        """Lock held by caller. Reserves one credit slot; returns None when the caller must wait."""
        nonlocal processed_count, credit_queue
        if max_processed_attempts is not None and processed_count >= max_processed_attempts:
            if reserved_targets:
                return None
            raise RuntimeError(
                "Üretim hedefi tamamlanamadı: "
                f"{options.question_count} yeni soru hedefinden {produced_count} soru kaydedildi; "
                f"{duplicate_count} tekrar ve {failed_count} başarısız üretim oluştu. "
                "İş tamamlandı olarak işaretlenmedi."
            )

        if len(credit_queue) <= len(reserved_targets):
            if options.question_count is None:
                credit_queue.extend(
                    repository.build_credit_queue(
                        pool=options.credit_pool,
                        country=options.country if options.country_profile_enabled else None,
                        country_local_only=options.generation_mode == "country_local",
                        basic_only=basic_only,
                        equal_distribution=options.generation_mode == "custom" and options.custom_mode == "equal",
                        category=options.category,
                        subcategory=options.subcategory,
                    )
                )
            elif reserved_targets:
                return None
            else:
                raise RuntimeError(
                    "Sheet kredi kuyruğu hedef sayı tamamlanmadan tükendi; iş tamamlandı olarak işaretlenmedi."
                )

        if reserved_targets and options.visual_questions_enabled:
            # Parallel items map to the next unsaved schedule positions; never run a visual one in parallel.
            visual_schedule.peek(credit_queue[:10])
            position = visual_schedule._position + len(reserved_targets)
            if position >= 10 or position in visual_schedule._visual_positions:
                return None

        processed_count += 1
        item_index = processed_count
        # Slot yalnızca yeni soru Firestore'a başarıyla yazıldığında tüketilir.
        # Duplicate veya üretim hatası aynı kredi slotunu bir sonraki denemeye bırakır.
        if options.visual_questions_enabled and visual_schedule.starts_block and not reserved_targets:
            prioritize_visual_targets(credit_queue)
        reserved_ids = {id(t) for t in reserved_targets}
        algorithm_target = next(t for t in credit_queue if id(t) not in reserved_ids)
        reserved_targets.append(algorithm_target)
        primary_cat = algorithm_target.category
        target_sub = algorithm_target.subcategory

        equal_distribution = options.generation_mode == "custom" and options.custom_mode == "equal"
        is_combo = (
            not basic_only
            and not equal_distribution
            and options.generation_mode != "country_local"
            and random.randint(1, 100) <= options.combo_percent
        )
        secondary_cat = _choose_secondary(primary_cat, repository) if is_combo else None
        visual_question = bool(
            options.visual_questions_enabled and visual_schedule.peek(credit_queue[:10])
        )
        profile_country = (
            options.country
            if options.country_profile_enabled
            else getattr(algorithm_target, "target_country", None)
        )
        generation_plan = gen_planner.create_plan(
            category=primary_cat,
            subcategory=target_sub,
            force_scope=options.scope,
            force_country=profile_country,
        )
        planned_scope = generation_plan.scope
        planned_country = generation_plan.target_country
        if planned_scope == "local":
            # Local questions always carry the country's Sheets languages + English, whatever the UI selection is.
            question_languages = repository.question_languages(planned_country)
        else:
            # All global questions, including section 2 / item-focused questions,
            # must carry the complete configured 35-language set.
            question_languages = list(SUPPORTED_LANGUAGE_CODES)

        focus_info = []
        if getattr(algorithm_target, "category_focus", None):
            focus_info.append(f"Ana Kategori Odak Noktası: {algorithm_target.category_focus}")
        if getattr(algorithm_target, "guide", None):
            focus_info.append(f"Alt Kategori Kapsam Kılavuzu: {algorithm_target.guide}")
        if getattr(algorithm_target, "category_drafts", None):
            focus_info.append(f"Kategori Parametreleri: {algorithm_target.category_drafts}")
        if not focus_info:
            focus_info.append("Genel trivia standardı ve halkın bilgisine uygun temel bilgi kuralları uygulanır.")

        pool_description = (
            "Tüm kategori-alt kategori hedefleri eşit"
            if equal_distribution
            else (
                "seçilen ülke/bölgenin yerel hedefleri"
                if options.generation_mode == "country_local"
                else f"{options.credit_pool} kredi havuzu"
            )
        )
        if options.generation_mode == "custom" and options.custom_mode == "difficulty":
            pool_description += (
                f"; 10'luk zorluk kotası K:{options.difficulty_counts_per_10['Kolay']} "
                f"O:{options.difficulty_counts_per_10['Orta']} Z:{options.difficulty_counts_per_10['Zor']}"
            )

        algorithm_context = (
            f"Kaynak sayfa: {algorithm_target.source_sheet}; Algoritma sürümü: {repository.version}.\n"
            f"Kategori: {primary_cat} (Kredi Ağırlığı: {algorithm_target.category_weight})\n"
            f"Alt Kategori: {target_sub} (Kredi Ağırlığı: {algorithm_target.subcategory_weight}) "
            f"[{pool_description if not basic_only else 'Temel kategori hedef havuzu'}]\n"
            f"Algoritma Bölümü: {algorithm_target.section or 'profil sayfası'}\n"
            f"Kapsam Dengesi: %{round(algorithm_target.global_ratio * 100)} GL / %{round(algorithm_target.local_ratio * 100)} LO\n"
            f"ODAK NOKTALARI & REHBER:\n" + "\n".join(f"- {line}" for line in focus_info)
        )

        _emit(
            on_event,
            "planning",
            index=item_index,
            category=primary_cat,
            subcategory=target_sub,
            combo=bool(secondary_cat),
            secondary_category=secondary_cat,
            target_country=planned_country,
            pool=options.credit_pool,
            mode=options.generation_mode,
            visual_question=visual_question,
        )

        reserved_difficulty = (
            diff_balancer.get_next_target()
            if options.difficulty is None
            else None
        )
        reserved_local_difficulty = (
            local_difficulty_balancer.get_next_target()
            if reserved_difficulty is not None and planned_scope == "global"
            else None
        )
        local_target = reserved_local_difficulty or reserved_difficulty
        return (
            item_index, algorithm_target, primary_cat, target_sub, equal_distribution,
            secondary_cat, visual_question, generation_plan, planned_scope, planned_country,
            question_languages, algorithm_context, reserved_difficulty,
            reserved_local_difficulty, local_target,
        )

    def _consume_slot(target: Any) -> None:
        for i, queued in enumerate(credit_queue):
            if queued is target:
                del credit_queue[i]
                return


    def _run_item(plan):
        nonlocal produced_count, duplicate_count, failed_count, resolved_image_service
        (item_index, algorithm_target, primary_cat, target_sub, equal_distribution,
         secondary_cat, visual_question, generation_plan, planned_scope, planned_country,
         question_languages, algorithm_context, reserved_difficulty,
         reserved_local_difficulty, local_target) = plan
        success_this_item = False
        try:
            for attempt in range(1, options.max_retries + 1):
                if stop_event.is_set():
                    break
                starting_assignment_pending = False
                try:
                    q_data = generate_question_with_fallback(
                        primary_category=primary_cat,
                        target_subcategory=target_sub,
                        secondary_category=secondary_cat,
                        target_country=planned_country,
                        target_country_credit=generation_plan.target_country_credit,
                        scope=planned_scope,
                        base_difficulty=(local_target.level if local_target else options.difficulty),
                        target_score=local_target.score if local_target else None,
                        target_global_difficulty=(
                            reserved_difficulty.level
                            if reserved_difficulty is not None and planned_scope == "global"
                            else None
                        ),
                        target_global_score=(
                            reserved_difficulty.score
                            if reserved_difficulty is not None and planned_scope == "global"
                            else None
                        ),
                        balance_categories=False,
                        difficulty_balancer=diff_balancer,
                        generation_planner=gen_planner,
                        languages=question_languages,
                        duration_seconds=options.duration_seconds,
                        algorithm_context=algorithm_context,
                        algorithm_version=repository.version,
                        visual_question=visual_question,
                        llm_manager=manager,
                    )

                    if visual_question:
                        with lock:
                            if resolved_image_service is None:
                                resolved_image_service = WikimediaQuestionImageResolver(
                                    bucket=get_storage_bucket() if options.write_to_firestore else None
                                )
                        resolved_image_service.resolve(q_data)
                        if not getattr(q_data, "question_image_url", None):
                            raise ValueError("Görsel soru için question_image_url oluşturulamadı.")

                    q_data.categories_meta = [
                        CategoryMeta(name=cat, color=get_category_color(cat))
                        for cat in q_data.categories
                    ]
                    q_data.sub_categories_meta = [
                        SubCategoryMeta(name=sub, image_url=get_subcategory_image(primary_cat, sub))
                        for sub in q_data.sub_categories
                    ]
                    q_data.algorithm_source = {
                        "spreadsheet_id": repository.source.get("spreadsheet_id"),
                        "sheet": algorithm_target.source_sheet,
                        "credit_pool": options.credit_pool,
                        "generation_mode": options.generation_mode,
                        "custom_mode": options.custom_mode,
                        "difficulty_counts_per_10": (
                            dict(options.difficulty_counts_per_10)
                            if options.generation_mode == "custom" and options.custom_mode == "difficulty"
                            else None
                        ),
                        "equal_target_distribution": equal_distribution,
                        "country_profile_enabled": options.country_profile_enabled,
                        "basic_only": basic_only,
                        "category_weight": algorithm_target.category_weight,
                        "subcategory_weight": algorithm_target.subcategory_weight,
                        "global_ratio": algorithm_target.global_ratio,
                        "local_ratio": algorithm_target.local_ratio,
                    }

                    with lock:
                        try:
                                # Bu ek alan normal zorluk üretildikten sonra atanır; zorluk,
                                # süre, kategori veya diğer dengeleyicilere geri beslenmez.
                                if options.difficulty is None:
                                    starting_difficulty_assigner.assign(q_data)
                                    starting_assignment_pending = True

                                saved = save_worker_question(db, q_data) if options.write_to_firestore else True
                                if saved:
                                    reserved_difficulty = None
                                    reserved_local_difficulty = None
                                    if starting_assignment_pending:
                                        starting_difficulty_assigner.commit()
                                        starting_assignment_pending = False
                                    visual_schedule.commit()
                                    _consume_slot(algorithm_target)
                                    produced_count += 1
                                    success_this_item = True
                                    preview = q_data.translations.get("tr") or next(iter(q_data.translations.values()))
                                    _emit(
                                        on_event,
                                        "saved" if options.write_to_firestore else "generated",
                                        index=item_index,
                                        produced=produced_count,
                                        category=primary_cat,
                                        subcategory=target_sub,
                                        provider=getattr(q_data, "generation_provider", "GEMINI"),
                                        question=preview.question,
                                        firestore=options.write_to_firestore,
                                        quota=manager.status(live=False),
                                        visual_question=visual_question,
                                        question_image_url=getattr(q_data, "question_image_url", None),
                                        starting_difficulty=getattr(q_data, "starting_difficulty", None),
                                    )
                                else:
                                    if reserved_difficulty is not None:
                                        diff_balancer.return_target(reserved_difficulty)
                                        reserved_difficulty = None
                                    if reserved_local_difficulty is not None:
                                        local_difficulty_balancer.return_target(reserved_local_difficulty)
                                        reserved_local_difficulty = None
                                    if starting_assignment_pending:
                                        starting_difficulty_assigner.rollback(q_data)
                                        starting_assignment_pending = False
                                    duplicate_count += 1
                                    _emit(on_event, "duplicate", index=item_index, category=primary_cat)
                        except BaseException:
                            if starting_assignment_pending:
                                starting_difficulty_assigner.rollback(q_data)
                                starting_assignment_pending = False
                            raise
                    break
                except Exception as err:
                    if starting_assignment_pending:
                        starting_difficulty_assigner.rollback(q_data)
                    err_str = str(err)
                    if isinstance(err, TranslationIncompleteError):
                        with lock:
                            failed_count += 1
                        stop_event.set()
                        _emit(
                            on_event,
                            "translation_incomplete",
                            index=item_index,
                            missing_languages=list(err.languages),
                            message=err_str[:500],
                        )
                        logging.error(
                            "Soru çevirileri tamamlanamadı (%d dil); aynı soruyu yeniden üretmek yerine iş durduruluyor: %s",
                            len(err.languages),
                            ", ".join(err.languages),
                        )
                        break
                    if isinstance(err, AllProvidersCoolingDown):
                        retryable = bool(err.retryable)
                        can_retry_soon = retryable and err.retry_after <= 90
                        wait_sec = min(90, max(1, err.retry_after + 1))
                        _emit(
                            on_event,
                            "provider_unavailable",
                            index=item_index,
                            retry_after=wait_sec if can_retry_soon else err.retry_after,
                            message=err_str[:500],
                        )
                        if can_retry_soon and attempt < options.max_retries:
                            logging.warning(
                                "LLM sağlayıcıları geçici olarak kullanılamıyor; %ss sonra yeniden denenecek.",
                                wait_sec,
                            )
                            stop_event.wait(wait_sec)
                            continue
                        if err.quota_limited and not retryable:
                            raise RuntimeError(
                                "Ücretsiz LLM sağlayıcı kotaları tükenmiş; kısa süre sonra tekrar denemek "
                                "sonuç vermeyeceği için üretim işi durduruldu. Sağlayıcı kotası yenilendiğinde "
                                f"yeniden başlatın. Ayrıntı: {err}"
                            ) from err
                        if retryable and err.retry_after > 90:
                            wait_kind = "kota beklemesi" if err.quota_limited else "sağlayıcı bekleme süresi"
                            raise RuntimeError(
                                f"LLM {wait_kind} uzun ({err.retry_after}s); uzun bekleme yerine üretim işi "
                                f"durduruldu. Daha sonra yeniden başlatın. Ayrıntı: {err}"
                            ) from err
                        if retryable and attempt >= options.max_retries:
                            raise RuntimeError(
                                f"LLM sağlayıcıları {options.max_retries} kısa yeniden denemede yanıt vermedi; "
                                "tekrarları her soru için sürdürmemek üzere üretim işi durduruldu."
                            ) from err
                        raise RuntimeError(
                            "LLM sağlayıcılarında geçici olmayan erişim/yapılandırma hatası var; "
                            f"aynı hatayı sonraki sorularda tekrarlamamak için üretim durduruldu. Ayrıntı: {err}"
                        ) from err
                    is_quota = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                    wait_sec = extract_retry_delay(err_str) if is_quota else attempt * 5
                    _emit(
                        on_event,
                        "retry",
                        index=item_index,
                        attempt=attempt,
                        max_retries=options.max_retries,
                        wait_seconds=wait_sec,
                        quota_error=is_quota,
                        message=err_str[:500],
                    )
                    if attempt < options.max_retries:
                        stop_event.wait(wait_sec)
                    else:
                        with lock:
                            failed_count += 1
                        logging.error("Hata oluştu (%s/%s): %s", attempt, options.max_retries, err)

            if reserved_difficulty is not None:
                diff_balancer.return_target(reserved_difficulty)
            if reserved_local_difficulty is not None:
                local_difficulty_balancer.return_target(reserved_local_difficulty)

            if success_this_item:
                logging.info("📊 Toplam Başarılı Soru: %s", produced_count)
            if not stop_event.is_set() and options.sleep_between_requests > 0:
                stop_event.wait(options.sleep_between_requests)
        finally:
            with lock:
                reserved_targets[:] = [t for t in reserved_targets if t is not algorithm_target]

    fatal: List[BaseException] = []

    def _worker(plan: Any) -> None:
        try:
            _run_item(plan)
        except BaseException as exc:  # surfaced by the dispatcher after the pool drains
            fatal.append(exc)
            stop_event.set()

    if concurrency <= 1:
        while options.question_count is None or produced_count < options.question_count:
            if stop_event.is_set():
                break
            with lock:
                plan = _plan_item()
            _run_item(plan)
    else:
        pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="gen")
        futures: set = set()
        try:
            while not stop_event.is_set():
                futures = {f for f in futures if not f.done()}
                plan = None
                with lock:
                    target_met = (
                        options.question_count is not None
                        and produced_count + len(reserved_targets) >= options.question_count
                    )
                    if target_met and not reserved_targets:
                        break
                    if not target_met and len(futures) < concurrency:
                        plan = _plan_item()
                if plan is not None:
                    futures.add(pool.submit(_worker, plan))
                elif futures:
                    wait_futures(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                else:
                    stop_event.wait(0.05)
        except BaseException:
            stop_event.set()
            raise
        finally:
            pool.shutdown(wait=True)
        if fatal:
            raise fatal[0]


    summary = {
        "processed": processed_count,
        "produced": produced_count,
        "duplicates": duplicate_count,
        "failed": failed_count,
        "stopped": stop_event.is_set(),
        "algorithm_version": repository.version,
        "provider_status": manager.status(live=False),
    }
    _emit(on_event, "stopped" if stop_event.is_set() else "completed", **summary)
    return summary


def start_infinite_worker() -> None:
    logging.info("🚀 Soru Üretim Motoru v%s + Sheets %s Başlatıldı!", QUESTION_SCHEMA_VERSION, get_sheet_algorithm_repository().version)
    run_generation(GenerationOptions(question_count=None))

if __name__ == "__main__":
    try:
        start_infinite_worker()
    except KeyboardInterrupt:
        logging.info("🛑 Worker durduruldu.")
