"""The adaptive policy, the mastery rule, the misconception classifier and the curriculum, with no server.

The policy and the rule are pure functions of a history, so each rule is pinned here with the
smallest history that shows it. The question templates are checked across many seeds against the
verifier and against SymPy working the answer out another way.
"""

from __future__ import annotations

import random

import pytest
import sympy as sp

from quack.services.adaptive import MAX_TARGETED, Attempt, assess, choose_next, normal_progression
from quack.services.curriculum import Course, CurriculumError, course, validate
from quack.services.lesson_questions import EQUATION, FACTORISE, TEMPLATES, build
from quack.services.math_parser import parse_expression, parse_statement, to_sympy
from quack.services.math_verifier import LineInput, completes_answer, completes_factorisation, verify_lines
from quack.services.misconceptions import TAXONOMY, classify

ALGEBRA = course("algebra_foundations")
assert ALGEBRA is not None
LIKE = ALGEBRA.level("combine_like_terms")
FINAL = ALGEBRA.level("algebra_adventure")
assert LIKE is not None and FINAL is not None


def attempt(
    number: int, template: str = "like_terms.add_two", status: str = "correct", mistakes: tuple = (),
    assisted: bool = False, kind: str = "start", target: str | None = None, session: str = "visit",
) -> Attempt:
    found = TEMPLATES[template]
    return Attempt(f"p{number}", session, template, found.skill, found.tier, status, mistakes, assisted, kind, target)


DROPPED = (("variable_dropped", "confirmed"),)


class TestOutcomes:
    @pytest.mark.parametrize(
        ("status", "mistakes", "assisted", "outcome", "credit"),
        [
            ("correct", (), False, "independent", 1.0),
            ("correct", DROPPED, False, "self_corrected", 0.5),
            ("correct", (), True, "assisted", 0.5),
            ("revealed", (), False, "revealed", 0.0),
            ("attempted", DROPPED, False, "struggled", 0.0),
            ("open", (), False, "no_evidence", 0.0),
            ("skipped", (), False, "no_evidence", 0.0),
        ],
    )
    def test_each_ending_and_its_credit(self, status: str, mistakes: tuple, assisted: bool, outcome: str, credit: float) -> None:
        one = attempt(1, status=status, mistakes=mistakes, assisted=assisted)
        assert one.outcome == outcome
        assert one.credit == credit


