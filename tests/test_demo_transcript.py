"""demo/transcript.json is the recorded session the README picture is drawn
from. This test replays it and fails when the tool's real output drifts.

Each command runs with bash, in order, inside a fresh copy of the
checkout, with the budget ledger pointed at an empty temporary folder so
every replay starts from the same state. `eval-alarm` on PATH is a
two-line stand-in for the installed console script that runs the copy's
package. The committed history in demo/sentiment/results.jsonl is what
the first check is judged against; the copy's appends never reach the
checkout.

UPDATE_DEMO_TRANSCRIPT=1 rewrites the transcript from the replay instead
of comparing; that is the only way the file is meant to change.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from typing import List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSCRIPT = os.path.join(ROOT, "demo", "transcript.json")
PICTURE = os.path.join(ROOT, "demo", "terminal.svg")
PLACEHOLDER = "/path/to/checkout"
SVG = "{http://www.w3.org/2000/svg}"
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "build", "dist", "*.egg-info", ".venv")


def replay() -> List[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "eval-alarm")
        shutil.copytree(ROOT, copy, ignore=IGNORE)
        bin_dir = os.path.join(tmp, "bin")
        os.mkdir(bin_dir)
        shim = os.path.join(bin_dir, "eval-alarm")
        with open(shim, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" -m eval_alarm "$@"\n')
        os.chmod(shim, 0o755)
        env = {**os.environ, "EVAL_ALARM_STATE_DIR": os.path.join(tmp, "state"), "PYTHONPATH": copy,
               "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}
        env.pop("EVAL_ALARM_BUDGET", None)
        with open(TRANSCRIPT, encoding="utf-8") as f:
            entries = json.load(f)
        done = []
        for entry in entries:
            proc = subprocess.run(["bash", "-c", entry["cmd"]], cwd=copy, env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out = proc.stdout
            # macOS reports the temp copy under /private/var as well as /var.
            for form in (os.path.realpath(copy), copy):
                out = out.replace(form, PLACEHOLDER)
            done.append({"cmd": entry["cmd"], "out": out.rstrip("\n"), "status": proc.returncode})
        return done


def picture_rows() -> List[Tuple[str, str]]:
    """(kind, text) per drawn row of the picture: "cmd" for a prompt row,
    "cont" for a wrapped command's continuation, "out" for output."""
    rows = []
    for el in ET.parse(PICTURE).getroot().iter(SVG + "text"):
        if el.get("font-size"):
            continue  # the window title
        spans = el.findall(SVG + "tspan")
        if spans:
            rows.append(("cmd", spans[-1].text or ""))
        elif el.get("class") == "cmd":
            rows.append(("cont", (el.text or "")[4:]))
        else:
            rows.append(("out", el.text or ""))
    return rows


class TestDemoTranscript(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.replayed = replay()
        if os.environ.get("UPDATE_DEMO_TRANSCRIPT"):
            with open(TRANSCRIPT, "w", encoding="utf-8") as f:
                f.write(json.dumps(cls.replayed, indent=2, ensure_ascii=False) + "\n")
        with open(TRANSCRIPT, encoding="utf-8") as f:
            cls.recorded = json.load(f)

    def test_each_command_prints_what_was_recorded(self) -> None:
        self.assertEqual(len(self.replayed), len(self.recorded))
        for rec, got in zip(self.recorded, self.replayed):
            with self.subTest(cmd=rec["cmd"]):
                self.assertEqual(got["out"], rec["out"])
                self.assertEqual(got["status"], rec["status"])

    def test_the_picture_draws_the_transcript_in_order(self) -> None:
        """Every drawn row traces back to the transcript: a command's rows
        rejoin to the command, each output row is the next output line (or
        that line cut once at the end with an ellipsis), nothing is left
        over, and the picture stops only between commands."""
        rows = picture_rows()
        self.assertTrue(rows, "the picture has no rows")
        i = 0
        for entry in self.recorded:
            if i == len(rows):
                break
            kind, text = rows[i]
            self.assertEqual(kind, "cmd", f"row {i + 1} should start {entry['cmd']!r}")
            parts = [text]
            i += 1
            while i < len(rows) and rows[i][0] == "cont":
                parts.append(rows[i][1])
                i += 1
            joined = " ".join(p[:-2] if p.endswith(" \\") else p for p in parts)
            self.assertEqual(joined, entry["cmd"])
            for line in [l for l in entry["out"].splitlines() if l.strip()]:
                self.assertLess(i, len(rows), f"the picture stops inside {entry['cmd']!r}")
                kind, shown = rows[i]
                self.assertEqual(kind, "out", f"row {i + 1} should be {line!r}")
                cut = shown.endswith("…") and line.startswith(shown[:-1]) and len(shown) - 1 < len(line)
                self.assertTrue(shown == line or cut, f"row {i + 1} is {shown!r}, expected {line!r}")
                i += 1
        self.assertEqual(i, len(rows), "the picture has rows the transcript does not account for")


if __name__ == "__main__":
    unittest.main()
