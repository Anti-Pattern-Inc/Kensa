# Repository signals and verdict rules

A guide for turning the output of `W scan-repo --path . [--gh owner/repo]` (the `signals` object in `.kensa/evidence/repo-scan.json`) into verdicts for the items that can be checked on the repository side (mainly `ops_dev_integ_*`, `ops_mit_deploy_risks_*`, `sec_appsec_*`, `rel_tracking_change_management_*`, `perf_process_culture_*`, and so on).
Say only what the files and settings actually show. **Finding a document or some activity is grounds for a draft verdict; not finding one is not grounds for anything** (it may live in Notion, Drive, or Slack; `apply` rejects a ✗ on the draft route). Whether something is **actually in use** has to be checked against the real branch protection settings, the recent workflow run history, and the PR history (if `gh` is unavailable, keep confidence at M or lower; confidence is H/M/L = High/Medium/Low).

| signals | What is looked at | Main items (areas) |
|---|---|---|
| `iac` | tools (cdk/terraform/cloudformation/sam/pulumi) and the key files | iac |
| `ci` | workflows, oidc_to_aws, static_aws_keys_in_actions, tests, lint, sast, sca_or_image_scan, secret_scan, deploy_steps, preview_diff, environment_protection_used, rollback_hint, permissions_declared, actions_pinned_by_sha, pull_request_target_used, loadtest_in_ci | ci, tests, secscan, loadtest |
| `review` | codeowners, pr_template | review |
| `github` (`--gh`) | branch_protection (required_approvals, status_checks_required, force_push_allowed), rulesets, secret_scanning, push_protection, dependabot_alerts_enabled, visibility | review, secscan, deps |
| `deps` | dependabot / renovate, lockfiles | deps |
| `secscan` | gitleaks config, pre-commit secret-scanning hooks, SECURITY.md, CodeQL | secscan |
| `tests` | tests directories, number of test files, e2e | tests |
| `container` | number of Dockerfiles, non-root USER, unpinned/latest tags, HEALTHCHECK, .dockerignore | container |
| `docs` | README, runbooks, ADR, postmortems, threat_model, dr_plan, raci_or_ownership, escalation, architecture, slo, readiness_review (ORR), retrospective, data_classification, compliance, customer_comms, training, sustainability, cost, change_process, pentest, gameday, kpi | docs |
| `ownership` | codeowners, service_catalog (catalog-info.yaml / MAINTAINERS / OWNERS), issue_templates (count), pr_template | review, docs |
| `iac_resources` | dashboards, slo, fis_templates, resilience_hub, budgets, incident_manager, oncall_tooling (PagerDuty etc.), statuspage, cost_estimate_tool (infracost) found in IaC | iac, observability, resilience, ops-process, cost |
| `github.activity` (`--gh`) | over the last 90 days: number of releases, number of deployments with their environment names, number of merged PRs, number of PRs from dependency bots; number of issues labeled incident/postmortem/improvement, whether those labels exist; community files (CONTRIBUTING, issue/PR templates) | gh-activity, review |
| `loadtest` | k6/locust/artillery etc. files, and whether they are wired into CI | loadtest |
| `hardcoded_secrets` | number of files containing AWS-key-shaped strings, and their paths (values are never stored) | secscan |

## Guidelines for verdicts

