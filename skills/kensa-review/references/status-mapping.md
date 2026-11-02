# Verdict vocabulary and mappings

## This tool's verdicts → the W-A Tool

| Verdict | Meaning | W-A Tool (`ChoiceUpdates` of `update_answer`) |
|---|---|---|
| ○ | Confirmed that the best practice is met | `Status: SELECTED` |
| △ | Only partly (scope, coverage or operation is insufficient) | `Status: UNSELECTED` (keep the rationale in `Notes`, up to 250 characters) |
| ✗ | Not done / no evidence can be confirmed (including assumed items) | `Status: UNSELECTED` |
| N/A | The workload lacks the prerequisite | `Status: NOT_APPLICABLE` + `Reason` (default `OTHER`; N/A given through a card uses `BUSINESS_PRIORITIES`) |
| (every item in the question is N/A) | The question itself does not apply | `IsApplicable: false` + `Reason` + `Notes` |
| (a question with no ○ at all) | — | If a "None of these" choice exists, mark it `SELECTED` (only when it exists in the fetched lens) |

- The risk the W-A Tool computes (HIGH / MEDIUM / NONE) is determined on the AWS side from the **selected choices**. △ counts as unselected, so a △ alone does not lower the risk. To reach FTR (HRI=0), either actually satisfy the required choices (○), or use N/A only when the item truly does not apply.
- The "estimated HRI" on the `Summary` sheet is a reference value, the number of questions that have no ○ at all. Use the official value by reading it back from the W-A Tool after `push --apply` (`precheck` does this).
- What goes into `Notes` is a short form, `[verdict/source/confidence] rationale`. For identifiers such as account IDs, use `--mask-account-ids` with the sharing scope in mind (check the notes in the W-A Tool yourself before `push`).
- Check a ○ with confidence L before recording it in the W-A Tool (OpenItems). If you use a ○ that rests only on self-report in an FTR submission, prepare supporting evidence first.

## Source and confidence

| Source (`channel`) | Priority | Notes |
|---|---|---|
| `aws` | Highest | Live-environment configuration values. If it disagrees with IaC, take the live environment and write the difference in `notes` |
| `repo` | Equal to `aws` | IaC / GitHub settings. Strong when AWS can confirm that it is reflected in production |
| `interview` | Next | Self-reported. Confidence stops at M (even with an evidence URL, this tool does not verify it) |
| `imported` | Weak | A verdict imported from another environment. Confidence L. Re-checking is mandatory |
| `assumed` | Weakest | Placed as ✗ with neither evidence nor an answer. Confidence L. Appears in `OpenItems` |

A verdict from a strong source (`aws`/`repo` with confidence H/M) is not overwritten by `apply` from a weaker source (override with `--force`).

Exception: `aws`/`repo` on a draft route (`draft` in `routing.json`) only show that a document or activity exists, so they rank below `interview`. An item a person answered is not overwritten by `apply` from a rescan (override with `--force`). They still rank above imported (`imported`) and assumed (`assumed`).

## Mapping to the official skill `aws-well-architected-review` (aws-core plugin)

The official skill outputs findings from reading IaC / code / configuration, with a five-level status. The vocabulary on `main` (checked directly on 2026-11-01) is below.
**Caution: the skill is still being updated (the unmerged PR #464 proposes reading each best-practice page, pausing and resuming, and JSON output). Check the vocabulary against the output of the version you use. Its output varies between runs, so keep the confidence of imported verdicts at M at most.**

| Official skill status | This tool |
|---|---|
| Implemented | ○ (`channel: repo`, confidence M to H) |
| Partially Implemented | △ |
| Not Implemented | ✗ (the official skill requires evidence for every finding: a file path and line number, or a note that it is based on a description) |
| Not Applicable | N/A |
| Cannot Determine (organization/process items and others that cannot be judged from code) | Do not judge; route to the AWS scan, the activity record and the decision cards (the official skill asks you to describe the runtime or interview data that is needed) |

The official skill is **non-interactive** by default (it does not ask questions midway), and its deliverable is inline Markdown in the chat (saving to a file is requested in issue #472). It does not check the live environment through AWS APIs (apart from fetching documentation) and does not record to the W-A Tool. This tool imports its result with `apply`, supplements it with the live environment, the GitHub activity record and interviews, and records to the W-A Tool. We do not build on top of the official skill by adding instructions to it (the instructions would conflict with its non-interactive, inline-output design).

Sources (only short status labels and facts are used here; no text of the skill is reproduced):
- https://github.com/aws/agent-toolkit-for-aws (the skill is at `plugins/aws-core/skills/aws-well-architected-review`; the repository's `LICENSE` is Apache-2.0, and the skill's `SKILL.md` frontmatter has no license field of its own)
- https://github.com/aws/agent-toolkit-for-aws/pull/464 (open)
- https://github.com/aws/agent-toolkit-for-aws/issues/472 (open)
