import unittest

from scripts.backfill_v110_skill_fields import (
    build_updates, check_support, estimate, missing_fields, process_doc, usable_en, outcome, summarize, unresolved, valid_order,
)

FULL = {
    "question": "q", "options": {k: k for k in "ABCD"}, "hint": "h", "keyword": "k", "distractor_trap_note": "t",
    "simplified_question": "s", "clarified_options": {k: k for k in "ABCD"},
    "binary_fallback": {"statement": "x", "is_true": False},
}


def doc(**over):
    base = {"correct_answer": "A", "weakest_distractor_index": 1, "strongest_distractor_index": 2,
            "option_difficulty_order": [0, 1, 2, 3], "translations": {"en": dict(FULL), "tr": dict(FULL)}}
    base.update(over)
    return base


class BackfillPureTests(unittest.TestCase):
    def test_complete_doc_has_no_gaps(self):
        self.assertEqual(missing_fields(doc()), {"root": [], "locales": {}})

    def test_only_en_is_checked(self):
        tr = dict(FULL, hint="")
        self.assertEqual(missing_fields(doc(translations={"en": FULL, "tr": tr})), {"root": [], "locales": {}})
        en = dict(FULL, hint="", binary_fallback={"statement": "x"})
        gaps = missing_fields(doc(weakest_distractor_index=None, option_difficulty_order=None, translations={"en": en, "tr": tr}))
        self.assertEqual(gaps["root"], ["weakest_distractor_index", "option_difficulty_order"])
        self.assertEqual(gaps["locales"], {"en": ["hint", "binary_fallback"]})

    def test_valid_order(self):
        self.assertTrue(valid_order([3, 1, 0, 2]))
        self.assertFalse(valid_order([0, 0, 1, 2]))
        self.assertFalse(valid_order(None))

    def test_build_updates_only_missing_and_valid(self):
        gaps = {"root": ["strongest_distractor_index", "option_difficulty_order"], "locales": {"tr": ["hint", "keyword"]}}
        updates = build_updates(gaps, {"strongest_distractor_index": 2, "option_difficulty_order": [0, 0, 1, 2]},
                                {"tr": {"hint": "yeni", "keyword": " ", "question": "must not be written"}})
        self.assertEqual(updates, {("strongest_distractor_index",): 2, ("translations", "tr", "hint"): "yeni"})

    def test_check_support_rejects_answer_or_equal_indices(self):
        check_support("A", {"weakest_distractor_index": 1, "strongest_distractor_index": 2}, FULL, "en")
        for weakest, strongest in ((0, 2), (2, 2)):
            with self.assertRaises(ValueError):
                check_support("A", {"weakest_distractor_index": weakest, "strongest_distractor_index": strongest}, FULL, "en")

    def test_estimate_counts_one_call_per_doc(self):
        gaps = missing_fields(doc(translations={"en": {**FULL, "hint": ""}}))
        est = estimate([gaps, {"root": [], "locales": {}}], 4, 8.0, 450)
        self.assertEqual((est["docs"], est["llm_calls"], est["fields"]), (2, 1, 1))

    def test_process_doc_single_call_writes_only_missing_en_fields(self):
        class LLM:
            calls = 0

            def generate_trivia(self, system, prompt, response_schema=None):
                LLM.calls += 1
                return {"hint": "new hint", "weakest_distractor_index": 1, "strongest_distractor_index": 3, "option_difficulty_order": [1, 0, 2, 3]}

        data = doc(strongest_distractor_index=None, option_difficulty_order=None,
                   translations={"en": {**FULL, "hint": ""}, "tr": {**FULL, "hint": ""}})
        updates = process_doc(data, LLM())
        self.assertEqual(LLM.calls, 1)
        self.assertEqual(updates, {("strongest_distractor_index",): 3, ("option_difficulty_order",): [1, 0, 2, 3],
                                   ("translations", "en", "hint"): "new hint"})

    def test_process_doc_rejects_invalid_indices_and_missing_en(self):
        class LLM:
            def generate_trivia(self, *a, **k):
                return {"strongest_distractor_index": 0}  # equals the correct answer A

        with self.assertRaises(ValueError):
            process_doc(doc(strongest_distractor_index=None), LLM())
        with self.assertRaises(ValueError):
            process_doc(doc(translations={"tr": FULL}), LLM())
        self.assertFalse(usable_en({"question": "q"}))

    def test_unresolved_counts_unfilled_gaps(self):
        gaps = {"root": ["strongest_distractor_index", "option_difficulty_order"], "locales": {"tr": ["hint", "keyword"]}}
        updates = {("strongest_distractor_index",): 2, ("translations", "tr", "hint"): "x"}
        self.assertEqual(unresolved(gaps, updates), 1)

    def test_outcome_and_summary_account_for_every_doc(self):
        gaps = {"root": ["strongest_distractor_index"], "locales": {"tr": ["hint"]}}
        full = {("strongest_distractor_index",): 2, ("translations", "tr", "hint"): "x"}
        self.assertEqual(outcome(gaps, full), "complete")
        self.assertEqual(outcome(gaps, {("strongest_distractor_index",): 2}), "partial")
        self.assertEqual(outcome(gaps, {}), "unfilled")
        results = {"a": "written", "b": "skipped: not attempted", "c": "skipped: ValueError: x", "d": "complete(no change)"}
        self.assertEqual(summarize(results), {"written": 1, "skipped": 2, "complete(no change)": 1})


if __name__ == "__main__":
    unittest.main()


class SubAgentRoundTripTests(unittest.TestCase):
    def gappy(self):
        return doc(weakest_distractor_index=None, strongest_distractor_index=None, option_difficulty_order=None,
                   translations={"en": {**FULL, "hint": "", "keyword": ""}})

    def test_export_record_lists_missing_fields(self):
        from scripts.backfill_v110_skill_fields import export_record

        rec = export_record("d1", self.gappy())
        self.assertEqual(rec["doc_id"], "d1")
        self.assertEqual(rec["missing"], ["weakest_distractor_index", "strongest_distractor_index", "option_difficulty_order", "hint", "keyword"])
        self.assertEqual(rec["fixed"], {})

    def test_chunks_and_results_round_trip(self):
        import json
        import tempfile
        from pathlib import Path

        from scripts.backfill_v110_skill_fields import read_results, write_chunks

        with tempfile.TemporaryDirectory() as tmp:
            n = write_chunks([{"doc_id": str(i)} for i in range(5)], Path(tmp), 2)
            self.assertEqual(n, 3)
            self.assertTrue((Path(tmp) / "README_prompt.md").exists())
            (Path(tmp) / "result_001.json").write_text(json.dumps({"a": {"hint": "h"}, "b": "bad"}), encoding="utf-8")
            self.assertEqual(read_results(Path(tmp)), {"a": {"hint": "h"}})

    def test_apply_result_validates_like_the_llm_path(self):
        from scripts.backfill_v110_skill_fields import apply_result

        good = {"hint": "h", "keyword": "k", "weakest_distractor_index": 1, "strongest_distractor_index": 2, "option_difficulty_order": [0, 1, 2, 3]}
        updates = apply_result(self.gappy(), good)
        self.assertEqual(updates[("translations", "en", "hint")], "h")
        self.assertEqual(updates[("strongest_distractor_index",)], 2)
        for bad in ({**good, "strongest_distractor_index": 0}, {**good, "weakest_distractor_index": 2}, {**good, "hint": ""}):
            with self.assertRaises(ValueError):
                apply_result(self.gappy(), bad)
