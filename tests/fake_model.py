"""A stand-in model for the tests: no network, no randomness.

FAKE_ANSWERS names a JSON list of answers handed out in order, one per
call; the position is kept in FAKE_ANSWERS + ".n". Two answers act instead
of printing: "!fail" exits 3 with a message on stderr, "!hang" sleeps past
any test timeout. Every prompt received is appended to FAKE_ANSWERS +
".prompts" so a test can see what was sent.
"""
import json
import os
import sys
import time

path = os.environ["FAKE_ANSWERS"]
prompt = sys.stdin.read()
with open(path + ".prompts", "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt}) + "\n")
try:
    with open(path + ".n", encoding="utf-8") as f:
        n = int(f.read())
except FileNotFoundError:
    n = 0
with open(path + ".n", "w", encoding="utf-8") as f:
    f.write(str(n + 1))
with open(path, encoding="utf-8") as f:
    answer = json.load(f)[n]
if answer == "!fail":
    print("model unavailable", file=sys.stderr)
    sys.exit(3)
if answer == "!hang":
    time.sleep(30)
print(answer)
