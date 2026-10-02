"""Judging a suite's history: the alarm on its latest run, and whether its
covered files still match the last run that was scored.

The baseline is the suite's own recent runs, never a fixed pass mark. A
suite that has always scored 0.95 is held to 0.95, and one that has always
scored 0.60 is not alarmed on every run for being hard. Three comparisons
against the earlier runs in the window:

  DROP   the latest mean is below the lowest earlier mean by more than
         drop_margin: a step down past anything seen recently.
  SLIDE  the latest mean is below the earlier average by more than
         slide_margin: a slow decline that never breaks the floor in one go.
  WIDER  the latest spread is above the highest earlier spread by more than
         spread_margin: the answers got less consistent even if the mean held.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .suite import SuiteError, fingerprint

EPS = 1e-9  # 0.80 - 0.05 must not read as just under 0.75


def _number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def usable(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Records whose numbers can be compared; anything else is left out."""
    return [r for r in records
            if (r.get("mean") is None or _number(r.get("mean")))
            and (r.get("spread") is None or _number(r.get("spread")))]


def clean(record: Dict[str, Any]) -> bool:
    """A run that scored every planned call. Only clean runs form a baseline
    or vouch for the covered files: the mean of the calls that happened to
    succeed says little about the ones that did not."""
    return record.get("mean") is not None and not record.get("failed")


def judge(name: str, records: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The verdict on the latest record against the scored records before it."""
    v: Dict[str, Any] = {"suite": name, "alarms": [], "lines": [], "finding": False}
    if not records:
        v["status"] = "no runs"
        v["lines"].append(f"{name}: no runs yet")
        return v
    latest = records[-1]
    earlier = [r for r in records[:-1] if clean(r)][-cfg["window"]:]
    v.update(mean=latest.get("mean"), spread=latest.get("spread"), baseline_runs=len(earlier))
    if not clean(latest):
        # A run with failed calls is a finding of its own: it cannot be
        # compared, and a quiet exit would let it pass CI as healthy.
        failed = latest.get("failed") or 0
        mean = "no score" if latest.get("mean") is None else f"mean {latest['mean']:.2f}"
        v.update(status="incomplete", finding=True)
        v["lines"].append(f"INCOMPLETE {name}: {failed} call{'' if failed == 1 else 's'} failed; "
                          f"{mean} is not compared")
        return v
    if len(earlier) < cfg["min_baseline"]:
        v["status"] = "baseline building"
        v["lines"].append(f"{name}: baseline building ({len(earlier)} of {cfg['min_baseline']} runs), "
                          f"mean {latest['mean']:.2f}")
        return v

    means = [r["mean"] for r in earlier]
    floor, top, average = min(means), max(means), sum(means) / len(means)
    v.update(lowest=floor, highest=top, average=round(average, 4))
    n, mean = len(earlier), latest["mean"]
    if mean < floor - cfg["drop_margin"] - EPS:
        v["alarms"].append("DROP")
        v["lines"].append(f"ALARM {name} DROP: mean {mean:.2f}, lowest of last {n} was {floor:.2f}")
    if mean < average - cfg["slide_margin"] - EPS:
        v["alarms"].append("SLIDE")
        v["lines"].append(f"ALARM {name} SLIDE: mean {mean:.2f}, average of last {n} was {average:.2f}")
    spreads = [r["spread"] for r in earlier if r.get("spread") is not None]
    # A spread baseline needs as many runs as the mean baseline; a suite run
    # once per case has no spread and never alarms on it.
    if latest.get("spread") is not None and len(spreads) >= cfg["min_baseline"]:
        v["widest_spread"] = max(spreads)
        if latest["spread"] > max(spreads) + cfg["spread_margin"] + EPS:
            v["alarms"].append("WIDER")
            v["lines"].append(f"ALARM {name} WIDER: spread {latest['spread']:.2f}, "
                              f"widest of last {n} was {max(spreads):.2f}")
    v["status"] = "alarm" if v["alarms"] else "steady"
    v["finding"] = bool(v["alarms"])
    if not v["alarms"]:
        v["lines"].append(f"{name}: steady, mean {mean:.2f} (last {n} runs: {floor:.2f} to {top:.2f})")
    return v


def verify(suite: Dict[str, Any], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Whether every covered file matches the fingerprint of the last run
    that scored every call."""
    name = suite["name"]
    if not suite["covers"]:
        raise SuiteError(f"suite {suite['dir']} covers no files; set \"covers\" or \"prompt\" in suite.json")
    last: Optional[Dict[str, Any]] = next((r for r in reversed(records)
                                           if clean(r) and isinstance(r.get("covers"), dict)), None)
    v: Dict[str, Any] = {"suite": name, "changed": [], "added": [], "removed": []}
    if last is None:
        v["status"] = "never scored"
        v["lines"] = [f"STALE {name}: no run has scored every call yet",
                      f"re-score: eval-alarm run {suite['dir']}"]
        return v
    then, now = last["covers"], fingerprint(suite, strict=False)

    def shown(rel: str) -> str:
        return os.path.normpath(os.path.join(suite["dir"], rel))

    v["changed"] = sorted(shown(p) for p in now if p in then and now[p] != then[p])
    v["added"] = sorted(shown(p) for p in now if p not in then)
    v["removed"] = sorted(shown(p) for p in then if p not in now)
    lines = [f"STALE {name}: {what} {p}" for what in ("changed", "added", "removed") for p in v[what]]
    if lines:
        v["status"] = "stale"
        v["lines"] = lines + [f"re-score: eval-alarm run {suite['dir']}"]
    else:
        v["status"] = "current"
        count = f"{len(now)} covered file" + ("" if len(now) == 1 else "s")
        v["lines"] = [f"{name}: current ({count} unchanged)"]
    return v
