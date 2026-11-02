# Contributing

日本語: [docs/ja/CONTRIBUTING.md](docs/ja/CONTRIBUTING.md)

Issues and pull requests are welcome in English or Japanese. The skills, references, decision cards, CLI messages and documentation are written in English. The only Japanese content is the Japanese locale for deliverables (the Japanese cards, `skills/kensa-review/assets/cards.ja.json`, and the Japanese export labels) and the Japanese documentation (`README.ja.md`, `docs/ja/`).

## Tests

The tests run offline (neither boto3 nor a network is needed).

```bash
pip install openpyxl                 # needed for the xlsx export tests; without it those tests are skipped
python3 -B -m unittest discover -s tests
```

- Python 3.11 or later.
- Also check the IAM template with `cfn-lint iam/scan-role.yaml`.
- Manifests: edit `plugin.json` (Agent Plugins format; the one source of truth), then run `python3 tools/sync_manifests.py` to regenerate `.claude-plugin/plugin.json` and the versions in `.claude-plugin/marketplace.json` (the tests fail when they drift). Bumping a release = change `version` in `plugin.json` and run the script. Then check with `claude plugin validate .`.

## Guidelines

- **Do not add text written by AWS.** Question text and best-practice text are fetched from the W-A Tool at run time. AWS documentation is published under CC-BY-SA-4.0 (code in it under MIT-0), so if you want to bring in a translation or adaptation of that text, first decide how to handle the source, the attribution and ShareAlike (these are conditions separate from this project's Apache-2.0). Keep fixtures synthetic (`tests/fixtures/mini_checklist.csv` is synthetic down to the question titles, best-practice names and `Description`; `QuestionId` and `ChoiceId` are keys of the routing table, so the real identifiers are used).
- **Do not commit real account IDs, profile names, customer names or email addresses.** Use `123456789012` and `example.com` in examples.
- **Touch AWS read-only.** When you add an API to the scanner, update the correspondence between the Deny in `iam/scan-role.yaml` and `DENY_ACTIONS` (`kensa.py iam-policy --kind scan-deny`), and make the tests pass.
- **Do not write something as verified when you have not verified it.** Make the statements in the README and `docs/verification.md` match the checks that were actually done.
- **Keep the two locales in step.** Cards, skills and references are English-first. When you change `assets/cards.json`, keep the same keys, ids and placeholders (`{service}`, `{po}`, `{chat}`, `{vcs}`, `{ticket}`, and so on) in `assets/cards.ja.json`; only the prose values differ. The English documentation is the primary version, and the Japanese copies under `docs/ja/` and `README.ja.md` follow it.
- Do not build the AWS mark into a name (AWS Trademark Guidelines). Descriptive use in a description (for example "for AWS Well-Architected") is fine.
- **Keep `SKILL.md` short.** It is loaded in full every time the skill runs (a test caps its size). It holds the safety rules and the flow; the procedure of each phase lives in `skills/kensa-review/references/phase-*.md`. Do not repeat what the README or `docs/` already say.
- **Entry points**: `commands/*.md` are the Claude Code slash commands (`/kensa:start`, `/kensa:resume`, `/kensa:record`, `/kensa:demo`); they only hand over to the skills. Other agents use the skills directly.
- **Usability changes need a test too.** First-run behaviour (messages that say what to do next, the demo sandbox, `doctor`, the `finalize` guard) is pinned in `tests/test_kensa.py` (`Onboarding`).
