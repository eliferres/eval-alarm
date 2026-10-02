"""Running suites: plan the model calls, reserve them against the budget,
call the model, score each answer and append one record per suite.

The model is any command that reads a prompt on stdin and prints an
answer on stdout, for example `claude -p --model {model}` or
`llm -m {model}`. {model} in the command is replaced with the suite's
model. The command runs in the directory eval-alarm was started from.
"""
from __future__ import annotations

import datetime
import os
import shlex
import signal
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import ledger, scorers
from .suite import SuiteError, fingerprint


def resolve(suite: Dict[str, Any], command: Optional[str], model: Optional[str],
            runs: Optional[int]) -> Dict[str, Any]:
    """The suite's settings with the command-line overrides applied, and the
    argv the model is started with."""
    s = dict(suite["settings"])
    s["command"] = command or s["command"]
    s["model"] = model or s["model"]
    s["runs"] = runs or s["runs"]
    if not s["command"]:
        raise SuiteError(f"suite {suite['dir']}: no model command; set \"command\" in suite.json or pass --cmd")
    if "{model}" in s["command"] and not s["model"]:
        raise SuiteError(f"suite {suite['dir']}: the command uses {{model}} but no model is set; "
                         "set \"model\" in suite.json or pass --model")
    try:
        words = shlex.split(s["command"])
    except ValueError as e:
        raise SuiteError(f"suite {suite['dir']}: the command cannot be parsed ({e})") from None
    s["argv"] = [w.replace("{model}", s["model"] or "") for w in words]
    return s


def plan(suite: Dict[str, Any], settings: Dict[str, Any]) -> List[Tuple[str, int]]:
    return [(case["name"], n) for case in suite["cases"] for n in range(1, settings["runs"] + 1)]


def _kill(proc: subprocess.Popen) -> None:
    """Kill the model's whole process group: agent CLIs start children of
    their own, and killing only the parent leaves them holding the pipes."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def call_model(argv: List[str], prompt: str, timeout: int) -> Tuple[str, Optional[str]]:
    """(answer, None) or (whatever was printed, why the call failed)."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        return "", f"could not start {argv[0]} ({e.strerror})"
    try:
        out, err = proc.communicate(prompt.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        out, _ = proc.communicate()
        return out.decode("utf-8", "replace"), f"timed out after {timeout}s"
    except KeyboardInterrupt:
        # The model runs in its own session, so Ctrl-C never reaches it;
        # without this it would keep running, and spending, after we exit.
        _kill(proc)
        proc.wait()
        raise
    if proc.returncode != 0:
        last = (err.decode("utf-8", "replace").strip().splitlines() or [""])[-1][:200]
        return out.decode("utf-8", "replace"), f"exited {proc.returncode}" + (f": {last}" if last else "")
    return out.decode("utf-8", "replace"), None


def _mean(xs: List[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 4) if xs else None


def run_suite(suite: Dict[str, Any], settings: Dict[str, Any], prints: Dict[str, str],
              started: datetime.datetime, say: Callable[[str], None]) -> Dict[str, Any]:
    """Call the model for every planned run, score the answers and append the
    record to the suite's results.jsonl. The budget has already been
    reserved. A failed call is recorded with its reason and kept out of the
    mean and spread: an outage is not a worse prompt."""
    answers_dir = os.path.join(ledger.state_dir(), "answers", suite["name"],
                               started.strftime("%Y%m%dT%H%M%S%fZ"))
    os.makedirs(answers_dir, exist_ok=True)
    width = max(len(c["name"]) for c in suite["cases"])
    runs: List[Dict[str, Any]] = []
    by_case: Dict[str, List[float]] = {}
    for case in suite["cases"]:
        shown, scored, failures = [], [], []
        for n in range(1, settings["runs"] + 1):
            answer, error = call_model(settings["argv"], case["prompt"], settings["timeout"])
            # Every answer is kept, so a low score can be read instead of re-run.
            with open(os.path.join(answers_dir, f"{case['name']}-r{n}.txt"), "w", encoding="utf-8") as f:
                f.write(answer)
            if error:
                runs.append({"case": case["name"], "run": n, "score": None, "error": error})
                shown.append("fail")
                failures.append(f"run {n} failed: {error}")
                continue
            value = scorers.score(answer, case["expected"])
            runs.append({"case": case["name"], "run": n, "score": value, "error": None})
            scored.append(value)
            shown.append(f"{value:.2f}")
        by_case[case["name"]] = scored
        say(f"  {case['name']:<{width}}  " + "  ".join(shown))
        for failure in failures:
            say(f"  {'':<{width}}  {failure}")

    scored = [x for xs in by_case.values() for x in xs]
    # Spread is how far repeat reads of the same case disagree, averaged over
    # the cases read at least twice. A case read once shows no disagreement.
    spread = _mean([max(xs) - min(xs) for xs in by_case.values() if len(xs) > 1])
    record = {
        "suite": suite["name"],
        "started": started.isoformat(timespec="seconds"),
        "model": settings["model"],
        "command": settings["command"],
        "runs": runs,
        "case_means": {name: _mean(xs) for name, xs in by_case.items()},
        "mean": _mean(scored),
        "spread": spread,
        "scored": len(scored),
        "failed": len(runs) - len(scored),
        "covers": prints,
    }
    ledger.append_result(os.path.join(suite["dir"], "results.jsonl"), record)
    return record


def run_all(suites: List[Dict[str, Any]], overrides: Dict[str, Any], budget: int,
            now: datetime.datetime, dry: bool, say: Callable[[str], None]) -> List[Dict[str, Any]]:
    """Plan every suite, check the whole plan against the budget, then run.
    Nothing starts unless the whole plan fits, so a run is never cut off
    half way through a suite by the cap."""
    prepared = []
    for suite in suites:
        settings = resolve(suite, overrides.get("command"), overrides.get("model"), overrides.get("runs"))
        # The fingerprint is taken before any call is spent, so the record
        # names the version of the files that was actually scored.
        prepared.append((suite, settings, plan(suite, settings), fingerprint(suite)))
    need = sum(len(p) for _, _, p, _ in prepared)
    path = os.path.join(ledger.state_dir(), "budget.jsonl")

    if dry:
        for suite, settings, calls, _ in prepared:
            say(f"{suite['name']}: {len(suite['cases'])} cases x {settings['runs']} runs = "
                f"{len(calls)} model calls on {settings['model'] or 'the default model'}")
        used = ledger.budget_used(path, now)
        say(f"budget: {used} used in the last 7 days + {need} planned = {used + need} of {budget}")
        if used + need > budget:
            raise ledger.OverBudget(used, need, budget)
        say("fits; nothing was started (dry run)")
        return []

    rows = [{"suite": suite["name"], "case": case, "run": n}
            for suite, _, calls, _ in prepared for case, n in calls]
    used = ledger.reserve(path, rows, budget, now)
    records = []
    for suite, settings, _, prints in prepared:
        record = run_suite(suite, settings, prints, now, say)
        mean = "none" if record["mean"] is None else f"{record['mean']:.2f}"
        spread = "none" if record["spread"] is None else f"{record['spread']:.2f}"
        failed = f", {record['failed']} failed" if record["failed"] else ""
        say(f"{suite['name']}: mean {mean}, spread {spread} over {record['scored']} scored runs{failed}")
        records.append(record)
    say(f"budget: {used} of {budget} model calls used in the last 7 days")
    return records
