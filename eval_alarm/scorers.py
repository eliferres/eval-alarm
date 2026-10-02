"""Deterministic scorers: each turns one model answer into a score from 0 to 1.

A case's expected.json names its scorer and that scorer's settings:

  exact     {"scorer": "exact", "expected": "positive"}
            1 when the trimmed answer equals the expected string (or any one
            of a list of them), else 0.
  contains  {"scorer": "contains", "expected": ["refund", "apolog"]}
            the share of expected strings that appear in the answer.
  regex     {"scorer": "regex", "patterns": ["VERDICT: (PASS|FAIL)$"],
             "must_not": ["as an AI"]}
            the share of checks that pass: each pattern must be found in the
            trimmed answer, each must_not pattern must not. ^ and $ anchor
            the whole answer, so the example needs the verdict on the last
            line; start a pattern with (?m) to anchor per line instead.
  json      {"scorer": "json", "shape": {"label": "string"},
             "fields": {"label": "negative"}}
            0 when the answer is not JSON (a single fenced json block is
            unwrapped first); otherwise the share of checks that pass: each
            shape key present with that type, each field equal to its value.
            With neither, any valid JSON scores 1. Values compare strictly:
            true is not 1, though 1 and 1.0 are the same number.

exact, contains and regex accept "ignore_case": true.

Partial credit is deliberate: a regression that breaks one check of four
moves the mean by a quarter, so the alarm sees a slide before a collapse.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List

TYPES: Dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}
FENCE = re.compile(r"^```(?:json)?[ \t]*\n(.*)\n```$", re.S)


def _strings(exp: dict, key: str, allow_empty: bool = False) -> List[str]:
    value = exp.get(key, [])
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(s, str) for s in items):
        raise ValueError(f"{key} must be a string or a list of strings")
    if not items and not allow_empty:
        raise ValueError(f"{key} is empty, so nothing would be scored")
    return items


def _patterns(exp: dict, key: str) -> List[str]:
    items = _strings(exp, key, allow_empty=True)
    for p in items:
        try:
            matches_nothing = re.search(p, "") is not None
        except re.error as e:
            raise ValueError(f"{key} pattern {p!r} does not compile ({e})") from None
        if matches_nothing:
            # Such a pattern is found in every answer, so it would score the
            # same whatever the model said.
            raise ValueError(f"{key} pattern {p!r} matches an empty answer, so it tests nothing")
    return items


def _ignore_case(exp: dict) -> bool:
    flag = exp.get("ignore_case", False)
    if not isinstance(flag, bool):
        raise ValueError("ignore_case must be true or false")
    return flag


def validate(exp: Any) -> None:
    """Raise ValueError naming the problem when an expected.json cannot be
    scored, so a broken case stops the run before any model call is spent."""
    if not isinstance(exp, dict):
        raise ValueError("it is not a JSON object")
    scorer = exp.get("scorer")
    if scorer not in SCORERS:
        raise ValueError("scorer must be one of " + ", ".join(sorted(SCORERS)))
    if scorer in ("exact", "contains"):
        wanted = _strings(exp, "expected")
        if scorer == "contains" and "" in wanted:
            raise ValueError("expected holds an empty string, which every answer contains")
        _ignore_case(exp)
    elif scorer == "regex":
        if not _patterns(exp, "patterns") + _patterns(exp, "must_not"):
            raise ValueError("patterns and must_not are both empty, so nothing would be scored")
        _ignore_case(exp)
    else:
        shape, fields = exp.get("shape", {}), exp.get("fields", {})
        if not isinstance(shape, dict) or any(t not in TYPES for t in shape.values()):
            raise ValueError("shape must map keys to one of " + ", ".join(TYPES))
        if not isinstance(fields, dict):
            raise ValueError("fields must be an object of expected values")


def score_exact(answer: str, exp: dict) -> float:
    fold = str.casefold if exp.get("ignore_case") else str
    got = fold(answer.strip())
    return 1.0 if any(got == fold(e.strip()) for e in _strings(exp, "expected")) else 0.0


def score_contains(answer: str, exp: dict) -> float:
    fold = str.casefold if exp.get("ignore_case") else str
    wanted = _strings(exp, "expected")
    return sum(fold(w) in fold(answer) for w in wanted) / len(wanted)


def score_regex(answer: str, exp: dict) -> float:
    flags = re.I if exp.get("ignore_case") else 0
    text = answer.strip()
    must, must_not = _patterns(exp, "patterns"), _patterns(exp, "must_not")
    passed = sum(bool(re.search(p, text, flags)) for p in must)
    passed += sum(not re.search(p, text, flags) for p in must_not)
    return passed / (len(must) + len(must_not))


def _same(got: Any, want: Any) -> bool:
    """JSON equality without Python's bool-is-int: true never equals 1."""
    if isinstance(got, bool) or isinstance(want, bool):
        return type(got) is type(want) and got == want
    if isinstance(got, dict) and isinstance(want, dict):
        return got.keys() == want.keys() and all(_same(got[k], want[k]) for k in want)
    if isinstance(got, list) and isinstance(want, list):
        return len(got) == len(want) and all(_same(g, w) for g, w in zip(got, want))
    return got == want


def score_json(answer: str, exp: dict) -> float:
    text = answer.strip()
    fenced = FENCE.match(text)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        return 0.0
    shape, fields = exp.get("shape", {}), exp.get("fields", {})
    if not shape and not fields:
        return 1.0
    if not isinstance(data, dict):
        return 0.0
    passed = sum(key in data and TYPES[kind](data[key]) for key, kind in shape.items())
    passed += sum(key in data and _same(data[key], value) for key, value in fields.items())
    return passed / (len(shape) + len(fields))


SCORERS: Dict[str, Callable[[str, dict], float]] = {
    "exact": score_exact,
    "contains": score_contains,
    "regex": score_regex,
    "json": score_json,
}


def score(answer: str, exp: dict) -> float:
    """The answer's score under the scorer its expected.json names, rounded
    to four places so a mean stored in the ledger reads the same everywhere."""
    return round(SCORERS[exp["scorer"]](answer, exp), 4)
