---
description: Record a finished kensa review in the AWS Well-Architected Tool and pre-check the FTR (dry-run first)
argument-hint: "[workload-id]"
---
Use the `kensa-partner-record` skill to record the finished review (`.kensa/worklist.json`) in the W-A Tool and to run the FTR pre-check.

- Check the prerequisites first (`finalize` was run; OpenItems reviewed; IaC-only verdicts and assumed items understood).
- Always do the dry-run (`W push`) and show it to the user. **Run `--apply` only after the user explicitly says so**, after `auth-check --for wa` showed the write-target account and the user confirmed it.
- Then `W precheck` and report OK / NG / WARN in a table.

Workload id, if the user gave one: $ARGUMENTS