- **Programmatic deployment** (`sec_appsec_deploy_software_programmatically`, `ops_dev_integ_auto_integ_deploy`, `ops_dev_integ_build_mgmt_sys`): deploy steps live in CI (`deploy_steps`) and AWS is reached through OIDC (`oidc_to_aws` is true, `static_aws_keys_in_actions` is false) → ○. If static keys (`secrets.AWS_ACCESS_KEY_ID`) are used → △, and recommend a fix (P1). Manual deployment → ✗. Having no CodePipeline on the AWS side is not a problem (GitHub Actions is the CI/CD).
- **Change validation and testing** (`ops_dev_integ_test_val_chg`, `ops_mit_deploy_risks_test_val_chg`, `rel_tracking_change_management_functional_testing`): tests run in CI (`tests` is true) and are required for merging (`branch_protection.status_checks_required`) → ○. Tests exist but are not required → △. None → ✗.
- **Code quality and review** (`ops_dev_integ_code_quality`, `sec_appsec_manual_code_reviews`): branch protection with at least 1 approval, plus lint, plus CODEOWNERS → ○. Review by convention only (no protection settings) → △. If `gh` is unavailable and the protection settings cannot be seen, use confidence L (and add "Check the branch protection settings" to OpenItems).
- **Pipeline security** (`sec_appsec_regularly_assess_security_properties_of_pipelines`): `permissions:` declared, third-party Actions pinned by SHA (`actions_pinned_by_sha`), `pull_request_target` not used, approval through `environment` → all in place → ○. Some in place → △.
- **Automated security testing** (`sec_appsec_automate_testing_throughout_lifecycle`): count how many of SAST (CodeQL etc.), SCA (Dependabot/dependency-review) and secret scanning are present: three → ○, one or two → △, none → ✗. GitHub features on private repositories (CodeQL, secret scanning, etc.) depend on the plan/license, so when `github.secret_scanning` is null/disabled, write "may not be available on the plan" in `notes`.
- **Central management of dependencies** (`sec_appsec_centralize_services_for_packages_and_dependencies`): lockfile + Dependabot/Renovate → △ to ○ (○ if a private registry/CodeArtifact is used).
- **Hardcoded keys**: `hardcoded_secrets.count` > 0 calls for an **immediate fix (P1)**. Mark `sec_identities_secrets` as ✗ and recommend rotation. Never put the key values in the output.
- **Containers** (`sec_protect_compute_hardened_images`): non-root USER, pinned base-image tags, HEALTHCHECK → ○/△.
- **IaC** (`sec_securely_operate_automate_security_controls`, `cost_evaluate_cost_effort_*`, `sus_sus_dev_a2`): IaC exists and CI reviews `cdk diff`/`terraform plan` → ○. IaC exists but is applied by hand → △. None → ✗.
- **Runbooks / procedure documents / ADRs / post-mortems / threat models / DR plans** (`ops_ready_to_support_use_*`, `rel_tracking_change_management_planned_changemgmt`, `ops_evolve_ops_knowledge_management`, …): the file exists and was updated recently → ○ to △ (confidence M). If it does not exist, go to the interview card (show the card's options).
- **Load testing** (`rel_adapt_to_changes_load_tested_adapt`, `perf_process_culture_load_test`): a k6/Locust (or similar) script exists and runs in CI or on a schedule → ○; script only → △.
- **Branching strategy / small changes** (`ops_dev_integ_freq_sm_rev_chg`): average PR size is not collected (`github.activity.merged_prs_in_window` and the release/deployment frequency can serve as a reference). Confirm in the interview (C-DEV-PROCESS).

## Drafts from activity records and documents (items on the `draft` route)

Use only counts and file names; never read or store titles, bodies, or people's names. **Present → ○ to △ (confidence up to M); absent → no verdict (a person confirms through the decision card).**

- **Clear owners and responsibilities** (`ops_ops_model_def_proc_owners`, `…_activity_owners`, `…_responsibilities_ownership`): CODEOWNERS (`review.codeowners`) + a service catalog / ownership document (`docs.raci_or_ownership`, `ownership.service_catalog`) → ○ (M). CODEOWNERS alone → △.
- **A way to request changes and exceptions** (`ops_ops_model_req_add_chg_exception`): issue/PR templates (`ownership.issue_templates` ≥ 1 / `pr_template`) + branch protection requiring approval → ○ (M). Templates only → △.
- **Event response process** (`ops_event_response_event_incident_problem_process`): runbooks (`docs.runbooks`) + an incident label/template (`github.activity.labels.incident` ≥ 1) → ○ (M). Only one of the two → △.
- **Prioritizing events** (`ops_event_response_prioritize_events`): severity/priority labels (`labels.priority` ≥ 1) are in use (`issues_incident_in_window` ≥ 1) → ○ (M). Labels only → △.
- **Escalation paths** (`ops_event_response_define_escalation_paths`): described in `docs.escalation` / `docs.runbooks`, or `iac_resources.oncall_tooling` exists → ○ (M).
- **Customer communication plan** (`ops_event_response_push_notify`): `docs.customer_comms` or `iac_resources.statuspage` → ○ (M).
- **Post-incident analysis and sharing lessons** (`ops_evolve_ops_perform_rca_process`, `ops_evolve_ops_share_lessons_learned`, `rel_testing_resiliency_rca_resiliency`, `sec_incident_response_establish_incident_framework`): `docs.postmortems` / `docs.retrospective`, or issues with the postmortem label (`issues_postmortem_in_window` ≥ 1) → ○ (M). The document exists but is old (check the file's update date with `git log`) → △.
- **Continuous improvement and feedback** (`ops_evolve_ops_process_cont_imp`, `…_feedback_loops`, `ops_operations_health_review_ops_metrics_prioritize_improvement`): an issue with an improvement label (tech-debt / improvement) exists in the last 90 days (`issues_improvement_in_window` ≥ 1) + `docs.retrospective` → ○ (M).
- **Making time for improvement** (`ops_evolve_ops_allocate_time_for_imp`): issues with an improvement label were closed in the last 90 days → ○ (M). The number of closed issues is not collected, so if many remain open and old → △ (have a person check the issues).
- **Operational readiness review** (`ops_ready_to_support_const_orr`): `docs.readiness_review` → ○ (M). Whether it is actually used (a record of a recent release) is confirmed with a person through the decision card.
- **KPIs** (`ops_observability_identify_kpis`, `ops_operations_health_measure_ops_goals_kpis`): `iac_resources.dashboards` / `iac_resources.slo` / `docs.kpi` / `docs.slo` → ○ (M).
- **Cost awareness** (`cost_cloud_financial_management_cost_awareness`, `cost_type_size_number_resources_cost_modeling`, `perf_architecture_factor_cost_into_architectural_decisions`): `cost_estimate_in_ci` in CI (infracost), `iac_resources.cost_estimate_tool`, `docs.cost` → ○ (M). A mention of cost in an ADR counts in favor (the file contents are not read, so the existence of an ADR alone never gives ○ → △).
- **Recording design decisions** (`perf_architecture_evaluate_trade_offs`, `cost_evaluate_new_services_review_*`): `docs.adr` / `docs.architecture` updated within the last year → ○ (M).
- **Threat model** (`sec_securely_operate_threat_model`, `ops_priorities_eval_threat_landscape`): `docs.threat_model` → ○ (M) (△ if it was last updated more than a year ago).
- **Penetration testing / DAST** (`sec_appsec_perform_regular_penetration_testing`): `docs.pentest` → ○ (M). Only `dast_in_ci` in CI → △ (automated dynamic testing is not a substitute for an assessment).
- **Security training and ownership** (`sec_appsec_train_for_application_security`, `sec_appsec_build_program_that_embeds_security_ownership_in_teams`): `docs.training` / SECURITY.md / `docs.runbooks`, CODEOWNERS naming a security owner + SAST/SCA required in CI → ○ (M).
- **Game days** (`rel_testing_resiliency_game_days_resiliency`): `iac_resources.fis_templates` / `ci.chaos_in_ci` / `docs.gameday` → △ (back this up with the FIS experiment history in AWS as evidence of execution, and confirm manual drills through the decision card).
- **DR and recovery objectives** (`rel_planning_for_recovery_objective_defined_recovery`): `docs.dr_plan` states RTO/RPO values (opening the file to check is fine) → ○ (M). No values → △.
- **Data classification and compliance** (`sec_data_classification_identify_data`, `ops_priorities_governance_reqs`, `ops_priorities_compliance_reqs`): `docs.data_classification` / `docs.compliance` → ○ (M).
- **Sharing sustainability goals** (`sus_sus_dev_a1`): `docs.sustainability` → △ (a person confirms whether it is shared with the team).

## Reconciling with the live environment

- A resource that IaC defines but AWS lacks, or that AWS has but IaC lacks, is a "suspected drift". Record it in `notes` and give the verdict **in favor of the live environment**.
- For a multi-repository setup, pass `--path` several times (for example when IaC and the application are in separate repositories).
