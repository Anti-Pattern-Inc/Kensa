# CLI reference: the order the agent runs things in

日本語: [ja/cli.md](ja/cli.md)

You normally do not type these: ask the agent ("run a W-A review", or `/kensa:start` in Claude Code) and the `kensa-review` skill runs them. They are listed here so you can see what happens, and so you can run a step yourself. `W <command> --help` is the complete reference.

`W` is `uv run skills/kensa-review/scripts/kensa.py` (without `uv`, `python3 …`, which needs `pip install openpyxl boto3`). After any step, **`W plan` prints what is done and the numbered next steps**; it is the quickest way to find your place again.

```bash
W="uv run skills/kensa-review/scripts/kensa.py"
$W doctor                                          # offline environment check (Python, uv, boto3, openpyxl, gh, aws) and the access level in use
# Try it first, with no AWS access: a synthetic 12-item sandbox (push --apply is disabled in it)
#   $W --dir .kensa-demo init --demo   →   $W --dir .kensa-demo cards next   →   ...
$W init                                            # (if .kensa/ already holds a review, it says how far it got and points to `plan`)
$W context set service.name="…" team.size=4 tools.chat=Slack aws.regions='["ap-northeast-1"]' repo.github=owner/repo
$W business show                                   # the 9 business-requirement questions (with recommendations)
$W business answer B-CRITICALITY --option high
$W seed --from-wa-tool --workload-id <workload-id>   # the workload's lens (warns if it is not the current version; or --from-csv). The ID is 32 hex digits (printed by `workload-create`)
$W coverage                                        # breakdown: decided / draft / interview only
$W scan-repo --path . --gh owner/repo              # IaC, documents, GitHub settings and the last 90 days of activity, CI
$W context set aws.profile=kensa-scan aws.wa_profile=kensa-wa     # profile for reading / profile for the W-A Tool (wa_profile is optional)
$W auth-check                                      # check credentials and the target account (STS only; exit code 3 if it fails). For the W-A Tool side: --for wa
$W scan-aws --profile kensa-scan                   # read-only (37 checks); Regions come from aws.regions, or pass --regions ap-northeast-1
# If you cannot read AWS, instead of scan-aws: $W mode iac → $W scan-iac --path .   (Confidence up to Medium; shown as 'IaC (live environment not verified)')
#   → the agent writes verdicts following the interpretation rules, then: $W apply answers.json
$W cards next -n 3                                 # decision cards for items that were not decided (with the evidence already found)
#   "waiting for evidence collection" = a scan the cards depend on has not run (or been skipped with `plan --skip`); it names the scan
# Another lens or new items: $W route-unknown > unknown.json → classify and write routing.json → $W route-apply routing.json
$W cards answer C-OPS-OWNERSHIP --current partial --choose recommend
$W finalize && $W export --out .kensa/review.xlsx --mask-account-ids   # --format json for machine-readable output (csv is also available)
#   finalize refuses an untouched review (nothing scanned, recorded or answered); `plan --skip aws,repo` skips scans on purpose, --allow-empty overrides
$W push --workload-id <workload-id>                       # dry-run (--apply records; --exclude-assumed does not send assumed items)
$W precheck --workload-id <workload-id> --solution-name "…"             # FTR pre-check
# Re-review (for example every quarter): save the previous state → rescan and apply → diff
$W snapshot --label 2026Q4                         # saves to .kensa/history/ (take it when a review is finished)
$W diff                                            # worse / improved / confirmed since / lens updates / stale evidence (--since / --stale-days / --json)
$W push --workload-id <workload-id> --apply --milestone "2026Q4 review"   # the milestone is created only if every question was recorded successfully
$W iam-policy --kind scan-deny                     # permissions: extra Deny for value-returning reads (wa-tool-read / wa-tool-write also exist)
```

## Exit codes and messages

- Exit code 2: a usage or state error (the message after `error:` says what to do). Exit code 3: the AWS credentials or the target account did not check out (nothing was scanned or saved). `doctor` exits 1 only when Python is too old.
- CLI messages are always English (the agent relays them in your language); only the deliverables and the decision-card text have a `ja` locale ([Language](../README.md#language)).
