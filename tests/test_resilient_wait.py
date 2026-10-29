import os
import unittest
from unittest.mock import patch

from core.llm_manager import AllProvidersCoolingDown, MultiLLMManager
from core.sheet_algorithm import SheetAlgorithmRepository
from worker import GenerationOptions, run_generation
from tests.test_worker_generation import FakeManager, NonBlockingStopEvent, fake_question


def _opts(retries=2):
    return GenerationOptions(
        question_count=1, generation_mode="basic", difficulty="Orta", combo_percent=0,
        sleep_between_requests=0, max_retries=retries, write_to_firestore=False,
        visual_questions_enabled=False,
    )


def _q():
    return fake_question(primary_category="Genel", target_subcategory="Genel", visual_question=False)


def _run(stop=None, events=None):
    return run_generation(
        _opts(), repository=SheetAlgorithmRepository(), llm_manager=FakeManager(),
        stop_event=stop or NonBlockingStopEvent(), on_event=(events.append if events is not None else None),
    )


class ResilientWaitTests(unittest.TestCase):
    def test_long_cooldown_pauses_and_resumes_beyond_retry_limit(self):
        # 3 long outages with max_retries=2: downtime must not burn item retries.
        errs = [AllProvidersCoolingDown(299, "x", provider_errors={"cohere": "ReadTimeout"}) for _ in range(3)]
        stop, events = NonBlockingStopEvent(), []
        with patch("worker.generate_question_with_fallback", side_effect=errs + [_q()]):
            summary = _run(stop, events)
        self.assertEqual(summary["produced"], 1)
        self.assertEqual(stop.waits, [90, 90, 90])
        self.assertEqual(sum(e["event"] == "provider_unavailable" for e in events), 3)

    def test_stop_during_wait_ends_run_without_failure(self):
        class StopOnWait(NonBlockingStopEvent):
            def wait(self, seconds):
                self.stopped = True
                return True
        with patch("worker.generate_question_with_fallback", side_effect=AllProvidersCoolingDown(299, "x")):
            summary = _run(StopOnWait())
        self.assertEqual(summary["produced"], 0)
        self.assertTrue(summary["stopped"])

    def test_cap_exceeded_fails_with_reasons(self):
        err = AllProvidersCoolingDown(299, "x", provider_errors={"gemini": "HTTP 503 high demand"})
        with patch.dict(os.environ, {"LLM_OUTAGE_MAX_WAIT_SECONDS": "0"}), \
                patch("worker.generate_question_with_fallback", side_effect=err):
            with self.assertRaises(RuntimeError) as ctx:
                _run()
        self.assertIn("gemini: HTTP 503 high demand", str(ctx.exception))

    def test_transient_errors_do_not_escalate_to_long_cooldown(self):
        for text in ("HTTP 503 high demand", "504 Deadline", "ReadTimeout timed out"):
            self.assertLessEqual(MultiLLMManager._cooldown_for_error(RuntimeError(text)), 60, text)


if __name__ == "__main__":
    unittest.main()
