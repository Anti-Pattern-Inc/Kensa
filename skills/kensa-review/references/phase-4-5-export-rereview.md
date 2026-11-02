# Phase 4-5: finish, export, re-review

> Detail for the phases of the `kensa-review` skill. `SKILL.md` has the safety rules and the overall flow; read this file when you start the phases below. `W` is the command defined in `SKILL.md` (`uv run …/scripts/kensa.py`).

## Phase 4: Finish and export

1. `W finalize` — sets every undecided item to `✗ / assumed (needs confirmation) / Confidence L` (never ○ by guesswork). It also fills default priorities.
2. `W export --format xlsx --out .kensa/review.xlsx` (add `--mask-account-ids` for the shared version; keep the output inside `.kensa/` so it is not committed by mistake). With `--format json` you can save verdict, source, confidence, rationale, confirmation date and route in machine-readable form (`csv` is also available). The xlsx sheets:
   - **Summary**: per pillar, the counts of ○/△/✗/N/A/undecided (formulas), the **recorded rate** (share with a verdict) and the **confirmed rate** (share excluding assumed items too), FTR scope, estimated HRI (reference value), and the breakdown by confirmation route (Decided / Draft / Interview only). **An assumed item does not mean "confirmed as not done"**, so convey completeness with the confirmed rate.
   - **Checklist**: the imported columns (Pillar through Description, AWSCheckable if present, Verdict) plus Source, Confidence, rationale, Org shape / tools, Recommended action, Effort, Priority, Owner, CardId, Route, Notes and **Confirmed on**.
   - **Questions**: the answered decision cards, with the chosen option, the recommendation and the alternatives.
   - **OpenItems**: items that need confirmation: assumed ones, self-reported ○, low confidence, drafts from evidence, **anything whose evidence was recorded 180 or more days ago**, and so on.
   - **ActionPlan**: △/✗ items in priority order, with a **due date** column and an **action status** column (Not started / In progress / Done). Edits made in the xlsx are not imported (a re-export restores them), so update through `due` / `action_status` in `apply`.
   - **Context**: the premises (business requirements).
3. Tell the user where the xlsx is (even if the agent has a way to hand over files, get the user's confirmation before uploading or sharing). In the summary, report: the verdict counts per pillar, the top P1 items, the number of OpenItems, the **confirmed rate and the number of assumed items** and why, and any scans not done (insufficient permissions or not connected).
4. **If the user wants it on Drive** (a Google Drive or similar connector is connected and `tools.docs` is Drive): the xlsx may contain account IDs and infrastructure details, so **ask the user first whether uploading is acceptable** (also ask about the sharing scope). After approval, upload the xlsx made with `--mask-account-ids` through the connector and return the link. Do not convert to Sheets or re-import edits made in Sheets (re-export the xlsx if needed). If there is no connector, skip this step.
5. If recording in the W-A Tool or an FTR check is needed, go to the `kensa-partner-record` skill.

## Phase 5: Re-review (ongoing)

A W-A review works best when repeated, for example every quarter, rather than done once. If `.kensa/` is still there, update only the differences **instead of starting over**.

1. Save the previous state: `W snapshot --label 2026Q4` (stored in `.kensa/history/`; take one when the first review ends, too).
2. Pull in lens updates: `W seed --from-wa-tool --workload-id <workload-id>` (keeps existing verdicts and marks added or removed items). New items start from the Phase 1 routing (`route-unknown`).
3. Rescan: `W scan-repo`, `W scan-aws`. Record with `apply` (new evidence of equal or higher strength replaces the old value. A person's answer is overwritten by strong AWS/repository evidence (a route that is not `draft`), but not by weak or draft evidence. Use `--force` only when you mean to change it).
4. Look at the difference with `W diff`. Report **regressions (○→△/✗)** first. Then improvements, items whose confirmation progressed, lens additions and removals, and **items with stale evidence** (180 days by default; change with `--stale-days`; `--json` for all of them). Stale items also appear in OpenItems.
5. Update the progress of fixes with `action_status` / `due` in `apply`, and share with `W export`.
6. To record in the W-A Tool as well, use `kensa-partner-record` (with `push --apply --milestone name`, a milestone is created only when every question was recorded successfully).
7. A reminder for the next round can be registered with `/schedule` or `/loop` (for example, "run `W diff` every quarter and tell me about regressions"). Ask the user whether to register it.
