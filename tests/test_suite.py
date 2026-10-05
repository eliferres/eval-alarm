from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from eval_alarm.suite import SuiteError, fingerprint, load
from helpers import make_suite, write


class TestLoad(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def assertRefused(self, folder: str, words: str) -> None:
        with self.assertRaises(SuiteError) as caught:
            load(folder)
        self.assertIn(words, str(caught.exception))

    def test_a_suite_without_suite_json_takes_the_defaults(self) -> None:
        suite = load(make_suite(self.root))
        self.assertEqual(suite["name"], "sentiment")
        self.assertEqual([c["name"] for c in suite["cases"]], ["praise", "refund"])
        self.assertEqual(suite["settings"]["runs"], 3)
        self.assertEqual(suite["covers"], ["cases"])

    def test_the_template_wraps_each_case_and_is_covered_by_default(self) -> None:
        write(os.path.join(self.root, "prompts", "label.md"), "Label this:\n{{case}}\n")
        suite = load(make_suite(self.root, settings={"prompt": "../prompts/label.md"}))
        self.assertEqual(suite["cases"][0]["prompt"], "Label this:\nI love it\n")
        self.assertEqual(suite["covers"], ["../prompts/label.md", "cases", "suite.json"])

    def test_a_template_without_the_placeholder_is_refused(self) -> None:
        write(os.path.join(self.root, "prompts", "label.md"), "Label this.\n")
        folder = make_suite(self.root, settings={"prompt": "../prompts/label.md"})
        self.assertRefused(folder, "has no {{case}} placeholder")

    def test_an_unknown_setting_is_refused(self) -> None:
        self.assertRefused(make_suite(self.root, settings={"rnus": 3}), "unknown setting rnus")

    def test_runs_must_be_positive(self) -> None:
        self.assertRefused(make_suite(self.root, settings={"runs": 0}), "runs must be")

    def test_min_baseline_above_window_is_refused(self) -> None:
        folder = make_suite(self.root, settings={"alarm": {"window": 2, "min_baseline": 3}})
        self.assertRefused(folder, "min_baseline not above window")

    def test_a_broken_expected_json_names_the_case(self) -> None:
        folder = make_suite(self.root, cases={"bad": ("x", {"scorer": "vibes"})})
        self.assertRefused(folder, os.path.join("bad", "expected.json"))

    def test_no_cases_is_refused(self) -> None:
        os.makedirs(os.path.join(self.root, "empty"))
        self.assertRefused(os.path.join(self.root, "empty"), "has no cases")

    def test_a_prompt_that_is_not_utf8_is_refused_by_name(self) -> None:
        folder = make_suite(self.root)
        with open(os.path.join(folder, "cases", "praise", "prompt.md"), "wb") as f:
            f.write(b"\xff\xfe")
        self.assertRefused(folder, "is not UTF-8 text")

    def test_margins_finer_than_one_answer_are_warned_about(self) -> None:
        # 2 cases x 3 runs: one answer moves the mean by 1/6, one case's
        # spread moves the suite's spread by 1/2.
        suite = load(make_suite(self.root))
        self.assertEqual(suite["warnings"], [
            "sentiment: drop_margin 0.05 is below one answer's weight on the mean (1/6 = 0.17), "
            "so one wrong answer can raise DROP",
            "sentiment: slide_margin 0.10 is below one answer's weight on the mean (1/6 = 0.17), "
            "so one wrong answer can raise SLIDE",
            "sentiment: spread_margin 0.10 is below one case's weight on the spread (1/2 = 0.50), "
            "so one wrong answer can raise WIDER",
        ])

    def test_a_suite_big_enough_for_its_margins_loads_quietly(self) -> None:
        cases = {f"c{i}": ("x", {"scorer": "exact", "expected": "y"}) for i in range(10)}
        self.assertEqual(load(make_suite(self.root, cases=cases))["warnings"], [])

    def test_suite_json_that_is_not_json(self) -> None:
        folder = make_suite(self.root)
        write(os.path.join(folder, "suite.json"), "{runs: 3")
        self.assertRefused(folder, "is not valid JSON")


class TestFingerprint(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        write(os.path.join(self.root, "skill", "SKILL.md"), "one")
        write(os.path.join(self.root, "skill", "refs", "a.md"), "two")
        write(os.path.join(self.root, "notes.md"), "three")
        self.folder = make_suite(self.root, settings={"covers": ["../skill", "../notes.md"]})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_folders_expand_to_their_files(self) -> None:
        prints = fingerprint(load(self.folder))
        self.assertEqual([p for p in sorted(prints) if p.startswith("..")],
                         ["../notes.md", "../skill/SKILL.md", "../skill/refs/a.md"])

    def test_the_cases_and_suite_json_are_always_covered(self) -> None:
        prints = fingerprint(load(self.folder))
        self.assertIn("cases/praise/expected.json", prints)
        self.assertIn("suite.json", prints)

    def test_hidden_files_are_left_out(self) -> None:
        before = fingerprint(load(self.folder))
        write(os.path.join(self.folder, "cases", "praise", ".DS_Store"), "finder")
        write(os.path.join(self.root, "skill", ".SKILL.md.swp"), "editor")
        write(os.path.join(self.root, "skill", ".cache", "x"), "tool")
        self.assertEqual(fingerprint(load(self.folder)), before)

    def test_a_suite_inside_a_covered_folder_leaves_out_its_own_results(self) -> None:
        folder = make_suite(self.root, "inner", settings={"covers": [".."]})
        state = os.path.join(self.root, "state")
        with mock.patch.dict(os.environ, {"EVAL_ALARM_STATE_DIR": state}):
            before = fingerprint(load(folder))
            write(os.path.join(folder, "results.jsonl"), '{"mean": 1.0}\n')
            write(os.path.join(state, "answers", "inner-1", "t", "praise-r1.txt"), "positive")
            write(os.path.join(state, "budget.jsonl"), "{}\n")
            self.assertEqual(fingerprint(load(folder)), before)

    def test_an_edit_changes_only_that_file(self) -> None:
        before = fingerprint(load(self.folder))
        write(os.path.join(self.root, "skill", "refs", "a.md"), "two, edited")
        after = fingerprint(load(self.folder))
        self.assertEqual([k for k in before if before[k] != after[k]], ["../skill/refs/a.md"])

    def test_an_edit_inside_a_linked_folder_changes_the_fingerprint(self) -> None:
        write(os.path.join(self.root, "shared", "tone.md"), "calm")
        os.symlink(os.path.join(self.root, "shared"), os.path.join(self.root, "skill", "shared"))
        before = fingerprint(load(self.folder))
        self.assertIn("../skill/shared/tone.md", before)
        write(os.path.join(self.root, "shared", "tone.md"), "brisk")
        self.assertNotEqual(fingerprint(load(self.folder)), before)

    def test_a_link_back_to_its_own_parent_does_not_loop(self) -> None:
        os.symlink(os.path.join(self.root, "skill"), os.path.join(self.root, "skill", "refs", "up"))
        prints = fingerprint(load(self.folder))
        self.assertEqual([p for p in sorted(prints) if p.startswith("..")],
                         ["../notes.md", "../skill/SKILL.md", "../skill/refs/a.md"])

    def test_a_missing_cover_is_refused_before_a_run_and_absent_for_verify(self) -> None:
        os.remove(os.path.join(self.root, "notes.md"))
        suite = load(self.folder)
        with self.assertRaises(SuiteError):
            fingerprint(suite)
        self.assertNotIn("../notes.md", fingerprint(suite, strict=False))


if __name__ == "__main__":
    unittest.main()
