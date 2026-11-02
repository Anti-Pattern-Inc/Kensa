# W-A related FTR requirements, and how this skill handles them

Based on public information as of October to November 2026. The rules change, so always check the latest in the FTR guide in AWS Partner Central. This page states facts in our own words and does not reproduce AWS text.

Sources (public pages read for this summary):
- https://docs.aws.amazon.com/partner-central/latest/builder-guide/requesting-ftr.html
- https://aws.amazon.com/partners/foundational-technical-review/
- https://aws.amazon.com/about-aws/whats-new/2026/06/aws-partner-central-foundational-technical-review

Scope: this skill covers only the route where a Well-Architected Framework Review (WAFR) report is submitted. The same Partner Central flow also accepts a SOC 2 Type II report as an alternative; this plugin does not handle that. The solution must also meet Partner Central's own prerequisites (for example, a software product linked from AWS Marketplace); this plugin does not check them either.

| Requirement | Check in `precheck` |
|---|---|
| The review is done in the AWS Well-Architected Tool, on the solution's primary AWS-hosted workload, and is complete | UNANSWERED is 0 (our reading of "complete") |
| The review is less than 1 year old | `UpdatedAt` of the LensReview is under 365 days |
| Zero high-risk issues (HRI) in each of Security, Operational Excellence and Reliability | `RiskCounts.HIGH` is 0 for each of those pillars |
| The solution name appears in the workload name or the workload description | Judged only when `--solution-name` is given, and only against the workload name (so put the solution name in the name) |
| The reviewer owner field shows a full name and an email address | `ReviewOwner` contains a name and an email-shaped string |
| The report is a PDF of at most 3 MB, uploaded in Partner Central | Decodes the Base64 from `get_lens_review_report` and checks the size (saved with `--pdf-out`) |

## Scope of verification, and cautions

- Calling `update_answer` with `ChoiceUpdates` (a `Status` per choice) was verified on one test workload (PREPRODUCTION): only the choices that were sent became `SELECTED`, confirmed by reading back with `list-answers` (see `docs/verification.md` in the repository). **Unverified**: recording `N/A` (not applicable at the question level), truncation when `Notes` is long, multiple lenses, and workloads in other regions. Before recording to a production workload, try `push --apply` on a test workload and use `precheck` to confirm that the risk comes out as expected.
- The "None of these" choice that exists in the official lenses is detected from the fetched lens's `Choices` and used. If a lens does not have one, nothing is done.
- The W-A Tool's risk calculation is AWS-side logic. △ is treated as unselected, so reaching HRI 0 requires satisfying the required choices (○).
- This plugin does not judge the other technical requirements of FTR (the individual security, operations and reliability checks). It only records the W-A review and checks HRI.
