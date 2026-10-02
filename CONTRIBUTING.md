# Contributing

Welcome:

- A new scorer, if it is deterministic (same answer, same score, no model
  or network involved) and its settings are checked by `validate()` so a
  broken expected.json stops a run before any call is spent.
- Bug reports where an alarm fired on a run that was fine, or stayed quiet
  on one that was not. The suite's `results.jsonl` lines make the best
  reproduction.
- Fixes to anything the README claims that turns out not to be true.

Ground rules: the package stays standard library only and runs on Python
3.9. Every behaviour change comes with a test, and every test that needs a
model uses the fake in `tests/fake_model.py`, never a real one. Keep
`python3 -m unittest discover -s tests` green. If your change alters what a
command prints, regenerate the demo session with
`UPDATE_DEMO_TRANSCRIPT=1 python3 -m unittest discover -s tests` and say so
in the pull request; the picture in the README is drawn from that file.
