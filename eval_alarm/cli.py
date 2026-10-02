"""The eval-alarm command: run, check and verify.

Exit codes: 0 clean, 1 findings (an alarm, a stale suite, a failed call or
a run refused by the budget), 2 usage or configuration error.
"""
from __future__ import annotations

import argparse
import datetime
import functools
import json
import os
import sys
from typing import Callable, List, Optional

from . import __version__, alarm, ledger, runner
from .suite import SuiteError, load

PROG = "eval-alarm"


def _positive(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number of 1 or more")
    return n


def _budget(text: Optional[str]) -> int:
    if text is None:
        return ledger.DEFAULT_BUDGET
    try:
        n = int(text)
    except ValueError:
        n = -1
    if n < 0:
        raise SuiteError(f"budget {text!r} is not a whole number of 0 or more")
    return n


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, description="Regression alarms for prompts, skills and agent instructions.")
    p.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = p.add_subparsers(dest="command", metavar="{run,check,verify}")

    run = sub.add_parser("run", help="score suites by sending each case to the model N times")
    run.add_argument("suites", nargs="+", metavar="SUITE", help="suite folder")
    run.add_argument("--cmd", help="model command reading the prompt on stdin; {model} is replaced")
    run.add_argument("--model", help="model name substituted for {model}")
    run.add_argument("--runs", type=_positive, help="runs per case (default: the suite's, else 3)")
    run.add_argument("--budget", help="model calls allowed per rolling 7 days "
                                      f"(default: EVAL_ALARM_BUDGET, else {ledger.DEFAULT_BUDGET})")
    run.add_argument("--dry", action="store_true", help="print the plan and the budget arithmetic, start nothing")

    for name, text in (("check", "alarm when the latest run fell or widened against the suite's history"),
                       ("verify", "fail when a covered file changed since the suite's last scored run")):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("suites", nargs="+", metavar="SUITE", help="suite folder")
        cmd.add_argument("--json", action="store_true", help="print the verdicts as JSON")
    return p


def _history(suite: dict) -> tuple:
    records, torn = ledger.read_results(os.path.join(suite["dir"], "results.jsonl"))
    kept = alarm.usable(records)
    return kept, torn + len(records) - len(kept)


def cmd_run(args: argparse.Namespace, say: Callable[[str], None]) -> int:
    budget = _budget(args.budget if args.budget is not None else os.environ.get("EVAL_ALARM_BUDGET"))
    suites = [load(s) for s in args.suites]
    overrides = {"command": args.cmd, "model": args.model, "runs": args.runs}
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        records = runner.run_all(suites, overrides, budget, now, args.dry, say)
    except ledger.OverBudget as e:
        print(f"{PROG}: refused: {e}", file=sys.stderr)
        return 1
    return 1 if any(r["failed"] for r in records) else 0


def cmd_review(args: argparse.Namespace, say: Callable[[str], None]) -> int:
    """check and verify share their shape: one verdict per suite, exit 1
    when any verdict is a finding."""
    verdicts = []
    for folder in args.suites:
        suite = load(folder)
        records, skipped = _history(suite)
        if args.command == "check":
            v = alarm.judge(suite["name"], records, suite["settings"]["alarm"])
            finding = bool(v["alarms"])
        else:
            v = alarm.verify(suite, records)
            finding = v["status"] != "current"
        if skipped:
            v["lines"].append(f"{suite['name']}: {skipped} unreadable line(s) in results.jsonl skipped")
        v["skipped"] = skipped
        v["finding"] = finding
        verdicts.append(v)
    if args.json:
        say(json.dumps([{k: val for k, val in v.items() if k != "lines"} for v in verdicts], indent=2))
    else:
        for v in verdicts:
            for line in v["lines"]:
                say(line)
    return 1 if any(v["finding"] for v in verdicts) else 0


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.command is None:
        parser().print_usage(sys.stderr)
        return 2
    say = functools.partial(print, flush=True)  # flushed, so progress shows during a long run
    try:
        return cmd_run(args, say) if args.command == "run" else cmd_review(args, say)
    except SuiteError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"{PROG}: {e.filename or 'a file'}: {e.strerror}", file=sys.stderr)
        return 2
