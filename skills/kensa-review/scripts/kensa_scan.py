"""Evidence collectors: REPO (local checkout, optional `gh`) and AWS (read-only).

Design rules
- The AWS scanner can only call list_/describe_/get_ style read APIs; secret-bearing reads are blocked (ReadOnly proxy).
- Results are summaries (counts, flags, short samples). No secret values, no object contents, no user names.
- Every check is isolated: AccessDenied / not-subscribed / errors are recorded per check and never abort the scan.
- boto3 is imported lazily (only by make_session()).
"""
import collections
import datetime
import json
import os
import re
import shutil
import subprocess
import urllib.parse

# ---------------------------------------------------------------- REPO scan
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", ".next", "cdk.out", ".terraform",
             "__pycache__", "target", ".gradle", ".idea", "vendor", ".tox", ".mypy_cache", ".pytest_cache"}
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yml", ".yaml", ".toml", ".tf", ".env", ".sh", ".rb", ".go",
            ".java", ".kt", ".md", ".txt", ".cfg", ".ini", ".properties", ".conf", ".xml", ".gradle", ".hcl"}
MAX_FILES, MAX_BYTES = 20000, 1_000_000
AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")


IAC_RESOURCES = {
    "dashboards": r"aws_cloudwatch_dashboard|AWS::CloudWatch::Dashboard|cloudwatch\.Dashboard\b",
    "slo": r"aws_applicationsignals_service_level_objective|AWS::ApplicationSignals::ServiceLevelObjective",
    "fis_templates": r"aws_fis_experiment_template|AWS::FIS::ExperimentTemplate|CfnExperimentTemplate",
    "resilience_hub": r"AWS::ResilienceHub|aws_resiliencehub",
    "budgets": r"aws_budgets_budget|AWS::Budgets::Budget",
    "incident_manager": r"aws_ssmincidents|AWS::SSMIncidents|aws_ssmcontacts|AWS::SSMContacts",
    "oncall_tooling": r"pagerduty|opsgenie|splunk[-_]on[-_]call",
    "statuspage": r"statuspage|instatus|betteruptime|status\.io",
    "cost_estimate_tool": r"infracost",
}


def _read(path, limit=MAX_BYTES):
    try:
        if os.path.getsize(path) > limit:
            return ""
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def _any(text, pats):
    return any(re.search(p, text, re.I) for p in pats)


