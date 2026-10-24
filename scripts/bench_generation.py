"""Generation speed benchmark.

--simulate (default): real worker.run_generation + real translate_localized_content (batching, workers, Azure
on/off) with mocked providers/Firestore and fake latencies compressed by --time-scale. Simulation, not live.
--live N --dry-run: N real questions with Firestore writes OFF; prints per-question wall time and stage timings.
Only the mocked simulate mode runs by default; live needs both flags and never runs with writes enabled.
"""
import argparse
import logging
import os
import random
import sys
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# NOTE: OLD == CURRENT (1.00x) is a simulation artefact: simulated Azure never fails, so LLM batching never runs (the batching
# gain applies to real Azure failures/429s/timeouts). Visuals are off in all simulated scenarios.
SCENARIOS = [  # name, env, concurrency
    ("OLD-equivalent (conc 1, batch 4, workers 1, Azure on)",
     {"TRANSLATION_LLM_BATCH_SIZE": "4", "TRANSLATION_LLM_WORKERS": "1", "TRANSLATION_USE_AZURE": "1"}, 1),
    ("CURRENT main (batch 8, workers 3, Azure on)",
     {"TRANSLATION_LLM_BATCH_SIZE": "8", "TRANSLATION_LLM_WORKERS": "3", "TRANSLATION_USE_AZURE": "1"}, 1),
    ("AZURE-OFF (batch 8, workers 3)",
     {"TRANSLATION_LLM_BATCH_SIZE": "8", "TRANSLATION_LLM_WORKERS": "3", "TRANSLATION_USE_AZURE": "0"}, 1),
    ("AZURE-OFF + concurrency 4 (workers 4)",
     {"TRANSLATION_LLM_BATCH_SIZE": "8", "TRANSLATION_LLM_WORKERS": "4", "TRANSLATION_USE_AZURE": "0"}, 4),
]


class ScaledEvent(threading.Event):
    """stop_event whose wait() (retry backoff, inter-request sleep) is compressed like every other latency."""

    def __init__(self, scale):
        super().__init__()
        self.scale = scale

    def wait(self, timeout=None):
        return super().wait(None if timeout is None else timeout * self.scale)


class Sim:
    def __init__(self, a):
        self.a, self.rng, self.lock = a, random.Random(a.seed), threading.Lock()
        self.azure_usage, self.counter = deque(), 0

    def sleep(self, seconds):
        time.sleep(seconds * self.a.time_scale)

    def roll(self, rate):
        with self.lock:
            return self.rng.random() < rate

    def azure_budget(self, chars):
        """Rolling 60 s window like core.translation_service._wait_for_azure_character_budget (time-scaled)."""
        limit = int(os.getenv("AZURE_TRANSLATOR_CHARACTERS_PER_MINUTE", "30000"))
        window = 60 * self.a.time_scale
        if chars > limit:
            return
        while True:
            with self.lock:
                now = time.monotonic()
                while self.azure_usage and self.azure_usage[0][0] <= now - window:
                    self.azure_usage.popleft()
                if sum(c for _t, c in self.azure_usage) + chars <= limit:
                    self.azure_usage.append((now, chars))
                    return
                delay = self.azure_usage[0][0] + window - now
            time.sleep(max(delay, 0.001))

    def azure(self, source, source_language, targets):
        self.azure_budget(self.a.source_chars * len(targets))
        self.sleep(self.a.azure_s)
        if self.roll(self.a.rate_429):
            return {}, list(targets), "HTTP 429"
        return {t: {"question": t} for t in targets}, [], None

    def llm_translate(self, source, source_language, langs, manager):
        if self.roll(self.a.rate_timeout):
            self.sleep(self.a.llm_timeout_s)
            raise TimeoutError("simulated provider timeout")
        self.sleep(self.a.translate_s)
        if self.roll(self.a.rate_429):
            raise ValueError("simulated 429 / truncated JSON")  # real code splits the batch
        return {lang: {"question": lang} for lang in langs}, "SIM_LLM", []

    def generate(self, **kwargs):
        from core import translation_service as ts
        with self.lock:
            self.counter += 1
            n = self.counter
        is_global = n % 2 == 1  # alternating mix: half global (35 languages), half local (country + English)
        langs = list(ts.SUPPORTED_LANGUAGE_CODES) if is_global else list(kwargs["languages"])[:3]
        if self.roll(self.a.rate_timeout):
            self.sleep(self.a.llm_timeout_s)
            raise TimeoutError("simulated source timeout")
        self.sleep(self.a.source_s)
        ts.translate_localized_content({"question": "Q" * self.a.source_chars, "options": {}}, "en", langs,
                                       llm_manager=MagicMock(), prefer_azure=True)
        cat, sub = kwargs["primary_category"], kwargs["target_subcategory"]
        return SimpleNamespace(
            categories=[cat], sub_categories=[sub], translations={"tr": SimpleNamespace(question=f"q{n}")},
            generation_provider="SIM", categories_meta=[], sub_categories_meta=[], algorithm_source=None,
            is_visual_question=False, question_image_url=None)

    def save(self, db, q):
        self.sleep(self.a.firestore_s)
        return True


