"""The README's first code block is the ten-second example; it has to run
as written. Its eval-alarm lines run in a copy of the checkout (standing in
for the clone the block makes) with `eval-alarm` on PATH as a stand-in for
the installed command, and each must exit 0."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "build", "dist", "*.egg-info", ".venv")


def first_block() -> list:
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        block = re.search(r"```bash\n(.*?)```", f.read(), re.S).group(1)
    return [line for line in block.splitlines() if line.startswith("eval-alarm ")]


class TestTenSecondExample(unittest.TestCase):
    def test_the_install_block_runs_as_written(self) -> None:
        lines = first_block()
        self.assertTrue(lines, "the first block runs no eval-alarm command")
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
            for line in lines:
                proc = subprocess.run(["bash", "-c", line], cwd=copy, env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(proc.returncode, 0, f"{line}\n{proc.stdout}")


if __name__ == "__main__":
    unittest.main()
