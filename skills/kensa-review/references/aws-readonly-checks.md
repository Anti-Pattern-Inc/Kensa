# AWS read-only checks and verdict rules

The checks that `W scan-aws` runs, and **guidelines** for deciding ○/△/✗ from its results (`.kensa/evidence/aws-scan.json`). The rules are a starting point for judgment, not a mechanical verdict. Adjust how strict you are by the workload's criticality (`business.criticality`): stricter for critical/high, looser for low.

## Ground rules

- Recommended role: `ReadOnlyAccess` + `SecurityAudit`, with the extra Deny for calls that fetch values (`W iam-policy --kind scan-deny` prints the JSON), plus `ce:Get*` / `ce:List*`, `budgets:ViewBudget`, `support:DescribeSeverityLevels`, `compute-optimizer:GetEnrollmentStatus`, `ssm-incidents:List*`, `ssm-contacts:List*`, `resiliencehub:List*`, `fis:List*`, `application-signals:ListServiceLevelObjectives`.
- Organizations (SCP), billing, Cost Explorer, and Budgets may be visible only from the **management account**. If they are not visible, the status is `denied` / `not_enabled`. In that case the verdict is "cannot be confirmed": write the reason in `evidence` and send it to `OpenItems` (ask the management-account owner).
- A `scan-aws` result holds only counts, flags, and short samples. Secret values, object contents, and user names are never stored.
- Statuses: `ok` / `denied` (not enough permission) / `not_enabled` (service not enabled or not subscribed; in most cases this is evidence that the practice is not in place) / `error` / `blocked`. `not_enabled` may be used as **"not enabled" = grounds for ✗ (confidence H)**. `denied` and `error` are not grounds for anything.
- If you want to check more, run **read-only commands only**, with the `aws` CLI or the AWS MCP Server (the examples below use CLI syntax). The MCP tool to use is `aws___run_script` (it runs Python in a sandbox; the legacy tool `aws___call_aws` is not in AWS's current tool list), and you write the same check as list/describe/get API calls. `run_script` can run arbitrary Python, and the proxy's `--read-only` (which hides every tool that is not `readOnlyHint=true`) only filters per tool, so IAM is the only thing that can stop write APIs (AWS's own docs say IAM actions such as `aws-mcp:CallReadOnlyTool` do not take effect). The AWS MCP Server uses SigV4, so **the permissions of the credentials used to connect apply as they are**: connect with a read-only profile (`ReadOnlyAccess` + `SecurityAudit` + `iam-policy --kind scan-deny`) and pass **only that one profile** to `--profile` (if you pass several, the agent can switch between them on every call).

## Check list (check id → main output → areas)

| check id | scope | main output | areas |
|---|---|---|---|
| `identity` | global | account, principal_type | org, iam |
| `organizations` | global | in_org, is_management_account, scp_count | org |
| `iam_account` | global | root_mfa_enabled, root_access_keys_present, password_policy, users/roles/groups | iam, root |
| `iam_users` | global | users_total, users_with_active_keys, active_keys_older_than_90d, console_users_without_mfa | iam |
| `federation` | global | oidc_providers, github_actions_oidc, saml_providers, identity_center_instances | iam, iac-aws |
| `access_analyzer` | regional | analyzers(type/status) | threat-detect, iam |
| `cloudtrail` | regional | trails(multi_region, log_validation, kms, org_trail, is_logging) | cloudtrail |
| `config` | regional | recorders, recording, all_supported, rules, conformance_packs | config |
| `guardduty` | regional | enabled, features_enabled | threat-detect |
| `securityhub` | regional | subscribed, standards | threat-detect |
| `inspector` | regional | ec2/ecr/lambda status | threat-detect, compute |
| `macie` | regional | status | threat-detect, s3 |
| `kms` | regional | customer_managed_keys, with_rotation | kms |
| `secrets` | regional | secretsmanager_total, rotation_enabled, ssm_secure_string, ssm_plain_string | secrets |
| `tls_acm` | regional | certificates, imported, expiring_within_30d | tls |
| `elb` | regional | load_balancers(scheme, azs, https_listeners, http_listeners_without_redirect, ssl_policies, access_logs) | tls, network, compute |
| `waf_cdn` | global | cloudfront_web_acls, distributions, distributions_with_waf, viewer_policy_allow_all, min_tls | waf, tls, network |
| `waf_regional` | regional | regional_web_acls | waf |
| `s3` | global | total, public_access_block_all, sse_kms, sse_s3_only, versioning_on, lifecycle, public_policy | s3, kms |
| `databases` | regional | instances, storage_unencrypted, publicly_accessible, multi_az_instances, backup_retention_min_days, deletion_protection_off, performance_insights_on, dynamodb_pitr_on | db, backup, multi-az |
| `backup` | regional | plans, rules_with_cross_region_or_account_copy, vaults_with_kms, restore_jobs_completed | backup |
| `vpc` | regional | vpcs, subnet_azs, flow_logs, vpc_endpoints, nat_gateways, sg_open_all_to_world, sg_risky_ports_open_to_world | network, multi-az |
| `ec2` | regional | instances_running, imdsv2_required, with_public_ip, arm64, volumes_unencrypted, volumes_unattached, ebs_default_encryption, ssm_managed_instances, unassociated_eips | compute, kms, network |
| `ecs` | regional | services, circuit_breaker_enabled/rollback, desired_ge_2, arm64_task_defs, privileged_containers | compute, scaling, multi-az |
| `lambda` | regional | functions, runtimes, arm64, xray_active | compute |
| `ecr` | regional | repositories, scan_on_push, tags_mutable, kms_encrypted, registry_scan_type | compute, container |
| `scaling` | regional | asg, app_autoscaling_{ecs,dynamodb,rds,lambda}_targets | scaling, cost |
| `observability` | regional | alarms, alarms_with_actions, alarms_with_description, usage_quota_alarms, dashboards, log_groups_without_retention, xray_sampling_rules, synthetics_canaries, sns_topics, eventbridge_rules | observability |
| `incident_tooling` | regional | ssm_incident_response_plans | observability, threat-detect |
| `resilience` | regional | resiliency_policies(total, with_rto_rpo), resilience_hub_apps, fis_templates, fis_experiments(total, last_365d, last_90d) | resilience, multi-az |
| `ops_process` | regional | ssm_contacts(total, escalation_plans, oncall_schedules), incident_records(total, last_365d, resolved), ops_items(sampled, with_severity, with_priority, last_90d), change_templates | ops-process |
| `slo` | regional | service_level_objectives | slo, observability |
| `quotas` | regional | quota_increase_requests | quotas |
| `iac_aws` | global | stacks, cdk_toolkit, env_markers | iac-aws, ci |
| `cost` | global | budgets, budgets_with_notifications, anomaly_monitors/subscriptions, cost_allocation_tags_active, last_month_total_usd, savings_plans_active, reserved_instances_active, compute_optimizer, tag_keys | cost, org |
| `support_plan` | global | plan_at_least_business | support |
| `route53` | global | hosted_zones, health_checks | network, multi-az |

A regional check records one result per region given in `--regions` (`regions.<region>`).

## Guidelines for verdicts (by area)

A row marked `—` is "not grounds for anything". When unsure, go one level lower and lower the confidence.

### iam / root (`sec_identities_*`, `sec_permissions_*`, `sec_securely_operate_aws_account`)
- ○: root MFA is enabled, there are no root access keys, there are few or no IAM users (people sign in through SSO such as Identity Center), and no access key is older than 90 days.
- △: root MFA is enabled, but there are IAM users with long-lived access keys, keys older than 90 days, or no password policy.
- ✗: root MFA is disabled, or a root access key exists.
- Least privilege (`least_privileges`) and continuous reduction (`continuous_reduction`): ✗ (confidence M) unless Access Analyzer (unused access) is enabled. Even when it is enabled, a person still has to examine individual policies → △, confidence L.
- Break-glass (`sec_permissions_emergency_process`): cannot be judged from AWS. Ask in the interview (C-SEC-FORENSIC).

### cloudtrail / config / threat-detect (`sec_detect_investigate_events_*`, `sec_incident_response_*`, `sec_securely_operate_*`)
- Log collection (`app_service_logging`/`logs`): ○ = a multi-region CloudTrail is on with log validation + VPC Flow Logs exist + ALB access logs are on. If some are missing → △. No CloudTrail → ✗.
- Fixing non-compliant resources (`noncompliant_resources`): ○ = Config records all resources + rules/conformance packs + automatic remediation. Recording only → △. No Config → ✗.
- Correlating security events (`security_alerts`): ○ = GuardDuty is on + Security Hub is subscribed + notifications (EventBridge → SNS/chat). GuardDuty only → △.
- Keeping up with threats (`updated_threats`) and automated protection (`auto_protection`): Inspector is on for EC2/ECR/Lambda → ○, on for some → △, off → ✗.
- Tooling prepared in advance for recovery (`pre_deploy_tools`): CloudTrail + Config + GuardDuty + Flow Logs are all in place → ○.

### kms / s3 / db (`sec_protect_data_rest_*`, `sec_data_classification_*`)
- Encryption (`encrypt`): ○ = RDS/EBS/S3 are all encrypted (S3 is encrypted with SSE-S3 by default, so judge the quality of key management by whether `sse_kms` is present). Any unencrypted RDS/EBS → ✗ (or △ when only newly created resources are encrypted).
- Key management (`key_mgmt`): a CMK (customer managed key) for sensitive data with rotation on → ○. Only AWS managed keys and low sensitivity → △.
- Access control (`access_control`): S3 public access block on for every bucket + no public policy + RDS not public → ○. Even one public exposure → ✗.
- Automation (`automate_protection`): encryption enforced through Config rules or SCPs → ○. EBS default encryption only → △.
- Lifecycle (`lifecycle_management`): by the share of buckets that have a retention/expiry lifecycle defined: ○ (all) / △ (some) / ✗ (none). Data classification (`identify_data`) is for the interview. If Macie is on, `auto_classification` is ○ or △.

### tls / waf / network (`sec_protect_data_transit_*`, `sec_network_protection_*`, `perf_networking_*`)
- Encryption in transit (`encrypt`): ○ = every ALB has an HTTPS listener + HTTP redirects to HTTPS + a TLS 1.2 or newer policy (such as `ELBSecurityPolicy-TLS13-1-2-*`), CloudFront has no `allow-all`, and databases force SSL (needs confirmation). An ALB without an HTTP redirect → △.
- Certificate management (`key_cert_mgmt`): issued by ACM with automatic renewal → ○. An imported certificate close to expiry → △/✗.
- Network layers (`create_layers`/`layered`): public/private subnet separation + databases and apps in private subnets + minimal SGs (`sg_open_all_to_world` empty, `sg_risky_ports_open_to_world` empty) → ○. Risky ports open to 0.0.0.0/0 → ✗.
- Inspection (`inspection`) and automated protection (`auto_protect`): WAF (managed rules) in front of internet-facing entry points → ○. A public ALB/CloudFront without WAF → ✗.
- IPv6, VPC endpoints, and NAT redundancy feed the performance, cost, and reliability items (no endpoints + a single NAT = a cost versus redundancy trade-off; write it in `notes`).

### compute / container (`sec_protect_compute_*`, `perf_compute_*`, `sus_*hardware*`)
- Vulnerability management (`vulnerability_management`): Inspector on + ECR scanning on → ○, only one → △, neither → ✗.
- Less manual management (`reduce_manual_management`): SSM-managed instances + no public SSH/RDP + ECS Exec/Session Manager in use → ○. 22/3389 open → ✗.
- Hardening (`hardened_images`): judged on the repository side from IaC/Dockerfiles: non-root, pinned tags, minimal base image. ECR tag immutability = IMMUTABLE counts in favor.
- IMDSv2 required (EC2): required on all → ○.
- Graviton (equivalent of `sus_sus_hardware_a3`): high arm64 share → ○, some → △, none → ✗ (N/A is possible depending on compatibility).

### backup / multi-az (`rel_backing_up_data_*`, `rel_fault_isolation_*`, `rel_planning_for_recovery_*`, `rel_withstand_component_failures_*`)
- Backups: AWS Backup (or automated backups + PITR) is on + encrypted + the retention period meets the RPO → ○. No cross-region/account copy → DR is ✗/△.
- Restore testing (`periodic_recovery_testing_data`): `restore_jobs_completed` has at least one in the last year → △ to ○ (○ if the testing is automated). Zero → ✗.
- Multi-AZ: ALB in 2 or more AZs + RDS Multi-AZ (or multiple Aurora members) + ECS desired ≥ 2 spread across AZs → ○. A single-AZ database → ✗ (`fault_isolation_*`). A single NAT → △.
- DR (`disaster_recovery`): no resources and no backup copies in another region → ✗ (if the RTO/RPO is relaxed and the policy is Backup & Restore, △ provided that decision is recorded).

### observability / scaling (`ops_observability_*`, `ops_workload_observability_*`, `rel_monitor_*`, `rel_adapt_to_changes_*`)
- Alarms: the number of alarms and `alarms_with_actions` (has a notification target). Zero dashboards → `create_dashboards` is ✗. For X-Ray/Synthetics, their presence decides `dist_trace`/`customer_telemetry`.
- Log retention: many `log_groups_without_retention` → △ (cost and compliance).
- Autoscaling: targets exist for ECS/ASG/DynamoDB/Lambda → ○; fixed capacity with a steady peak profile (`business.traffic`) → △ (acceptable as static stability; write the reason).
- Quotas (`manage_service_limits_*`): `usage_quota_alarms` is 0 → `monitor_manage_limits` is ✗. Having no change history is not a problem in itself.

### cost (`cost_*`)
- Budgets and anomaly detection (`budget_forecast`, `proactive_process`, `usage_report`): Budgets with notifications + an Anomaly Monitor/Subscription → ○. Only one → △. Neither → ✗. If `denied`, it cannot be confirmed.
- Tags (`monitor_usage_*`, `decomissioning_resources_track`): active cost allocation tags + consistent tag keys → ○.
- Purchase options (`pricing_model_*`): for a production workload in steady operation, look at whether Savings Plans/RIs exist. In an early launch phase ✗ can be reasonable → do not use N/A; give ✗ plus the reason (recommend together with `business.budget_sensitivity`).
- Right-sizing (`type_size_number_resources_*`): whether Compute Optimizer is enabled (Active).

### org / support / quotas / iac-aws
- Account structure (`securely_operate_multi_accounts`, `cost_govern_usage_account_structure`): production and non-production in separate accounts under Organizations → ○. A single account → ✗ (check that it agrees with `business.environments`).
- SCP/guardrails: `scp_count` > 1 (excluding FullAWSAccess) → ○. Outside the management account it is `denied` → cannot be confirmed.
- Support plan (`ops_ready_to_support_enable_support_plans`): `plan_at_least_business` is true → ○. `not_enabled` (SubscriptionRequired) → below Business (✗ to △; for a small team, confirm the intent in the interview → C-OPS-SUPPORT-PLAN).
- IaC: CloudFormation stacks (including CDK) exist and there are several environment markers → ○. If there are no stacks, check Terraform and the like on the repository side (repo-signals).

### resilience / ops-process / slo (evidence of operating processes; many of these go by the **draft** route)
Most of the items covered by these three areas are on the `draft` route (`routing.json`). **You can say that something is in use (present), but not that it is unused (absent)** (the team may run it in another tool). Present → a ○ to △ draft (confidence up to M). Absent → no verdict; ask through a decision card (`apply` rejects a ✗ from aws/repo on the draft route).
- Recovery objectives (`rel_planning_for_recovery_objective_defined_recovery`): a Resilience Hub policy has RTO/RPO (`with_rto_rpo` ≥ 1) → ○ (M). A policy exists but has no values → △. None → no verdict; check the repository's DR document, `business.rto/rpo`, and the card.
- Game days (`rel_testing_resiliency_game_days_resiliency`): an FIS experiment ran in the last year (`fis_experiments.last_365d` ≥ 1) → ○ (M). Templates only (`fis_templates` ≥ 1, no runs) → △. None → no verdict (manual drills may be recorded in documents or Slack).
- Escalation and on-call (`ops_event_response_define_escalation_paths`): `ssm_contacts.escalation_plans` ≥ 1 or `oncall_schedules` ≥ 1 → ○ (M). Contacts only → △. If PagerDuty or similar is used, fill the gap from IaC (`iac_resources.oncall_tooling`) or the card.
- Handling incidents and events (`ops_event_response_event_incident_problem_process`, `ops_event_response_prioritize_events`): `incident_records.last_365d` ≥ 1, and `ops_items.with_severity` / `with_priority` are set → ○ (M). If Incident Manager / OpsCenter is not used, fill the gap from labeled GitHub issues (`repo-signals.md`) and the card.
- Post-mortems (`ops_evolve_ops_perform_rca_process`, `rel_testing_resiliency_rca_resiliency`, `sec_incident_response_establish_incident_framework`): even with `incident_records.resolved`, you cannot tell whether an analysis was done → at most △. Combine with the repository's documents for ○.
- A way to request changes and exceptions (`ops_ops_model_req_add_chg_exception`): `change_templates` ≥ 1 (Change Manager) → ○ (M). GitHub PR/Issue templates and branch protection (`repo-signals.md`) can also satisfy it.
- SLOs (`rel_withstand_component_failures_service_level_agreements`, `ops_operations_health_measure_ops_goals_kpis`): `service_level_objectives` ≥ 1 → ○ (M). If there is a CloudWatch dashboard (`observability.dashboards`), it is grounds for a draft on KPIs (`ops_observability_identify_kpis`).
- Budget/cost KPIs (`cost_monitor_usage_define_kpi`) and cost responsibility (`cost_cloud_financial_management_function`): Budgets with notifications and active cost allocation tags → ○ (M).
- Ownership (`ops_ops_model_def_activity_owners`): `cost.tag_keys` contains a key equivalent to owner / team / service / cost-center → draft ○ (M). Tag coverage is unknown, so it is fine to stop at △.
- Data classification (`sec_data_classification_identify_data`): a tag key equivalent to `classification` / `data-class` exists, or Macie is on → draft. A person confirms whether the classification scheme is understood.
- Guidance from the cloud provider (`perf_architecture_guidance_architecture_patterns_best_practices`): support plan is Business or higher (`plan_at_least_business`) → draft ○.

### sustainability
- Region: the region in use is close to the users/requirements (for example ap-northeast-1 for users in Japan) → ○.
- Data tiering: no S3 lifecycle and everything in Standard → ✗ (△ if partial).
- Managed services: ECS/Fargate/RDS/Lambda rather than self-managed EC2 → ○.

## Examples of extra read-only commands you may run

`aws iam list-attached-role-policies` / `aws accessanalyzer list-findings-v2` (unused access) / `aws s3api get-bucket-logging` / `aws rds describe-db-parameters` (`rds.force_ssl`) / `aws ssm describe-patch-baselines` / `aws ecs describe-services` / `aws cloudwatch describe-alarms` / `aws backup list-restore-testing-plans`.
Before running one, check that the command does not fetch values (secrets, object contents, user data).
If you make the same check through MCP's `aws___run_script`, again call only list/describe/get-style APIs. After connecting, check how to call it in the tool's description.
