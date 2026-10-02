from __future__ import annotations

import unittest

from eval_alarm.scorers import score, validate


class TestExact(unittest.TestCase):
    def test_trimmed_answer_equal_to_expected_scores_one(self) -> None:
        self.assertEqual(score("  positive\n", {"scorer": "exact", "expected": "positive"}), 1.0)

    def test_any_of_a_list_is_accepted(self) -> None:
        exp = {"scorer": "exact", "expected": ["PASS", "OK"]}
        self.assertEqual(score("OK", exp), 1.0)
        self.assertEqual(score("FAIL", exp), 0.0)

    def test_a_chatty_answer_around_the_right_word_scores_zero(self) -> None:
        self.assertEqual(score("It is positive.", {"scorer": "exact", "expected": "positive"}), 0.0)

    def test_case_is_kept_unless_ignore_case(self) -> None:
        self.assertEqual(score("Positive", {"scorer": "exact", "expected": "positive"}), 0.0)
        self.assertEqual(
            score("Positive", {"scorer": "exact", "expected": "positive", "ignore_case": True}), 1.0)


class TestContains(unittest.TestCase):
    def test_score_is_the_share_of_strings_found(self) -> None:
        exp = {"scorer": "contains", "expected": ["refund", "sorry", "tracking"]}
        self.assertEqual(score("We are sorry; a refund is on its way.", exp), 0.6667)

    def test_ignore_case(self) -> None:
        exp = {"scorer": "contains", "expected": "NEGATIVE", "ignore_case": True}
        self.assertEqual(score("this one reads negative to me", exp), 1.0)


class TestRegex(unittest.TestCase):
    def test_patterns_and_must_not_count_as_one_check_each(self) -> None:
        exp = {"scorer": "regex", "patterns": [r"^VERDICT: (PASS|FAIL)$", r"line \d+"],
               "must_not": [r"as an AI"]}
        self.assertEqual(score("Bug on line 4.\nVERDICT: FAIL", exp), 1.0)
        self.assertEqual(score("As an AI:\nVERDICT: FAIL", {**exp, "ignore_case": True}), 0.3333)

    def test_patterns_match_per_line(self) -> None:
        exp = {"scorer": "regex", "patterns": [r"^## Summary$"]}
        self.assertEqual(score("intro\n## Summary\nbody", exp), 1.0)


class TestJson(unittest.TestCase):
    exp = {"scorer": "json", "shape": {"label": "string", "confidence": "number"},
           "fields": {"label": "negative"}}

    def test_every_check_passing_scores_one(self) -> None:
        self.assertEqual(score('{"label": "negative", "confidence": 0.9}', self.exp), 1.0)

    def test_a_single_fenced_block_is_unwrapped(self) -> None:
        answer = '```json\n{"label": "negative", "confidence": 1}\n```'
        self.assertEqual(score(answer, self.exp), 1.0)

    def test_wrong_type_and_wrong_value_each_cost_a_share(self) -> None:
        self.assertEqual(score('{"label": "positive", "confidence": "high"}', self.exp), 0.3333)

    def test_a_boolean_is_not_a_number(self) -> None:
        self.assertEqual(score('{"label": "negative", "confidence": true}', self.exp), 0.6667)

    def test_prose_scores_zero(self) -> None:
        self.assertEqual(score("The label is negative.", self.exp), 0.0)

    def test_no_checks_means_any_valid_json(self) -> None:
        self.assertEqual(score("[1, 2]", {"scorer": "json"}), 1.0)


class TestValidate(unittest.TestCase):
    def assertInvalid(self, exp: object, words: str) -> None:
        with self.assertRaises(ValueError) as caught:
            validate(exp)
        self.assertIn(words, str(caught.exception))

    def test_unknown_scorer(self) -> None:
        self.assertInvalid({"scorer": "vibes"}, "scorer must be one of")

    def test_empty_expected(self) -> None:
        self.assertInvalid({"scorer": "contains", "expected": []}, "nothing would be scored")

    def test_regex_that_does_not_compile(self) -> None:
        self.assertInvalid({"scorer": "regex", "patterns": ["("]}, "does not compile")

    def test_regex_with_no_checks(self) -> None:
        self.assertInvalid({"scorer": "regex"}, "nothing would be scored")

    def test_unknown_json_type(self) -> None:
        self.assertInvalid({"scorer": "json", "shape": {"a": "float"}}, "shape must map keys")

    def test_not_an_object(self) -> None:
        self.assertInvalid(["exact"], "not a JSON object")

    def test_ignore_case_must_be_boolean(self) -> None:
        self.assertInvalid({"scorer": "exact", "expected": "a", "ignore_case": "yes"}, "true or false")


if __name__ == "__main__":
    unittest.main()
