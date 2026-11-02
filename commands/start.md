---
description: Start an AWS Well-Architected review with kensa (checks the environment, then follows the kensa-review skill)
argument-hint: "[service overview, site URL or workload-id]"
---
Start an AWS Well-Architected review with the `kensa-review` skill. Follow its "Step 0" first:

1. Run `W doctor` (the offline environment check) and fix anything it marks `[NG]`.
2. If a review is already in progress in this directory (`.kensa/worklist.json`), do not start over: show where it stands with `W plan` and ask whether to continue it, re-review it, or start a new one in another `--dir`.
3. Otherwise begin with Phase 0. Use what the user wrote below as the service overview, and ask only for what is missing.

What the user gave me: $ARGUMENTS