class TestMastery:
    def test_three_right_alone_up_to_a_challenge(self) -> None:
        history = [
            attempt(1, "like_terms.add_two"),
            attempt(2, "like_terms.three_terms"),
            attempt(3, "like_terms.two_letters"),
        ]
        state = assess(LIKE, history)
        assert state.mastered
        assert state.credits == 3

    def test_credit_alone_isnt_enough(self) -> None:
        # Plenty of points, all from the easiest kind of question, never a challenge.
        history = [attempt(n, "like_terms.add_two") for n in range(5)]
        state = assess(LIKE, history)
        assert state.credits == 5
        assert not state.mastered
        assert state.distinct_templates == 1
        assert not state.independent_at_met

    def test_the_same_question_counted_twice_counts_once(self) -> None:
        history = [attempt(1, "like_terms.add_two"), attempt(1, "like_terms.add_two"), attempt(1, "like_terms.add_two")]
        assert assess(LIKE, history).credits == 1

    def test_unsure_or_skipped_questions_change_nothing(self) -> None:
        history = [attempt(1, status="open"), attempt(2, status="skipped")]
        state = assess(LIKE, history)
        assert state.credits == 0
        assert state.open_misconceptions == ()

    def test_a_confirmed_misconception_must_be_cleared_on_its_own(self) -> None:
        history = [
            attempt(1, "like_terms.add_two", mistakes=DROPPED),
            attempt(2, "like_terms.three_terms"),
            attempt(3, "like_terms.two_letters"),
            attempt(4, "like_terms.hidden_one"),
        ]
        # like_terms.three_terms practises a dropped variable, and was right alone after it.
        assert assess(LIKE, history).open_misconceptions == ()
        stuck = [attempt(1, "like_terms.two_letters_negative"), attempt(2, "like_terms.add_two", mistakes=DROPPED),
                 attempt(3, "like_terms.two_letters_negative", status="correct")]
        assert "variable_dropped" in assess(LIKE, stuck).open_misconceptions

    def test_help_isnt_a_clearing_success(self) -> None:
        history = [attempt(1, mistakes=DROPPED), attempt(2, "like_terms.subtract_two", assisted=True)]
        assert assess(LIKE, history).open_misconceptions == ("variable_dropped",)

    def test_a_plausible_naming_doesnt_have_to_be_cleared(self) -> None:
        history = [attempt(1, mistakes=(("variable_dropped", "plausible"),))]
        assert assess(LIKE, history).open_misconceptions == ()

    def test_moving_on_is_offered_after_enough_questions(self) -> None:
        history = [attempt(n, status="skipped") for n in range(LIKE.mastery.move_on_after)]
        assert assess(LIKE, history).can_move_on
        assert not assess(LIKE, history[:-1]).can_move_on

    def test_the_final_challenge_wants_several_skills(self) -> None:
        one_skill = [attempt(n, t) for n, t in enumerate(
            ["brackets.two_brackets", "brackets.factorise_minus", "brackets.two_brackets_added", "brackets.factorise_larger"]
        )]
        assert not assess(FINAL, one_skill).mastered
        mixed = [attempt(n, t) for n, t in enumerate(
            ["brackets.two_brackets", "linear_equations.brackets", "substitution.square_negative",
             "linear_equations.brackets_both_sides"]
        )]
        assert assess(FINAL, mixed).mastered


