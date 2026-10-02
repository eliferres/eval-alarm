from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from typing import List
from unittest import mock

from eval_alarm import ledger, runner
from eval_alarm.suite import SuiteError, load
from helpers import make_suite, write

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_model.py")
NOW = datetime.datetime(2026, 10, 2, 12, 0, tzinfo=datetime.timezone.utc)


class RunnerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.answers = os.path.join(self.root, "answers.json")
        env = {"EVAL_ALARM_STATE_DIR": os.path.join(self.root, "state"), "FAKE_ANSWERS": self.answers}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.said: List[str] = []
        self.command = f"{sys.executable} {FAKE} --model {{model}}"

    def answer(self, *answers: str) -> None:
        write(self.answers, json.dumps(list(answers)))

    def run_suites(self, folders: List[str], budget: int = 100, dry: bool = False, runs: int = 2,
                   now: datetime.datetime = NOW) -> list:
        suites = [load(f) for f in folders]
        overrides = {"command": self.command, "model": "small", "runs": runs}
        return runner.run_all(suites, overrides, budget, now, dry, self.said.append)

    def budget_rows(self) -> list:
        path = os.path.join(self.root, "state", "budget.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f]


class TestRun(RunnerCase):
    def test_a_run_scores_every_case_and_appends_one_record(self) -> None:
        self.answer("positive", "positive", "negative", "neutral")
        folder = make_suite(self.root)
        (record,) = self.run_suites([folder])
        self.assertEqual([r["score"] for r in record["runs"]], [1.0, 1.0, 1.0, 0.0])
        self.assertEqual(record["case_means"], {"praise": 1.0, "refund": 0.5})
        self.assertEqual(record["mean"], 0.75)
        self.assertEqual(record["spread"], 0.5)
        self.assertEqual(record["model"], "small")
        with open(os.path.join(folder, "results.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.read())["mean"], 0.75)
        self.assertIn("  praise  1.00  1.00", self.said)
        self.assertIn("  refund  1.00  0.00", self.said)

    def test_the_model_placeholder_reaches_the_command(self) -> None:
        self.answer("positive", "negative")
        self.run_suites([make_suite(self.root)], runs=1)
        with open(self.answers + ".prompts", encoding="utf-8") as f:
            sent = [json.loads(line) for line in f]
        self.assertEqual(sent[0], {"argv": ["--model", "small"], "prompt": "I love it"})

    def test_a_failed_call_is_recorded_and_kept_out_of_the_mean(self) -> None:
        self.answer("positive", "!fail", "negative", "negative")
        (record,) = self.run_suites([make_suite(self.root)])
        self.assertEqual(record["runs"][1]["error"], "exited 3: model unavailable")
        self.assertEqual((record["mean"], record["scored"], record["failed"]), (1.0, 3, 1))
        self.assertEqual(record["spread"], 0.0)  # praise was read once, refund twice alike
        self.assertIn("run 2 failed: exited 3: model unavailable", self.said[1])

    def test_a_hung_model_is_timed_out(self) -> None:
        self.answer("!hang")
        folder = make_suite(self.root, settings={"timeout": 1},
                            cases={"only": ("x", {"scorer": "exact", "expected": "y"})})
        (record,) = self.run_suites([folder], runs=1)
        self.assertEqual(record["runs"][0]["error"], "timed out after 1s")
        self.assertIsNone(record["mean"])

    def test_a_command_that_does_not_exist_fails_each_call(self) -> None:
        self.command = "no-such-model-command-here"
        (record,) = self.run_suites([make_suite(self.root)], runs=1)
        self.assertTrue(record["runs"][0]["error"].startswith("could not start no-such-model-command-here"))

    def test_every_answer_is_kept_on_disk(self) -> None:
        self.answer("positive", "negative")
        self.run_suites([make_suite(self.root)], runs=1)
        kept = os.path.join(self.root, "state", "answers", "sentiment")
        (stamp,) = os.listdir(kept)
        self.assertEqual(sorted(os.listdir(os.path.join(kept, stamp))), ["praise-r1.txt", "refund-r1.txt"])

    def test_the_record_carries_the_covered_files(self) -> None:
        write(os.path.join(self.root, "prompts", "p.md"), "Label: {{case}}")
        self.answer("positive", "negative")
        folder = make_suite(self.root, settings={"prompt": "../prompts/p.md"})
        (record,) = self.run_suites([folder], runs=1)
        self.assertEqual(list(record["covers"]), ["../prompts/p.md"])


class TestBudget(RunnerCase):
    def test_the_whole_plan_is_checked_before_anything_starts(self) -> None:
        self.answer(*["positive"] * 8)
        a = make_suite(self.root, "a")
        b = make_suite(self.root, "b")
        with self.assertRaises(ledger.OverBudget) as caught:
            self.run_suites([a, b], budget=7)
        self.assertEqual(str(caught.exception),
                         "the last 7 days used 0 of 7 model calls; this run needs 8, which would make 8")
        self.assertFalse(os.path.exists(self.answers + ".n"), "a model call was made")
        self.assertEqual(self.budget_rows(), [])

    def test_each_planned_call_is_one_ledger_row(self) -> None:
        self.answer(*["positive"] * 4)
        self.run_suites([make_suite(self.root)])
        rows = self.budget_rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], {"ts": "2026-10-02T12:00:00+00:00", "suite": "sentiment", "case": "praise", "run": 1})

    def test_the_week_is_rolling(self) -> None:
        self.answer(*["positive"] * 8)
        folder = make_suite(self.root)
        self.run_suites([folder], budget=4, now=NOW - datetime.timedelta(days=7, seconds=1))
        self.run_suites([folder], budget=4)  # the first run is now outside the window
        with self.assertRaises(ledger.OverBudget):
            self.run_suites([folder], budget=4, now=NOW + datetime.timedelta(days=6))

    def test_a_dry_run_prints_the_arithmetic_and_spends_nothing(self) -> None:
        self.run_suites([make_suite(self.root)], dry=True, runs=3)
        self.assertEqual(self.said, [
            "sentiment: 2 cases x 3 runs = 6 model calls on small",
            "budget: 0 used in the last 7 days + 6 planned = 6 of 100",
            "fits; nothing was started (dry run)",
        ])
        self.assertEqual(self.budget_rows(), [])

    def test_parallel_runs_never_spend_past_the_cap(self) -> None:
        """Twelve processes race for a budget of five; the lock lets exactly
        five reservations through."""
        path = os.path.join(self.root, "state", "budget.jsonl")
        code = ("import datetime, sys; from eval_alarm import ledger; "
                "now = datetime.datetime.now(datetime.timezone.utc)\n"
                "try: ledger.reserve(sys.argv[1], [{'run': 1}], 5, now)\n"
                "except ledger.OverBudget: sys.exit(1)")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        procs = [subprocess.Popen([sys.executable, "-c", code, path], cwd=root) for _ in range(12)]
        codes = sorted(p.wait() for p in procs)
        self.assertEqual(codes, [0] * 5 + [1] * 7)
        self.assertEqual(len(self.budget_rows()), 5)

    def test_a_torn_ledger_line_is_skipped(self) -> None:
        write(os.path.join(self.root, "state", "budget.jsonl"), '{"ts": "2026-10-02T11:00:00+00:00"}\n{"ts": "20')
        self.assertEqual(ledger.budget_used(os.path.join(self.root, "state", "budget.jsonl"), NOW), 1)


class TestResolve(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_no_command_anywhere_is_a_configuration_error(self) -> None:
        suite = load(make_suite(self.tmp.name))
        with self.assertRaisesRegex(SuiteError, "no model command"):
            runner.resolve(suite, None, None, None)

    def test_a_model_placeholder_without_a_model(self) -> None:
        suite = load(make_suite(self.tmp.name, settings={"command": "llm -m {model}"}))
        with self.assertRaisesRegex(SuiteError, "no model is set"):
            runner.resolve(suite, None, None, None)

    def test_command_line_overrides_win(self) -> None:
        suite = load(make_suite(self.tmp.name, settings={"command": "llm -m {model}", "model": "a", "runs": 5}))
        s = runner.resolve(suite, None, "b", 2)
        self.assertEqual((s["argv"], s["runs"]), (["llm", "-m", "b"], 2))


if __name__ == "__main__":
    unittest.main()
