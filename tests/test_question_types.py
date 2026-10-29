import copy
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from pydantic import ValidationError

from core import generator
from core.generator import GeneratedQuestion, build_prompt
from core.question_types import QuestionTypePlanner, normalize_question_types
from core.sheet_algorithm import SheetAlgorithmRepository
from core.topic_diversity import topic_terms
from worker import GenerationOptions, run_generation


def content(question, options, **over):
    base = {
        "question": question, "options": options, "explanation": "e", "filter_tag": "t",
        "hint": "h", "keyword": "k", "distractor_trap_note": "n",
        "binary_fallback": {"statement": question, "is_true": True},
        "simplified_question": question, "clarified_options": dict(options),
    }
    base.update(over)
    return base


def payload(qtype="multiple_choice", **over):
    if qtype == "true_false":
        opts, q, answer = {"A": "True", "B": "False"}, "Jupiter is the largest planet.", "A"
    elif qtype == "fill_blank":
        opts, q, answer = {"A": "Jupiter", "B": "Mars", "C": "Venus", "D": "Earth"}, "The largest planet is _____.", "A"
    else:
        opts, q, answer = {"A": "Jupiter", "B": "Mars", "C": "Venus", "D": "Earth"}, "Which planet is largest?", "A"
    local = {"label": "Orta", "score": 5}
    data = {
        "question_type": qtype, "categories": ["Bilim"], "sub_categories": ["Astronomi"],
        "difficulty_profile": {"local": local, "global": local}, "correct_answer": answer,
        "weakest_distractor_index": None if qtype == "true_false" else 1,
        "strongest_distractor_index": None if qtype == "true_false" else 2,
        "translations": {"en": content(q, opts)},
    }
    data.update(over)
    return data


class PlannerTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_question_types(None), ["true_false", "fill_blank"])
        self.assertEqual(normalize_question_types("mixed"), ["true_false", "fill_blank"])
        self.assertEqual(normalize_question_types("fill_blank"), ["fill_blank"])
        self.assertEqual(normalize_question_types(["fill_blank", "true_false", "fill_blank"]), ["fill_blank", "true_false"])
        with self.assertRaises(ValueError):
            normalize_question_types("multiple_choice")

    def test_round_robin_and_balanced_truth(self):
        planner = QuestionTypePlanner(["true_false", "fill_blank"])
        plans = [planner.next() for _ in range(8)]
        self.assertEqual([p[0] for p in plans], ["true_false", "fill_blank"] * 4)
        self.assertEqual([p[1] for p in plans if p[0] == "true_false"], [True, False, True, False])
        self.assertTrue(all(p[1] is None for p in plans if p[0] == "fill_blank"))
        self.assertEqual(QuestionTypePlanner().next(), ("multiple_choice", None))


class OptionsTests(unittest.TestCase):
    def test_other_modes_stay_multiple_choice(self):
        for mode in ("154", "64", "basic"):
            options = GenerationOptions(generation_mode=mode, question_types=["true_false"]).validate()
            self.assertEqual(options.question_types, ["multiple_choice"], mode)

    def test_question_types_mode(self):
        options = GenerationOptions(generation_mode="question_types", question_types="true_false").validate()
        self.assertEqual(options.question_types, ["true_false"])
        self.assertFalse(options.visual_questions_enabled)
        self.assertEqual(
            GenerationOptions(generation_mode="question_types").validate().question_types, ["true_false", "fill_blank"]
        )
        with self.assertRaises(ValueError):
            GenerationOptions(generation_mode="question_types", question_types=["bogus"]).validate()


class PromptTests(unittest.TestCase):
    def prompt(self, **kw):
        return build_prompt("Bilim", "Astronomi", languages=["en"], **kw)

    def test_multiple_choice_has_no_type_block(self):
        text = self.prompt()
        self.assertNotIn("SORU TİPİ", text)
        self.assertIn('"C": "..."', text)
        self.assertIn("çoktan seçmeli soru", text)

    def test_true_false_block(self):
        text = self.prompt(question_type="true_false", true_false_answer=False)
        self.assertIn("DOĞRU/YANLIŞ", text)
        self.assertIn("KESİNLİKLE YANLIŞ", text)
        self.assertIn('"correct_answer": "B"', text)
        self.assertIn('"options": {"A": "...", "B": "..."}', text)
        self.assertNotIn('"C": "..."', text)
        self.assertIn('"question_type": "true_false"', text)

    def test_fill_blank_block(self):
        text = self.prompt(question_type="fill_blank")
        self.assertIn("BOŞLUK DOLDURMA", text)
        self.assertIn("_____", text)
        self.assertIn('"C": "..."', text)

    def test_unknown_type_rejected(self):
        with self.assertRaises(ValueError):
            self.prompt(question_type="essay")