class TestSelection:
    def rng(self) -> random.Random:
        return random.Random(7)

    def test_a_corrected_misconception_gets_another_on_the_same_idea(self) -> None:
        history = [attempt(1, mistakes=DROPPED)]
        chosen = choose_next(LIKE, history, "visit", self.rng())
        assert chosen.kind == "targeted"
        assert chosen.target == "variable_dropped"
        assert chosen.template != "like_terms.add_two"
        assert "variable_dropped" in TEMPLATES[chosen.template].targets
        assert chosen.basis["previous_outcome"] == "self_corrected"
        assert chosen.basis["rule"] == "corrected_confirmed_misconception"

    def test_without_naming_it_the_choice_is_ordinary(self) -> None:
        history = [attempt(1, mistakes=(("verified_error_unclassified", "unclassified"),))]
        chosen = choose_next(LIKE, history, "visit", self.rng())
        assert chosen.kind == "same_tier"
        assert chosen.target is None

    def test_a_plausible_misconception_is_never_targeted(self) -> None:
        history = [attempt(1, status="attempted", mistakes=(("variable_dropped", "plausible"),))]
        chosen = choose_next(LIKE, history, "visit", self.rng())
        assert chosen.target is None

    def test_different_misconceptions_choose_different_practice(self) -> None:
        brackets = ALGEBRA.level("brackets_and_factors")
        equations = ALGEBRA.level("solve_linear_equations")
        assert brackets is not None and equations is not None
        dropped = choose_next(LIKE, [attempt(1, status="attempted", mistakes=DROPPED)], "v", self.rng())
        distributed = choose_next(
            brackets, [attempt(1, "brackets.expand", status="attempted", mistakes=(("distribution_incomplete", "confirmed"),))],
            "v", self.rng(),
        )
        inverse = choose_next(
            equations,
            [attempt(1, "linear_equations.two_step", status="attempted", mistakes=(("incorrect_inverse_operation", "confirmed"),))],
            "v", self.rng(),
        )
        assert TEMPLATES[dropped.template].skill == "algebra.like_terms"
        assert TEMPLATES[distributed.template].skill == "algebra.brackets"
        assert TEMPLATES[inverse.template].skill == "algebra.linear_equations"
        assert {dropped.target, distributed.target, inverse.target} == {
            "variable_dropped", "distribution_incomplete", "incorrect_inverse_operation"
        }

    def test_in_the_mixed_challenge_a_bracket_slip_goes_back_to_brackets(self) -> None:
        history = [attempt(1, "linear_equations.brackets", status="attempted",
                           mistakes=(("distribution_incomplete", "confirmed"),))]
        chosen = choose_next(FINAL, history, "visit", self.rng())
        assert chosen.kind == "simpler"
        assert TEMPLATES[chosen.template].skill == "algebra.brackets"
        # And put right, it's checked again on the same kind of question it was made on.
        fixed = [attempt(1, "brackets.two_brackets_added", mistakes=(("distribution_incomplete", "confirmed"),))]
        again = choose_next(FINAL, fixed, "visit", self.rng())
        assert TEMPLATES[again.template].skill == "algebra.brackets"

    def test_reinforcement_is_bounded(self) -> None:
        history = [attempt(0, mistakes=DROPPED, status="attempted")]
        kinds = []
        for number in range(1, 6):
            chosen = choose_next(LIKE, history, "visit", self.rng())
            kinds.append(chosen.kind)
            history.append(attempt(number, chosen.template, status="attempted", mistakes=DROPPED,
                                   kind=chosen.kind, target=chosen.target))
        targeted = [kind for kind in kinds if kind in ("targeted", "simpler", "concept_review")]
        assert len(targeted) == MAX_TARGETED
        assert kinds.count("concept_review") == 1
        assert kinds[MAX_TARGETED] == "change_of_pace"

    def test_right_alone_steps_up_and_stops_at_the_top(self) -> None:
        assert choose_next(LIKE, [attempt(1)], "visit", self.rng()).kind == "step_up"
        top = choose_next(LIKE, [attempt(1, "like_terms.two_letters")], "visit", self.rng())
        assert top.kind == "keep_going"
        assert top.tier == "challenge"

    def test_a_new_visit_starts_from_what_was_left_open(self) -> None:
        history = [attempt(1, status="attempted", mistakes=DROPPED, session="before")]
        chosen = choose_next(LIKE, history, "now", self.rng())
        assert chosen.kind == "resume_targeted"
        assert chosen.target == "variable_dropped"

    def test_the_first_question_ever_is_the_opener(self) -> None:
        chosen = choose_next(LIKE, [], "v", self.rng())
        assert (chosen.kind, chosen.template) == ("start", "like_terms.add_two")

    def test_the_same_history_always_gives_the_same_choice(self) -> None:
        history = [attempt(1, mistakes=DROPPED)]
        one = choose_next(LIKE, history, "visit", random.Random(3))
        two = choose_next(LIKE, history, "visit", random.Random(3))
        assert one == two

    def test_the_baseline_ignores_what_went_wrong(self) -> None:
        history = [attempt(1, mistakes=DROPPED)]
        assert normal_progression(LIKE, history, "visit", self.rng()).kind == "step_up"


