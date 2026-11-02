# Comparison with existing tools (research notes as of 2026-11-01)

日本語: [ja/comparison.md](ja/comparison.md)

> **These are unofficial research notes.** AWS preview features and official skills can change, so the numbers and specifications here may already be out of date. Check the official documentation before relying on them. This project is not affiliated with AWS, and the comparison here is not an evaluation by AWS. The summaries are our own short notes of facts; the sources are listed at the end.


| Existing tool | What it does | What it lacks for this purpose |
|---|---|---|
| `aws-well-architected-review` skill (the aws-core plugin of Agent Toolkit for AWS) | Reads IaC, code and CI and reports findings per pillar (Implemented / Partially Implemented / Not Implemented / Not Applicable / Cannot Determine). Checked in its `main` SKILL.md on 2026-11-01: a five-level verdict, evidence required for every finding, non-interactive by default, results returned as inline Markdown in the chat. PR #464 in https://github.com/aws/agent-toolkit-for-aws (read every best-practice page, resumable runs, JSON output) is not yet merged (still open on 2026-11-02) | It calls no AWS API, so it never looks at the live environment. Organization and process items stay "Cannot Determine" (62 of 307 items, according to an explainer article). Results vary from run to run. It does not record answers to the W-A Tool |
| AWS Well-Architected Agent (preview; announced 2026-10-01, checked in the official documentation on 2026-11-01) | Scans a live account once a week through a read-only role, and ranks its suggestions using your business goals and application details. It also outputs IaC / CLI steps for the fixes. Architecture review of IaC is run manually (Framework lens only) | Covers 4 pillars only (no Operational Excellence or Sustainability). Requires **Business+ or higher** support on every monitored account (plain Business and Developer are excluded). A profile can only be created in 3 US Regions, and one generation run produces at most 30 items. The output is suggestions, not answers to the W-A questions. It has no mechanism for organization / process questions or interviews, and the documents we read do not mention recording answers to the W-A Tool |
| AWS MCP Server (managed) | Documentation search (`aws___search_documentation`, `aws___read_documentation`), and API calls that run Python in a sandbox (`aws___run_script`) | It has no W-A review procedure of its own. Reading the live environment is done by our scanner (writes are blocked in code, and results are reproducible). Only the documentation search, which works unauthenticated, is bundled by default (`aws-docs`); the setup that can run APIs is an **optional part** ([docs/aws-mcp.md](aws-mcp.md)) |
| `awslabs.well-architected-security-mcp-server` | Checks the Security pillar | Security only (can optionally be used alongside) |
| Community `wellarchitected-mcp-server` | Mostly reads from the W-A Tool | Unofficial. We do not depend on it |
| W-A Tool API (`UpdateAnswer` and others) | Can record answers | We found no existing tool that uses it to record answers |

**"Should we provide our own skill?" → Yes. But instead of rebuilding AWS's, provide an orchestration skill that calls them.**
AWS's skill and MCP are good building blocks for "read the IaC" and "look up the documentation", and the Agent reads a running environment. But the combined flow of (1) looking at both the live environment and the IaC, (2) filling the business requirements and the organization items through an interview, and (3) filling all items of the 6 pillars with evidence and recording them to the W-A Tool is not something we found in what we surveyed (the Agent has (1) alone, but in preview with 4 pillars). This plugin provides that flow. Output of the official skill can be imported with `apply` (`kensa-review/references/status-mapping.md`). There is not yet a mapping table for importing the Agent's suggestions.

## Sources

Facts above were taken from these public pages (the URLs were re-checked to load on 2026-11-02; the content can change, so see the pages themselves):

- `aws-well-architected-review` skill: <https://github.com/aws/agent-toolkit-for-aws/tree/main/skills/core-skills/aws-well-architected-review>
- PR #464 (open as of 2026-11-02): <https://github.com/aws/agent-toolkit-for-aws/pull/464>
- AWS Well-Architected Agent (preview): <https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent.html>
- AWS Well-Architected Agent quotas and limits: <https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent-quotas.html>
- AWS MCP Server: <https://docs.aws.amazon.com/agent-toolkit/latest/userguide/mcp-server.html>
- `awslabs.well-architected-security-mcp-server`: <https://github.com/awslabs/mcp/tree/main/src/well-architected-security-mcp-server>

Not linked because no URL was recorded: the explainer article behind the "62 of 307" figure, and the community `wellarchitected-mcp-server`.

No AWS text is reproduced here; only facts (names, limits, dates) are noted, in our own words.
