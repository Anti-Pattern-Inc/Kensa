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
- After changing a manifest, check with `claude plugin validate .`.

## Guidelines

- **Do not add text written by AWS.** Question text and best-practice text are fetched from the W-A Tool at run time. AWS documentation is published under CC-BY-SA-4.0 (code in it under MIT-0), so if you want to bring in a translation or adaptation of that text, first decide how to handle the source, the attribution and ShareAlike (these are conditions separate from this project's Apache-2.0). Keep fixtures synthetic (`tests/fixtures/mini_checklist.csv` is synthetic down to the question titles, best-practice names and `Description`; `QuestionId` and `ChoiceId` are keys of the routing table, so the real identifiers are used).
- **Do not commit real account IDs, profile names, customer names or email addresses.** Use `123456789012` and `example.com` in examples.
- **Touch AWS read-only.** When you add an API to the scanner, update the correspondence between the Deny in `iam/scan-role.yaml` and `DENY_ACTIONS` (`kensa.py iam-policy --kind scan-deny`), and make the tests pass.
- **Do not write something as verified when you have not verified it.** Make the statements in the README and `docs/verification.md` match the checks that were actually done.
- **Keep the two locales in step.** Cards, skills and references are English-first. When you change `assets/cards.json`, keep the same keys, ids and placeholders (`{service}`, `{po}`, `{chat}`, `{vcs}`, `{ticket}`, and so on) in `assets/cards.ja.json`; only the prose values differ. The English documentation is the primary version, and the Japanese copies under `docs/ja/` and `README.ja.md` follow it.
- Do not build the AWS mark into a name (AWS Trademark Guidelines). Descriptive use in a description (for example "for AWS Well-Architected") is fine.