class TestClassifier:
    @pytest.mark.parametrize(
        ("question", "method", "category", "error_type", "before", "after", "named", "confidence"),
        [
            ("3x + 2x", "identity", "algebra_like_terms", "lost_variable", "3x + 2x", "5", "variable_dropped", "confirmed"),
            ("3x + 2x", "identity", "algebra_like_terms", "combining_like_terms", "3x + 2x", "5x^2", "letters_multiplied",
             "confirmed"),
            ("4x + 3", "identity", "algebra_like_terms", "combining_like_terms", "4x + 3", "7x", "unlike_terms_combined",
             "confirmed"),
            ("7x - 3x + 2x", "identity", "algebra_like_terms", "combining_like_terms", "7x - 3x + 2x", "2x", "sign_error",
             "confirmed"),
            ("3(x + 2)", "identity", "algebra_expansion", "incorrect_distribution", "3(x + 2)", "3x + 2",
             "distribution_incomplete", "confirmed"),
            ("6x + 9", "factorise", "algebra_factorisation", "incorrect_factorisation", "6x + 9", "3(2x + 9)",
             "common_factor_incomplete", "confirmed"),
            ("3x + 5 = 17", "equation", "equation_solving", "sign_when_moving_term", "3x + 5 = 17", "3x = 22",
             "incorrect_inverse_operation", "confirmed"),
            ("4(x - 1) = 12", "equation", "equation_solving", "solution_set_changed", "4(x - 1) = 12", "4x - 1 = 12",
             "distribution_incomplete", "confirmed"),
            ("3x + 5 = 17", "equation", "equation_solving", "solution_set_changed", "3x = 12", "x = 9",
             "incorrect_inverse_operation", "confirmed"),
            ("2(-3) + 7", "arithmetic", "arithmetic", "arithmetic_error", "2(-3) + 7", "13", "sign_error", "confirmed"),
            # Kinds of mistake that fit without the value proving it, and mistakes with nothing to say.
            ("3x + 5 = 17", "equation", "equation_solving", "solution_set_changed", "3x = 12", "x = 5",
             "incorrect_inverse_operation", "plausible"),
            ("6x + 9", "factorise", "algebra_factorisation", "incorrect_factorisation", "6x + 9", "2(3x + 4)",
             "common_factor_incomplete", "plausible"),
            ("3(4) + 2", "arithmetic", "arithmetic", "arithmetic_error", "3(4) + 2", "15", "verified_error_unclassified",
             "unclassified"),
            ("5a + 3b - 2a + b", "identity", "algebra_like_terms", "combining_like_terms", "5a + 3b - 2a + b", "3a + 3b",
             "verified_error_unclassified", "unclassified"),
        ],
    )
    def test_named_only_when_the_maths_shows_it(
        self, question: str, method: str, category: str, error_type: str, before: str, after: str, named: str,
        confidence: str,
    ) -> None:
        found = classify("skill", question, method, (), category, error_type, before, after)
        assert (found.misconception, found.confidence) == (named, confidence)

    def test_a_predicted_wrong_answer_is_confirmed(self) -> None:
        found = classify("s", "3(4) + 2", "arithmetic", (("substitution_as_digits", "36"),), "arithmetic",
                         "arithmetic_error", "3(4) + 2", "36")
        assert (found.misconception, found.evidence) == ("substitution_as_digits", "probe")

    def test_unreadable_text_is_unclassified_not_an_error(self) -> None:
        found = classify("s", "3x", "identity", (), "algebra", "x", "3x +", "((")
        assert found.confidence == "unclassified"

    def test_every_template_names_only_known_misconceptions(self) -> None:
        for template in TEMPLATES.values():
            assert set(template.targets) <= set(TAXONOMY), template.id
            for misconception, _ in build(template.id, random.Random(0)).probes:
                assert misconception in TAXONOMY, template.id


def _in_degrees(math: str) -> sp.Expr:
    """The paths' trigonometry is in degrees; the parser's SymPy form is in radians, so convert here."""
    return to_sympy(parse_expression(math)).replace(
        lambda part: isinstance(part, (sp.sin, sp.cos, sp.tan)), lambda part: part.func(part.args[0] * sp.pi / 180)
    )