def run_scenario(a, env, concurrency):
    import worker
    from core import translation_service as ts
    from core.sheet_algorithm import SheetAlgorithmRepository
    sim, n = Sim(a), a.questions * 2
    repo = SheetAlgorithmRepository()
    queue = repo.build_credit_queue(pool="154", basic_only=True, total_count=n)
    opts = worker.GenerationOptions(
        question_count=n, generation_mode="basic", combo_percent=0, visual_questions_enabled=False,
        write_to_firestore=True, concurrency=concurrency,
        sleep_between_requests=worker.SLEEP_BETWEEN_REQUESTS if concurrency == 1 else 0.3)
    opts.sleep_between_requests *= a.time_scale
    with patch.dict(os.environ, env), \
            patch.object(worker, "generate_question_with_fallback", sim.generate), \
            patch.object(worker, "save_worker_question", sim.save), \
            patch.object(ts, "_azure_translate", sim.azure), \
            patch.object(ts, "_llm_translate_with_cooldown", sim.llm_translate), \
            patch.object(repo, "build_credit_queue", return_value=queue):
        start = time.monotonic()
        summary = worker.run_generation(opts, repository=repo, llm_manager=MagicMock(status=lambda live=False: {}),
                                        db=MagicMock(), stop_event=ScaledEvent(a.time_scale))
        wall = time.monotonic() - start
    return summary["produced"], wall / a.time_scale


def simulate(a):
    print(f"SIMULATION (not live): {a.questions} global + {a.questions} local questions, time-scale {a.time_scale}, "
          f"LLM src {a.source_s}s, translate {a.translate_s}s/batch, Azure {a.azure_s}s + 30k chars/min, "
          f"429/timeout rates {a.rate_429}/{a.rate_timeout}, seed {a.seed}")
    rows = []
    for name, env, conc in SCENARIOS:
        produced, sim_s = run_scenario(a, env, conc)
        rows.append((name, produced, sim_s, sim_s / max(produced, 1)))
    base = rows[0][2]
    print(f"\n{'configuration':58} {'q':>3} {'total s':>9} {'s/question':>11} {'speedup':>8}")
    for name, produced, total, per in rows:
        print(f"{name:58} {produced:>3} {total:>9.0f} {per:>11.1f} {base / total:>7.2f}x")


def live(a):
    if os.getenv("KNOWLEY_GENERATION_WRITES_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        sys.exit("Refusing to run: KNOWLEY_GENERATION_WRITES_ENABLED is enabled. Unset it for --live.")
    if not a.dry_run:
        sys.exit("--live requires --dry-run (Firestore writes stay disabled).")
    import worker
    import core.generator as generator
    from core.llm_manager import MultiLLMManager
    stages, lock = [], threading.Lock()

    def timed(label, fn):
        def wrapper(*args, **kwargs):
            t = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                with lock:
                    stages.append((label, time.monotonic() - t))
        return wrapper

    marks, t0 = {}, time.monotonic()

    def on_event(e):
        if e["event"] == "planning":
            marks[e["index"]] = time.monotonic()
        elif e["event"] in {"generated", "saved"}:
            print(f"question {e['produced']}: {time.monotonic() - marks.get(e['index'], t0):.1f}s wall "
                  f"({e.get('provider')})")

    opts = worker.GenerationOptions(question_count=a.live, write_to_firestore=False, visual_questions_enabled=False)
    with patch.object(generator, "translate_localized_content", timed("translate", generator.translate_localized_content)), \
            patch.object(MultiLLMManager, "generate_trivia", timed("source_llm", MultiLLMManager.generate_trivia)):
        summary = worker.run_generation(opts, on_event=on_event)
    total = time.monotonic() - t0
    print(f"total {total:.1f}s for {summary['produced']} questions ({total / max(summary['produced'], 1):.1f}s each), "
          f"concurrency {opts.concurrency}")
    for label in ("source_llm", "translate"):
        vals = [d for lbl, d in stages if lbl == label]
        if vals:
            print(f"stage {label}: calls {len(vals)}, mean {sum(vals) / len(vals):.1f}s, max {max(vals):.1f}s")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--simulate", action="store_true", help="default mode")
    p.add_argument("--live", type=int, metavar="N")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--questions", type=int, default=20, help="per scope (global and local)")
    p.add_argument("--time-scale", type=float, default=0.02)
    p.add_argument("--source-s", type=float, default=6.0)
    p.add_argument("--translate-s", type=float, default=8.0)
    p.add_argument("--azure-s", type=float, default=1.5)
    p.add_argument("--firestore-s", type=float, default=0.3)
    p.add_argument("--llm-timeout-s", type=float, default=15.0)
    p.add_argument("--source-chars", type=int, default=450, help="source characters (x languages = Azure budget use)")
    p.add_argument("--rate-429", type=float, default=0.03)
    p.add_argument("--rate-timeout", type=float, default=0.02)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    logging.disable(logging.WARNING)
    live(a) if a.live else simulate(a)


if __name__ == "__main__":
    main()
