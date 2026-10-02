from __future__ import annotations

import os
import tempfile
import unittest

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
        self.assertEqual(suite["covers"], [])

    def test_the_template_wraps_each_case_and_is_covered_by_default(self) -> None:
        write(os.path.join(self.root, "prompts", "label.md"), "Label this:\n{{case}}\n")
        suite = load(make_suite(self.root, settings={"prompt": "../prompts/label.md"}))
        self.assertEqual(suite["cases"][0]["prompt"], "Label this:\nI love it\n")
        self.assertEqual(suite["covers"], ["../prompts/label.md"])

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
        self.assertEqual(sorted(prints), ["../notes.md", "../skill/SKILL.md", "../skill/refs/a.md"])

    def test_an_edit_changes_only_that_file(self) -> None:
        before = fingerprint(load(self.folder))
        write(os.path.join(self.root, "skill", "refs", "a.md"), "two, edited")
        after = fingerprint(load(self.folder))
        self.assertEqual([k for k in before if before[k] != after[k]], ["../skill/refs/a.md"])

    def test_a_missing_cover_is_refused_before_a_run_and_absent_for_verify(self) -> None:
        os.remove(os.path.join(self.root, "notes.md"))
        suite = load(self.folder)
        with self.assertRaises(SuiteError):
            fingerprint(suite)
        self.assertNotIn("../notes.md", fingerprint(suite, strict=False))


if __name__ == "__main__":
    unittest.main()
