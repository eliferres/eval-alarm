"""The command end to end: real subprocesses, the fake model, exit codes."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import make_suite, write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = os.path.join(ROOT, "tests", "fake_model.py")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class TestCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.answers = os.path.join(self.root, "answers.json")
        self.env = {**os.environ, "EVAL_ALARM_STATE_DIR": os.path.join(self.root, "state"),
                    "FAKE_ANSWERS": self.answers, "PYTHONPATH": ROOT}
        self.env.pop("EVAL_ALARM_BUDGET", None)
        write(os.path.join(self.root, "prompts", "p.md"), "Label: {{case}}")
        self.suite = make_suite(self.root, settings={
            "prompt": "../prompts/p.md", "command": f"{sys.executable} {FAKE}", "runs": 1})

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "eval_alarm", *args], cwd=self.root, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def score(self, *answers: str) -> subprocess.CompletedProcess:
        write(self.answers, json.dumps(list(answers)))
        if os.path.exists(self.answers + ".n"):
            os.remove(self.answers + ".n")
        return self.cli("run", self.suite)

    def test_version(self) -> None:
        self.assertEqual(self.cli("--version").stdout, "eval-alarm 0.1.0\n")

    def test_no_command_is_a_usage_error(self) -> None:
        self.assertEqual(self.cli().returncode, 2)

    def test_a_clean_run_exits_zero(self) -> None:
        done = self.score("positive", "negative")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("sentiment: mean 1.00, spread none over 2 scored runs", done.stdout)

    def test_a_failed_call_exits_one(self) -> None:
        self.assertEqual(self.score("positive", "!fail").returncode, 1)

    def test_over_budget_exits_one_with_one_line(self) -> None:
        write(self.answers, json.dumps(["positive", "negative"]))
        done = self.cli("run", self.suite, "--budget", "1")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stderr, "eval-alarm: refused: the last 7 days used 0 of 1 model calls; "
                                      "this run needs 2, which would make 2\n")

    def test_the_budget_comes_from_the_environment(self) -> None:
        self.env["EVAL_ALARM_BUDGET"] = "1"
        self.assertEqual(self.cli("run", self.suite, "--dry").returncode, 1)
        self.env["EVAL_ALARM_BUDGET"] = "lots"
        done = self.cli("run", self.suite, "--dry")
        self.assertEqual((done.returncode, done.stderr), (2, "eval-alarm: budget 'lots' is not a whole number "
                                                              "of 0 or more\n"))

    def test_a_broken_suite_exits_two_without_a_traceback(self) -> None:
        write(os.path.join(self.suite, "suite.json"), "{")
        done = self.cli("check", self.suite)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(len(done.stderr.splitlines()), 1)
        self.assertNotIn("Traceback", done.stderr)

    def test_ctrl_c_stops_the_model_and_exits_130(self) -> None:
        write(self.answers, json.dumps(["!hang"]))
        proc = subprocess.Popen([sys.executable, "-m", "eval_alarm", "run", self.suite], cwd=self.root,
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        pid_file = self.answers + ".pid"
        deadline = time.monotonic() + 10
        while not os.path.exists(pid_file) and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.1)
        with open(pid_file, encoding="utf-8") as f:
            model_pid = int(f.read())
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=10)
        self.assertEqual((proc.returncode, err), (130, "eval-alarm: interrupted\n"))
        deadline = time.monotonic() + 5
        while alive(model_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(model_pid), "the model process outlived the interrupt")

    def test_check_exits_one_on_an_alarm(self) -> None:
        for _ in range(3):
            self.score("positive", "negative")
        self.assertEqual(self.cli("check", self.suite).returncode, 0)
        self.score("positive", "it is negative")
        done = self.cli("check", self.suite)
        self.assertEqual(done.returncode, 1)
        self.assertTrue(done.stdout.startswith("ALARM sentiment DROP: mean 0.50"))

    def test_check_exits_one_when_most_calls_failed(self) -> None:
        for _ in range(3):
            self.score("positive", "negative")
        self.assertEqual(self.score("positive", "!fail").returncode, 1)
        done = self.cli("check", self.suite)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "INCOMPLETE sentiment: 1 call failed; mean 1.00 is not compared\n")

    def test_check_json(self) -> None:
        for _ in range(4):
            self.score("positive", "negative")
        (verdict,) = json.loads(self.cli("check", self.suite, "--json").stdout)
        self.assertEqual((verdict["status"], verdict["alarms"], verdict["finding"]), ("steady", [], False))

    def test_a_torn_results_line_is_reported_not_fatal(self) -> None:
        self.score("positive", "negative")
        with open(os.path.join(self.suite, "results.jsonl"), "a", encoding="utf-8") as f:
            f.write('{"mean": 0.')
        done = self.cli("check", self.suite)
        self.assertEqual(done.returncode, 0)
        self.assertIn("1 unreadable line(s) in results.jsonl skipped", done.stdout)

    def test_a_cut_off_last_line_does_not_swallow_the_next_run(self) -> None:
        for _ in range(3):
            self.score("positive", "negative")
        with open(os.path.join(self.suite, "results.jsonl"), "a", encoding="utf-8") as f:
            f.write('{"mean": 0.')
        self.score("positive", "it is negative")
        done = self.cli("check", self.suite)
        self.assertEqual(done.returncode, 1)
        self.assertTrue(done.stdout.startswith("ALARM sentiment DROP: mean 0.50"), done.stdout)
        self.assertIn("1 unreadable line(s) in results.jsonl skipped", done.stdout)

    def test_a_results_line_that_is_not_utf8_is_skipped(self) -> None:
        self.score("positive", "negative")
        with open(os.path.join(self.suite, "results.jsonl"), "ab") as f:
            f.write(b"\xff\n")
        done = self.cli("check", self.suite)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("1 unreadable line(s) in results.jsonl skipped", done.stdout)

    def test_verify_refuses_an_edit_that_was_never_rescored(self) -> None:
        self.assertEqual(self.cli("verify", self.suite).returncode, 1)  # never scored
        self.score("positive", "negative")
        self.assertEqual(self.cli("verify", self.suite).returncode, 0)
        write(os.path.join(self.root, "prompts", "p.md"), "Classify: {{case}}")
        done = self.cli("verify", self.suite, "--json")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(json.loads(done.stdout)[0]["changed"], [os.path.join(self.root, "prompts", "p.md")])
        self.score("positive", "negative")
        self.assertEqual(self.cli("verify", self.suite).returncode, 0)


if __name__ == "__main__":
    unittest.main()
