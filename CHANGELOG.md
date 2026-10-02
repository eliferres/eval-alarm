# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-02

### Added

- `eval-alarm run` sends every case of one or more suites to any model command that reads stdin, several times each, and scores each answer.
- Four deterministic scorers for a case's expected.json: `exact`, `contains`, `regex` and `json`, each scoring the share of its checks that pass.
- A prompt template per suite, with a `{{case}}` placeholder each case's text fills.
- A budget of model calls per rolling seven days, checked against the whole planned run before the first call; `--dry` prints the plan and the arithmetic.
- Failed or timed-out calls are recorded with their reason and kept out of the scores.
- Every run is appended to the suite's `results.jsonl`, and every answer is kept on disk for reading.
- `eval-alarm check` raises DROP, SLIDE and WIDER alarms against the suite's own recent runs, and reports "baseline building" until there are enough of them.
- `eval-alarm verify` fails when a file the suite covers changed since its last scored run, naming the file.
- `--json` output for `check` and `verify`, and exit codes 0, 1 and 2 for CI.
- An offline demo suite with a stand-in model, replayed by the test suite on every push.
