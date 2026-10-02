from __future__ import annotations

import os
import tempfile
import unittest
from typing import Optional

from eval_alarm.alarm import judge, usable, verify
from eval_alarm.suite import DEFAULTS, SuiteError, fingerprint, load
from helpers import make_suite, write

CFG = dict(DEFAULTS["alarm"])  # window 5, min_baseline 3, drop 0.05, slide 0.10, spread 0.10


def rec(mean: Optional[float], spread: Optional[float] = None, failed: int = 0) -> dict:
    return {"mean": mean, "spread": spread, "failed": failed}


class TestJudge(unittest.TestCase):
    def test_no_records(self) -> None:
        self.assertEqual(judge("s", [], CFG)["lines"], ["s: no runs yet"])

    def test_baseline_building_never_alarms(self) -> None:
        v = judge("s", [rec(0.9), rec(0.9), rec(0.1)], CFG)
        self.assertEqual(v["status"], "baseline building")
        self.assertEqual(v["lines"], ["s: baseline building, 2 of 3 earlier runs; latest mean 0.10"])

    def test_steady(self) -> None:
        v = judge("s", [rec(0.9, 0.1), rec(0.8, 0.1), rec(0.85, 0.0), rec(0.82, 0.05)], CFG)
        self.assertEqual(v["lines"], ["s: steady, mean 0.82, spread 0.05 against the last 3 runs (0.80 to 0.90)"])

    def test_drop_below_the_lowest_earlier_run(self) -> None:
        v = judge("s", [rec(0.8), rec(0.8), rec(0.82), rec(0.74)], CFG)
        self.assertEqual(v["alarms"], ["DROP"])
        self.assertEqual(v["lines"], ["ALARM s DROP: mean 0.74 is below the lowest of the last 3 runs (0.80) "
                                      "by more than 0.05"])

    def test_exactly_the_margin_is_not_a_drop(self) -> None:
        self.assertEqual(judge("s", [rec(0.8), rec(0.8), rec(0.8), rec(0.75)], CFG)["status"], "steady")

    def test_slide_below_the_average_without_breaking_the_floor(self) -> None:
        v = judge("s", [rec(1.0), rec(1.0), rec(0.6), rec(0.62)], CFG)
        self.assertEqual(v["alarms"], ["SLIDE"])

    def test_wider_spread(self) -> None:
        v = judge("s", [rec(0.9, 0.05), rec(0.9, 0.1), rec(0.9, 0.0), rec(0.9, 0.25)], CFG)
        self.assertEqual(v["alarms"], ["WIDER"])

    def test_a_suite_read_once_never_alarms_on_spread(self) -> None:
        self.assertEqual(judge("s", [rec(0.9)] * 3 + [rec(0.9, 0.9)], CFG)["status"], "steady")

    def test_only_the_window_counts(self) -> None:
        # A bad run six back has left the window of five, so 0.5 is a drop.
        history = [rec(0.4)] + [rec(0.9)] * 5 + [rec(0.5)]
        self.assertIn("DROP", judge("s", history, CFG)["alarms"])

    def test_a_failed_latest_run_is_not_a_drop(self) -> None:
        v = judge("s", [rec(0.9)] * 3 + [rec(None, failed=6)], CFG)
        self.assertEqual(v["status"], "failed")
        self.assertEqual(v["alarms"], [])

    def test_failed_runs_stay_out_of_the_baseline(self) -> None:
        v = judge("s", [rec(0.9), rec(None, failed=6), rec(0.9), rec(0.9)], CFG)
        self.assertEqual(v["status"], "baseline building")

    def test_records_with_unreadable_numbers_are_left_out(self) -> None:
        self.assertEqual(usable([rec(0.9), {"mean": "high"}, {"mean": True}]), [rec(0.9)])


class TestVerify(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        write(os.path.join(self.root, "prompts", "p.md"), "Label: {{case}}")
        write(os.path.join(self.root, "prompts", "extra.md"), "notes")
        self.folder = make_suite(self.root, settings={"prompt": "../prompts/p.md",
                                                      "covers": ["../prompts"]})
        self.suite = load(self.folder)
        self.scored = {"mean": 1.0, "covers": fingerprint(self.suite)}

    def test_unchanged_files_are_current(self) -> None:
        v = verify(self.suite, [self.scored])
        self.assertEqual(v["lines"], ["sentiment: current, 2 covered files unchanged since the last scored run"])

    def test_an_edit_is_stale_and_named(self) -> None:
        write(os.path.join(self.root, "prompts", "p.md"), "Classify: {{case}}")
        v = verify(self.suite, [self.scored])
        self.assertEqual(v["status"], "stale")
        self.assertEqual(v["changed"], [os.path.join(self.root, "prompts", "p.md")])

    def test_added_and_removed_files_are_stale(self) -> None:
        os.remove(os.path.join(self.root, "prompts", "extra.md"))
        write(os.path.join(self.root, "prompts", "new.md"), "x")
        v = verify(self.suite, [self.scored])
        self.assertEqual([len(v["added"]), len(v["removed"])], [1, 1])

    def test_a_failed_run_after_the_edit_does_not_count_as_scored(self) -> None:
        write(os.path.join(self.root, "prompts", "p.md"), "Classify: {{case}}")
        failed = {"mean": None, "covers": fingerprint(self.suite)}
        self.assertEqual(verify(self.suite, [self.scored, failed])["status"], "stale")

    def test_never_scored(self) -> None:
        self.assertEqual(verify(self.suite, [])["status"], "never scored")

    def test_a_suite_covering_nothing_is_a_configuration_error(self) -> None:
        with self.assertRaisesRegex(SuiteError, "covers no files"):
            verify(load(make_suite(self.root, "bare")), [])


if __name__ == "__main__":
    unittest.main()
