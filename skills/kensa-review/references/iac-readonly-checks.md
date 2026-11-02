# Static IaC scan and verdict rules (`mode iac`)

The values that `W scan-iac` writes to `.kensa/evidence/iac-scan.json`, and **guidelines** for deciding ○/△/✗ from them. The live AWS environment is not read (`W mode iac`), so everything here rests on **having read IaC declarations only**. When the live environment can be read (`mode both`), `aws-readonly-checks.md` takes precedence.

## Ground rules (always follow)

- **IaC is a declaration, not the state of the live environment.** Manual changes, other stacks or repositories, changes that have not been deployed yet, and per-environment differences are all invisible. So an item judged from IaC alone is capped at **confidence M** (`apply` lowers H to M). The Source becomes `IaC (live environment not verified)` and the item appears in OpenItems.
- **Not found in IaC does not mean not in AWS.** Even when a resource is missing or a flag is 0, do not treat that as grounds for ✗. **Give no verdict** for the item and send it to an interview card (leave it undecided). You may give ✗ / △ only when **IaC contains the matching resource and declares a weak setting** (for example, an RDS exists but `StorageEncrypted` is absent).
- **What can be read**: Terraform (`aws_` resources in `.tf`; a module's definition is counted once), CloudFormation / SAM (`.yml` `.yaml` `.json` `.template`), and `cdk synth` output (`cdk.out/*.template.json`).
  **What cannot be read**: CDK source (`.ts` `.py`; run `cdk synth` to produce `cdk.out` and then scan again; the scanner does not run synth), Pulumi, expansion through Terraform `count` / `for_each` / variables, values passed through CloudFormation `!Ref` or parameters, and `terraform plan` output.
- **A flag approximates "the number of resources that declare that setting".** Settings declared as separate resources (S3 encryption, public access block, etc.) are added to the count, but **they are not matched to a specific bucket**. Compare the flag with `count`: on all of them → ○, on some → △ as a rule of thumb. **Before judging, open the files listed in `files` and check them** (especially for ✗ / △).
- **What AWS does by default even when IaC says nothing**: since January 2023 every S3 bucket is encrypted with SSE-S3 (so `encryption` at 0 does not mean "unencrypted"; whether KMS is used is seen through `kms_encryption`). DynamoDB is always encrypted at rest. RDS and EFS are not encrypted unless you ask for it (for EBS it depends on the account's default encryption setting).
- The output holds only counts, flags, and file names; values (passwords, keys) are not stored.

## Output keys (resources.<key> → flags → areas)

Use `W items --unresolved --area <area> --json` to narrow down the relevant items. A missing key means that resource was not found in IaC.

| key | flags | areas |
|---|---|---|
| `s3_bucket` | public_access_block, encryption, kms_encryption, versioning, lifecycle | s3, kms |
| `rds` | storage_encrypted, multi_az, backup_retention, deletion_protection, publicly_accessible | db, backup, multi-az |
| `dynamodb` | pitr | db, backup |
| `kms_key` | rotation | kms |
| `ebs_volume` / `efs` | encrypted | kms, compute |
| `load_balancer` / `lb_listener` | https, http, redirect | tls, network |
| `security_group` | world_ingress, world_ingress_non_web (`count` is the number of SGs) | network |
| `waf_web_acl` / `waf_association` | — | waf |
| `cloudtrail` | multi_region, log_validation, kms | cloudtrail |
| `guardduty` / `securityhub` / `access_analyzer` / `config_recorder` | — | threat-detect, config |
| `backup_plan` | copy_action | backup |
| `vpc` / `subnet` / `nat_gateway` / `vpc_flow_log` / `vpc_endpoint` | — | network, multi-az |
| `cloudwatch_alarm` / `log_group` | with_actions / retention | observability |
| `lambda_function` | tracing_active, dead_letter, reserved_concurrency | compute, observability |
| `ecs_service` | circuit_breaker, desired_ge_2 | compute, scaling, multi-az |
| `autoscaling_group` / `app_autoscaling_target` / `scaling_policy` | — | scaling |
| `secret` / `secret_rotation` | — | secrets |
| `ecr_repository` | scan_on_push | container |
| `route53_health_check` | — | multi-az, network |
| `iam` | wildcard_action, administrator_access | iam |
| `iam_user` | — | iam |

`sources` (the kinds of files read), `tools`, and `notes` (the reasons something could not be read) are also output. Always read `notes`.

## Guidelines for verdicts (by area)

`—` means "not grounds for anything". When unsure, go one level lower and lower the confidence. In every case confidence is capped at M.

- **s3**: `public_access_block` at or above `s3_bucket.count` → ○, on some → △, 0 → no verdict. Read `versioning` / `lifecycle` the same way. For encryption, take the default described above into account; if `kms_encryption` covers all of them, KMS encryption is ○.
- **kms**: `kms_key.rotation` equals `kms_key.count` → ○, some → △. No `kms_key` → no verdict (they may be using only AWS managed keys).
- **db**: `rds.storage_encrypted` / `multi_az` / `backup_retention` / `deletion_protection` at or above `count` → ○, some → △. An RDS exists but `storage_encrypted` is 0 → ✗ (weak declaration). However, **Aurora specifies encryption and similar settings only on the cluster**, so open `files` to check what applies to the instances. `publicly_accessible` > 0 → suspected public exposure (△ or ✗).
- **backup**: a `backup_plan` exists → △ or better (which resources it is assigned to is not examined). `copy_action` > 0 means a copy to another region or account is declared. If `dynamodb.pitr` is at or above `count`, DynamoDB has recovery points.
- **network**: `security_group.world_ingress_non_web` > 0 → suspected opening of ports other than 80/443 to `0.0.0.0/0` or `::/0` (△ or ✗; check which ports in `files`). `vpc_flow_log` > 0 → flow logs are declared. Even with several `subnet`s, the spread across AZs is not read.
- **tls**: `lb_listener.https` > 0 and `http` at or below `redirect` → ○. `http` above `redirect` → △ (some listener accepts plain HTTP).
- **waf**: `waf_web_acl` > 0 → △ or better. Open `files` to check what it is associated with (ALB / CloudFront).
- **cloudtrail**: `multi_region` and `log_validation` equal `count` → ○, some → △. No `cloudtrail` → no verdict (an organization trail from Organizations or Control Tower may have created it).
- **threat-detect / config**: if `guardduty` / `securityhub` / `access_analyzer` / `config_recorder` exist, they count toward ○. If absent → no verdict (they are often enabled from the organization's management account).
- **secrets**: both `secret` and `secret_rotation` present → ○, `secret` only → △. No `secret` → no verdict.
- **iam**: `wildcard_action` > 0 or `administrator_access` > 0 → a declaration that runs against least privilege (△ or ✗). `iam_user` > 0 → suspected use of long-lived credentials (△). A 0 never earns ○ (it may simply not be written in IaC).
- **observability**: `log_group.retention` equals `count` → ○ (a retention period is specified). `cloudwatch_alarm` is 0 → no verdict.
- **compute / scaling / multi-az**: `ecs_service.desired_ge_2` and `circuit_breaker` equal `count` → ○; if `autoscaling_group` / `app_autoscaling_target` / `scaling_policy` exist, scaling is declared. `lambda_function.tracing_active` equals `count` → X-Ray is declared.
- **container**: `ecr_repository.scan_on_push` equals `count` → ○.
- **iac / ci / tests / docs**: as in `references/repo-signals.md` (`scan-repo`). They do not change with `mode`.

## What goes to the interview (not judged from IaC)

Account-wide settings (Organizations and SCPs, billing/budgets/support plan, the root user, IAM Identity Center, enabling Security Hub standards, quotas), operational track record (whether FIS experiments were run, incident records), and manually created resources either do not appear in IaC or, when they do, do not show whether they were actually carried out. Give no verdict and send them to decision cards (`finalize` turns whatever is left into assumed (needs confirmation) ✗).

## Writing for `apply`

```json
{"choice_id": "…", "verdict": "○", "channel": "repo", "confidence": "M",
 "evidence": "IaC (infra/main.tf): both rds resources have storage_encrypted=true. Only the IaC declaration was read; the live environment is not verified"}
```

- `channel` must be `repo`. In `mode iac`, a verdict with `channel=aws` is not recorded (so that something that was never read is not counted as evidence from the live environment).
- In `evidence`, write what was read and in which file, and that the live environment is not verified.
- For items that have an `aws` route, `apply` lowers `confidence=H` to M and sets a `basis=iac_only` marker. Applying a live-environment (`aws`) verdict removes the marker.

## Once the live environment can be read

`W mode both` → `W auth-check` → `W scan-aws` → `apply` following `aws-readonly-checks.md` (`channel=aws`). Items judged from IaC alone are replaced by live-environment verdicts (in the `apply` precedence, the live environment (`aws`) always beats IaC (`repo`); it replaces the IaC verdict even when its confidence is L). If IaC and the live environment disagree, **adopt the live environment** and record the difference in `notes` as suspected drift.
