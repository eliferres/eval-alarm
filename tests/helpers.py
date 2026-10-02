"""Builds suite folders on disk for the tests."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_suite(root: str, name: str = "sentiment", settings: Optional[Dict[str, Any]] = None,
               cases: Optional[Dict[str, tuple]] = None) -> str:
    """A suite folder under root. cases maps a case name to (prompt text,
    expected dict); the default is two exact-match cases."""
    folder = os.path.join(root, name)
    if cases is None:
        cases = {"praise": ("I love it", {"scorer": "exact", "expected": "positive"}),
                 "refund": ("It broke, refund me", {"scorer": "exact", "expected": "negative"})}
    if settings is not None:
        write(os.path.join(folder, "suite.json"), json.dumps(settings))
    for case, (prompt, expected) in cases.items():
        write(os.path.join(folder, "cases", case, "prompt.md"), prompt)
        write(os.path.join(folder, "cases", case, "expected.json"), json.dumps(expected))
    return folder
