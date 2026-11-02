---
description: Try kensa on a synthetic checklist without any AWS access (a sandbox; nothing can be sent to the W-A Tool)
---
Show the user how kensa works, with no AWS access and no credentials, using the `kensa-review` skill:

1. `W --dir .kensa-demo init --demo` (a 12-item synthetic checklist; `push --apply` is disabled in this directory).
2. `W --dir .kensa-demo business show`, then `W --dir .kensa-demo cards next -n 2`. Present each card the way Phase 3 describes (translated into the user's language, recommendation first) and record the user's answers with `cards answer`.
3. `W --dir .kensa-demo finalize` and `W --dir .kensa-demo export --format csv --out .kensa-demo/demo.csv`; explain Summary-style counts (`W --dir .kensa-demo status`) and what "assumed (needs confirmation)" means.
4. Finish with how the real review differs (live AWS scan, 300+ items, the W-A Tool) and offer `/kensa:start`.
