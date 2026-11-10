---
name: kensa-demo
description: Try kensa (the AWS Well-Architected review plugin) on a synthetic 12-item checklist, with no AWS access and no credentials. A sandbox; nothing can be sent to the W-A Tool. The same steps as the Claude Code command /kensa:demo, as a skill for agents that do not read plugin commands (Kiro and others). Japanese triggers 「kensaのデモ」「kensaを試す」「AWSなしで試す」.
license: Apache-2.0
compatibility: Python 3.11+ (uv recommended). Needs the kensa-review skill installed next to this one (its scripts are used). No AWS access.
---

# kensa demo (kensa-demo)

Show the user how kensa works without AWS. Follow the `kensa-review` skill (next to this one) for how to present cards and for its language and safety rules; this skill only fixes the order of the steps.

`W` below stands for `uv run <kensa-review skill dir>/scripts/kensa.py` (without `uv`: `python3 …`). **Write the whole command on one line every time, one command per call. Never define `W` as a shell function, alias or variable**, and do not chain commands with `&&`, `;` or pipes (see "Running the CLI" in `kensa-review`).

1. `W --dir .kensa-demo init --demo` (a 12-item synthetic checklist; `push --apply` is disabled in this directory).
2. `W --dir .kensa-demo business show`, then `W --dir .kensa-demo cards next -n 2`. Present each card the way Phase 3 of `kensa-review` describes (translated into the user's language, recommendation first) and record the user's answers with `W --dir .kensa-demo cards answer <card-id> --current <full|partial|none|na>`.
3. `W --dir .kensa-demo finalize` and `W --dir .kensa-demo export --format csv --out .kensa-demo/demo.csv`; explain the Summary-style counts (`W --dir .kensa-demo status`) and what "assumed (needs confirmation)" means.
4. Finish with how the real review differs (live AWS scan, 300+ items, the W-A Tool) and offer to start one (the `kensa-review` skill; in Claude Code, `/kensa:start`).