def scan_repo(path=".", gh_repo=None, runner=subprocess.run):
    path = os.path.abspath(path)
    files = []
    for root, dirs, fs in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in fs:
            files.append(os.path.relpath(os.path.join(root, f), path))
            if len(files) >= MAX_FILES:
                break
        if len(files) >= MAX_FILES:
            break
    low = {f: f.lower() for f in files}
    names = lambda pred: [f for f in files if pred(low[f])]  # noqa: E731

    sig = {}
    # --- IaC
    tools, evid = [], []
    def mark(tool, fl):
        if fl:
            tools.append(tool)
            evid.extend(fl[:3])
    mark("cdk", names(lambda p: os.path.basename(p) == "cdk.json"))
    mark("terraform", names(lambda p: p.endswith(".tf")))
    cfn = [f for f in files if low[f].endswith((".yml", ".yaml", ".json", ".template"))
           and ("AWSTemplateFormatVersion" in _read(os.path.join(path, f), 200_000)[:4000])]
    mark("cloudformation", cfn)
    mark("sam/serverless", names(lambda p: os.path.basename(p) in ("samconfig.toml", "serverless.yml", "serverless.yaml")))
    mark("pulumi", names(lambda p: os.path.basename(p) in ("pulumi.yaml", "pulumi.yml")))
    sig["iac"] = {"tools": tools, "files": evid[:10]}

    # --- CI (GitHub Actions)
    wfs = names(lambda p: p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml")))
    texts = {w: _read(os.path.join(path, w)) for w in wfs}
    alltxt = "\n".join(texts.values())
    uses = re.findall(r"uses:\s*([\w./-]+)@([\w.-]+)", alltxt)
    pinned = sum(1 for _, ref in uses if re.fullmatch(r"[0-9a-f]{40}", ref))
    sig["ci"] = {
        "workflows": [os.path.basename(w) for w in wfs],
        "oidc_to_aws": bool(re.search(r"role-to-assume", alltxt)),
        "static_aws_keys_in_actions": bool(re.search(r"aws-access-key-id|secrets\.AWS_ACCESS_KEY_ID", alltxt, re.I)),
        "tests": _any(alltxt, [r"\bpytest\b", r"npm (run )?test", r"yarn test", r"pnpm (run )?test", r"go test", r"\bjest\b", r"vitest", r"rspec", r"mvn.* test", r"gradle.* test"]),
        "lint": _any(alltxt, [r"eslint", r"\bruff\b", r"flake8", r"golangci", r"rubocop", r"prettier", r"\bmypy\b", r"\btsc\b"]),
        "sast": _any(alltxt, [r"github/codeql-action", r"semgrep", r"sonarqube|sonarcloud", r"bandit"]),
        "sca_or_image_scan": _any(alltxt, [r"dependency-review-action", r"trivy", r"snyk", r"grype", r"osv-scanner"]),
        "secret_scan": _any(alltxt, [r"gitleaks", r"trufflehog", r"detect-secrets"]),
        "deploy_steps": _any(alltxt, [r"cdk deploy", r"terraform apply", r"ecs update-service", r"amazon-ecs-deploy-task-definition", r"codedeploy", r"sam deploy", r"aws s3 sync"]),
        "preview_diff": _any(alltxt, [r"cdk diff", r"terraform plan"]),
        "environment_protection_used": bool(re.search(r"^\s*environment:", alltxt, re.M)),
        "rollback_hint": _any(alltxt, [r"rollback", r"circuit.?breaker"]),
        "permissions_declared": "permissions:" in alltxt,
        "actions_pinned_by_sha": f"{pinned}/{len(uses)}",
        "pull_request_target_used": "pull_request_target" in alltxt,
        "loadtest_in_ci": _any(alltxt, [r"\bk6\b", r"locust", r"artillery", r"jmeter", r"gatling"]),
        "cost_estimate_in_ci": _any(alltxt, [r"infracost"]),
        "dast_in_ci": _any(alltxt, [r"zaproxy", r"owasp[-/]zap", r"\bnuclei\b", r"burp"]),
        "chaos_in_ci": _any(alltxt, [r"litmus", r"toxiproxy", r"fis start-experiment", r"chaos[-_ ]?(mesh|monkey|toolkit)"]),
    }

    # --- review / branch protection
    sig["review"] = {
        "codeowners": bool(names(lambda p: p in (".github/codeowners", "codeowners", "docs/codeowners"))),
        "pr_template": bool(names(lambda p: "pull_request_template" in p)),
    }

    # --- deps
    locks = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock", "go.sum", "gemfile.lock", "uv.lock", "cargo.lock"]
    sig["deps"] = {
        "dependabot": bool(names(lambda p: p in (".github/dependabot.yml", ".github/dependabot.yaml"))),
        "renovate": bool(names(lambda p: os.path.basename(p) in ("renovate.json", ".renovaterc", ".renovaterc.json") or p == ".github/renovate.json")),
        "lockfiles": sorted({os.path.basename(f) for f in files if os.path.basename(low[f]) in locks}),
    }

    # --- secscan
    sig["secscan"] = {
        "gitleaks_config": bool(names(lambda p: os.path.basename(p) in (".gitleaks.toml", "gitleaks.toml"))),
        "precommit_secret_hook": _any(_read(os.path.join(path, ".pre-commit-config.yaml")), [r"gitleaks", r"detect-secrets"]),
        "security_md": bool(names(lambda p: os.path.basename(p) == "security.md")),
        "codeql_workflow": sig["ci"]["sast"],
    }

    # --- tests
    tdirs = sorted({d for f in files for d in [f.split("/")[0]] if d.lower() in ("tests", "test", "__tests__", "spec", "e2e") and "/" in f})
    tfiles = [f for f in files if re.search(r"(^|/)(test_[^/]+\.py|[^/]+_test\.(py|go)|[^/]+\.(test|spec)\.(js|ts|tsx|jsx))$", f)]
    sig["tests"] = {"dirs": tdirs, "test_files": len(tfiles),
                    "e2e": bool(names(lambda p: any(k in p for k in ("playwright.config", "cypress.config", "cypress/", "e2e/"))))}

    # --- containers
    dfs = names(lambda p: os.path.basename(p).startswith("dockerfile"))
    nonroot = latest = health = 0
    for d in dfs:
        t = _read(os.path.join(path, d))
        nonroot += bool(re.search(r"^\s*USER\s+(?!root\b)\S+", t, re.M | re.I))
        latest += bool(re.search(r"^\s*FROM\s+\S+(:latest)?\s*($|AS)", t, re.M | re.I) and re.search(r"^\s*FROM\s+[^\s:@]+(:latest)?(\s|$)", t, re.M | re.I))
        health += bool(re.search(r"^\s*HEALTHCHECK", t, re.M | re.I))
    sig["container"] = {"dockerfiles": len(dfs), "non_root_user": nonroot, "unpinned_or_latest_base": latest, "healthcheck": health,
                        "dockerignore": bool(names(lambda p: os.path.basename(p) == ".dockerignore"))}

    # --- docs / runbooks / ADR
    def has(*keys):
        return [f for f in files if low[f].endswith((".md", ".mdx", ".txt", ".rst")) and any(k in low[f] for k in keys)][:5]
    sig["docs"] = {
        "readme": bool(names(lambda p: os.path.basename(p).startswith("readme"))),
        "runbooks": has("runbook", "playbook", "oncall", "on-call"),
        "adr": bool(names(lambda p: ("/adr/" in "/" + p or "/decisions/" in "/" + p) and p.endswith(".md"))),
        "postmortems": has("postmortem", "post-mortem", "incident-report"),
        "threat_model": has("threat-model", "threat_model", "threatmodel"),
        "dr_plan": has("dr-plan", "disaster", "recovery-plan", "dr_plan"),
        "raci_or_ownership": has("raci", "ownership", "responsibilit"),
        "escalation": has("escalation"),
        "architecture": has("architecture"),
    }

    def hasre(*keys, exts=(".md", ".mdx", ".txt", ".rst", ".adoc")):
        pats = [re.compile(r"(^|[^a-z0-9])" + re.escape(k) + r"([^a-z0-9]|$)") for k in keys]
        return [f for f in files if low[f].endswith(exts) and any(p.search(low[f]) for p in pats)][:5]
    # Look only at whether a document exists (existing = grounds for a draft; missing = it may be somewhere else, so it is not grounds for anything)
    sig["docs"].update({
        "slo": hasre("slo", "sli", "sla", "error-budget", "availability-target"),
        "readiness_review": hasre("orr", "operational-readiness", "production-readiness", "launch-checklist", "readiness"),
        "retrospective": hasre("retro", "retrospective", "lessons-learned", "lessons", "kpt"),
        "data_classification": hasre("data-classification", "data_classification", "classification"),
        "compliance": hasre("compliance", "isms", "soc2", "pci-dss", "gdpr", "privacy", "appi"),
        "customer_comms": hasre("status-page", "statuspage", "customer-communication", "incident-communication"),
        "training": hasre("training", "onboarding", "security-champion", "secure-coding"),
        "sustainability": hasre("sustainability", "carbon", "green"),
        "cost": hasre("finops", "cost-model", "cost-estimate", "cost"),
        "change_process": hasre("change-management", "change-request", "rfc", "release-process"),
        "pentest": hasre("pentest", "penetration", "vulnerability-assessment"),
        "gameday": hasre("gameday", "game-day", "chaos", "fault-injection"),
        "kpi": hasre("kpi", "okr"),
    })
    sig["ownership"] = {
        "codeowners": sig["review"]["codeowners"],
        "service_catalog": names(lambda p: os.path.basename(p) in ("catalog-info.yaml", "catalog-info.yml", "maintainers", "owners", "owners.md"))[:3],
        "issue_templates": len(names(lambda p: p.startswith(".github/issue_template/") or p == ".github/issue_template.md")),
        "pr_template": sig["review"]["pr_template"],
    }
    res_found = {k: [] for k in IAC_RESOURCES}
    cand = [f for f in files if os.path.splitext(f)[1].lower() in (".tf", ".hcl", ".yml", ".yaml", ".json", ".ts", ".py", ".template")][:3000]
    for f in cand:
        t = _read(os.path.join(path, f), 300_000)
        if not t:
            continue
        for k, pat in IAC_RESOURCES.items():
            if len(res_found[k]) < 3 and re.search(pat, t, re.I):
                res_found[k].append(f)
    sig["iac_resources"] = res_found

    # --- loadtest
    sig["loadtest"] = {"files": names(lambda p: any(k in p for k in ("k6", "locustfile", "artillery", "jmeter", "gatling")))[:5],
                       "in_ci": sig["ci"]["loadtest_in_ci"]}

    # --- hardcoded AWS keys (report counts and paths only, never the value)
    hits = []
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext in TEXT_EXT or os.path.basename(f).startswith(".env"):
            if AWS_KEY_RE.search(_read(os.path.join(path, f), 300_000)):
                hits.append(f)
    sig["hardcoded_secrets"] = {"aws_key_pattern_files": hits[:20], "count": len(hits)}

    if gh_repo:
        sig["github"] = scan_github(gh_repo, runner)
    return {"scanned_at": _now(), "path": path, "files_scanned": len(files), "signals": sig}


# ---------------------------------------------------------------- IaC static scan (no AWS access: `mode iac`)
# IaC is a *declaration*, not the state of the live environment. Terraform / CloudFormation (including SAM and `cdk synth` output)
# is read **as text**, and only a count per resource kind plus flags for the main settings (how many resources declare that setting) is produced.
# Values and secrets are never stored. CDK source (.ts/.py) is not parsed (the `cdk synth` output cdk.out/*.template.json is read when present).
IAC_MAX_FILES, IAC_MAX_BYTES = 3000, 2_000_000
IAC_SKIP_DIRS = SKIP_DIRS - {"cdk.out"}


def _F(cfn=None, tf=None, extra=(), extra_re=None):
    """A flag definition. cfn / tf = regex searched inside the resource body. extra = Terraform types that declare the setting as a separate resource (added to the count)."""
    return {"cfn": cfn, "tf": tf, "extra": tuple(extra), "extra_re": extra_re}


def _cfn_true(k):
    return k + r'"?\s*:\s*"?true\b'


def _tf_true(k):
    return k + r'\s*=\s*"?true\b'


def _cfn_pos(k):
    return k + r'"?\s*:\s*"?[1-9]\d*'


def _tf_pos(k):
    return k + r'\s*=\s*"?[1-9]\d*'


# (key, Terraform types, CloudFormation types, {flag name: _F(...)})
IAC_SPECS = [
    ("s3_bucket", ("aws_s3_bucket",), ("AWS::S3::Bucket",), {
        "public_access_block": _F(cfn=r"PublicAccessBlockConfiguration", extra=("aws_s3_bucket_public_access_block",)),
        "encryption": _F(cfn=r"BucketEncryption", tf=r"server_side_encryption_configuration",
                         extra=("aws_s3_bucket_server_side_encryption_configuration",)),
        "kms_encryption": _F(cfn=r"aws:kms", tf=r"aws:kms", extra=("aws_s3_bucket_server_side_encryption_configuration",), extra_re=r"aws:kms"),
        "versioning": _F(cfn=r'VersioningConfiguration"?\s*:\s*\{?\s*"?Status"?\s*:\s*"?Enabled', tf=r"versioning\s*\{[^}]*enabled\s*=\s*true",
                         extra=("aws_s3_bucket_versioning",), extra_re=r'status\s*=\s*"Enabled"'),
        "lifecycle": _F(cfn=r"LifecycleConfiguration", tf=r"lifecycle_rule", extra=("aws_s3_bucket_lifecycle_configuration",)),
    }),
    ("rds", ("aws_db_instance", "aws_rds_cluster"), ("AWS::RDS::DBInstance", "AWS::RDS::DBCluster"), {
        "storage_encrypted": _F(cfn=_cfn_true("StorageEncrypted"), tf=_tf_true("storage_encrypted")),
        "multi_az": _F(cfn=_cfn_true("MultiAZ"), tf=_tf_true("multi_az")),
        "backup_retention": _F(cfn=_cfn_pos("BackupRetentionPeriod"), tf=_tf_pos("backup_retention_period")),
        "deletion_protection": _F(cfn=_cfn_true("DeletionProtection"), tf=_tf_true("deletion_protection")),
        "publicly_accessible": _F(cfn=_cfn_true("PubliclyAccessible"), tf=_tf_true("publicly_accessible")),
    }),
    ("dynamodb", ("aws_dynamodb_table",), ("AWS::DynamoDB::Table", "AWS::DynamoDB::GlobalTable"), {
        "pitr": _F(cfn=_cfn_true("PointInTimeRecoveryEnabled"), tf=r"point_in_time_recovery\s*\{[^}]*enabled\s*=\s*true"),
    }),
    ("kms_key", ("aws_kms_key",), ("AWS::KMS::Key",), {
        "rotation": _F(cfn=_cfn_true("EnableKeyRotation"), tf=_tf_true("enable_key_rotation")),
    }),
    ("ebs_volume", ("aws_ebs_volume",), ("AWS::EC2::Volume",), {"encrypted": _F(cfn=_cfn_true("Encrypted"), tf=_tf_true("encrypted"))}),
    ("efs", ("aws_efs_file_system",), ("AWS::EFS::FileSystem",), {"encrypted": _F(cfn=_cfn_true("Encrypted"), tf=_tf_true("encrypted"))}),
    ("load_balancer", ("aws_lb", "aws_alb", "aws_elb"),
     ("AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::ElasticLoadBalancing::LoadBalancer"), {}),
    ("lb_listener", ("aws_lb_listener", "aws_alb_listener"), ("AWS::ElasticLoadBalancingV2::Listener",), {
        "https": _F(cfn=r'Protocol"?\s*:\s*"?(HTTPS|TLS)\b', tf=r'protocol\s*=\s*"(HTTPS|TLS)"'),
        "http": _F(cfn=r'Protocol"?\s*:\s*"?HTTP\b', tf=r'protocol\s*=\s*"HTTP"'),
        "redirect": _F(cfn=r'Type"?\s*:\s*"?redirect\b', tf=r'type\s*=\s*"redirect"'),
    }),
    ("waf_web_acl", ("aws_wafv2_web_acl",), ("AWS::WAFv2::WebACL",), {}),
    ("waf_association", ("aws_wafv2_web_acl_association",), ("AWS::WAFv2::WebACLAssociation",), {}),
    ("cloudtrail", ("aws_cloudtrail",), ("AWS::CloudTrail::Trail",), {
        "multi_region": _F(cfn=_cfn_true("IsMultiRegionTrail"), tf=_tf_true("is_multi_region_trail")),
        "log_validation": _F(cfn=_cfn_true("EnableLogFileValidation"), tf=_tf_true("enable_log_file_validation")),
        "kms": _F(cfn=r"KMSKeyId", tf=r"kms_key_id"),
    }),
    ("guardduty", ("aws_guardduty_detector",), ("AWS::GuardDuty::Detector",), {}),
    ("config_recorder", ("aws_config_configuration_recorder",), ("AWS::Config::ConfigurationRecorder",), {}),
    ("securityhub", ("aws_securityhub_account",), ("AWS::SecurityHub::Hub",), {}),
    ("access_analyzer", ("aws_accessanalyzer_analyzer",), ("AWS::AccessAnalyzer::Analyzer",), {}),
    ("backup_plan", ("aws_backup_plan",), ("AWS::Backup::BackupPlan",), {"copy_action": _F(cfn=r"CopyActions", tf=r"copy_action")}),
    ("vpc", ("aws_vpc",), ("AWS::EC2::VPC",), {}),
    ("subnet", ("aws_subnet",), ("AWS::EC2::Subnet",), {}),
    ("nat_gateway", ("aws_nat_gateway",), ("AWS::EC2::NatGateway",), {}),
    ("vpc_flow_log", ("aws_flow_log",), ("AWS::EC2::FlowLog",), {}),
    ("vpc_endpoint", ("aws_vpc_endpoint",), ("AWS::EC2::VPCEndpoint",), {}),
    ("cloudwatch_alarm", ("aws_cloudwatch_metric_alarm", "aws_cloudwatch_composite_alarm"),
     ("AWS::CloudWatch::Alarm", "AWS::CloudWatch::CompositeAlarm"), {"with_actions": _F(cfn=r"AlarmActions", tf=r"alarm_actions")}),
    ("log_group", ("aws_cloudwatch_log_group",), ("AWS::Logs::LogGroup",), {
        "retention": _F(cfn=r'RetentionInDays"?\s*:', tf=_tf_pos("retention_in_days")),
    }),
    ("lambda_function", ("aws_lambda_function",), ("AWS::Lambda::Function", "AWS::Serverless::Function"), {
        "tracing_active": _F(cfn=r'Tracing(Config)?"?\s*:\s*\{?\s*("?Mode"?\s*:\s*)?"?Active\b', tf=r'tracing_config\s*\{[^}]*mode\s*=\s*"Active"'),
        "dead_letter": _F(cfn=r"DeadLetterConfig|DeadLetterQueue", tf=r"dead_letter_config"),
        "reserved_concurrency": _F(cfn=r"ReservedConcurrentExecutions", tf=r"reserved_concurrent_executions\s*=\s*\d"),
    }),
    ("ecs_service", ("aws_ecs_service",), ("AWS::ECS::Service",), {
        "circuit_breaker": _F(cfn=r"DeploymentCircuitBreaker", tf=r"deployment_circuit_breaker"),
        "desired_ge_2": _F(cfn=r'DesiredCount"?\s*:\s*"?([2-9]|[1-9]\d+)\b', tf=r'desired_count\s*=\s*"?([2-9]|[1-9]\d+)\b'),
    }),
    ("autoscaling_group", ("aws_autoscaling_group",), ("AWS::AutoScaling::AutoScalingGroup",), {}),
    ("app_autoscaling_target", ("aws_appautoscaling_target",), ("AWS::ApplicationAutoScaling::ScalableTarget",), {}),
    ("scaling_policy", ("aws_appautoscaling_policy", "aws_autoscaling_policy"),
     ("AWS::ApplicationAutoScaling::ScalingPolicy", "AWS::AutoScaling::ScalingPolicy"), {}),
    ("secret", ("aws_secretsmanager_secret",), ("AWS::SecretsManager::Secret",), {}),
    ("secret_rotation", ("aws_secretsmanager_secret_rotation",), ("AWS::SecretsManager::RotationSchedule",), {}),
    ("ecr_repository", ("aws_ecr_repository",), ("AWS::ECR::Repository",), {"scan_on_push": _F(cfn=_cfn_true("ScanOnPush"), tf=_tf_true("scan_on_push"))}),
    ("route53_health_check", ("aws_route53_health_check",), ("AWS::Route53::HealthCheck",), {}),
    ("iam", ("aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy", "aws_iam_role"),
     ("AWS::IAM::Policy", "AWS::IAM::ManagedPolicy", "AWS::IAM::Role"), {
         # Action is just * (approximation: a * in the second or later position, as in ["s3:*", "*"], is not picked up)
         "wildcard_action": _F(cfn=r'''["']?Action["']?\s*[:=]\s*(?:\[\s*)?(?:-\s*)?["']\*["']''', tf=r'''["']?Action["']?\s*[:=]\s*(?:\[\s*)?["']\*["']'''),
         "administrator_access": _F(cfn=r"policy/AdministratorAccess\b", tf=r"policy/AdministratorAccess\b",
                                    extra=("aws_iam_role_policy_attachment", "aws_iam_user_policy_attachment", "aws_iam_group_policy_attachment"),
                                    extra_re=r"policy/AdministratorAccess\b"),
     }),
    ("iam_user", ("aws_iam_user",), ("AWS::IAM::User",), {}),
]

_WORLD_RE = re.compile(r"0\.0\.0\.0/0|::/0")


def _match_brace(t, i):
    """When t[i] is '{', return the position of the matching '}' (braces inside strings and heredocs are ignored; len(t) if it is never closed)."""
    depth, n = 0, len(t)
    while i < n:
        c = t[i]
        if c == '"':
            i += 1
            while i < n and t[i] != '"':
                i += 2 if t[i] == "\\" else 1
        elif c == "<" and t.startswith("<<", i):
            m = re.match(r"<<-?\s*([A-Za-z_]\w*)[ \t]*\n", t[i:i + 80])
            if m:
                em = re.compile(r"^[ \t]*" + re.escape(m.group(1)) + r"[ \t]*$", re.M).search(t, i + m.end())
                i = em.end() if em else n
                continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _tf_blocks(text, rel):
    text = "\n".join(ln for ln in text.splitlines() if not re.match(r"\s*(#|//)", ln))   # resources on comment lines are not counted
    out = []
    for m in re.finditer(r'^[ \t]*resource\s+"(aws_[\w-]*)"\s+"([^"]+)"\s*\{', text, re.M):
        end = _match_brace(text, m.end() - 1)
        out.append({"kind": "tf", "type": m.group(1), "name": m.group(2), "file": rel, "text": text[m.end():end]})
    return out


def _cfn_json_blocks(text, rel):
    try:
        doc = json.loads(text)
    except ValueError:
        return []
    res = doc.get("Resources") if isinstance(doc, dict) else None
    if not isinstance(res, dict):
        return []
    return [{"kind": "cfn", "type": r["Type"], "name": n, "file": rel, "text": json.dumps(r, indent=1, ensure_ascii=False)}
            for n, r in res.items() if isinstance(r, dict) and str(r.get("Type", "")).startswith("AWS::")]


def _cfn_yaml_blocks(text, rel):
    """Without PyYAML: split the entries directly under Resources: by indentation (tags such as !Ref do not matter). Flow-style {Type: ...} is not read."""
    lines = text.splitlines()
    start = next((i + 1 for i, ln in enumerate(lines) if re.match(r"^Resources\s*:\s*(#.*)?$", ln)), None)
    if start is None:
        return []
    blocks, cur, res_indent = [], None, None
    for ln in lines[start:]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            if cur is not None:
                cur["lines"].append(ln)
            continue
        ind = len(ln) - len(ln.lstrip(" "))
        if ind == 0:
            break
        if res_indent is None:
            res_indent = ind
        m = re.match(r"^ *['\"]?([\w.:-]+)['\"]?\s*:\s*(#.*)?$", ln) if ind == res_indent else None
        if m:
            cur = {"name": m.group(1), "lines": []}
            blocks.append(cur)
        elif cur is not None:
            cur["lines"].append(ln)
    out = []
    for b in blocks:
        body = [ln for ln in b["lines"] if ln.strip() and not ln.lstrip().startswith("#")]
        if not body:
            continue
        mi = min(len(ln) - len(ln.lstrip(" ")) for ln in body)
        tm = re.search(r"^ {%d}Type\s*:\s*['\"]?([\w:]+)" % mi, "\n".join(body), re.M)
        if tm and tm.group(1).startswith("AWS::"):
            out.append({"kind": "cfn", "type": tm.group(1), "name": b["name"], "file": rel, "text": "\n".join(b["lines"])})
    return out


def _iac_files(path):
    out = []
    for root, dirs, fs in os.walk(path):
        dirs[:] = [d for d in dirs if d not in IAC_SKIP_DIRS and not d.startswith("asset.")]
        in_cdk_out = "cdk.out" in os.path.relpath(root, path).split(os.sep)
        for f in fs:
            low = f.lower()
            if (not low.endswith(".template.json")) if in_cdk_out else (not low.endswith((".tf", ".yml", ".yaml", ".json", ".template"))):
                continue
            out.append(os.path.join(root, f))
            if len(out) >= IAC_MAX_FILES:
                return out
    return out


def _world_ingress(b):
    """(whether 0.0.0.0/0 or ::/0 is opened anywhere, whether any of that is a port other than 80/443). The port is approximated from a nearby FromPort/ToPort."""
    t, typ = b["text"], b["type"]
    parts = []
    if b["kind"] == "tf":
        if typ == "aws_security_group":
            parts = [t[m.end():_match_brace(t, m.end() - 1)] for m in re.finditer(r"^[ \t]*ingress\s*\{", t, re.M)]
        elif typ == "aws_security_group_rule" and re.search(r'\btype\s*=\s*"ingress"', t):
            parts = [t]
        elif typ == "aws_vpc_security_group_ingress_rule":
            parts = [t]
    elif typ == "AWS::EC2::SecurityGroupIngress":
        parts = [t]
    elif typ == "AWS::EC2::SecurityGroup":
        m = re.search(r"SecurityGroupIngress", t)
        if m:
            seg = t[m.end():]
            e = re.search(r"SecurityGroupEgress", seg)
            parts = re.split(r"(?m)^\s*-\s+|^\s*\{\s*$", seg[:e.start()] if e else seg)
    anyw = nonweb = False
    for p in parts:
        if not _WORLD_RE.search(p):
            continue
        anyw = True
        ports = [int(x) for x in re.findall(r"(?:from_?port|to_?port)\W+(\d+)", p, re.I)]
        if not ports or any(x not in (80, 443) for x in ports):
            nonweb = True
    return anyw, nonweb


def scan_iac(path="."):
    """Statically read IaC (Terraform / CloudFormation / `cdk synth` output) and return a count per resource kind plus setting flags. Never touches AWS."""
    path = os.path.abspath(path)
    blocks, src, notes = [], {"terraform_files": 0, "cfn_templates": 0, "cdk_out_templates": 0}, []
    files, large = _iac_files(path), 0
    for fp in files:
        rel = os.path.relpath(fp, path)
        low = rel.lower()
        try:
            if os.path.getsize(fp) > IAC_MAX_BYTES:
                large += 1
                continue
        except OSError:
            continue
        text = _read(fp, IAC_MAX_BYTES)
        if not text:
            continue
        in_cdk_out = "cdk.out" in rel.split(os.sep)
        if low.endswith(".tf"):
            bs = _tf_blocks(text, rel)
            src["terraform_files"] += bool(bs)
        elif "AWS::" not in text or "Resources" not in text:
            continue
        elif low.endswith(".json") or text.lstrip().startswith("{"):
            bs = _cfn_json_blocks(text, rel)
            src["cdk_out_templates" if in_cdk_out else "cfn_templates"] += bool(bs)
        else:
            bs = _cfn_yaml_blocks(text, rel)
            src["cfn_templates"] += bool(bs)
        blocks.extend(bs)
    if large:
        notes.append(f"{large} file(s) were not read because they exceed {IAC_MAX_BYTES // 1_000_000}MB")
    if os.path.exists(os.path.join(path, "cdk.json")) and not src["cdk_out_templates"]:
        notes.append("cdk.json exists but there are no templates in cdk.out. CDK source (.ts/.py) is not parsed. If you have the `cdk synth` output, rescan to read it (this scanner does not run synth)")
    if not blocks:
        notes.append("no readable resource definitions were found (only Terraform .tf files and CloudFormation / SAM / cdk synth templates are read)")
    by = collections.defaultdict(list)
    for b in blocks:
        by[(b["kind"], b["type"])].append(b)
    res = {}
    for key, tf_types, cfn_types, flags in IAC_SPECS:
        mine = [b for t in tf_types for b in by.get(("tf", t), [])] + [b for t in cfn_types for b in by.get(("cfn", t), [])]
        if not mine:
            continue
        entry = {"count": len(mine), "files": sorted({b["file"] for b in mine})[:3]}
        fl = {}
        for name, f in flags.items():
            n = sum(1 for b in mine if (f["tf"] if b["kind"] == "tf" else f["cfn"]) and re.search(f["tf"] if b["kind"] == "tf" else f["cfn"], b["text"], re.I))
            n += sum(1 for t in f["extra"] for b in by.get(("tf", t), []) if not f["extra_re"] or re.search(f["extra_re"], b["text"], re.I))
            fl[name] = n
        if fl:
            entry["flags"] = fl
        res[key] = entry
    sgs = [b for t in ("aws_security_group", "aws_security_group_rule", "aws_vpc_security_group_ingress_rule") for b in by.get(("tf", t), [])] + \
          [b for t in ("AWS::EC2::SecurityGroup", "AWS::EC2::SecurityGroupIngress") for b in by.get(("cfn", t), [])]
    if sgs:
        w = [_world_ingress(b) for b in sgs]
        main = by.get(("tf", "aws_security_group"), []) + by.get(("cfn", "AWS::EC2::SecurityGroup"), [])
        res["security_group"] = {"count": len(main), "files": sorted({b["file"] for b in sgs})[:3],
                                 "flags": {"world_ingress": sum(x[0] for x in w), "world_ingress_non_web": sum(x[1] for x in w)}}
    tools = [n for n, ok in (("terraform", src["terraform_files"]), ("cloudformation", src["cfn_templates"]),
                             ("cdk(synth)", src["cdk_out_templates"]),
                             ("sam", any(t.startswith("AWS::Serverless::") for _, t in by))) if ok]
    return {"scanned_at": _now(), "path": path, "files_scanned": len(files), "sources": src, "tools": tools, "notes": notes,
            "resources": res}


def _gh(args, runner):
    if not shutil.which("gh") and runner is subprocess.run:
        return {"status": "unavailable", "error": "gh CLI not installed"}
    try:
        p = runner(["gh", "api", *args], capture_output=True, text=True, timeout=20)
    except Exception as e:  # noqa: BLE001
        return {"status": "unavailable", "error": str(e)[:120]}
    if p.returncode != 0:
        return {"status": "error", "error": (p.stderr or "").strip()[:160]}
    try:
        return {"status": "ok", "data": json.loads(p.stdout) if p.stdout.strip() else None}
    except ValueError:
        return {"status": "ok", "data": (p.stdout or "").strip()[:160]}


def scan_github(repo, runner=subprocess.run):
    """Read-only GET calls through `gh api` (needs `gh auth login`; some calls need admin permission)."""
    out = {"repo": repo}
    info = _gh([f"repos/{repo}"], runner)
    out["repo_info"] = info["status"]
    branch = "main"
    if info["status"] == "ok" and isinstance(info["data"], dict):
        d = info["data"]
        branch = d.get("default_branch", "main")
        sa = d.get("security_and_analysis") or {}
        out.update({"visibility": d.get("visibility"), "default_branch": branch,
                    "secret_scanning": (sa.get("secret_scanning") or {}).get("status"),
                    "push_protection": (sa.get("secret_scanning_push_protection") or {}).get("status"),
                    "dependabot_security_updates": (sa.get("dependabot_security_updates") or {}).get("status")})
    bp = _gh([f"repos/{repo}/branches/{branch}/protection"], runner)
    if bp["status"] == "ok" and isinstance(bp["data"], dict):
        d = bp["data"]
        rv = d.get("required_pull_request_reviews") or {}
        out["branch_protection"] = {
            "enabled": True, "required_approvals": rv.get("required_approving_review_count", 0),
            "status_checks_required": bool((d.get("required_status_checks") or {}).get("contexts") or (d.get("required_status_checks") or {}).get("checks")),
            "force_push_allowed": (d.get("allow_force_pushes") or {}).get("enabled"), "enforce_admins": (d.get("enforce_admins") or {}).get("enabled")}
    else:
        out["branch_protection"] = {"enabled": False if "404" in str(bp.get("error", "")) or "not protected" in str(bp.get("error", "")).lower() else None,
                                    "note": bp.get("error", bp["status"])}
    rs = _gh([f"repos/{repo}/rulesets"], runner)
    out["rulesets"] = len(rs["data"]) if rs["status"] == "ok" and isinstance(rs["data"], list) else rs["status"]
    va = _gh([f"repos/{repo}/vulnerability-alerts"], runner)
    out["dependabot_alerts_enabled"] = (va["status"] == "ok")
    out["activity"] = scan_github_activity(repo, runner)
    return out


# Keyword patterns for issue / PR labels. The Japanese words are deliberate: they match labels used in Japanese-language repositories.
LABEL_GROUPS = {
    "incident": r"incident|outage|障害|sev[-_ ]?\d|\bp[0-3]\b",
    "postmortem": r"post-?mortem|\brca\b|retro|振り返り",
    "improvement": r"tech[-_ ]?debt|improvement|refactor|技術的負債|改善",
    "priority": r"priority|severity|sev[-_ ]?\d|優先",
}


def _list(resp):
    return resp["data"] if resp.get("status") == "ok" and isinstance(resp.get("data"), list) else None


def scan_github_activity(repo, runner=subprocess.run, days=90):
    """Counts only (no titles/bodies/people): how the team actually works. Read-only GETs through `gh api`."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"window_days": days}
    rel = _list(_gh([f"repos/{repo}/releases?per_page=100"], runner))
    if rel is not None:
        out["releases_in_window"] = sum(1 for r in rel if (r.get("published_at") or r.get("created_at") or "") >= cutoff)
    dep = _list(_gh([f"repos/{repo}/deployments?per_page=100"], runner))
    if dep is not None:
        recent = [d for d in dep if (d.get("created_at") or "") >= cutoff]
        out["deployments_in_window"] = len(recent)
        out["deployment_environments"] = sorted({d.get("environment", "") for d in recent if d.get("environment")})[:6]
    prs = _list(_gh([f"repos/{repo}/pulls?state=closed&per_page=100&sort=updated&direction=desc"], runner))
    if prs is not None:
        merged = [p for p in prs if (p.get("merged_at") or "") >= cutoff]
        out["merged_prs_in_window"] = len(merged)
        out["dependency_bot_prs_in_window"] = sum(1 for p in merged if re.search(r"dependabot|renovate", (p.get("user") or {}).get("login", ""), re.I))
    labels = _list(_gh([f"repos/{repo}/labels?per_page=100"], runner))
    if labels is not None:
        names = [l.get("name", "") for l in labels]
        found = {g: [n for n in names if re.search(p, n, re.I)] for g, p in LABEL_GROUPS.items()}
        out["labels"] = {g: len(v) for g, v in found.items()}
        for g in ("incident", "postmortem", "improvement"):
            tot, seen = 0, False
            for lab in found[g][:3]:
                iss = _list(_gh([f"repos/{repo}/issues?state=all&labels={urllib.parse.quote(lab)}&since={cutoff}&per_page=100"], runner))
                if iss is not None:
                    seen = True
                    tot += sum(1 for x in iss if "pull_request" not in x)
            if seen:
                out[f"issues_{g}_in_window"] = tot
    comm = _gh([f"repos/{repo}/community/profile"], runner)
    if comm["status"] == "ok" and isinstance(comm.get("data"), dict):
        files = comm["data"].get("files") or {}
        out["community"] = {k: bool(files.get(k)) for k in ("contributing", "issue_template", "pull_request_template", "code_of_conduct")}
    return out


# ---------------------------------------------------------------- AWS scan
READ_RE = re.compile(r"^(list|describe|get|batch_get|search|lookup)_")
DENY_RE = re.compile(r"(secret_value|^get_parameters?(_by_path|_history)?$|password_data|token|^get_session|federation|"
                     r"credentials_for_identity|private_key|download|presign|^get_object|^get_function(_configuration|_url_config)?$|"
                     r"^describe_instance_attribute$|launch_template|^get_credential_report$|^get_ssh|^get_service_credentials|"
                     # Reads that return data bodies, log text or credentials. The scanner never calls them (the IAM Deny is DENY_ACTIONS).
                     # get_api_key(s) cannot be denied in IAM because apigateway:GET covers every GET, so only the code guard blocks it
                     r"^(batch_)?get_item$|^get_records$|^get_log_events$|^get_query_results$|^get_template$|^get_blob$|"
                     r"^get_console_output$|^get_cluster_credentials|^get_credentials$|^get_instance_access_details$|^get_api_keys?$|"
                     r"^get_file$|^batch_get_builds$|^get_execution_history$|^get_log_record$)")
# The IAM actions that correspond to the value-returning reads the scanner forbids (DENY_RE).
# Even when a broad managed policy such as ReadOnlyAccess / SecurityAudit includes them, these deny the known reads of values and bodies (not exhaustive).
# `kensa.py iam-policy --kind scan-deny` prints the extra Deny. A test pins that every key is blocked by ReadOnlyClient.
DENY_ACTIONS = {
    "get_secret_value": ["secretsmanager:GetSecretValue"],
    "batch_get_secret_value": ["secretsmanager:BatchGetSecretValue"],
    "get_parameter": ["ssm:GetParameter"],
    "get_parameters": ["ssm:GetParameters"],
    "get_parameters_by_path": ["ssm:GetParametersByPath"],
    "get_parameter_history": ["ssm:GetParameterHistory"],
    "get_password_data": ["ec2:GetPasswordData"],
    "get_object": ["s3:GetObject", "s3:GetObjectVersion"],
    "get_function": ["lambda:GetFunction"],
    "get_function_configuration": ["lambda:GetFunctionConfiguration"],
    "get_credential_report": ["iam:GetCredentialReport"],
    "get_authorization_token": ["ecr:GetAuthorizationToken"],
    "get_download_url_for_layer": ["ecr:GetDownloadUrlForLayer"],
    "get_item": ["dynamodb:GetItem"],
    "batch_get_item": ["dynamodb:BatchGetItem"],
    "get_records": ["kinesis:GetRecords"],
    "get_log_events": ["logs:GetLogEvents"],
    "get_query_results": ["logs:GetQueryResults", "athena:GetQueryResults"],
    "get_template": ["cloudformation:GetTemplate"],
    "get_blob": ["codecommit:GetBlob"],
    "get_console_output": ["ec2:GetConsoleOutput"],
    "get_cluster_credentials": ["redshift:GetClusterCredentials", "redshift:GetClusterCredentialsWithIAM"],
    "get_credentials": ["redshift-serverless:GetCredentials"],
    "get_instance_access_details": ["lightsail:GetInstanceAccessDetails"],
    "get_file": ["codecommit:GetFile"],
    "batch_get_builds": ["codebuild:BatchGetBuilds"],
    "get_execution_history": ["states:GetExecutionHistory"],
    "get_log_record": ["logs:GetLogRecord"],
}
# Reads of data bodies that the scanner never calls but that a broad read policy may include (denying them is harmless if they are not included).
EXTRA_DENY_ACTIONS = ["dynamodb:Query", "dynamodb:Scan", "sqs:ReceiveMessage", "kms:Decrypt",
                      "logs:FilterLogEvents", "logs:StartQuery", "codecommit:GitPull"]


def scan_deny_policy():
    acts = sorted({a for v in DENY_ACTIONS.values() for a in v} | set(EXTRA_DENY_ACTIONS))
    return {"Version": "2012-10-17",
            "Statement": [{"Sid": "KensaDenySecretAndDataReads", "Effect": "Deny", "Action": acts, "Resource": "*"}]}


EXTRA_OK = {"can_paginate", "get_paginator", "get_waiter", "meta", "exceptions", "close"}
LIMIT = 200
GLOBAL_REGION = "us-east-1"


class ReadOnlyClient:
    """Proxy that refuses anything except list/describe/get read APIs (and secret-bearing reads)."""

    def __init__(self, real, service):
        self._real, self._svc = real, service

    @staticmethod
    def allowed(name):
        return bool(READ_RE.match(name)) and not DENY_RE.search(name)

    def __getattr__(self, name):
        if name in EXTRA_OK:
            attr = getattr(self._real, name)
            if name == "get_paginator":
                def gp(op):
                    if not ReadOnlyClient.allowed(op):
                        raise PermissionError(f"blocked non-read operation: {self._svc}.{op}")
                    return attr(op)
                return gp
            return attr
        if not self.allowed(name):
            raise PermissionError(f"blocked non-read operation: {self._svc}.{name}")
        return getattr(self._real, name)


def make_session(profile=None, region=None):
    try:
        import boto3
    except ImportError:
        raise SystemExit("boto3 not found. Run `pip install boto3`.")
    try:
        return boto3.Session(profile_name=profile, region_name=region)
    except Exception as e:  # noqa: BLE001  an unknown profile name raises ProfileNotFound as soon as the Session is created
        if type(e).__name__ in _AUTH_ERRORS:
            raise AuthError(_auth_message(e, profile)) from None
        raise


# ---- credentials pre-flight
# The scan swallows failures per check and carries on, so running it without (or with expired) credentials
# would end with every check in error yet report "done". Before touching AWS, check the credentials and the target account with
# STS GetCallerIdentity (no permission needed) and stop here if that fails. How to authenticate (SSO, environment variables,
# assume-role, instance role) is left to boto3's standard lookup order; there is no per-method branching.
_SSO_ERRORS = {"TokenRetrievalError", "UnauthorizedSSOTokenError", "SSOTokenLoadError"}
_AUTH_ERRORS = _SSO_ERRORS | {"NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError", "ProfileNotFound"}
_AUTH_CODES = re.compile(r"ExpiredToken|InvalidClientTokenId|UnrecognizedClient|SignatureDoesNotMatch|InvalidSignature|"
                         r"IncompleteSignature|AuthFailure|RequestExpired|InvalidToken|MissingAuthenticationToken")


class AuthError(Exception):
    """The credentials cannot be used, or the target is not an expected account. str(e) is a message that includes the next step."""


def principal_type(arn):
    return "root" if arn.endswith(":root") else "assumed-role" if ":assumed-role/" in arn else "user" if ":user/" in arn else "other"


def _auth_message(e, profile):
    name = type(e).__name__
    code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
    detail = str(getattr(e, "response", {}).get("Error", {}).get("Message", "") or e)[:200]
    flag = f" --profile {profile}" if profile else ""
    login = f"aws sso login{flag}" if profile else "aws sso login --profile <profile-name>"
    if name in _SSO_ERRORS:
        why, fix = ("the SSO session has expired or you are not logged in",
                    f"run `{login}` and try again (it needs a browser, so the user runs it themselves)")
    elif name == "ProfileNotFound":
        why, fix = (f"profile {profile!r} was not found", "check the name with `aws configure list-profiles`")
    elif name == "NoCredentialsError":
        why, fix = ("no credentials were found",
                    "pass `--profile`, or set `AWS_PROFILE` or environment variables (such as `AWS_ACCESS_KEY_ID`). "
                    f"For an SSO profile, run `{login}` first")
    elif name == "PartialCredentialsError":
        why, fix = ("the credentials are incomplete", "check that the access key and secret (and the session token, if needed) are all set")
    elif name in _AUTH_ERRORS or _AUTH_CODES.search(code):
        why, fix = ("the credentials are invalid or have expired",
                    f"get temporary credentials again (for SSO, `{login}`; for assume-role, run it again)")
    else:
        why, fix = ("cannot connect to AWS", "check the network, proxy and region settings")
    return f"{why}: {name}{': ' + code if code and code != name else ''} ({detail}). {fix}"


def preflight(session, expected_accounts=None, profile=None, region=None):
    """Check the credentials with STS GetCallerIdentity. Returns {"account", "arn", "principal_type"}; raises AuthError if they cannot be used.

    If expected_accounts (the context's aws.account_ids) is given and the target is not one of them, stop
    (this prevents reading the wrong account in an environment with several profiles)."""
    try:
        r = session.client("sts", region_name=region or GLOBAL_REGION).get_caller_identity()
    except Exception as e:  # noqa: BLE001
        raise AuthError(_auth_message(e, profile)) from None
    account, arn = str(r.get("Account", "")), r.get("Arn", "")
    exp = [str(x).strip() for x in (expected_accounts or []) if str(x).strip()]
    if exp and account not in exp:
        raise AuthError(f"the target account {account} is not among the expected accounts {', '.join(exp)} (the context's aws.account_ids). "
                        "Pass a different profile, or if this target is intended, update it with `context set aws.account_ids='[\"…\"]'`")
    return {"account": account, "arn": arn, "principal_type": principal_type(arn)}


def classify_error(e):
    code = getattr(e, "response", {}).get("Error", {}).get("Code", "") or type(e).__name__
    msg = str(getattr(e, "response", {}).get("Error", {}).get("Message", "") or e)[:160]
    if isinstance(e, PermissionError):
        return "blocked", code, msg
    if re.search(r"AccessDenied|UnauthorizedOperation|AuthorizationError|Forbidden|NotAuthorized", code):
        # Some services that are not enabled, such as Macie, return AccessDeniedException ("Macie is not enabled").
        # Mixing that up with a permission problem would give the wrong advice ("add permissions"); confirmed by scanning a real account.
        if re.search(r"not (been )?(enabled|subscribed|enrolled)", msg, re.I):
            return "not_enabled", code, msg
        return "denied", code, msg
    if re.search(r"SubscriptionRequired|OptInRequired|NotSubscribed|InvalidAccessException|AWSOrganizationsNotInUse|"
                 r"ResourceNotFound|NoSuch|NotFound|NotEnabled|NotEnrolled", code):
        return "not_enabled", code, msg
    return "error", code, msg


class Scanner:
    def __init__(self, session, regions, account=None, limit=LIMIT):
        self.session, self.regions, self.account, self.limit = session, regions, account, limit
        self._cache = {}

    def c(self, service, region=None):
        region = region or self.regions[0]
        key = (service, region)
        if key not in self._cache:
            self._cache[key] = ReadOnlyClient(self.session.client(service, region_name=region), service)
        return self._cache[key]

    def g(self, service):
        return self.c(service, GLOBAL_REGION)

    def pages(self, client, op, key, **kw):
        items = []
        if client.can_paginate(op):
            for page in client.get_paginator(op).paginate(**kw):
                items.extend(page.get(key, []))
                if len(items) >= self.limit:
                    break
        else:
            items = getattr(client, op)(**kw).get(key, [])
        return items[: self.limit]


CHECKS = []


def check(cid, areas, scope="global"):
    def deco(fn):
        CHECKS.append({"id": cid, "areas": areas.split(","), "scope": scope, "fn": fn})
        return fn
    return deco


def _age_days(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - dt).days


# ---- identity / org / IAM
@check("identity", "org,iam")
def _identity(S):
    r = S.g("sts").get_caller_identity()
    S.account = r["Account"]
    return {"account": r["Account"], "principal_type": principal_type(r.get("Arn", ""))}


@check("organizations", "org")
def _org(S):
    o = S.g("organizations")
    try:
        d = o.describe_organization()["Organization"]
    except Exception as e:  # noqa: BLE001
        if "NotInUse" in str(getattr(e, "response", {}).get("Error", {}).get("Code", "")):
            return {"in_org": False}
        raise
    out = {"in_org": True, "feature_set": d.get("FeatureSet"), "is_management_account": d.get("MasterAccountId") == S.account}
    try:
        out["scp_count"] = len(o.list_policies(Filter="SERVICE_CONTROL_POLICY").get("Policies", []))
    except Exception as e:  # noqa: BLE001
        out["scp_count"] = classify_error(e)[0]
    return out


@check("iam_account", "iam,root")
def _iam_account(S):
    iam = S.g("iam")
    m = iam.get_account_summary().get("SummaryMap", {})
    out = {"root_mfa_enabled": bool(m.get("AccountMFAEnabled")), "root_access_keys_present": bool(m.get("AccountAccessKeysPresent")),
           "users": m.get("Users"), "roles": m.get("Roles"), "groups": m.get("Groups")}
    try:
        pp = iam.get_account_password_policy()["PasswordPolicy"]
        out["password_policy"] = {"min_length": pp.get("MinimumPasswordLength"), "require_symbols": pp.get("RequireSymbols"),
                                  "max_age": pp.get("MaxPasswordAge")}
    except Exception as e:  # noqa: BLE001
        out["password_policy"] = None if classify_error(e)[0] == "not_enabled" else classify_error(e)[0]
    return out


@check("iam_users", "iam")
def _iam_users(S):
    iam = S.g("iam")
    users = S.pages(iam, "list_users", "Users")
    keys_users = old_keys = console_no_mfa = 0
    for u in users[:100]:
        n = u["UserName"]
        ak = [k for k in iam.list_access_keys(UserName=n).get("AccessKeyMetadata", []) if k.get("Status") == "Active"]
        keys_users += bool(ak)
        old_keys += sum(1 for k in ak if (_age_days(k.get("CreateDate")) or 0) > 90)
        try:
            iam.get_login_profile(UserName=n)
            console = True
        except Exception as e:  # noqa: BLE001
            # Exclude only when there is no login profile (= no console use). A permission problem and the like must not look like '0 users'; record the whole check as failed
            if getattr(e, "response", {}).get("Error", {}).get("Code") != "NoSuchEntity":
                raise
            console = False
        if console and not iam.list_mfa_devices(UserName=n).get("MFADevices"):
            console_no_mfa += 1
    return {"users_total": len(users), "users_with_active_keys": keys_users, "active_keys_older_than_90d": old_keys,
            "console_users_without_mfa": console_no_mfa, "sampled": min(len(users), 100)}


@check("federation", "iam,iac-aws")
def _federation(S):
    iam = S.g("iam")
    oidc = [p["Arn"].rsplit("/", 1)[-1] for p in iam.list_open_id_connect_providers().get("OpenIDConnectProviderList", [])]
    saml = len(iam.list_saml_providers().get("SAMLProviderList", []))
    out = {"oidc_providers": oidc, "github_actions_oidc": any("token.actions.githubusercontent.com" in o for o in oidc), "saml_providers": saml}
    try:
        out["identity_center_instances"] = len(S.c("sso-admin").list_instances().get("Instances", []))
    except Exception as e:  # noqa: BLE001
        out["identity_center_instances"] = classify_error(e)[0]
    return out


# ---- detection / logging
@check("access_analyzer", "threat-detect,iam", "regional")
def _aa(S, r):
    an = S.pages(S.c("accessanalyzer", r), "list_analyzers", "analyzers")
    return {"analyzers": [{"type": a.get("type"), "status": a.get("status")} for a in an]}


@check("cloudtrail", "cloudtrail", "regional")
def _ct(S, r):
    ct = S.c("cloudtrail", r)
    trails = ct.describe_trails(includeShadowTrails=False).get("trailList", [])
    out = []
    for t in trails[:20]:
        logging_on = None
        try:
            logging_on = ct.get_trail_status(Name=t["TrailARN"]).get("IsLogging")
        except Exception:  # noqa: BLE001
            pass
        out.append({"multi_region": t.get("IsMultiRegionTrail"), "log_validation": t.get("LogFileValidationEnabled"),
                    "kms": bool(t.get("KmsKeyId")), "org_trail": t.get("IsOrganizationTrail"), "is_logging": logging_on})
    return {"trails": out}


@check("config", "config", "regional")
def _config(S, r):
    c = S.c("config", r)
    rec = c.describe_configuration_recorders().get("ConfigurationRecorders", [])
    st = c.describe_configuration_recorder_status().get("ConfigurationRecordersStatus", [])
    out = {"recorders": len(rec), "recording": any(s.get("recording") for s in st),
           "all_supported": any((x.get("recordingGroup") or {}).get("allSupported") for x in rec)}
    out["rules"] = len(S.pages(c, "describe_config_rules", "ConfigRules"))
    try:
        out["conformance_packs"] = len(c.describe_conformance_packs().get("ConformancePackDetails", []))
    except Exception as e:  # noqa: BLE001
        out["conformance_packs"] = classify_error(e)[0]
    return out


@check("guardduty", "threat-detect", "regional")
def _gd(S, r):
    g = S.c("guardduty", r)
    ids = g.list_detectors().get("DetectorIds", [])
    if not ids:
        return {"enabled": False}
    d = g.get_detector(DetectorId=ids[0])
    return {"enabled": d.get("Status") == "ENABLED", "features_enabled": sorted(f["Name"] for f in d.get("Features", []) if f.get("Status") == "ENABLED")}


@check("securityhub", "threat-detect", "regional")
def _sh(S, r):
    sh = S.c("securityhub", r)
    sh.describe_hub()
    return {"subscribed": True, "standards": [_standard_name(s.get("StandardsArn", "")) for s in sh.get_enabled_standards().get("StandardsSubscriptions", [])]}


def _standard_name(arn):
    """arn:aws:securityhub:REGION::standards/aws-foundational-security-best-practices/v/1.0.0 → aws-foundational-security-best-practices
    (the older CIS form is ::ruleset/cis-aws-foundations-benchmark/v/1.2.0). Cutting from the end would pick up the version's `v`."""
    m = re.search(r"(?:standards|ruleset)/([^/]+)", arn or "")
    return m.group(1) if m else (arn or "")


@check("inspector", "threat-detect,compute", "regional")
def _insp(S, r):
    i = S.c("inspector2", r)
    kw = {"accountIds": [S.account]} if S.account else {}
    accts = i.batch_get_account_status(**kw).get("accounts", [])
    rs = (accts[0].get("resourceState") if accts else None) or {}
    return {k: (v or {}).get("status") for k, v in rs.items()}


@check("macie", "threat-detect,s3", "regional")
def _macie(S, r):
    return {"status": S.c("macie2", r).get_macie_session().get("status")}


# ---- data protection
@check("kms", "kms", "regional")
def _kms(S, r):
    k = S.c("kms", r)
    keys = S.pages(k, "list_keys", "Keys")[:100]
    cust = rot = 0
    for x in keys:
        md = k.describe_key(KeyId=x["KeyId"]).get("KeyMetadata", {})
        if md.get("KeyManager") == "CUSTOMER" and md.get("KeyState") == "Enabled" and md.get("KeySpec", "SYMMETRIC_DEFAULT") == "SYMMETRIC_DEFAULT":
            cust += 1
            try:
                rot += bool(k.get_key_rotation_status(KeyId=x["KeyId"]).get("KeyRotationEnabled"))
            except Exception:  # noqa: BLE001
                pass
    return {"customer_managed_keys": cust, "with_rotation": rot}


@check("secrets", "secrets", "regional")
def _secrets(S, r):
    sm = S.pages(S.c("secretsmanager", r), "list_secrets", "SecretList")
    ps = S.pages(S.c("ssm", r), "describe_parameters", "Parameters")
    return {"secretsmanager_total": len(sm), "rotation_enabled": sum(1 for s in sm if s.get("RotationEnabled")),
            "ssm_parameters": len(ps), "ssm_secure_string": sum(1 for p in ps if p.get("Type") == "SecureString"),
            "ssm_plain_string": sum(1 for p in ps if p.get("Type") == "String")}


@check("tls_acm", "tls", "regional")
def _acm(S, r):
    a = S.c("acm", r)
    certs = S.pages(a, "list_certificates", "CertificateSummaryList")
    expiring = imported = 0
    for c in certs[:50]:
        d = a.describe_certificate(CertificateArn=c["CertificateArn"]).get("Certificate", {})
        imported += d.get("Type") == "IMPORTED"
        na = d.get("NotAfter")
        if na and _age_days(na) is not None and _age_days(na) > -30:
            expiring += 1
    return {"certificates": len(certs), "imported": imported, "expiring_within_30d": expiring}


@check("elb", "tls,network,compute", "regional")
def _elb(S, r):
    e = S.c("elbv2", r)
    lbs = S.pages(e, "describe_load_balancers", "LoadBalancers")
    out = []
    for lb in lbs[:30]:
        ls = e.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"]).get("Listeners", [])
        http_no_redirect = sum(1 for x in ls if x.get("Protocol") == "HTTP" and not any(a.get("Type") == "redirect" for a in x.get("DefaultActions", [])))
        attrs = {a["Key"]: a["Value"] for a in e.describe_load_balancer_attributes(LoadBalancerArn=lb["LoadBalancerArn"]).get("Attributes", [])}
        out.append({"scheme": lb.get("Scheme"), "type": lb.get("Type"), "azs": len(lb.get("AvailabilityZones", [])),
                    "https_listeners": sum(1 for x in ls if x.get("Protocol") in ("HTTPS", "TLS")),
                    "http_listeners_without_redirect": http_no_redirect,
                    "ssl_policies": sorted({x.get("SslPolicy") for x in ls if x.get("SslPolicy")}),
                    "access_logs": attrs.get("access_logs.s3.enabled"), "deletion_protection": attrs.get("deletion_protection.enabled"),
                    "drop_invalid_headers": attrs.get("routing.http.drop_invalid_header_fields.enabled")})
    return {"load_balancers": out}


@check("waf_cdn", "waf,tls,network")
def _waf(S):
    w = S.c("wafv2", GLOBAL_REGION)
    cf_acls = len(w.list_web_acls(Scope="CLOUDFRONT", Limit=100).get("WebACLs", []))
    dl = S.g("cloudfront").list_distributions().get("DistributionList", {}).get("Items", []) or []
    out = {"cloudfront_web_acls": cf_acls, "distributions": len(dl),
           "distributions_with_waf": sum(1 for d in dl if d.get("WebACLId")),
           "viewer_policy_allow_all": sum(1 for d in dl if (d.get("DefaultCacheBehavior") or {}).get("ViewerProtocolPolicy") == "allow-all"),
           "min_tls": sorted({(d.get("ViewerCertificate") or {}).get("MinimumProtocolVersion") for d in dl if (d.get("ViewerCertificate") or {}).get("MinimumProtocolVersion")}),
           "ipv6": sum(1 for d in dl if d.get("IsIPV6Enabled"))}
    return out


@check("waf_regional", "waf", "regional")
def _waf_r(S, r):
    return {"regional_web_acls": len(S.c("wafv2", r).list_web_acls(Scope="REGIONAL", Limit=100).get("WebACLs", []))}


@check("s3", "s3,kms")
def _s3(S):
    s3 = S.c("s3", S.regions[0])
    buckets = s3.list_buckets().get("Buckets", [])[:S.limit]
    cap = min(len(buckets), 100)
    st = {"total": len(buckets), "checked": cap, "public_access_block_all": 0, "sse_kms": 0, "sse_s3_only": 0, "versioning_on": 0,
          "lifecycle": 0, "public_policy": 0, "errors": 0}
    for b in buckets[:cap]:
        n = b["Name"]
        try:
            try:
                pab = s3.get_public_access_block(Bucket=n)["PublicAccessBlockConfiguration"]
                st["public_access_block_all"] += all(pab.get(k) for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"))
            except Exception as e:  # noqa: BLE001
                if classify_error(e)[0] not in ("not_enabled",):
                    raise
            try:
                rules = s3.get_bucket_encryption(Bucket=n)["ServerSideEncryptionConfiguration"]["Rules"]
                algos = {r_["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] for r_ in rules}
                st["sse_kms"] += bool(algos & {"aws:kms", "aws:kms:dsse"})
                st["sse_s3_only"] += not (algos & {"aws:kms", "aws:kms:dsse"})
            except Exception as e:  # noqa: BLE001
                if classify_error(e)[0] not in ("not_enabled",):
                    raise
            st["versioning_on"] += s3.get_bucket_versioning(Bucket=n).get("Status") == "Enabled"
            try:
                st["lifecycle"] += bool(s3.get_bucket_lifecycle_configuration(Bucket=n).get("Rules"))
            except Exception as e:  # noqa: BLE001
                if classify_error(e)[0] not in ("not_enabled",):
                    raise
            try:
                st["public_policy"] += bool(s3.get_bucket_policy_status(Bucket=n)["PolicyStatus"]["IsPublic"])
            except Exception as e:  # noqa: BLE001
                if classify_error(e)[0] not in ("not_enabled",):
                    raise
        except Exception:  # noqa: BLE001
            st["errors"] += 1
    return st


@check("databases", "db,backup,multi-az", "regional")
def _db(S, r):
    rds = S.c("rds", r)
    inst = S.pages(rds, "describe_db_instances", "DBInstances")
    try:
        clus = S.pages(rds, "describe_db_clusters", "DBClusters")
    except Exception:  # noqa: BLE001
        clus = []
    out = {"instances": len(inst), "clusters": len(clus),
           "engines": sorted({i.get("Engine") for i in inst}),
           "storage_unencrypted": sum(1 for i in inst if not i.get("StorageEncrypted")),
           "publicly_accessible": sum(1 for i in inst if i.get("PubliclyAccessible")),
           "multi_az_instances": sum(1 for i in inst if i.get("MultiAZ")),
           "clusters_multi_az_members": [len(c.get("DBClusterMembers", [])) for c in clus],
           "backup_retention_min_days": min([i.get("BackupRetentionPeriod", 0) for i in inst] + [c.get("BackupRetentionPeriod", 0) for c in clus] or [None]) if (inst or clus) else None,
           "deletion_protection_off": sum(1 for i in inst if not i.get("DeletionProtection")) + sum(1 for c in clus if not c.get("DeletionProtection")),
           "performance_insights_on": sum(1 for i in inst if i.get("PerformanceInsightsEnabled")),
           "iam_auth_on": sum(1 for i in inst if i.get("IAMDatabaseAuthenticationEnabled")),
           "graviton_classes": sum(1 for i in inst if re.search(r"\.\w*g\w*\.", i.get("DBInstanceClass", "") + ".") and "db.t3" not in i.get("DBInstanceClass", ""))}
    try:
        ddb = S.c("dynamodb", r)
        tables = S.pages(ddb, "list_tables", "TableNames")[:50]
        pitr = 0
        for t in tables:
            d = ddb.describe_continuous_backups(TableName=t).get("ContinuousBackupsDescription", {})
            pitr += (d.get("PointInTimeRecoveryDescription") or {}).get("PointInTimeRecoveryStatus") == "ENABLED"
        out["dynamodb_tables"], out["dynamodb_pitr_on"] = len(tables), pitr
    except Exception as e:  # noqa: BLE001
        out["dynamodb"] = classify_error(e)[0]
    return out


@check("backup", "backup", "regional")
def _backup(S, r):
    b = S.c("backup", r)
    plans = S.pages(b, "list_backup_plans", "BackupPlansList")
    copy_actions = rules = 0
    for p in plans[:20]:
        rl = b.get_backup_plan(BackupPlanId=p["BackupPlanId"]).get("BackupPlan", {}).get("Rules", [])
        rules += len(rl)
        copy_actions += sum(1 for x in rl if x.get("CopyActions"))
    vaults = b.list_backup_vaults().get("BackupVaultList", [])
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365)
    restores = b.list_restore_jobs(ByCreatedAfter=since).get("RestoreJobs", [])
    return {"plans": len(plans), "rules": rules, "rules_with_cross_region_or_account_copy": copy_actions,
            "vaults": len(vaults), "vaults_with_kms": sum(1 for v in vaults if v.get("EncryptionKeyArn")),
            "restore_jobs_last_365d": len(restores), "restore_jobs_completed": sum(1 for x in restores if x.get("Status") == "COMPLETED")}


# ---- network / compute
@check("vpc", "network,multi-az", "regional")
def _vpc(S, r):
    e = S.c("ec2", r)
    sgs = S.pages(e, "describe_security_groups", "SecurityGroups")
    risky_ports = {22, 3389, 3306, 5432, 1433, 6379, 9200, 27017, 11211}
    open_all, open_risky = [], []
    for sg in sgs:
        for p in sg.get("IpPermissions", []):
            world = any(x.get("CidrIp") == "0.0.0.0/0" for x in p.get("IpRanges", [])) or any(x.get("CidrIpv6") == "::/0" for x in p.get("Ipv6Ranges", []))
            if not world:
                continue
            if p.get("IpProtocol") == "-1":
                open_all.append(sg["GroupId"])
            else:
                lo, hi = p.get("FromPort"), p.get("ToPort")
                if lo is not None and any(lo <= port <= hi for port in risky_ports):
                    open_risky.append(sg["GroupId"])
    subnets = S.pages(e, "describe_subnets", "Subnets")
    return {"vpcs": len(S.pages(e, "describe_vpcs", "Vpcs")), "subnets": len(subnets),
            "subnet_azs": len({s.get("AvailabilityZone") for s in subnets}),
            "flow_logs": len(S.pages(e, "describe_flow_logs", "FlowLogs")),
            "vpc_endpoints": len(S.pages(e, "describe_vpc_endpoints", "VpcEndpoints")),
            "nat_gateways": [n.get("State") for n in S.pages(e, "describe_nat_gateways", "NatGateways")],
            "security_groups": len(sgs), "sg_open_all_to_world": sorted(set(open_all))[:10],
            "sg_risky_ports_open_to_world": sorted(set(open_risky))[:10]}


@check("ec2", "compute,kms,network", "regional")
def _ec2(S, r):
    e = S.c("ec2", r)
    res = S.pages(e, "describe_instances", "Reservations")
    ins = [i for rr in res for i in rr.get("Instances", [])]
    running = [i for i in ins if i.get("State", {}).get("Name") == "running"]
    vols = S.pages(e, "describe_volumes", "Volumes")
    out = {"instances_running": len(running), "instances_stopped": sum(1 for i in ins if i.get("State", {}).get("Name") == "stopped"),
           "imdsv2_required": sum(1 for i in running if (i.get("MetadataOptions") or {}).get("HttpTokens") == "required"),
           "with_public_ip": sum(1 for i in running if i.get("PublicIpAddress")),
           "arm64": sum(1 for i in running if i.get("Architecture") == "arm64"),
           "volumes": len(vols), "volumes_unencrypted": sum(1 for v in vols if not v.get("Encrypted")),
           "volumes_unattached": sum(1 for v in vols if v.get("State") == "available"),
           "volumes_gp2": sum(1 for v in vols if v.get("VolumeType") == "gp2"),
           "ebs_default_encryption": e.get_ebs_encryption_by_default().get("EbsEncryptionByDefault"),
           "unassociated_eips": sum(1 for a in e.describe_addresses().get("Addresses", []) if not a.get("AssociationId"))}
    try:
        out["ssm_managed_instances"] = len(S.pages(S.c("ssm", r), "describe_instance_information", "InstanceInformationList"))
    except Exception as ex:  # noqa: BLE001
        out["ssm_managed_instances"] = classify_error(ex)[0]
    return out


@check("ecs", "compute,scaling,multi-az", "regional")
def _ecs(S, r):
    e = S.c("ecs", r)
    out = {"clusters": 0, "services": 0, "circuit_breaker_enabled": 0, "circuit_breaker_rollback": 0, "desired_ge_2": 0,
           "arm64_task_defs": 0, "privileged_containers": 0, "readonly_rootfs_all": 0, "exec_enabled": 0, "task_defs_checked": 0}
    clusters = S.pages(e, "list_clusters", "clusterArns")
    out["clusters"] = len(clusters)
    for cl in clusters[:10]:
        arns = S.pages(e, "list_services", "serviceArns", cluster=cl)[:40]
        for i in range(0, len(arns), 10):
            for s in e.describe_services(cluster=cl, services=arns[i:i + 10]).get("services", []):
                out["services"] += 1
                cb = (s.get("deploymentConfiguration") or {}).get("deploymentCircuitBreaker") or {}
                out["circuit_breaker_enabled"] += bool(cb.get("enable"))
                out["circuit_breaker_rollback"] += bool(cb.get("rollback"))
                out["desired_ge_2"] += s.get("desiredCount", 0) >= 2
                out["exec_enabled"] += bool(s.get("enableExecuteCommand"))
                td = e.describe_task_definition(taskDefinition=s["taskDefinition"]).get("taskDefinition", {})
                out["task_defs_checked"] += 1
                out["arm64_task_defs"] += (td.get("runtimePlatform") or {}).get("cpuArchitecture") == "ARM64"
                cds = td.get("containerDefinitions", [])
                out["privileged_containers"] += sum(1 for c in cds if c.get("privileged"))
                out["readonly_rootfs_all"] += bool(cds) and all(c.get("readonlyRootFilesystem") for c in cds)
    return out


@check("lambda", "compute", "regional")
def _lambda(S, r):
    fs = S.pages(S.c("lambda", r), "list_functions", "Functions")
    rt = {}
    for f in fs:
        rt[f.get("Runtime", "container/other")] = rt.get(f.get("Runtime", "container/other"), 0) + 1
    return {"functions": len(fs), "runtimes": rt, "arm64": sum(1 for f in fs if "arm64" in f.get("Architectures", [])),
            "xray_active": sum(1 for f in fs if (f.get("TracingConfig") or {}).get("Mode") == "Active")}


@check("ecr", "compute,container", "regional")
def _ecr(S, r):
    e = S.c("ecr", r)
    repos = S.pages(e, "describe_repositories", "repositories")
    out = {"repositories": len(repos), "scan_on_push": sum(1 for x in repos if (x.get("imageScanningConfiguration") or {}).get("scanOnPush")),
           "tags_mutable": sum(1 for x in repos if x.get("imageTagMutability") == "MUTABLE"),
           "kms_encrypted": sum(1 for x in repos if (x.get("encryptionConfiguration") or {}).get("encryptionType") == "KMS")}
    try:
        out["registry_scan_type"] = (e.get_registry_scanning_configuration().get("scanningConfiguration") or {}).get("scanType")
    except Exception as ex:  # noqa: BLE001
        out["registry_scan_type"] = classify_error(ex)[0]
    return out


@check("scaling", "scaling,cost", "regional")
def _scaling(S, r):
    out = {"asg": len(S.pages(S.c("autoscaling", r), "describe_auto_scaling_groups", "AutoScalingGroups"))}
    aa = S.c("application-autoscaling", r)
    for ns in ("ecs", "dynamodb", "rds", "lambda"):
        out[f"app_autoscaling_{ns}_targets"] = len(aa.describe_scalable_targets(ServiceNamespace=ns).get("ScalableTargets", []))
    return out


# ---- observability / operations
@check("observability", "observability", "regional")
def _obs(S, r):
    cw = S.c("cloudwatch", r)
    alarms = S.pages(cw, "describe_alarms", "MetricAlarms")
    logs = S.pages(S.c("logs", r), "describe_log_groups", "logGroups")
    out = {"alarms": len(alarms), "alarms_with_actions": sum(1 for a in alarms if a.get("AlarmActions")),
           "alarms_with_description": sum(1 for a in alarms if a.get("AlarmDescription")),
           "usage_quota_alarms": sum(1 for a in alarms if a.get("Namespace") == "AWS/Usage"),
           "dashboards": len(cw.list_dashboards().get("DashboardEntries", [])),
           "log_groups": len(logs), "log_groups_without_retention": sum(1 for l in logs if not l.get("retentionInDays")),
           "log_groups_kms": sum(1 for l in logs if l.get("kmsKeyId"))}
    for label, svc, op, key in (("xray_sampling_rules", "xray", "get_sampling_rules", "SamplingRuleRecords"),
                                ("synthetics_canaries", "synthetics", "describe_canaries", "Canaries"),
                                ("sns_topics", "sns", "list_topics", "Topics")):
        try:
            out[label] = len(getattr(S.c(svc, r), op)().get(key, []))
        except Exception as e:  # noqa: BLE001
            out[label] = classify_error(e)[0]
    try:
        rules = S.pages(S.c("events", r), "list_rules", "Rules") if S.c("events", r).can_paginate("list_rules") else S.c("events", r).list_rules().get("Rules", [])
        out["eventbridge_rules"] = len(rules)
        out["eventbridge_security_or_health_rules"] = sum(1 for x in rules if re.search(r"guardduty|securityhub|inspector|health|config", x.get("Name", ""), re.I))
    except Exception as e:  # noqa: BLE001
        out["eventbridge_rules"] = classify_error(e)[0]
    return out


@check("incident_tooling", "observability,threat-detect", "regional")
def _inc(S, r):
    return {"ssm_incident_response_plans": len(S.c("ssm-incidents", r).list_response_plans().get("responsePlanSummaries", []))}


def _try(out, label, fn):
    """One sub-call must never hide the others (e.g. a service that is not available in the region)."""
    try:
        out[label] = fn()
    except Exception as e:  # noqa: BLE001
        out[label] = classify_error(e)[0]


@check("resilience", "resilience,multi-az", "regional")
def _resilience(S, r):
    out = {}
    rh, fis = S.c("resiliencehub", r), S.c("fis", r)

    def policies():
        pol = rh.list_resiliency_policies().get("resiliencyPolicies", [])
        with_targets = sum(1 for p in pol if any((v or {}).get("rtoInSecs") is not None and (v or {}).get("rpoInSecs") is not None
                                                 for v in (p.get("policy") or {}).values()))
        return {"total": len(pol), "with_rto_rpo": with_targets}
    _try(out, "resiliency_policies", policies)
    _try(out, "resilience_hub_apps", lambda: len(rh.list_apps().get("appSummaries", [])))
    _try(out, "fis_templates", lambda: len(fis.list_experiment_templates().get("experimentTemplates", [])))

    def experiments():
        exps = fis.list_experiments().get("experiments", [])
        ages = [_age_days(e.get("creationTime") or e.get("startTime")) for e in exps]
        return {"total": len(exps), "last_365d": sum(1 for a in ages if a is not None and a <= 365),
                "last_90d": sum(1 for a in ages if a is not None and a <= 90)}
    _try(out, "fis_experiments", experiments)
    return out


@check("ops_process", "ops-process", "regional")
def _ops_process(S, r):
    out = {}
    contacts, inc, ssm = S.c("ssm-contacts", r), S.c("ssm-incidents", r), S.c("ssm", r)

    def contact_summary():
        cs = contacts.list_contacts().get("Contacts", [])
        t = [c.get("Type") for c in cs]
        return {"total": len(cs), "escalation_plans": t.count("ESCALATION"), "oncall_schedules": t.count("ONCALL_SCHEDULE")}
    _try(out, "ssm_contacts", contact_summary)

    def incident_records():
        recs = inc.list_incident_records().get("incidentRecordSummaries", [])
        ages = [_age_days(x.get("creationTime")) for x in recs]
        return {"total": len(recs), "last_365d": sum(1 for a in ages if a is not None and a <= 365),
                "resolved": sum(1 for x in recs if x.get("status") == "RESOLVED")}
    _try(out, "incident_records", incident_records)

    def opsitems():
        its = ssm.describe_ops_items(MaxResults=50).get("OpsItemSummaries", [])
        ages = [_age_days(x.get("CreatedTime")) for x in its]
        return {"sampled": len(its), "with_severity": sum(1 for x in its if x.get("Severity")),
                "with_priority": sum(1 for x in its if x.get("Priority")), "last_90d": sum(1 for a in ages if a is not None and a <= 90)}
    _try(out, "ops_items", opsitems)
    _try(out, "change_templates", lambda: len(ssm.list_documents(Filters=[
        {"Key": "DocumentType", "Values": ["Automation.ChangeTemplate"]}, {"Key": "Owner", "Values": ["Self"]}]).get("DocumentIdentifiers", [])))
    return out


@check("slo", "slo,observability", "regional")
def _slo(S, r):
    return {"service_level_objectives": len(S.pages(S.c("application-signals", r), "list_service_level_objectives", "SloSummaries"))}


@check("quotas", "quotas", "regional")
def _quotas(S, r):
    h = S.c("service-quotas", r).list_requested_service_quota_change_history().get("RequestedQuotas", [])
    return {"quota_increase_requests": len(h)}


@check("iac_aws", "iac-aws,ci")
def _iac(S):
    cf = S.c("cloudformation", S.regions[0])
    stacks = [s for s in S.pages(cf, "list_stacks", "StackSummaries") if s.get("StackStatus") != "DELETE_COMPLETE"]
    names = [s.get("StackName", "").lower() for s in stacks]
    return {"stacks": len(stacks), "cdk_toolkit": any(n.startswith("cdktoolkit") for n in names),
            "env_markers": {"prod": sum(1 for n in names if "prod" in n), "stg": sum(1 for n in names if re.search(r"stg|stage|staging", n)),
                            "dev": sum(1 for n in names if re.search(r"(^|-)dev", n))}}


# ---- cost / support
@check("cost", "cost,org")
def _cost(S):
    out = {}
    if S.account:
        b = S.g("budgets")
        budgets = b.describe_budgets(AccountId=S.account).get("Budgets", [])
        with_n = 0
        for x in budgets[:20]:
            with_n += bool(b.describe_notifications_for_budget(AccountId=S.account, BudgetName=x["BudgetName"]).get("Notifications"))
        out["budgets"], out["budgets_with_notifications"] = len(budgets), with_n
    ce = S.g("ce")
    out["anomaly_monitors"] = len(ce.get_anomaly_monitors().get("AnomalyMonitors", []))
    out["anomaly_subscriptions"] = len(ce.get_anomaly_subscriptions().get("AnomalySubscriptions", []))
    try:
        out["cost_allocation_tags_active"] = len(ce.list_cost_allocation_tags(Status="Active").get("CostAllocationTags", []))
    except Exception as e:  # noqa: BLE001
        out["cost_allocation_tags_active"] = classify_error(e)[0]
    first_this = datetime.date.today().replace(day=1)
    last_start = (first_this - datetime.timedelta(days=1)).replace(day=1)
    try:
        res = ce.get_cost_and_usage(TimePeriod={"Start": last_start.isoformat(), "End": first_this.isoformat()}, Granularity="MONTHLY",
                                    Metrics=["UnblendedCost"], GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])
        groups = res.get("ResultsByTime", [{}])[0].get("Groups", [])
        rows = sorted(((g["Keys"][0], float(g["Metrics"]["UnblendedCost"]["Amount"])) for g in groups), key=lambda x: -x[1])
        out["last_month_total_usd"] = round(sum(a for _, a in rows), 2)
        out["last_month_top_services"] = [{"service": k, "usd": round(v, 2)} for k, v in rows[:5]]
    except Exception as e:  # noqa: BLE001
        out["last_month_cost"] = classify_error(e)[0]
    for label, svc, op, key, kw in (("savings_plans_active", "savingsplans", "describe_savings_plans", "savingsPlans", {"states": ["active"]}),
                                    ("reserved_instances_active", "ec2", "describe_reserved_instances", "ReservedInstances", {"Filters": [{"Name": "state", "Values": ["active"]}]})):
        try:
            out[label] = len(getattr(S.c(svc, GLOBAL_REGION if svc == "savingsplans" else S.regions[0]), op)(**kw).get(key, []))
        except Exception as e:  # noqa: BLE001
            out[label] = classify_error(e)[0]
    try:
        out["compute_optimizer"] = S.c("compute-optimizer", S.regions[0]).get_enrollment_status().get("status")
    except Exception as e:  # noqa: BLE001
        out["compute_optimizer"] = classify_error(e)[0]
    try:
        out["tag_keys"] = sorted(S.c("resourcegroupstaggingapi", S.regions[0]).get_tag_keys().get("TagKeys", []))[:50]
    except Exception as e:  # noqa: BLE001
        out["tag_keys"] = classify_error(e)[0]
    return out


@check("support_plan", "support")
def _support(S):
    S.g("support").describe_severity_levels()
    return {"plan_at_least_business": True}


@check("route53", "network,multi-az")
def _r53(S):
    r = S.g("route53")
    return {"hosted_zones": len(r.list_hosted_zones().get("HostedZones", [])), "health_checks": len(r.list_health_checks().get("HealthChecks", []))}


def run_aws_scan(session, regions, limit=LIMIT, only=None):
    S = Scanner(session, regions, limit=limit)
    results = {}
    ident = [c for c in CHECKS if c["id"] == "identity"][0]
    order = [ident] + [c for c in CHECKS if c["id"] != "identity"]
    for ch in order:
        if only and ch["id"] not in only and ch["id"] != "identity":
            continue
        rec = {"areas": ch["areas"], "scope": ch["scope"]}
        try:
            if ch["scope"] == "regional":
                regional = {}
                for r in regions:
                    try:
                        regional[r] = {"status": "ok", "data": ch["fn"](S, r)}
                    except Exception as e:  # noqa: BLE001
                        st, code, msg = classify_error(e)
                        regional[r] = {"status": st, "code": code, "message": msg}
                rec["regions"] = regional
                rec["status"] = "ok" if any(v["status"] == "ok" for v in regional.values()) else next(iter(regional.values()))["status"]
            else:
                rec["data"] = ch["fn"](S)
                rec["status"] = "ok"
        except Exception as e:  # noqa: BLE001
            st, code, msg = classify_error(e)
            rec.update({"status": st, "code": code, "message": msg})
        results[ch["id"]] = rec
    return {"scanned_at": _now(), "regions": regions, "account": S.account, "checks": results}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