class TestTemplates:
    @pytest.mark.parametrize("template_id", sorted(TEMPLATES))
    def test_every_seed_gives_a_question_the_verifier_accepts_and_sympy_agrees_with(self, template_id: str) -> None:
        for seed in range(12):
            question = build(template_id, random.Random(seed))
            math, expected = question.question.math, question.question.expected
            if question.method == EQUATION:
                left, right = parse_statement(math).parts
                x = sp.Symbol("x", real=True)
                solved = sp.solve(sp.Eq(to_sympy(left), to_sympy(right)), x)
                assert solved == [sp.Integer(int(expected.split("=")[1]))], (template_id, seed, math)
                report = verify_lines([LineInput(0, expected, continues="yes", intent="solve", given=math)])
                assert report.status == "valid", (template_id, seed, math)
                assert completes_answer(math, expected, expected)
                continue
            if math.startswith("∫"):
                # SymPy can't hold an indefinite integral here, so check the answer by differentiating it back.
                integrand, answer = math.removeprefix("∫").removesuffix("dx"), expected.removesuffix("+ C")
                assert expected.endswith("+ C"), (template_id, expected)
                antiderivative = to_sympy(parse_expression(answer))
                x = next(iter(antiderivative.free_symbols))
                assert sp.expand(sp.diff(antiderivative, x) - to_sympy(parse_expression(integrand))) == 0
            else:
                assert sp.simplify(_in_degrees(math) - _in_degrees(expected)) == 0, (template_id, seed, math)
            line = f"{math} = {expected}"
            assert verify_lines([LineInput(0, line, posed=True)]).status == "valid", (template_id, seed, line)
            if question.method == FACTORISE:
                assert completes_factorisation(math, line)
                # The question written again is true, and isn't an answer.
                assert not completes_factorisation(math, f"{math} = {math}")
            else:
                assert completes_answer(math, expected, line), (template_id, seed, line)
            for _, wrong in question.probes:
                # A predicted wrong answer really is wrong, so it can't be called right by mistake.
                assert sp.simplify(_in_degrees(wrong) - _in_degrees(expected)) != 0

    def test_questions_vary(self) -> None:
        for template_id in TEMPLATES:
            seen = {build(template_id, random.Random(seed)).question.math for seed in range(20)}
            assert len(seen) >= 5, template_id

    def test_harder_tiers_ask_for_more_steps(self) -> None:
        """Not bigger numbers: more terms, more kinds of term, or more operations."""

        def size(template_id: str) -> int:
            math = build(template_id, random.Random(1)).question.math
            return sum(math.count(sign) for sign in "+-=(") + len({c for c in math if c.isalpha()})

        for level in ALGEBRA.levels:
            by_tier = {tier: [t.id for t in TEMPLATES.values() if t.skill in level.skills and t.tier == tier]
                       for tier in level.tiers}
            easiest = min(size(t) for t in by_tier[level.tiers[0]])
            hardest = max(size(t) for t in by_tier[level.tiers[-1]])
            assert hardest > easiest, level.id


class TestCurriculum:
    def test_the_course_loads_and_holds_together(self) -> None:
        assert [level.number for level in ALGEBRA.levels] == [1, 2, 3, 4, 5, 6, 7]
        assert ALGEBRA.levels[-1].kind == "challenge"
        for level in ALGEBRA.levels:
            for skill in level.skills:
                assert ALGEBRA.skill(skill) is not None

    def broken(self, change) -> Course:  # type: ignore[no-untyped-def]
        data = ALGEBRA.model_dump()
        change(data)
        return Course.model_validate(data)

    def test_a_loop_in_prerequisites_is_refused(self) -> None:
        def loop(data: dict) -> None:
            data["levels"][0]["prerequisites"] = ["algebra_adventure"]

        with pytest.raises(CurriculumError, match="loop"):
            validate(self.broken(loop))

    def test_a_tier_with_no_questions_is_refused(self) -> None:
        def no_demonstrate(data: dict) -> None:
            data["levels"][0]["tiers"] = ["learn", "demonstrate"]

        with pytest.raises(CurriculumError, match="no demonstrate questions"):
            validate(self.broken(no_demonstrate))

    def test_an_unknown_misconception_is_refused(self) -> None:
        def unknown(data: dict) -> None:
            data["skills"][0]["misconceptions"] = ["bad_vibes"]

        with pytest.raises(CurriculumError, match="unknown misconception"):
            validate(self.broken(unknown))

    def test_a_lesson_that_gives_away_its_first_answer_is_refused(self) -> None:
        def spoil(data: dict) -> None:
            level = next(level for level in data["levels"] if level["id"] == "combine_like_terms")
            level["lesson"]["steps"][2] = "Keep the letter: 3x + 2x = 5x."

        with pytest.raises(CurriculumError, match="shows the answer"):
            validate(self.broken(spoil))

    def test_nudges_never_hold_a_number(self) -> None:
        for template in TEMPLATES.values():
            for hint in template.hints:
                assert not any(character.isdigit() for character in hint), (template.id, hint)



