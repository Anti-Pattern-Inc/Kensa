# Security Policy

## Reporting a vulnerability

If you find a vulnerability, do not open a public issue. Report it through GitHub's **Private vulnerability reporting** (the repository's Security tab → *Report a vulnerability*; direct link: https://github.com/Anti-Pattern-Inc/Kensa/security/advisories/new ). Do not include account IDs, credentials, or details of real infrastructure.

Please include:

- The affected file or command (for example `kensa.py scan-aws` or `iam/scan-role.yaml`)
- Steps to reproduce, and the expected and actual behavior
- The impact (for example: writing is possible beyond the read-only boundary, or a secret value is stored or printed)

This project is provided as-is, and we cannot promise a response deadline. We will reply as soon as we are able to look at it.

日本語: 脆弱性を見つけた場合は、公開の Issue に書かず、GitHub の Private vulnerability reporting(リポジトリの Security タブ → *Report a vulnerability*)で報告してください。アカウント ID、資格情報、実在のインフラの情報は書かないでください。このプロジェクトは現状有姿(as-is)で提供しており、対応の期限は約束できません。確認でき次第、返信します。

## Scope and assumptions

- In scope: the scripts in this repository (`skills/*/scripts/`), the CloudFormation template (`iam/scan-role.yaml`), and the skill procedures (`SKILL.md`).
- **It is provided as-is.** The IAM template and the Deny list have not been audited by a third party. Before using them on a real account, read them and try them on a test account.
- The boundary by design is IAM. The blocking done by the scanner's code is an aid that protects the range of APIs the scanner calls; it does not stop other commands that the agent runs. Use a read-only role, and do not use a profile with administrator privileges.
- Known limitations are in [docs/limitations.md](docs/limitations.md).
