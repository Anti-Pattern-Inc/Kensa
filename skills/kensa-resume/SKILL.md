---
name: kensa-resume
description: Continue a kensa AWS Well-Architected review that is in progress in this directory (.kensa/worklist.json), or start the next quarterly re-review (diff against the previous run). The same steps as the Claude Code command /kensa:resume, as a skill for agents that do not read plugin commands (Kiro and others). Japanese triggers 「kensaの続き」「レビューを再開」「再レビュー」「前回との差分」.
license: Apache-2.0
compatibility: Python 3.11+ (uv recommended). Needs the kensa-review skill installed next to this one (its scripts and phases are used).
---

# Resume a kensa review (kensa-resume)

Continue the Well-Architected review in this directory with the `kensa-review` skill (next to this one). Its rules (account confirmation before touching AWS, read-only, dry-run before any W-A Tool write) all apply; this skill only fixes how to pick up where the review stopped.

`W` below stands for `uv run <kensa-review skill dir>/scripts/kensa.py` (without `uv`: `python3 …`). **Write the whole command on one line every time, one command per call. Never define `W` as a shell function, alias or variable**, and do not chain commands with `&&`, `;` or pipes (see "Running the CLI" in `kensa-review`).

1. If there is no `.kensa/worklist.json` here (or in the `--dir` the user names), say so and offer to start a review with `kensa-review`.
2. Run `W plan` and `W status`, and tell the user in two lines where the review stands (items resolved, scans done, cards left, the next step).
3. If the review is unfinished, follow the next steps `W plan` prints. When time is short, ask which pillars to cover first and use `W cards next --pillar <ids>` (for example `security,reliability,operationalExcellence`, the FTR pillars).
4. If it is finished, or the user asked for a re-review, run Phase 5 of `kensa-review`: `W snapshot --label <period>`, re-seed for lens updates, rescan, `apply`, then `W diff`, and report the regressions first.
5. To record the result in the W-A Tool and pre-check the FTR, use the `kensa-partner-record` skill.