class SchemaTests(unittest.TestCase):
    def test_valid_types(self):
        for qtype in ("multiple_choice", "true_false", "fill_blank"):
            self.assertEqual(GeneratedQuestion.model_validate(payload(qtype)).question_type, qtype)

    def test_default_is_multiple_choice(self):
        data = payload()
        del data["question_type"]
        self.assertEqual(GeneratedQuestion.model_validate(data).question_type, "multiple_choice")

    def test_true_false_has_two_options_and_no_null_keys(self):
        dumped = GeneratedQuestion.model_validate(payload("true_false")).model_dump(by_alias=True)
        self.assertEqual(set(dumped["translations"]["en"]["options"]), {"A", "B"})
        self.assertEqual(set(dumped["translations"]["en"]["clarified_options"]), {"A", "B"})

    def test_true_false_rejects_extra_options_and_bad_answer(self):
        bad = payload("true_false")
        bad["translations"]["en"]["options"].update({"C": "x", "D": "y"})
        with self.assertRaises(ValidationError):
            GeneratedQuestion.model_validate(bad)
        with self.assertRaises(ValidationError):
            GeneratedQuestion.model_validate(payload("true_false", correct_answer="C"))
        with self.assertRaises(ValidationError):  # is_true stays True but the answer says False
            GeneratedQuestion.model_validate(payload("true_false", correct_answer="B"))

    def test_fill_blank_requires_single_blank(self):
        for text in ("No blank here.", "Two _____ blanks _____."):
            bad = payload("fill_blank")
            bad["translations"]["en"]["question"] = text
            with self.assertRaises(ValidationError):
                GeneratedQuestion.model_validate(bad)

    def test_multiple_choice_still_requires_four_options(self):
        bad = payload()
        del bad["translations"]["en"]["options"]["D"]
        with self.assertRaises(ValidationError):
            GeneratedQuestion.model_validate(bad)

    def test_topic_terms_ignores_true_false_answer(self):
        q = GeneratedQuestion.model_validate(payload("true_false"))
        self.assertEqual(topic_terms(q), ["k"])


class GenerationFlowTests(unittest.TestCase):
    def run_flow(self, qtype, truth=None, translated=None):
        data = {**payload(qtype), "_provider": "gemini"}
        manager = Mock()
        manager.generate_trivia.return_value = data
        planner = MagicMock()
        planner.create_plan.return_value = SimpleNamespace(
            scope="global", target_country=None, target_country_credit=1, is_global_eligible=True
        )
        balancer = MagicMock()
        balancer.get_balanced_score_for_level.return_value = 5
        repo = MagicMock()
        repo.country_code.return_value = "GL"
        repo.question_languages.return_value = ["en"]
        planner.sheet_repository = repo
        seen = {}

        def fake_translate(source, source_language, langs, **kw):
            seen["source"] = copy.deepcopy(source)
            return {"en": translated or source}, "P"

        with patch.object(generator, "translate_localized_content", side_effect=fake_translate), \
             patch("core.sheet_algorithm.get_sheet_algorithm_repository", return_value=repo), \
             patch("core.option_balancer.get_option_balancer") as get_balancer:
            get_balancer.return_value.balance_question.side_effect = lambda question: question
            q = generator.generate_question_with_fallback(
                primary_category="Bilim", target_subcategory="Astronomi", languages=["en"], llm_manager=manager,
                generation_planner=planner, difficulty_balancer=balancer, base_difficulty="Orta", target_score=5,
                question_type=qtype, true_false_answer=truth,
            )
        return q, seen, get_balancer

    def test_true_false_flow_pads_for_translation_and_strips_after(self):
        q, seen, get_balancer = self.run_flow("true_false", truth=True)
        self.assertEqual(set(seen["source"]["options"]), {"A", "B", "C", "D"})  # padded for the A-D pipeline
        self.assertEqual(set(q.translations["en"].options.model_dump()), {"A", "B"})
        self.assertEqual(q.question_type, "true_false")
        self.assertEqual(q.correct_answer, "A")
        get_balancer.assert_not_called()  # A=True / B=False must not be shuffled
        self.assertEqual(q.payload["question_type"], "true_false")

    def test_true_false_truth_mismatch_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.run_flow("true_false", truth=False)

    def test_fill_blank_blank_lost_in_translation_is_rejected(self):
        broken = content("The largest planet is Jupiter.", {"A": "J", "B": "M", "C": "V", "D": "E"})
        with self.assertRaises(RuntimeError):  # retries exhausted; last error names the missing blank
            self.run_flow("fill_blank", translated=broken)

    def test_multiple_choice_flow_uses_balancer(self):
        q, _, get_balancer = self.run_flow("multiple_choice")
        self.assertEqual(q.question_type, "multiple_choice")
        get_balancer.assert_called()


