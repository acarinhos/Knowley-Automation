"""Question-level concurrency in worker.run_generation (fake generator + fake saver only)."""
import itertools
import threading
import time
import unittest
from collections import Counter
from unittest.mock import MagicMock, patch

from core.sheet_algorithm import SheetAlgorithmRepository
from tests.test_worker_generation import FakeManager, fake_question
from worker import GenerationOptions, run_generation

N = 10


class FastEvent(threading.Event):
    def wait(self, timeout=None):
        return super().wait(min(timeout or 0, 0.005))


class Harness:
    def __init__(self, texts=None, fail_first_for=None, delay=0.02):
        self.calls, self.saved, self.events = [], [], []
        self.active = self.max_active = 0
        self.saving = self.max_saving = 0
        self.lock = threading.Lock()
        self.counter = itertools.count()
        self.texts, self.fail_first_for, self.delay = texts, fail_first_for, delay
        self.failed_once = set()
        self.repo = SheetAlgorithmRepository()
        self.queue = self.repo.build_credit_queue(pool="154", basic_only=True, total_count=N)

    def generate(self, **kwargs):
        with self.lock:
            n = next(self.counter)
            self.calls.append(kwargs)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            key = kwargs["target_subcategory"]
            if self.fail_first_for == key and key not in self.failed_once:
                self.failed_once.add(key)
                raise ValueError("boom")
            q = fake_question(**kwargs)
            text = self.texts(n) if self.texts else f"q{n}"
            q.translations = {"tr": type("T", (), {"question": text})()}
            return q
        finally:
            with self.lock:
                self.active -= 1

    def save(self, db, q):
        with self.lock:
            self.saving += 1
            self.max_saving = max(self.max_saving, self.saving)
        time.sleep(0.005)
        text = q.translations["tr"].question
        with self.lock:
            self.saving -= 1
            if text in self.saved:
                return False
            self.saved.append(text)
            return True

    def run(self, concurrency, count=N, stop_event=None, on_event=None):
        opts = GenerationOptions(question_count=count, generation_mode="basic", combo_percent=0,
                                 sleep_between_requests=0, visual_questions_enabled=False,
                                 concurrency=concurrency)
        def emit(e):
            self.events.append(e)
            if on_event:
                on_event(e)
        with patch("worker.generate_question_with_fallback", side_effect=self.generate), \
                patch("worker.save_worker_question", side_effect=self.save), \
                patch.object(self.repo, "build_credit_queue", return_value=list(self.queue)):
            return run_generation(opts, repository=self.repo, llm_manager=FakeManager(), db=MagicMock(),
                                  stop_event=stop_event or FastEvent(), on_event=emit)

    def names(self):
        return [e["event"] for e in self.events]


class ConcurrencyTests(unittest.TestCase):
    def test_sequential_event_sequence_unchanged(self):
        h = Harness(delay=0)
        summary = h.run(1, count=2)
        self.assertEqual(h.names(), ["started", "planning", "saved", "planning", "saved", "completed"])
        self.assertEqual(h.max_active, 1)
        self.assertEqual(summary["produced"], 2)

    def test_parallel_saves_exactly_n_with_same_credits_and_difficulty(self):
        seq, par = Harness(), Harness()
        par.queue = seq.queue  # identical fixed plan
        s1, s2 = seq.run(1), par.run(4)
        self.assertEqual((s1["produced"], s2["produced"]), (N, N))
        self.assertGreater(par.max_active, 1)
        self.assertEqual(par.max_saving, 1)  # check+write is serialised
        self.assertEqual(len(par.saved), N)
        self.assertEqual(len(set(par.saved)), N)
        self.assertEqual(len([e for e in par.events if e["event"] == "saved"]), N)
        slots = lambda h: Counter((c["primary_category"], c["target_subcategory"]) for c in h.calls)
        self.assertEqual(slots(seq), slots(par))
        levels = lambda h: Counter(str(c["target_global_difficulty"] or c["base_difficulty"]) for c in h.calls)
        self.assertEqual(levels(seq), levels(par))
        self.assertEqual(Counter(e["produced"] for e in par.events if e["event"] == "saved"), Counter(range(1, N + 1)))

    def test_duplicate_is_not_saved_and_slot_is_retried(self):
        h = Harness(texts=lambda n: "q1" if n == 2 else f"q{n}")
        summary = h.run(4, count=6)
        self.assertEqual(summary["produced"], 6)
        self.assertEqual(summary["duplicates"], 1)
        self.assertEqual(len(set(h.saved)), 6)

    def test_failing_question_retries_without_blocking_others(self):
        h = Harness()
        h.fail_first_for = h.queue[0].subcategory
        summary = h.run(4)
        self.assertEqual(summary["produced"], N)
        self.assertIn("retry", h.names())
        self.assertEqual(len(set(h.saved)), N)

    def test_stop_cancels_pending_work(self):
        h = Harness(delay=0.03)
        stop = FastEvent()
        summary = h.run(3, count=N, stop_event=stop,
                        on_event=lambda e: stop.set() if e["event"] == "saved" and e["produced"] >= 2 else None)
        self.assertTrue(summary["stopped"])
        self.assertLess(summary["produced"], N)
        self.assertEqual(h.active, 0)

    def test_fatal_error_in_worker_propagates(self):
        h = Harness()
        with patch("worker.generate_question_with_fallback", side_effect=RuntimeError("fatal")), \
                patch.object(h.repo, "build_credit_queue", return_value=list(h.queue)):
            from worker import GenerationOptions as G
            opts = G(question_count=4, generation_mode="basic", combo_percent=0, sleep_between_requests=0,
                     visual_questions_enabled=False, concurrency=3, max_retries=1)
            # a plain RuntimeError is retried then marked failed; use the provider-cooldown path for fatal.
            from core.llm_manager import AllProvidersCoolingDown
            with patch("worker.generate_question_with_fallback",
                       side_effect=AllProvidersCoolingDown(5, "x", retryable=False)):
                with self.assertRaises(RuntimeError):
                    run_generation(opts, repository=h.repo, llm_manager=FakeManager(), db=MagicMock(),
                                   stop_event=FastEvent())

    def test_concurrency_options(self):
        o = GenerationOptions(concurrency=99).validate()
        self.assertEqual(o.concurrency, 6)
        self.assertEqual(o.sleep_between_requests, 0.3)
        o = GenerationOptions(concurrency=3, sleep_between_requests=2.0).validate()
        self.assertEqual(o.sleep_between_requests, 2.0)
        self.assertEqual(GenerationOptions().validate().sleep_between_requests, 1.5)


if __name__ == "__main__":
    unittest.main()