class TestReviewFindings:
    """From the independent review of the maths and the policy. Each failed on the first version."""

    @pytest.mark.parametrize(
        ("question", "after"),
        [("3(x + 4) - 2(x - 4)", "20"), ("3(x + 4) - 2(x - 4)", "x + 24"), ("5(x + 4) - 3(x - 2)", "2x + 30")],
    )
    def test_a_bracket_after_a_minus_keeps_its_sign(self, question: str, after: str) -> None:
        found = classify("algebra.brackets", question, "identity", (), "algebra_expansion", "incorrect_distribution",
                         question, after)
        assert found.misconception != "distribution_incomplete" or found.confidence != "confirmed"

    @pytest.mark.parametrize("after", ["x + 8", "x + 16", "2x + 20"])
    def test_a_real_missed_term_after_a_minus_is_still_found(self, after: str) -> None:
        # -2(x - 4) with the -4 copied, with only the minus applied, or with the x not multiplied.
        found = classify("algebra.brackets", "3(x + 4) - 2(x - 4)", "identity", (), "algebra_expansion",
                         "incorrect_distribution", "3(x + 4) - 2(x - 4)", after)
        assert (found.misconception, found.confidence) == ("distribution_incomplete", "confirmed")

    def test_a_misconception_no_question_here_practises_doesnt_trap_anyone(self) -> None:
        simplify = ALGEBRA.level("simplify_expressions")
        assert simplify is not None
        history = [attempt(0, "simplifying.subtract_term", status="attempted", mistakes=DROPPED)]
        state = assess(simplify, history)
        assert state.open_misconceptions == ()
        kinds = []
        for number in range(1, 5):
            chosen = choose_next(simplify, history, "visit", random.Random(number))
            kinds.append(chosen.kind)
            history.append(attempt(number, chosen.template, kind=chosen.kind, target=chosen.target))
        assert "step_up" in kinds
        assert assess(simplify, history).mastered

    def test_two_misconceptions_predicting_one_value_predict_neither(self) -> None:
        question = build("substitution.subtract", random.Random(0), {"a": 5, "value": 8, "b": 9})
        assert all(wrong != "49" for _, wrong in question.probes)
        for template_id in TEMPLATES:
            for seed in range(30):
                probes = build(template_id, random.Random(seed)).probes
                values = {}
                for misconception, wrong in probes:
                    value = sp.expand(to_sympy(parse_expression(wrong)))
                    assert values.setdefault(value, misconception) == misconception, (template_id, seed, wrong)

    def test_factorising_doesnt_clear_an_expansion_slip(self) -> None:
        brackets = ALGEBRA.level("brackets_and_factors")
        assert brackets is not None
        history = [
            attempt(0, "brackets.expand", status="attempted", mistakes=(("distribution_incomplete", "confirmed"),)),
            attempt(1, "brackets.factorise"),
        ]
        assert assess(brackets, history).open_misconceptions == ("distribution_incomplete",)

    def test_simpler_is_only_said_when_it_is(self) -> None:
        history = [attempt(1, status="attempted", mistakes=(("letters_multiplied", "confirmed"),))]
        chosen = choose_next(LIKE, history, "visit", random.Random(7))
        assert chosen.tier == "learn"
        assert "simpler" not in chosen.reason

    def test_one_of_a_letter_is_written_as_the_letter(self) -> None:
        question = build("simplifying.two_letters_bracket", random.Random(0), {"a": 7, "c": 4, "d": 1})
        assert "1b" not in question.question.display
