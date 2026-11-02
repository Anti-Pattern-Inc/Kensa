# Design notes

日本語: [ja/design.md](ja/design.md)

## Key design points

1. **Decide the evidence route first**: the bundled routing table (`assets/routing.json`) sorts every item by `coverage` into one of three routes. For the standard lens (version 2025-02-25, 307 items) the split is Decided 226 / Draft 40 / Interview only 41.
   - **Decided**: AWS and repository evidence is enough to give a verdict (encryption, network defense, logging and detection, secrets, CI and test automation, and so on).
   - **Draft**: the evidence can only say that something exists (postmortems, runbooks, escalation, threat models, DR, game days, retrospectives and similar; the material is AWS Incident Manager / Resilience Hub / FIS and GitHub activity and documents). The evidence is pre-filled into the decision card, so only a **confirmation** is needed. "Not found" is never used as grounds for ✗ (it may live somewhere else).
   - **Interview only**: only a person can answer (culture, priorities, management involvement). Whether a document, a regular meeting or a training exists outside AWS and the repository also lands here (kensa does not search Slack, Drive and the like; a decision card asks a person instead).
   Evidence can touch 266 of the 307 items (87%). A "Draft" is not final until a person confirms it, and it appears in OpenItems. (The classification is a design decision of this plugin; `assets/routing.json` is the source of truth.)
2. **Ask by "decision card", not by item**: the items that might need an interview are grouped into **30 cards**. Items that belong to no card are judged from AWS/IaC alone, and if no evidence can be found they become assumed (needs confirmation). For each card you only choose "the current state is ○, △ or ✗" (the evidence already found is shown at the top). Each card carries a **recommendation (steps, effort, priority, owner) and alternatives (when to choose them, trade-offs)** aimed at a small team of about four people, and `{po}`, `{chat}` and `{vcs}` are filled in with the team's tools. To adapt it to an organization, override it in `.kensa/cards.local.json`.
3. **Never invent facts**: an item with no evidence and no answer is not marked ○; it becomes `✗ / assumed (needs confirmation) / Confidence Low`. A self-reported ○ stops at Confidence Medium and appears in OpenItems. Strong AWS/repository evidence is not overwritten by a self-report (the Draft route is the exception: its evidence only shows that something exists, so a person's answer takes precedence and a rescan does not overwrite it).
4. **Live environment first**: if the IaC and the live environment disagree, the live environment wins and the difference (suspected drift) is recorded in Notes.
5. **The latest lens**: `seed --from-wa-tool` fetches the latest questions, choices and improvement-plan URLs from the W-A Tool's `list_answers`. When the lens is updated, running the same command again keeps the existing verdicts and marks added or removed items.
6. **Not a one-off (re-review)**: `snapshot` saves the verdicts, and after a rescan `diff` reports **what got worse (○→△/✗), what improved, items confirmed since, lens additions and removals, and items with stale evidence (default 180 days)**. Each item keeps the time its evidence was recorded (`evidence_at`), and the due date and action status of each improvement (`due` / `action_status`) are saved too.
7. **Do not mix "unconfirmed" with "not done"**: an assumed ✗ is not a check that was never performed. Summary reports the **recorded rate** (share of items with a verdict) and the **confirmed rate** (share that also excludes assumed items) separately, and `push` warns about the number of assumed items and lets you skip them with `--exclude-assumed`. The verdict vocabulary is not extended (the W-A Tool has no "unconfirmed" state, and an extra value would corrupt what gets submitted for the FTR).

## Generality (not tied to the sample)

| Question | Answer |
|---|---|
| Can all items be fetched through MCP/API? | **Yes.** The W-A Tool's `list_answers` returns the questions, choices and improvement-plan URLs (`seed --from-wa-tool`). However, an official lens cannot be written out with ExportLens (AWS-provided lenses are explicitly excluded), and ListAnswers requires a WorkloadId. So `workload-create` makes a temporary workload that has no resources and costs nothing, and the items are read from it. The official skill builds its question / best-practice list from the pages of the official documentation (per best-practice ID), but it has no W-A Tool ChoiceId, so it cannot be used for `push`. **This is the only route that yields ChoiceId mechanically.** Calling the same API through the AWS MCP Server gives the same result. The official documentation search (`aws___search_documentation`) is for filling in details on individual items and carries no ChoiceId |
| Another lens | `--lens <alias or ARN>`. Use a separate `--dir` per lens |
| Items missing from the routing table (another lens, updates) | `route-unknown` prints the items and the vocabulary (areas, card list) as JSON → the agent classifies them → `route-apply FILE` registers them in `.kensa/routing.local.json` (the bundled routing table is not changed). The last resort is always an interview |
| Per-organization recommendations and cards | `.kensa/cards.local.json` (per-card overrides and additions) |
| CSV import | Any sheet works if it has the columns Pillar / QuestionId / QuestionTitle / ChoiceId / BestPractice / Description (AWSCheckable is optional) |

## IaC-only mode (`mode iac`)

An alternative for when the live AWS environment cannot or should not be read (production must not be touched, the permissions are not available, or the environment does not exist yet). Switch with `W mode iac` (after `seed`) and switch back with `W mode both`. `W mode` prints the current value. The mode is stored in the worklist and survives a re-seed.

| | `both` (default) | `iac` |
|---|---|---|
| AWS (live environment) | `scan-aws` (37 checks) | **Not read.** `scan-aws` stops, and no `channel=aws` verdict is recorded |
| IaC | `scan-repo` (kinds, CI, documents, GitHub) | `scan-repo` plus **`scan-iac`** (statically reads Terraform, CloudFormation/SAM and `cdk synth` output, and reports the count and configuration flags per resource type; no AWS access or credentials needed) |
| Confidence of a verdict | Configuration value checked directly = High | A declaration in IaC alone = **up to Medium** (`apply` lowers High to Medium for items that have an aws route) |
| How the source is shown | AWS (live environment) / Repository / IaC | An item with an aws route that was judged from IaC is shown as **'IaC (live environment not verified)'**. It appears in OpenItems, in the Summary notes, in `status`, and in the `push` warning |
| Decision cards | Items on the aws route wait for `scan-aws` | They wait for `scan-iac` (not for aws) |

- **"Not in the IaC" is never grounds for ✗.** Resources may have been created by hand, or live in another stack or repository. If nothing is found, no verdict is given and the item goes to an interview. ✗ / △ is used only when the IaC does declare the resource and the declared configuration is weak (interpretation rules: `references/iac-readonly-checks.md`).
- Once the live environment can be read, switch with `mode both` → `scan-aws`. A live-environment verdict (`channel=aws`) is always stronger than an IaC verdict: `apply` replaces it and the marker disappears.
- `scan-iac` writes only counts, flags and file names to `.kensa/evidence/iac-scan.json`, and does not store values (passwords, keys). It does not read CDK source (`.ts` / `.py`), so produce the `cdk synth` output (`cdk.out/*.template.json`) first and then rescan (the scanner does not run synth).
- The official skill `aws-well-architected-review` also reads IaC. The difference in this mode is that full coverage of all items, decision cards, recording to the W-A Tool and the re-review diff work the same way when only IaC is available.
