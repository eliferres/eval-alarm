"""Loading a suite folder and fingerprinting the files it covers.

A suite is a folder:

  suite.json                 settings, all optional (see DEFAULTS)
  cases/<name>/prompt.md     the text sent to the model
  cases/<name>/expected.json the scorer and its settings
  results.jsonl              written by `eval-alarm run`, one line per run

Paths inside suite.json resolve against the suite folder, so a suite reads
the same whatever directory the command is started from.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List

from . import ledger, scorers

CASE_PLACEHOLDER = "{{case}}"
CASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SKIP_DIRS = {".git", "__pycache__", "node_modules"}

# What a suite gets when suite.json leaves a setting out. runs is 3 so every
# case is read more than once and the run has a spread to watch; the alarm
# numbers are explained in the README.
DEFAULTS: Dict[str, Any] = {
    "command": None,
    "model": None,
    "runs": 3,
    "timeout": 300,
    "prompt": None,
    "covers": None,
    "alarm": {"window": 5, "min_baseline": 3, "drop_margin": 0.05,
              "slide_margin": 0.10, "spread_margin": 0.10},
}


class SuiteError(Exception):
    """A suite that cannot run as written; the message names the problem."""


def _read_json(path: str, what: str) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise SuiteError(f"{what} cannot be read ({e.strerror})") from None
    except ValueError as e:
        raise SuiteError(f"{what} is not valid JSON ({e})") from None


def _whole(value: Any, low: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _settings(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("it is not a JSON object")
    unknown = sorted(set(raw) - set(DEFAULTS) - {"description"})
    if unknown:
        raise ValueError("unknown setting " + ", ".join(unknown))
    if not isinstance(raw.get("alarm", {}), dict):
        raise ValueError("alarm must be an object")
    s = {**DEFAULTS, **raw, "alarm": {**DEFAULTS["alarm"], **raw.get("alarm", {})}}
    for key in ("command", "model", "prompt"):
        if s[key] is not None and (not isinstance(s[key], str) or not s[key].strip()):
            raise ValueError(f"{key} must be a non-empty string")
    if not _whole(s["runs"], 1):
        raise ValueError("runs must be a whole number, 1 or more")
    if not _whole(s["timeout"], 1):
        raise ValueError("timeout must be a whole number of seconds")
    covers = s["covers"]
    if covers is not None and (not isinstance(covers, list)
                               or not all(isinstance(c, str) and c for c in covers)):
        raise ValueError("covers must be a list of paths")
    a = s["alarm"]
    if set(a) != set(DEFAULTS["alarm"]):
        raise ValueError("unknown alarm setting " + ", ".join(sorted(set(a) - set(DEFAULTS["alarm"]))))
    if not _whole(a["window"], 1) or not _whole(a["min_baseline"], 1) or a["min_baseline"] > a["window"]:
        raise ValueError("alarm window and min_baseline must be whole numbers, min_baseline not above window")
    for key in ("drop_margin", "slide_margin", "spread_margin"):
        if isinstance(a[key], bool) or not isinstance(a[key], (int, float)) or not 0 <= a[key] <= 1:
            raise ValueError(f"alarm {key} must be a number from 0 to 1")
    return s


def load(folder: str) -> Dict[str, Any]:
    """The suite as a dict: its settings, its cases (prompt text and expected
    answer, in name order) and the absolute paths it covers."""
    folder = os.path.normpath(folder)
    name = os.path.basename(os.path.abspath(folder))
    if not os.path.isdir(folder):
        raise SuiteError(f"suite {folder} is not a folder")
    config = os.path.join(folder, "suite.json")
    raw = _read_json(config, config) if os.path.exists(config) else {}
    try:
        settings = _settings(raw)
    except ValueError as e:
        raise SuiteError(f"{config}: {e}") from None

    template = None
    if settings["prompt"]:
        path = os.path.join(folder, settings["prompt"])
        try:
            with open(path, encoding="utf-8") as f:
                template = f.read()
        except OSError:
            raise SuiteError(f"{config}: prompt {settings['prompt']} cannot be read") from None
        except UnicodeDecodeError:
            raise SuiteError(f"{config}: prompt {settings['prompt']} is not UTF-8 text") from None
        if CASE_PLACEHOLDER not in template:
            # Without the placeholder every case would send the same text and
            # the cases would silently stop being tested.
            raise SuiteError(f"{config}: prompt {settings['prompt']} has no {CASE_PLACEHOLDER} placeholder")
    covers = settings["covers"]
    if covers is None:
        covers = [settings["prompt"]] if settings["prompt"] else []
    # The cases and the settings are part of what a score means, so an edit
    # to either always calls for a re-score, whatever else is covered.
    covers = covers + [own for own in ("cases", "suite.json")
                       if own not in covers and os.path.exists(os.path.join(folder, own))]

    cases_dir = os.path.join(folder, "cases")
    names = sorted(n for n in os.listdir(cases_dir)
                   if os.path.isdir(os.path.join(cases_dir, n))) if os.path.isdir(cases_dir) else []
    if not names:
        raise SuiteError(f"suite {folder} has no cases (expected {cases_dir}/<name>/)")
    cases = []
    for case in names:
        where = os.path.join(cases_dir, case)
        if not CASE_NAME.match(case):
            raise SuiteError(f"case folder {where} needs a plain name (letters, digits, . _ -)")
        try:
            with open(os.path.join(where, "prompt.md"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            raise SuiteError(f"case {where} has no readable prompt.md") from None
        except UnicodeDecodeError:
            raise SuiteError(f"case {where}: prompt.md is not UTF-8 text") from None
        expected = _read_json(os.path.join(where, "expected.json"), f"{where}/expected.json")
        try:
            scorers.validate(expected)
        except ValueError as e:
            raise SuiteError(f"{where}/expected.json: {e}") from None
        prompt = template.replace(CASE_PLACEHOLDER, text) if template is not None else text
        cases.append({"name": case, "prompt": prompt, "expected": expected})

    return {"name": name, "dir": folder, "settings": settings, "cases": cases, "covers": covers,
            "warnings": margin_warnings(name, settings, len(cases))}


def margin_warnings(name: str, settings: Dict[str, Any], cases: int) -> List[str]:
    """One line per margin finer than a single answer. One wrong answer
    moves the mean by 1/(cases x runs) and, read more than once, moves its
    case's spread by up to 1, which is 1/cases of the suite's spread. A
    margin below that lets one unlucky answer raise the alarm."""
    a, answers = settings["alarm"], cases * settings["runs"]
    weights = [("drop_margin", "DROP", "one answer's weight on the mean", answers),
               ("slide_margin", "SLIDE", "one answer's weight on the mean", answers)]
    if settings["runs"] > 1:
        weights.append(("spread_margin", "WIDER", "one case's weight on the spread", cases))
    return [f"{name}: {key} {a[key]:.2f} is below {what} (1/{n} = {1 / n:.2f}), "
            f"so one wrong answer can raise {rule}"
            for key, rule, what, n in weights if a[key] < 1 / n - 1e-9]


def _files(folder: str, rel: str) -> List[tuple]:
    """(path as recorded, absolute path) for every file under rel. A folder
    expands to its files; the recorded path keeps the user's spelling plus
    the file's place inside it, always with forward slashes."""
    target = os.path.join(folder, rel)
    if os.path.isfile(target):
        return [(rel.replace(os.sep, "/"), target)]
    if not os.path.isdir(target):
        return []
    found, seen = [], set()
    # Linked folders are followed, since a skill often links shared files in
    # and an edit there changes what the model sees. A folder already walked
    # (by its real path) is not entered again, so a link to a parent ends.
    for root, dirs, files in os.walk(target, followlinks=True):
        real = os.path.realpath(root)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        # Hidden names are editor, OS and tool droppings (.DS_Store, swap
        # files); a fresh checkout lacks them, so they must not count.
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(f for f in files if not f.startswith(".")):
            full = os.path.join(root, f)
            inner = os.path.relpath(full, target).replace(os.sep, "/")
            found.append((rel.rstrip("/\\").replace(os.sep, "/") + "/" + inner, full))
    return found


def fingerprint(suite: Dict[str, Any], strict: bool = True) -> Dict[str, str]:
    """{recorded path: sha256 of its bytes} for every file the suite covers.
    strict refuses a covers entry that names nothing, which is right before a
    run (a typo would fingerprint nothing) and wrong for verify, where a
    deleted file is a change to report."""
    # The tool's own output changes on every run, so a suite inside a folder
    # it covers would never verify: its results and the state folder (budget
    # and saved answers) stay out of the fingerprint.
    results = os.path.realpath(os.path.join(suite["dir"], "results.jsonl"))
    state = os.path.realpath(ledger.state_dir()) + os.sep
    prints = {}
    for rel in suite["covers"]:
        files = [(recorded, full) for recorded, full in _files(suite["dir"], rel)
                 if os.path.realpath(full) != results and not os.path.realpath(full).startswith(state)]
        if not files and strict:
            raise SuiteError(f"suite {suite['dir']}: covers names {rel}, which does not exist")
        for recorded, full in files:
            with open(full, "rb") as f:
                prints[recorded] = hashlib.sha256(f.read()).hexdigest()
    return prints
