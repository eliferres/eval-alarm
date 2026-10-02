"""The two JSONL files: the run budget and each suite's results.

The budget ledger is per machine (it counts model calls you pay for); the
results ledger is per suite and lives beside it, so the history a suite is
judged against can be committed and read by CI.

Both are append-only and written under an exclusive flock on the file
itself. The budget needs it most: two runs started at once must not both
read "40 of 100 used" and together spend past the cap, so the count and
the append happen inside one lock.
"""
from __future__ import annotations

import datetime
import fcntl
import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Tuple

WEEK = datetime.timedelta(days=7)
DEFAULT_BUDGET = 100


def state_dir() -> str:
    """Where per-machine state goes: EVAL_ALARM_STATE_DIR, else the XDG state
    directory."""
    explicit = os.environ.get("EVAL_ALARM_STATE_DIR")
    if explicit:
        return explicit
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "eval-alarm")


class OverBudget(Exception):
    def __init__(self, used: int, need: int, budget: int) -> None:
        super().__init__(f"the last 7 days used {used} of {budget} model calls; "
                         f"this run needs {need}, which would make {used + need}")
        self.used, self.need, self.budget = used, need, budget


@contextmanager
def locked(path: str, shared: bool = False) -> Iterator[Any]:
    """The open file under a lock: shared for a read, so a read-only
    checkout can still be checked; exclusive (and created) for a write."""
    if not shared:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # errors="replace": a line with bad bytes then fails to parse as JSON and
    # is skipped like any other torn line, instead of stopping the reader.
    with open(path, "r" if shared else "a+", encoding="utf-8", errors="replace") as f:
        fcntl.flock(f, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield f
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _objects(lines: List[str]) -> List[Dict[str, Any]]:
    """Every line that holds a JSON object. A torn or hand-broken line is
    skipped rather than stopping the reader."""
    found = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            found.append(row)
    return found


def _used(f: Any, now: datetime.datetime) -> int:
    f.seek(0)
    since, used = now - WEEK, 0
    for row in _objects(f.readlines()):
        try:
            used += datetime.datetime.fromisoformat(row["ts"]) > since
        except (KeyError, TypeError, ValueError):
            continue
    return used


def budget_used(path: str, now: datetime.datetime) -> int:
    """Model calls recorded in the rolling seven days before now."""
    if not os.path.exists(path):
        return 0
    with locked(path, shared=True) as f:
        return _used(f, now)


def reserve(path: str, calls: List[Dict[str, Any]], budget: int, now: datetime.datetime) -> int:
    """Record every planned call before the first one starts, or raise
    OverBudget and record nothing. A call that later fails or is cut short
    still counts: the cap errs on the side of spending less. Returns the
    count used after the reservation."""
    with locked(path) as f:
        used = _used(f, now)
        if used + len(calls) > budget:
            raise OverBudget(used, len(calls), budget)
        ts = now.isoformat(timespec="seconds")
        f.write("".join(json.dumps({"ts": ts, **c}) + "\n" for c in calls))
        return used + len(calls)


def append_result(path: str, record: Dict[str, Any]) -> None:
    with locked(path) as f:
        f.write(json.dumps(record) + "\n")


def read_results(path: str) -> Tuple[List[Dict[str, Any]], int]:
    """(records oldest first, count of unreadable lines skipped)."""
    if not os.path.exists(path):
        return [], 0
    with locked(path, shared=True) as f:
        lines = [line for line in f if line.strip()]
    records = _objects(lines)
    return records, len(lines) - len(records)
