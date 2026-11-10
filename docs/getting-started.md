# Getting started

日本語: [ja/getting-started.md](ja/getting-started.md)

A first run, step by step. The commands in this page are what you say to the agent or what it runs for you; the full command list is in [cli.md](cli.md).

## 0. Before the day: preparation checklist

Setting up takes longer than the review itself, so do it **before** a workshop or a review session (allow 30 to 60 minutes the first time). If you cannot finish it, use the W-A Tool console on the day instead; nothing here is required for that.

- [ ] An agent: Claude Code (also on Amazon Bedrock) or Kiro. Others work through `gh skill install` (see the README)
- [ ] Python 3.11 or later, and [uv](https://docs.astral.sh/uv/) (`uv --version`)
- [ ] AWS CLI v2, signed in to a **test** account (`aws sts get-caller-identity`)
- [ ] The read-only role deployed from [`iam/scan-role.yaml`](../iam/scan-role.yaml) and its `ScanProfileSnippet` pasted into `~/.aws/config` ([Permissions (IAM)](aws-iam.md); an IAM user can be trusted too). Never an administrator profile
- [ ] Optional, to record answers: `CreateWaToolRole=true` and a test workload in the W-A Tool
- [ ] kensa installed, and `doctor` reports no `[NG]` (section 1 below)
- [ ] Optional: the demo once, to see the flow without AWS (section 2)
- [ ] Kiro: the local-only commands pre-approved ([section 6](#6-fewer-permission-prompts)), so the session is not one approval after another

## 1. Install and check the environment

Install the plugin ([README → Install](../README.md#install)). Then ask the agent to "run `kensa doctor`" (or run `W doctor` yourself, where `W` is `uv run skills/kensa-review/scripts/kensa.py`). It is an offline check that touches neither AWS nor the network (example output; yours will differ):

```text
kensa doctor (offline check)
  [ok]   python    3.12.3
  [ok]   uv        /usr/local/bin/uv
  [warn] boto3     not importable in this Python (needed for: scan-aws, seed --from-wa-tool, push --apply, precheck). `uv run` installs it on demand.
  [warn] openpyxl  not importable in this Python (needed for: export --format xlsx). `uv run` installs it on demand.
  [ok]   gh        /usr/bin/gh
  [ok]   aws CLI   /usr/local/bin/aws
access level (from configuration only; `auth-check` verifies it against AWS):
  * Lv0 no AWS access : interview + repository (+ `mode iac`); `init --demo` to try the flow
    Lv1 read-only AWS : `scan-aws` (set aws.profile)
    Lv2 + W-A Tool    : `seed --from-wa-tool` / `push` / `precheck` (set aws.wa_profile (or reuse aws.profile))
state: none here → `init` (or `--dir .kensa-demo init --demo` to try without AWS)
```

`[warn]` on boto3 / openpyxl is fine when `uv` is `[ok]`: `uv run` installs them on first use. Without `uv`, install them with `python3 -m pip install openpyxl boto3` (on a PEP 668 system, in a venv).

## 2. Try it without AWS (about a minute)

In Claude Code, run `/kensa:demo`; in Kiro, `/kensa-demo`. Anywhere else, ask the agent to "try kensa on the demo data". It creates `.kensa-demo/` with a 12-item **synthetic** checklist, shows you a decision card or two, and exports a CSV. Nothing in the demo can be sent to the W-A Tool (`push --apply` and `seed` refuse), and it never touches your real `.kensa/`.

## 3. Choose how much AWS access to give it

You do not need all of it on day one. Each level adds capability; the review works at every level.

| Level | You have | What kensa can do | How to get there |
|---|---|---|---|
| **Lv0** | No AWS access | Interview (decision cards) and repository / IaC evidence (`mode iac`); a checklist from `seed --from-csv` (your own export) | Nothing to set up |
| **Lv1** | A **read-only** AWS profile | Adds the live-environment scan (`scan-aws`, 37 checks) | Deploy [`iam/scan-role.yaml`](../iam/scan-role.yaml) ([explanation](aws-iam.md)); `context set aws.profile=…`; `auth-check` |
| **Lv2** | A **W-A Tool** profile (separate from the read-only one) | Fetches the latest lens from your workload (`seed --from-wa-tool`), records answers (`push`), FTR pre-check, creates workloads | Same template with `CreateWaToolRole=true`; `context set aws.wa_profile=…`; `auth-check --for wa` |

Start with a test account and a test workload, and never with an administrator profile ([README → What to know first](../README.md#what-to-know-first)). `auth-check` prints a warning when the credentials look like an administrator; it is a best-effort hint ([Permissions (IAM)](aws-iam.md)), so a quiet result is not proof that the profile is read-only. The agent always shows you the account ID and asks before touching AWS, and `push --apply` is only ever run when you say so.

## 4. Run a real review

Say "run a Well-Architected review" (Claude Code: `/kensa:start`; Kiro: `/kensa-review`; optionally followed by your service overview or site URL). What happens, in order:

1. **You describe the service once** (what it is, who uses it, what data, regulations, team, tools), and answer up to **9 business questions**, each with a recommended option.
2. **kensa fetches the lens and sorts every item** into decided / draft / interview-only (`coverage`).
3. **Scans run** (`scan-repo`, and `scan-aws` or `scan-iac`). The agent shows the AWS account ID first and waits for your OK.
4. **You answer decision cards** for what the evidence did not settle: at most **30 cards**, each a ○/△/✗ choice with the evidence already found at the top. The agent asks one to three at a time. If you are in a hurry, the agent records the recommended business answers as assumptions, marks what it cannot know as ✗ with a "provisional answer" note, and lists every assumption at the end.
5. **You get the xlsx** (and optionally the W-A Tool record and the FTR pre-check). Items that nobody confirmed stay `✗ / assumed (needs confirmation)` and are counted separately (confirmed rate), never presented as "confirmed not done".

**How long it takes.** In the first outside trial (Kiro, v0.3.0, [verification](verification.md)) the agent's work alone took about **28 minutes** for the whole review, and `seed` plus a scan of one Region about 6.5 minutes of that; your answers come on top. The number of questions (9 + at most 30) is the part you control: answer the ones you know, assume the rest. **In a time-boxed session, pick the pillars first** and say so, for example:

> Run the kensa review, but in the next 10 minutes ask me decision cards only for Security, Reliability and Operational Excellence (`cards next --pillar security,reliability,operationalExcellence`). List the other pillars as left for later.

## 5. Come back later

State lives in `.kensa/` (in the directory you ran it from), so you can stop at any point.

- **Continue**: `/kensa:resume` (Kiro: `/kensa-resume`; or "continue the kensa review"). The agent runs `W plan` and `W status`, tells you where you are, and carries on.
- **Re-review** (for example every quarter): `/kensa:resume re-review` (Kiro: `/kensa-resume re-review`). It takes a `snapshot`, re-reads the lens, rescans, and reports regressions first with `diff`.
- **A second review** (another lens or another workload): use another directory, `--dir .kensa-serverless`.
- `init` on a directory that already holds a review does not overwrite it: it tells you how far the review got and points to `plan`.

## 6. Fewer permission prompts

### Claude Code

Every `W …` call is a Bash call, so a full review can mean many approvals. You can pre-approve the **local-only** subcommands (they read or write only `.kensa/` and the repository you point them at) in `.claude/settings.json` (project) or `~/.claude/settings.json` (all projects):

```json
{
  "permissions": {
    "allow": [
      "Bash(uv run *kensa.py doctor*)",
      "Bash(uv run *kensa.py plan*)",
      "Bash(uv run *kensa.py status*)",
      "Bash(uv run *kensa.py coverage*)",
      "Bash(uv run *kensa.py items*)",
      "Bash(uv run *kensa.py diff*)",
      "Bash(uv run *kensa.py business *)",
      "Bash(uv run *kensa.py cards *)",
      "Bash(uv run *kensa.py context *)",
      "Bash(uv run *kensa.py apply *)",
      "Bash(uv run *kensa.py finalize*)",
      "Bash(uv run *kensa.py snapshot*)",
      "Bash(uv run *kensa.py scan-iac*)",
      "Bash(uv run *kensa.py seed --from-csv *)",
      "Bash(uv run *kensa.py iam-policy *)"
    ]
  }
}
```

- `*` may appear anywhere in a Bash rule ([Claude Code permissions](https://code.claude.com/docs/en/permissions)), so the leading `*kensa.py` matches whatever absolute path the agent uses. Without `uv`, repeat the rules with `python3` in place of `uv run`. A global option placed before the subcommand (`kensa.py --dir X plan`) does not match these rules; it just prompts. These rules were written from the documentation and have not been exercised in a live session here.
- **Do not add** `auth-check`, `scan-aws`, `seed --from-wa-tool`, `push`, `precheck` or `workload-create`: they touch AWS, and the approval prompt is the last place you can stop them. `export` (writes a file wherever `--out` says) and `scan-repo` (calls `gh` if you pass `--gh`) are also left out on purpose.

### Kiro

Copy the rule in [`optional/kiro-permissions.yaml`](../optional/kiro-permissions.yaml) into `~/.kiro/settings/permissions.yaml` (Kiro reads permission rules only from your home directory, never from a repository; [Kiro permissions](https://kiro.dev/docs/permissions/)). It allows the same local-only subcommands as the list above and keeps asking for everything that touches AWS.

- **Never choose "Always allow" for `uv run *`** (or `python3 *`) in the approval prompt. It lets any script run without asking, including `scan-aws`, `push --apply` and `workload-create --apply`, and in Kiro one `allow` in any scope is enough.
- The kensa skills tell the agent to write every call as one plain command (`uv run …/kensa.py <subcommand> …`), one per call. If you still see the agent defining a shell function or variable for it (`W() {…}`, `W="uv run …"`), tell it to stop: every call then needs its own approval, and under zsh the variable form fails.

## 7. Language

Talk to the agent in your language; it translates the cards and relays CLI messages. For Japanese or English deliverables (xlsx / csv / json and card text) set `W context set language=ja` (or `--lang ja`, or `KENSA_LANG=ja`). The CLI's own messages stay in English by design, because the agent reads them and relays them to you ([README → Language](../README.md#language)).
