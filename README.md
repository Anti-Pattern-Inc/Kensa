# kensa — AWS Well-Architected review assistant plugin

日本語: [README.ja.md](README.ja.md)

## Install this plugin (prompt to paste into your agent)

Paste the following, as is, into the agent you use (Claude Code, Kiro, Codex, Cursor, and so on). The details of each method are under [Install](#install).

```text
Install the unofficial AWS Well-Architected review assistant plugin "kensa", from the GitHub repository https://github.com/Anti-Pattern-Inc/Kensa, into yourself (this agent). Reply in my language.

1. Work out whether you are Claude Code, Kiro, or something else, and choose the matching method below. Before you run any command, show it to me and get my confirmation.
   - Claude Code: `claude plugin marketplace add Anti-Pattern-Inc/Kensa`, then `claude plugin install kensa@anti-pattern-plugins`
   - Kiro: this needs actions in the IDE, so I will do it myself. Walk me through it: Powers panel → Add Custom Power → Import power from GitHub, and enter the URL above.
   - Anything else (Codex, Cursor, Gemini CLI, etc.): with GitHub CLI 2.90 or later, `gh skill install Anti-Pattern-Inc/Kensa --all --agent <your agent name>` (this installs the skills only, not the MCP config or the IAM template; tell me if I need those)
2. After installing, check that the skills `kensa-review` and `kensa-partner-record` are available.
3. Do nothing beyond the installation. Do not connect to AWS and do not ask for credentials.
4. Finally, tell me in 3 lines how to get started (what I should ask first).
```

kensa is an *unofficial*, community-built plugin (Claude Code / Agent Plugins format) that fills in an AWS Well-Architected (W-A) review from the **live environment (read-only), IaC/GitHub (configuration and activity), and business-requirement questions**.
It fetches the latest lens (or another lens). Items whose evidence only supports a draft need just a confirmation. Organization and process items with no evidence are asked as **option cards with recommendations**. It then exports an xlsx with every item filled in, records the answers to the AWS Well-Architected Tool (W-A Tool), and runs a pre-check for the AWS Foundational Technical Review (FTR).

> Version 0.3.0. The parts that run offline are covered by automated tests (177 tests). The AWS scanner was run against one development account, and writing to the W-A Tool was tried on one test workload. What was and was not verified is listed in [Verification status](docs/verification.md).

## What to know first

- **This is an unofficial project.** It is not affiliated with, endorsed by, or sponsored by AWS. Amazon Web Services and AWS are trademarks of Amazon.com, Inc. or its affiliates. Names of AWS services and programs such as "Well-Architected" also belong to their respective owners.
- **No text written by AWS is bundled.** Question text and best-practice text are fetched at run time from your own W-A Tool (`seed --from-wa-tool`). Everything under `tests/fixtures/` is synthetic data, including the question titles and best-practice names (`QuestionId` and `ChoiceId` are keys of the routing table, so the real identifiers are used). The routing classification in `assets/routing.json` is a design decision of this project, not an AWS classification (`QuestionId` and `ChoiceId` are used as-is, as keys of the routing table).
- **Running it touches AWS.** Scans are read-only, but `scan-aws` calls the Cost Explorer API a few times (AWS charges 0.01 USD per request, billed per page). `push --apply` and `workload-create --apply` write to the W-A Tool.
- **A documentation-search MCP (`aws-docs`) is bundled by default.** It is an unauthenticated, documentation-search-only connection to a public endpoint managed by AWS, and it uses no credentials. Your search terms and page URLs are sent to AWS (scan results are not). You can turn it off ([docs/aws-mcp.md](docs/aws-mcp.md)).
- **Start with a test account and a test workload.** Do not use a profile with administrator privileges (the agent has a shell, so the profile's credentials can also be used by other `aws` commands). A template for a read-only role is [`iam/scan-role.yaml`](iam/scan-role.yaml) ([explanation](docs/aws-iam.md)).
- The software is provided as-is (Apache-2.0). The IAM template has not been audited by a third party.

## Language

English is the base language: the skills, references, decision cards, CLI messages and documentation are written in English.

- **The agent replies in your language.** Talk to it in Japanese, or another language, and it answers in that language and translates the card questions, options and recommendations as it presents them (option keys, ids, commands, file names and flags are never translated). CLI messages are in English, and the agent relays them in your language.
- **Deliverables (xlsx / csv / json labels and the text of the decision cards) are available in `en` (the default) and `ja`.** To get Japanese output, use any one of `--lang ja`, `kensa.py context set language=ja`, or the environment variable `KENSA_LANG=ja` (precedence: `--lang` > `KENSA_LANG` > `language` in `context.json` > `en`).
- **For any other language**, the artifacts are produced in English and the agent translates when it presents them.
- The Japanese cards (`skills/kensa-review/assets/cards.ja.json`) are maintained separately from the English ones and may drift ([Known limitations](docs/limitations.md)).

## Features

Details are in the [design notes](docs/design.md).

1. **Decide the evidence route first**: each item is sorted into Decided (judged from AWS and repository evidence), Draft (the evidence only shows that something exists; a person confirms), or Interview only.
2. **Ask by "decision card", not by item**: the items that need an interview are grouped into 30 cards. For each card you only pick the current state (○/△/✗), and it comes with a recommendation (steps, effort, priority, owner) and alternatives.
3. **Never invent facts**: an item with no evidence and no answer is not marked ○; it becomes `✗ / assumed (needs confirmation) / Confidence Low`. A self-reported ○ stops at Confidence Medium.
4. **Live environment first**: if the IaC and the live environment disagree, the live environment wins and the difference is recorded in Notes.
5. **The latest lens**: the latest questions and choices are fetched from the W-A Tool. For another lens, or for items missing from the routing table, the agent can classify them and import them.
6. **Re-review**: `snapshot` and `diff` show what got worse, what improved, what has been confirmed since, lens changes, and stale evidence.
7. **Do not mix "unconfirmed" with "not done"**: the recorded rate and the confirmed rate are reported separately, and you can choose not to send assumed items to the W-A Tool.
8. **Works without access to AWS**: `mode iac` judges from IaC and the repository alone (Confidence up to Medium).

An unofficial comparison with other tools (as of 2026-11-01) is in [docs/comparison.md](docs/comparison.md).

## Install

Requirement: Python 3.11 or later.

- **Claude Code**: `/plugin marketplace add Anti-Pattern-Inc/Kensa` → `/plugin install kensa@anti-pattern-plugins`. With Claude Code 2.1.275 or later, one command also works: `/plugin install kensa --marketplace Anti-Pattern-Inc/Kensa`. From a local clone: `/plugin marketplace add ./Kensa`.
- **Kiro (Power)**: Powers panel → **Add Custom Power** → **Import power from GitHub** (`https://github.com/Anti-Pattern-Inc/Kensa`). From a cloned folder, use **Import power from a folder**. The Power uses the root `plugin.json`, `skills/` and `mcp.json` (Agent Plugins format) as they are. It activates when the conversation touches a keyword (`well-architected`, `wafr`, `ftr`, `w-a-tool`, `kensa`). **Not verified on a real Kiro** (verified only as far as the official documentation and the Agent Plugins schema).
- **GitHub CLI (`gh skill`; 2.90 or later, preview)**: `gh skill install Anti-Pattern-Inc/Kensa --all --agent claude-code`. `--agent` can also be `kiro-cli`, `codex`, `cursor` and others, and `--scope user` installs for all projects. **Only the two skills under `skills/` are installed** (the scripts, assets and references live inside each skill). `.mcp.json` (documentation search), the `iam/` template and `docs/` are not installed; get them from the repository if you need them. The two skills are used one after the other (`kensa-partner-record` uses the scripts of `kensa-review`), so install them together with `--all`.
- **Other Agent Plugins-compatible agents**: they read the same root `plugin.json`, `skills/` and `mcp.json`.
- **Script dependencies**: `openpyxl` for the xlsx export and `boto3` for the AWS subcommands. **`uv` is recommended**: the top of `kensa.py` declares the dependencies and the Python version, so `uv run skills/kensa-review/scripts/kensa.py …` fetches them automatically on first run. Without `uv`, run `pip install openpyxl boto3` and use `python3`.
- **MCP**: an unauthenticated documentation search (`aws-docs`) is bundled (`.mcp.json` / `mcp.json`). A setup that authenticates with IAM and can also call APIs (`optional/aws-mcp.json`) can be added optionally. [docs/aws-mcp.md](docs/aws-mcp.md)

## Quick start

Ask the agent "run a W-A review" and the `kensa-review` skill takes it from there, in the order below (the command is `uv run skills/kensa-review/scripts/kensa.py`, or `python3` without `uv`).

```bash
W="uv run skills/kensa-review/scripts/kensa.py"   # without uv, use python3 (needs pip install openpyxl boto3)
$W init
$W context set service.name="…" team.size=4 tools.chat=Slack aws.regions='["ap-northeast-1"]' repo.github=owner/repo
$W business show                                   # the 9 business-requirement questions (with recommendations)
$W business answer B-CRITICALITY --option high
$W seed --from-wa-tool --workload-id <workload-id>   # the latest lens (or --from-csv). The ID is 32 hex digits (printed by `workload-create`)
$W coverage                                        # breakdown: decided / draft / interview only
$W scan-repo --path . --gh owner/repo              # IaC, documents, GitHub settings and the last 90 days of activity, CI
$W context set aws.profile=kensa-scan aws.wa_profile=kensa-wa     # profile for reading / profile for the W-A Tool (wa_profile is optional)
$W auth-check                                      # check credentials and the target account (STS only; exit code 3 if it fails). For the W-A Tool side: --for wa
$W scan-aws --profile kensa-scan --regions ap-northeast-1        # read-only (37 checks)
# If you cannot read AWS, instead of scan-aws: $W mode iac → $W scan-iac --path .   (Confidence up to Medium; shown as 'IaC (live environment not verified)')
#   → the agent writes verdicts following the interpretation rules, then: $W apply answers.json
$W cards next -n 3                                 # decision cards for items that were not decided (with the evidence already found)
# Another lens or new items: $W route-unknown > unknown.json → classify and write routing.json → $W route-apply routing.json
$W cards answer C-OPS-OWNERSHIP --current partial --choose recommend
$W finalize && $W export --out .kensa/review.xlsx --mask-account-ids   # --format json for machine-readable output (csv is also available)
$W push --workload-id <workload-id>                       # dry-run (--apply records; --exclude-assumed does not send assumed items)
$W precheck --workload-id <workload-id> --solution-name "…"             # FTR pre-check
# Re-review (for example every quarter): save the previous state → rescan and apply → diff
$W snapshot --label 2026Q4                         # saves to .kensa/history/ (take it when a review is finished)
$W diff                                            # worse / improved / confirmed since / lens updates / stale evidence (--since / --stale-days / --json)
$W push --workload-id <workload-id> --apply --milestone "2026Q4 review"   # the milestone is created only if every question was recorded successfully
$W iam-policy --kind scan-deny                     # permissions: extra Deny for value-returning reads (wa-tool-read / wa-tool-write also exist)
```

## Output (xlsx)

The labels below are the English (`en`) ones; with `ja` the same columns and labels come out in Japanese ([Language](#language)).

| Sheet | Contents |
|---|---|
| Summary | Per-pillar ○/△/✗/N/A/undetermined counts (formulas), the **recorded rate** (share of items with a verdict) and the **confirmed rate** (share that also excludes assumed items), FTR scope, an estimated HRI (reference value, from decided items only), and the breakdown by route (decided / draft / interview only) |
| Checklist | The imported columns (Pillar to Description, plus AWSCheckable if present) + Verdict, Org shape / tools, Source, Confidence, Evidence, Recommended action, Effort, Priority, Owner, CardId, Route, Notes, Confirmed on |
| Questions | The 30 decision cards (the question, with a plain business-language paraphrase for cards that start from technical terms; the ○△✗ states; the ★ recommendation and its steps; alternatives; the org shape / tools if adopted; answer status) |
| OpenItems | Items that need confirmation: undetermined, assumed, self-reported ○, low confidence, drafts from evidence, **evidence recorded 180 or more days ago**, and so on |
| ActionPlan | △/✗ items in priority order, with a **due date** and an **action status** (Not started / In progress / Done, with input validation) |
| Context | The premises (team, tools, business requirements) |

The JSON output (`--format json`) carries, per item, the verdict, source, confidence, evidence, confirmation date, route, priority, due date and action status, plus the lens version, the premises, and the counts and rates (`--mask-account-ids` applies).

## Security and handling

To report a vulnerability, see [SECURITY.md](SECURITY.md).

- **Operations on AWS are limited to read-only list/describe/get calls.** The scanner code blocks write calls and secret-retrieval calls (pinned by tests). But this guard covers **only the APIs the scanner calls**, and because it is an allow-list, read APIs the scanner never calls (`get_item`, `get_log_events`, and so on) are not on the block list. **The real boundary is IAM**, so run with a read-only role that has the Deny from [`iam/scan-role.yaml`](iam/scan-role.yaml). That Deny is a list of known reads that return values or content; it is not exhaustive ([docs/aws-iam.md](docs/aws-iam.md)).
- **The only writes are to W-A Tool workloads**, and only when you pass `push --apply` / `workload-create --apply` explicitly. `push --apply` overwrites existing answers, so for a workload that already has answers, save a milestone in the W-A Tool first and then run it. With root-user credentials, commands such as `auth-check` and `scan-aws` stop (they pass only if you give `--allow-root`).
- **A scan stores only counts, flags and short samples.** User names, object contents and environment-variable values are not stored. However, when Lambda or ECS task definitions are fetched, environment variables can reach the scanner's memory (they are not stored, and they are not covered by the Deny). If you do not want any values read at all, deny those APIs in IAM.
- **`.kensa/` (scan results and verdicts) contains account IDs and infrastructure details.** The commands that create the state directory also place a `.gitignore`, but share exported output only with `--mask-account-ids`. Keeping the xlsx export under `.kensa/` also keeps it from being committed by accident.
- `apply` masks key, token and private-key formats found in evidence text. This covers evidence, rationale, notes and similar fields, not every input field.
- **Scanned content is data, not instructions.** Instructions written in repository files, URLs, AWS tags and descriptions, or GitHub labels are not followed (stated explicitly in `SKILL.md`).
- In xlsx / CSV output, strings that start with `=`, `+`, `-` or `@` are neutralized so that they are not interpreted as formulas.

## Documentation

| | |
|---|---|
| [Design notes](docs/design.md) | Key design points, generality (another lens, CSV), the IaC-only mode |
| [Permissions (IAM)](docs/aws-iam.md) | The read-only role template, and the role for the W-A Tool |
| [AWS MCP Server](docs/aws-mcp.md) | The bundled documentation search (unauthenticated), and the optional IAM-authenticated setup |
| [Verification status](docs/verification.md) | What was verified and what was not, and the first-run checklist |
| [Known limitations](docs/limitations.md) | Limits of the verdicts, features not implemented |
| [Comparison notes](docs/comparison.md) | Comparison with existing tools (as of 2026-11-01, unofficial) |
| [CONTRIBUTING](CONTRIBUTING.md) | Development and testing |

## Layout

```
Kensa/
├── README.md / README.ja.md     # English (primary) / Japanese
├── plugin.json                  # Agent Plugins format
├── .claude-plugin/              # plugin.json / marketplace.json for Claude Code
├── .mcp.json / mcp.json         # default MCP (unauthenticated documentation search; for Claude Code / for Agent Plugins)
├── iam/scan-role.yaml           # CloudFormation template: read-only scan role (+ optional W-A Tool write role)
├── optional/aws-mcp.json        # example config for the IAM-authenticated AWS MCP Server (optional; not bundled by default)
├── docs/                        # design, IAM, verification status, known limitations (English; Japanese in docs/ja/)
├── skills/
│   ├── kensa-review/            # orchestration (Phase 0-5)
│   │   ├── SKILL.md
│   │   ├── assets/              # routing.json (routing table + vocabulary) / cards.json (English: 30 decision cards + 9 business questions) / cards.ja.json (Japanese)
│   │   ├── references/          # routing table, AWS checks and verdict rules, IaC static-scan and verdict rules, repository signals, verdict vocabulary, business requirements
│   │   └── scripts/             # kensa.py (state and export) / kensa_scan.py (AWS, repository, IaC, GitHub) / kensa_wa.py (W-A Tool) / kensa_i18n.py (labels and language resolution) / gen_routing_doc.py (maintenance)
│   └── kensa-partner-record/    # recording to the W-A Tool and the FTR pre-check
└── tests/                       # offline tests (no boto3 or network needed; the xlsx export tests are skipped without openpyxl)
```

## License

[Apache License 2.0](LICENSE). Copyright 2026 株式会社アンチパターン.
