---
description: Continue a kensa review in progress, or start the next quarterly re-review
argument-hint: "[re-review]"
---
Continue the Well-Architected review in this directory with the `kensa-review` skill.

1. Run `W plan` and `W status`, and tell the user in two lines where the review stands (items resolved, scans done, cards left, the next step).
2. If the review is unfinished, follow the next steps `W plan` prints. If it is finished, or the user said "re-review" ($ARGUMENTS), run Phase 5: `W snapshot --label <period>`, re-seed for lens updates, rescan, `apply`, then `W diff` and report the regressions first.
3. If there is no `.kensa/worklist.json` here, say so and offer `/kensa:start`.