class BasicOnlyTargetTests(unittest.TestCase):
    def test_exactly_21_basic_categories(self):
        queue = SheetAlgorithmRepository().build_credit_queue(basic_only=True)
        self.assertEqual(len({t.category for t in queue}), 21)
        self.assertEqual(len({(t.category, t.subcategory) for t in queue}), 21)

    def run_mode(self, mode, **kw):
        calls = []

        def fake(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                categories=[kwargs["primary_category"]], sub_categories=[kwargs["target_subcategory"]],
                translations={"tr": SimpleNamespace(question="q")}, generation_provider="F", categories_meta=[],
                sub_categories_meta=[], algorithm_source=None, is_visual_question=False,
                question_image_search_query=None, question_image_alt_text=None, question_image_url=None,
            )

        repository = SheetAlgorithmRepository()
        options = GenerationOptions(
            question_count=4, generation_mode=mode, difficulty="Orta", combo_percent=0,
            sleep_between_requests=0, write_to_firestore=False, concurrency=1,
            visual_questions_enabled=False, **kw,
        )
        with patch("worker.generate_question_with_fallback", side_effect=fake), \
             patch.object(repository, "build_credit_queue", wraps=repository.build_credit_queue) as queue:
            run_generation(options, repository=repository, llm_manager=SimpleNamespace(status=lambda live=False: {}))
        return calls, queue

    def test_question_types_mode_uses_basic_pool_and_alternates_types(self):
        calls, queue = self.run_mode("question_types", question_types="mixed")
        self.assertTrue(queue.call_args.kwargs["basic_only"])
        self.assertEqual([c["question_type"] for c in calls], ["true_false", "fill_blank"] * 2)
        self.assertEqual([c["true_false_answer"] for c in calls], [True, None, False, None])
        basic = {(t.category, t.subcategory) for t in SheetAlgorithmRepository().build_credit_queue(basic_only=True)}
        self.assertTrue({(c["primary_category"], c["target_subcategory"]) for c in calls} <= basic)

    def test_other_modes_always_multiple_choice(self):
        for mode in ("basic", "154"):
            calls, _ = self.run_mode(mode, question_types=["true_false"])
            self.assertEqual({c["question_type"] for c in calls}, {"multiple_choice"}, mode)
            self.assertEqual({c["true_false_answer"] for c in calls}, {None})


if __name__ == "__main__":
    unittest.main()


class ProviderCallErrorDetailTest(unittest.TestCase):
    def test_all_providers_error_carries_original_cause(self):
        from core.llm_manager import AllProvidersCoolingDown, MultiLLMManager, ProviderCallError

        with patch.dict("os.environ", {"COHERE_API_KEY": "k"}, clear=False),                 patch("core.llm_manager.cohere", create=True):
            m = MultiLLMManager()
        m.providers = ["cohere"]
        m._call_cohere = Mock(side_effect=ProviderCallError("Cohere", ValueError("model_not_found xyz")))
        with self.assertRaises(AllProvidersCoolingDown) as ctx:
            m.generate_trivia("s", "u")
        self.assertIn("model_not_found xyz", str(ctx.exception))
