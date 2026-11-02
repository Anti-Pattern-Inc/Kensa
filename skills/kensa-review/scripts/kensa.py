#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.34", "openpyxl>=3.1"]
# ///
"""kensa.py - state manager for AI-assisted AWS Well-Architected reviews.

Run with `uv run kensa.py ...` (installs boto3/openpyxl on first use; the block above is PEP 723 metadata),
or with plain `python3 kensa.py ...` after `pip install boto3 openpyxl`.

Offline by default. Needs only the standard library (openpyxl for .xlsx export).
boto3 is imported lazily, only by: auth-check, seed --from-wa-tool, scan-aws, push --apply, precheck, workload-create.
Each of those verifies the credentials (STS GetCallerIdentity) and shows the account before touching AWS.

Typical flow (see SKILL.md):
  init -> context set / business answer -> seed (--from-wa-tool | --from-csv) -> route
  -> coverage -> scan-repo / scan-aws -> apply (verdicts) -> cards next / cards answer
  -> finalize -> export -> push -> precheck
  No AWS access (IaC only): mode iac -> scan-repo / scan-iac -> apply (channel=repo, confidence<=M) -> ...   (mode both to go back)
  Re-review: snapshot -> (seed / scan / apply again) -> diff
  Other lens / new best practices: route-unknown -> (agent classifies) -> route-apply
  Permissions: iam-policy --kind scan-deny | wa-tool-read | wa-tool-write
State lives in ./.kensa (override with --dir or $KENSA_DIR).
Language of exports (xlsx/csv/json) and decision cards: --lang en|ja > $KENSA_LANG > context.json "language" > en.
CLI messages are always English.
"""
import argparse
import collections
import copy
import csv
import datetime
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ASSETS = os.path.normpath(os.path.join(HERE, "..", "assets"))
import kensa_i18n as i18n  # noqa: E402

VERDICTS = ("○", "△", "✗", "N/A")
VERDICT_ALIASES = {
    "○": "○", "o": "○", "ok": "○", "yes": "○", "full": "○", "implemented": "○", "実施": "○", "実施(自然な状態)": "○",
    "△": "△", "partial": "△", "tri": "△", "部分実施": "△", "部分": "△",
    "✗": "✗", "×": "✗", "x": "✗", "no": "✗", "none": "✗", "not": "✗", "未実施": "✗",
    "n/a": "N/A", "na": "N/A", "not_applicable": "N/A", "該当なし": "N/A",
}
CHANNELS = ("aws", "repo", "interview", "assumed", "imported")
CH_RANK = {"aws": 6, "repo": 5, "interview": 4, "imported": 1, "assumed": 0}   # the live environment (aws) outranks IaC (repo): on a conflict, take the live environment
EVIDENCE_CH = ("aws", "repo")
DRAFT_EVIDENCE_RANK = 3  # aws/repo evidence on a draft route ranks below an interview answer (4): a rescan never overwrites a person's answer
CONF_RANK = {"H": 3, "M": 2, "L": 1, None: 0}
PILLAR_ORDER = ["Security", "Reliability", "Operational Excellence", "Performance Efficiency", "Cost Optimization", "Sustainability"]
PILLAR_ID = {"Operational Excellence": "operationalExcellence", "Security": "security", "Reliability": "reliability",
             "Performance Efficiency": "performance", "Cost Optimization": "costOptimization", "Sustainability": "sustainability"}
FTR_PILLARS = {"security", "reliability", "operationalExcellence"}
LADDER_VERDICT = {"full": "○", "partial": "△", "none": "✗", "na": "N/A"}
VERDICT_SCORE = {"○": 3, "△": 2, "✗": 1}   # diff: higher = improved, lower = regressed (N/A and undetermined count as a plain "change")
STALE_DAYS = 180                            # evidence recorded this many days ago or more is due for re-checking
MODES = ("both", "iac")
ACTION_STATUSES = i18n.ACTION_STATUSES      # stored codes: todo | doing | done (labels per locale: kensa_i18n.LABELS[lang]["status"]; the Japanese 未着手 / 対応中 / 完了 are accepted as aliases)
EN = i18n.labels("en")                      # CLI messages are English only: they use the English labels whatever the export language is
ACTION_FIELDS = ("owner", "due", "action_status", "priority", "effort")   # improvement-management fields: updatable independently of the verdict and evidence
SECRET_PATTERNS = [
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    # also covers key=value / key: value and JSON API responses ("password": "…", "SessionToken": "…"); a quoted value is removed whole, inner spaces and \" included
    (re.compile(r"""(?i)["']?(aws_secret_access_key|secret[_-]?(?:access[_-]?)?key|session[_-]?token|api[_-]?key|client[_-]?secret|password|passwd|token)["']?"""
                r"""\s*[:=]\s*(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\S+)"""), r"\1=[REDACTED]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
]
ACCOUNT_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")

CONTEXT_TEMPLATE = {
    "language": "en",    # language of exports and decision cards: en | ja (--lang and $KENSA_LANG take precedence)
    "service": {"name": "", "description": "", "roles": [], "environment": "PRODUCTION"},
    "team": {"size": None, "composition": {}, "infra_owner": i18n.system_text("ctx.infra_owner"), "po": "PO", "infra_capacity": None},
    "tools": {"vcs": "GitHub", "chat": "Slack", "ci": "GitHub Actions", "ticket": "GitHub Issues", "docs": i18n.system_text("ctx.docs")},
    "business": {"criticality": None, "availability_target": None, "data_sensitivity": [], "compliance": [],
                 "traffic": None, "budget_sensitivity": None, "integrations": "", "ftr_target": False, "rto": None, "rpo": None},
    "aws": {"profile": None, "wa_profile": None, "regions": [], "account_ids": [], "workload_id": None, "lens": "wellarchitected",
            "environments": None, "solution_name": None},
    "repo": {"paths": ["."], "github": None},
    "answers": {"business": {}},
}


# ------------------------------------------------------------------ helpers
def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def redact(text):
    if not text:
        return text
    for pat, rep in SECRET_PATTERNS:
        text = pat.sub(rep, text)
    return text


def mask_accounts(text):
    return ACCOUNT_RE.sub("************", text) if isinstance(text, str) else text


_FORMULA_LEAD = "=+-@\t\r"


def csv_safe(v):
    """Opening a CSV in a spreadsheet can evaluate strings that start with = + - @ as formulas (CSV injection).
    Evidence and answers may contain external content, so prefix such strings with ' to keep them text (a lone `-` and numbers are left alone)."""
    if isinstance(v, str) and v and v[0] in _FORMULA_LEAD and v != "-":
        try:
            float(v)
            return v
        except ValueError:
            return "'" + v
    return v


def csv_unsafe_strip(v):
    """Remove the leading ' that `csv_safe` adds, when a CSV is imported."""
    return v[1:] if isinstance(v, str) and v[:1] == "'" and v[1:2] and v[1] in _FORMULA_LEAD else v


def load_json(path, default=None):
    if not os.path.exists(path):
        return copy.deepcopy(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


GITIGNORE_BODY = "# .kensa contains account IDs and infrastructure details. Do not commit.\n*\n"


def ensure_gitignore(d):
    """If the state directory exists, put a .gitignore in it so its contents are not committed (an existing one is left alone). Returns True when it created the file."""
    gi = os.path.join(d, ".gitignore")
    if os.path.isdir(d) and not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write(GITIGNORE_BODY)
        return True
    return False


def warn_unmasked_export(a):
    """When exporting without masking account IDs, remind the user to check before sharing (an output path outside the state directory could be committed)."""
    if not getattr(a, "mask_account_ids", False):
        print("warning: --mask-account-ids was not given, so the output may contain account IDs and similar details. Re-export with it before sharing.",
              file=sys.stderr)


class Store:
    def __init__(self, d, lang=None):
        self.dir = d
        self.p_ctx = os.path.join(d, "context.json")
        self.p_work = os.path.join(d, "worklist.json")
        self.p_cards = os.path.join(d, "card_answers.json")
        self.p_ev = os.path.join(d, "evidence")
        self._cli_lang = lang
        self._lang = None

    @property
    def lang(self):
        """Language of exports and cards: --lang > $KENSA_LANG > context.json "language" > en (resolved once, then cached)."""
        if self._lang is None:
            try:
                ctx = load_json(self.p_ctx, {})
            except (OSError, ValueError):
                ctx = {}
            ctx_lang = ctx.get("language") if isinstance(ctx, dict) else None
            self._lang = i18n.resolve_lang(self._cli_lang, os.environ.get(i18n.ENV_VAR), ctx_lang,
                                           warn=lambda m: print(f"warning: {m}", file=sys.stderr))
        return self._lang

    def ctx(self):
        return load_json(self.p_ctx, CONTEXT_TEMPLATE)

    def work(self):
        w = load_json(self.p_work)
        if not w:
            die("no worklist yet; run `seed` first.")
        return w

    def cards_state(self):
        return load_json(self.p_cards, {})


def assets(name):
    return load_json(os.path.join(ASSETS, name))


def load_routing(st):
    """Bundled routing + the user's overlay (.kensa/routing.local.json: questions/choices override by id)."""
    base = assets("routing.json")
    local = load_json(os.path.join(st.dir, "routing.local.json"))
    if not local:
        return base
    out = copy.deepcopy(base)
    for k in ("questions", "choices"):
        out.setdefault(k, {}).update(local.get(k, {}))
    return out


def cards_path(lang):
    """assets/cards.<lang>.json for a non-default language when that file exists, else assets/cards.json (English)."""
    if lang != i18n.DEFAULT:
        p = os.path.join(ASSETS, f"cards.{lang}.json")
        if os.path.exists(p):
            return p
    return os.path.join(ASSETS, "cards.json")


def load_cards(st):
    """Bundled cards for the active language + overlay (.kensa/cards.local.json: {"cards": {ID: {fields}}, "business": {...}}; new IDs are added)."""
    base = load_json(cards_path(st.lang))
    local = load_json(os.path.join(st.dir, "cards.local.json"))
    if not local:
        return base
    out = copy.deepcopy(base)
    for key in ("cards", "business"):
        by = {c["id"]: c for c in out.get(key, [])}
        for cid, patch in (local.get(key) or {}).items():
            if cid in by:
                by[cid].update(patch)
            else:
                out.setdefault(key, []).append(dict(patch, id=cid))
    return out


def norm_verdict(v):
    if v is None:
        return None
    s = str(v).strip()
    for key in (s, s.lower()):
        if key in VERDICT_ALIASES:
            return VERDICT_ALIASES[key]
    die(f"invalid verdict: {v!r} (use one of ○ △ ✗ N/A)")


def set_path(d, dotted, value):
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def get_path(d, dotted, default=None):
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


class _Fmt(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def fill(text, ctx, lang=None):
    """Fill {po} {infra_owner} {chat} ... placeholders; unknown ones stay as-is. A value that is not set falls back to the locale's default."""
    if not isinstance(text, str):
        return text
    d = i18n.labels(lang)["fill"]

    def val(path, key):
        return i18n.localize_stored(get_path(ctx, path), lang) or d[key]
    vals = _Fmt(po=val("team.po", "po"), infra_owner=val("team.infra_owner", "infra_owner"),
                chat=val("tools.chat", "chat"), vcs=val("tools.vcs", "vcs"),
                ci=val("tools.ci", "ci"), ticket=val("tools.ticket", "ticket"),
                docs=val("tools.docs", "docs"), team_size=get_path(ctx, "team.size") or "?",
                service=get_path(ctx, "service.name") or get_path(ctx, "service.description") or d["service"])
    try:
        return text.format_map(vals)
    except (ValueError, IndexError):
        return text


# ------------------------------------------------------------------ routing
def apply_routing(items, routing):
    qmap, cmap, fb = routing["questions"], routing["choices"], routing["fallback"]
    unknown = []
    for it in items:
        r = cmap.get(it["choice_id"])
        known = "choice"
        if r is None:
            r = qmap.get(it["question_id"])
            known = "question"
            if r is not None:
                r = {"ch": r["ch"], "areas": r["areas"], "draft": bool(r.get("draft")), "card": None}
            else:
                r, known = dict(fb), "none"
                unknown.append(it["choice_id"])
        it["route"] = {"ch": list(r["ch"]), "areas": list(r["areas"]),
                       "draft": bool(r.get("draft")), "card": r.get("card"), "known": known}
    return unknown


def new_item(row):
    return {"choice_id": row["choice_id"], "question_id": row["question_id"], "pillar": row["pillar"],
            "question_title": row.get("question_title", ""), "best_practice": row.get("best_practice", ""),
            "description": row.get("description", ""), "aws_checkable": row.get("aws_checkable", ""),
            "help_url": row.get("help_url", ""), "improvement_url": row.get("improvement_url", ""),
            "route": None, "verdict": None, "channel": None, "confidence": None, "evidence": "", "org_shape": "",
            "recommendation": "", "effort": "", "priority": "", "owner": "", "notes": "", "card_choice": None,
            "status": "open"}


# ------------------------------------------------------------------ seed
def parse_checklist_csv(path, sheet=0, import_verdicts=False):
    txt = open(path, encoding="utf-8-sig").read()
    if txt.lstrip().startswith("## Sheet name:"):
        parts = [p for p in txt.split("## Sheet name:")[1:]]
        usable = []
        for p in parts:
            head, _, body = p.partition("\n")
            first = body.split("\n", 1)[0]
            if "ChoiceId" in first and "QuestionId" in first and "BestPractice" in first:
                usable.append((head.strip(), body))
        if not usable:
            die("no sheet with ChoiceId / QuestionId / BestPractice columns was found.")
        if isinstance(sheet, str) and not sheet.isdigit():
            hit = [u for u in usable if sheet in u[0]]
            if not hit:
                die(f"no sheet name contains {sheet!r}: {[u[0] for u in usable]}")
            body = hit[0][1]
        else:
            idx = int(sheet)
            if idx >= len(usable):
                die(f"--sheet {idx} is out of range ({len(usable)} usable)")
            body = usable[idx][1]
    else:
        body = txt
    rows = []
    for r in csv.DictReader(io.StringIO(body)):
        if not r.get("ChoiceId"):
            continue
        row = {"choice_id": r["ChoiceId"].strip(), "question_id": r["QuestionId"].strip(), "pillar": r.get("Pillar", "").strip(),
               "question_title": r.get("QuestionTitle", ""), "best_practice": r.get("BestPractice", ""),
               "description": r.get("Description", ""), "aws_checkable": r.get("AWSCheckable", "")}
        if import_verdicts:
            v = i18n.import_header(r, i18n.VERDICT_COL).strip()      # "Verdict" or the Japanese header "判定"
            row["_verdict"] = {"○": "○", "△": "△", "✗": "✗", "N/A": "N/A"}.get(v)
            row["_org"] = csv_unsafe_strip(i18n.import_header(r, i18n.ORG_COL))   # "Org shape / tools" or "組織の形、ツール"
        rows.append(row)
    return rows


def merge_items(old, rows):
    """Keep state of known choices; flag new / removed ones (lens updates)."""
    by_id = {i["choice_id"]: i for i in old}
    seen, added = set(), []
    out = []
    for row in rows:
        seen.add(row["choice_id"])
        if row["choice_id"] in by_id:
            it = by_id[row["choice_id"]]
            for k in ("question_title", "best_practice", "description", "help_url", "improvement_url", "aws_checkable"):
                if row.get(k):
                    it[k] = row[k]
            it["removed"] = False
        else:
            it = new_item(row)
            it["status"] = "new_in_lens" if old else "open"
            if old:
                added.append(row["choice_id"])
        if row.get("_verdict"):
            it.update(verdict=row["_verdict"], channel="imported", confidence="L", org_shape=row.get("_org", ""),
                      evidence=i18n.system_text("ev.imported"))
            it.pop("basis", None)
        out.append(it)
    removed = []
    for i in old:
        if i["choice_id"] not in seen:
            i["removed"] = True
            i["status"] = "removed_from_lens"
            removed.append(i["choice_id"])
            out.append(i)
    return out, added, removed


def cmd_seed(a, st):
    refuse_demo(load_json(st.p_work) or {}, "seed")
    if a.from_csv:
        rows = parse_checklist_csv(a.from_csv, a.sheet, a.import_verdicts)
        meta = {"source": "csv", "file": os.path.basename(a.from_csv), "lens": "wellarchitected", "lens_version": None, "fetched_at": now_iso()}
    elif a.from_wa_tool:
        import kensa_wa
        ctx = st.ctx()
        check_auth(a, st, kind="wa")
        rows, m = kensa_wa.fetch(a.workload_id or get_path(ctx, "aws.workload_id") or die("--workload-id is required"),
                                a.lens or get_path(ctx, "aws.lens") or "wellarchitected",
                                region=a.region, profile=aws_profile(a, ctx, "wa"))
        meta = m
    else:
        die("specify --from-csv or --from-wa-tool.")
    old_w = load_json(st.p_work)
    old_items = old_w["items"] if old_w else []
    items, added, removed = merge_items(old_items, rows)
    routing = load_routing(st)
    unknown = apply_routing(items, routing)
    meta["seeded_at"] = now_iso()
    meta["scans"] = (old_w or {}).get("meta", {}).get("scans", {})
    if (old_w or {}).get("meta", {}).get("mode"):
        meta["mode"] = old_w["meta"]["mode"]     # a re-seed for a lens update must not lose the mode
    meta["none_choices"] = meta.get("none_choices") or (old_w or {}).get("meta", {}).get("none_choices", {})
    save_json(st.p_work, {"meta": meta, "items": items})
    print(f"seeded {len(rows)} best practices (source={meta['source']}, lens_version={meta.get('lens_version')})")
    if old_items:
        print(f"  merged into existing worklist: new={len(added)} removed={len(removed)}")
        if added:
            print("  new in lens (routed to interview/generic):", ", ".join(added[:10]) + (" ..." if len(added) > 10 else ""))
    if unknown:
        print(f"  {len(unknown)} choices have no routing (another lens / new items): {', '.join(unknown[:5])}{' ...' if len(unknown) > 5 else ''}\n"
              f"  → run `route-unknown` for material to classify them; the agent can register routes with `route-apply FILE` (unregistered ones go to individual interviews).")


def cmd_route(a, st):
    w = st.work()
    unknown = apply_routing(w["items"], load_routing(st))
    save_json(st.p_work, w)
    c = collections.Counter(i["route"]["ch"][0] for i in w["items"] if not i.get("removed"))
    print("routed. primary channel:", dict(c), "| unknown ids:", len(unknown))


# ------------------------------------------------------------------ state logic
def active(w):
    return [i for i in w["items"] if not i.get("removed")]


def resolved(it):
    return it.get("verdict") is not None and it.get("channel") != "assumed"


def ch_rank(ch, draft=False):
    """Strength of a source. aws/repo on a draft route only shows that something *exists*, so it ranks below a person's answer (interview)."""
    if draft and ch in EVIDENCE_CH:
        return DRAFT_EVIDENCE_RANK
    return CH_RANK.get(ch, 0)


def rank(it):
    draft = bool((it.get("route") or {}).get("draft"))
    return ch_rank(it.get("channel"), draft) * 10 + CONF_RANK.get(it.get("confidence"), 0)


def age_days(it):
    """Days since the evidence was recorded (None when unknown)."""
    s = it.get("evidence_at")
    if not s:
        return None
    try:
        t = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.astimezone()
    return (datetime.datetime.now().astimezone() - t).days


def is_stale(it, days=STALE_DAYS):
    age = age_days(it)
    return age is not None and age >= days


def rates(items):
    """recorded = has a verdict / confirmed = has a verdict and is not an assumed placeholder (no evidence and no answer)."""
    n = len(items)
    rec = sum(1 for i in items if i.get("verdict") is not None)
    ok = sum(1 for i in items if resolved(i))
    return {"total": n, "recorded": rec, "confirmed": ok,
            "recorded_rate": round(rec / n, 3) if n else 0.0, "confirmed_rate": round(ok / n, 3) if n else 0.0}


def settled_by_evidence(it):
    """Verdict backed by AWS/repo evidence with at least medium confidence: self-reported answers must not override it."""
    if (it.get("route") or {}).get("draft"):
        return False  # draft route: even with evidence a person confirms it (a self-reported answer can override it)
    return it.get("verdict") is not None and it.get("channel") in ("aws", "repo") and it.get("confidence") in ("H", "M")


def scans_done(w):
    return w["meta"].get("scans", {})


def review_mode(w):
    """both = live environment (aws) and IaC/repository (repo) / iac = the live AWS environment is not read; IaC and repository only."""
    return (w.get("meta") or {}).get("mode") or "both"


def ready_for_interview(it, w):
    """An item is ready for its card once the non-interview channels it can use have been tried (or skipped).
    mode=iac does not read the live environment (aws), so items with an aws route wait for the static IaC scan (scan-iac) instead."""
    return not missing_scans(it, w)


def missing_scans(it, w):
    """The scans (aws / repo / iac) an item still waits for before it can go to an interview card ([] = ready)."""
    sc = scans_done(w)
    iac = review_mode(w) == "iac"
    need = []
    for ch in it["route"]["ch"]:
        if ch not in ("aws", "repo"):
            continue
        scan = "iac" if iac and ch == "aws" else ch
        if scan not in sc and scan not in need:
            need.append(scan)
    return need


def iac_only_items(w):
    return [i for i in active(w) if i.get("basis") == "iac_only"]


def ch_label(it, lang=None):
    t = i18n.labels(lang)
    return t["iac_only"] if it.get("basis") == "iac_only" else t["ch"].get(it.get("channel"), "")


def pending_cards(w, cards):
    by = {c["id"]: c for c in cards["cards"]}
    pend = collections.OrderedDict()
    for it in active(w):
        cid = it["route"].get("card")
        if not cid or cid not in by:
            continue
        if resolved(it) and it["channel"] == "interview":
            continue
        if settled_by_evidence(it):
            continue
        if not ready_for_interview(it, w):
            continue
        pend.setdefault(cid, []).append(it["choice_id"])
    return [(by[c], ids) for c, ids in pend.items()]


def set_action_fields(it, e, cid):
    """Write owner / due / action status / priority / effort (verdict, evidence and the evidence time are untouched). An invalid value stops the command before anything is saved."""
    for k in ("effort", "priority", "owner"):
        if k in e:
            it[k] = e[k]
    if e.get("due") is not None:
        try:
            datetime.date.fromisoformat(str(e["due"]))
        except ValueError:
            die(f"due must be YYYY-MM-DD: {e['due']!r} ({cid})")
        it["due"] = str(e["due"])
    if e.get("action_status") is not None:
        code = i18n.norm_action_status(e["action_status"])    # stored as a code; "Not started" / "未着手" etc. are accepted
        if code is None:
            die(f"action_status must be one of {' / '.join(ACTION_STATUSES)}: {e['action_status']!r} ({cid})")
        it["action_status"] = code


def cmd_apply(a, st):
    w = st.work()
    data = load_json(a.file)
    entries = data["items"] if isinstance(data, dict) else data
    idx = {i["choice_id"]: i for i in w["items"]}
    n_ok = n_skip = n_cap = 0
    mode = review_mode(w)
    for e in entries:
        cid = e.get("choice_id") or die(f"missing choice_id: {e}")
        it = idx.get(cid)
        if not it:
            print(f"  skip (unknown choice_id): {cid}", file=sys.stderr)
            n_skip += 1
            continue
        if e.get("verdict") is None:
            # an update without a verdict (due / action status / owner changes): verdict, source, confidence and evidence time stay as they are
            if not any(e.get(k) is not None for k in ACTION_FIELDS):
                die(f"neither a verdict nor a management field ({'/'.join(ACTION_FIELDS)}) was given: {cid}")
            set_action_fields(it, e, cid)
            n_ok += 1
            continue
        v = norm_verdict(e.get("verdict"))
        ch = e.get("channel", "interview")
        if ch not in CHANNELS:
            die(f"invalid channel: {ch}")
        conf = e.get("confidence")
        if conf not in ("H", "M", "L", None):
            die(f"confidence must be H/M/L: {conf}")
        draft = bool((it.get("route") or {}).get("draft"))
        if mode == "iac" and ch == "aws":
            print(f"  skip (mode=iac: the live AWS environment is not read, so no channel=aws verdict is recorded; record verdicts read from IaC with channel=repo): {cid}", file=sys.stderr)
            n_skip += 1
            continue
        # an item that should be checked in the live environment (it has an aws route) but was judged from IaC only: only declarations were read, so confidence is capped at M and the item is marked for OpenItems
        iac_only = mode == "iac" and ch == "repo" and "aws" in it["route"]["ch"] and not draft
        if iac_only and conf == "H":
            conf = "M"
            n_cap += 1
        if v == "✗" and draft and ch in ("aws", "repo") and not a.force:
            print(f"  skip (not finding a document or activity is not grounds for ✗; confirm it on the decision card): {cid}", file=sys.stderr)
            n_skip += 1
            continue
        if resolved(it) and not a.force and ch_rank(ch, draft) * 10 + CONF_RANK[conf] < rank(it):
            if draft and ch in EVIDENCE_CH and it.get("channel") == "interview":
                print(f"  skip (a person's answer exists, so draft evidence does not overwrite it): {cid}", file=sys.stderr)
            elif ch == "repo" and it.get("channel") == "aws":
                print(f"  skip (a live-environment (aws) verdict exists, so IaC (repo) does not overwrite it; record any mismatch in notes as suspected drift): {cid}", file=sys.stderr)
            else:
                print(f"  skip (existing evidence is stronger): {cid}", file=sys.stderr)
            n_skip += 1
            continue
        it["verdict"], it["channel"], it["confidence"] = v, ch, conf
        it.pop("basis", None)    # an overwritten verdict does not inherit the earlier "IaC only" mark (a live-environment verdict replaces it)
        if iac_only:
            it["basis"] = "iac_only"
        for k in ("evidence", "org_shape", "recommendation", "notes"):
            if k in e:
                it[k] = redact(e[k])
        for k in ("na_reason", "na_reason_code"):
            if k in e:
                it[k] = e[k]
        set_action_fields(it, e, cid)
        it["status"] = "answered"
        it["evidence_at"] = now_iso()
        n_ok += 1
    save_json(st.p_work, w)
    print(f"applied {n_ok}, skipped {n_skip}")
    if n_cap:
        print(f"  mode=iac: capped the confidence of {n_cap} item(s) judged from IaC only, H → M (live environment not verified; they appear in OpenItems)")


# ------------------------------------------------------------------ cards
def detected_for(w, card_id):
    """Evidence already recorded for the items a card covers (aws/repo drafts), shown on the card for confirmation."""
    out = []
    for it in active(w) if w else []:
        if it["route"].get("card") != card_id or it.get("verdict") is None:
            continue
        if it.get("channel") in EVIDENCE_CH:
            out.append({"choice_id": it["choice_id"], "best_practice": it["best_practice"], "verdict": it["verdict"],
                        "channel": it["channel"], "confidence": it.get("confidence"), "evidence": it.get("evidence", "")})
    return out


def ask_text(c, ctx, lang=None):
    """Plain-language question first (when the card has `ask_plain`), with the technical wording attached."""
    if c.get("ask_plain"):
        return fill(c["ask_plain"], ctx, lang) + i18n.labels(lang)["ask_technical"].format(ask=fill(c["ask"], ctx, lang))
    return fill(c["ask"], ctx, lang)


def render_card(card, ctx, ids=None, as_json=False, detected=None):
    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return fill(o, ctx)
    c = walk(card)
    if ids is not None:
        c["pending_choice_ids"] = ids
    if detected:
        c["detected"] = detected
    if as_json:
        return json.dumps(c, ensure_ascii=False, indent=1)
    L = [f"### {c['id']} — {c['title']}", f"Why: {c['why']}", ""]
    if c.get("ask_plain"):
        L += [f"Question: {c['ask_plain']}", f"  (technical wording) {c['ask']}"]
    else:
        L.append(f"Question: {c['ask']}")
    if detected:
        L += ["", "[Detected evidence (draft; overwrite it if it differs from reality)]"]
        L += [f"  - {d['best_practice'][:48]}: {d['verdict']} ({EN['ch'].get(d['channel'], d['channel'])}/{EN['conf'].get(d.get('confidence'), '-')}) "
              f"{redact(str(d['evidence']))[:150]}" for d in detected]
    L += ["", "Which is closest to the current state?"]
    L += [f"  [full]    ○ {c['ladder']['full']}", f"  [partial] △ {c['ladder']['partial']}", f"  [none]    ✗ {c['ladder']['none']}"]
    if c.get("na_ok"):
        L.append(f"  [na]      N/A {c['ladder']['na']}")
    r = c["recommend"]
    L += ["", f"★ Recommended: {r['label']}  (effort {r['effort']} / priority {r['priority']} / owner {r['owner']})", f"  Why: {r['why']}"]
    L += [f"   {i}. {s}" for i, s in enumerate(r["steps"], 1)]
    for n, al in enumerate(c.get("alternatives", []), 1):
        L.append(f"  Alternative {n}: {al['label']} — {al['when']} / Trade-off: {al['tradeoff']}")
    L.append(f"  Org shape / tools if adopted: {c['org_shape']}")
    if ids:
        L.append(f"Items covered ({len(ids)}): " + ", ".join(ids))
    return "\n".join(L)


def card_is_answered(answered, cid, pending_ids):
    """Whether to treat the card as answered. If there are unanswered items that are not in the choice_ids recorded at answer time (added by a lens update), it is not answered.
    State recorded without choice_ids (older data) is judged per card, as before."""
    s = answered.get(cid)
    if not s:
        return False
    known = s.get("choice_ids")
    return True if known is None else not (set(pending_ids) - set(known))


def cmd_cards(a, st):
    cards = load_cards(st)
    ctx = st.ctx()
    by = {c["id"]: c for c in cards["cards"]}
    if a.cards_cmd == "list":
        w = load_json(st.p_work)
        pend = {c["id"]: ids for c, ids in pending_cards(w, cards)} if w else {}
        answered = st.cards_state()
        for c in cards["cards"]:
            mark = "answered" if card_is_answered(answered, c["id"], pend.get(c["id"], [])) else ("pending" if c["id"] in pend else "-")
            if a.pending and mark != "pending":
                continue
            print(f"{c['order']:>3} {c['id']:<22} {mark:<9} {c['title']}")
    elif a.cards_cmd == "show":
        card = by.get(a.id) or die(f"unknown card: {a.id}")
        w = load_json(st.p_work)
        ids = [i["choice_id"] for i in active(w) if i["route"].get("card") == a.id] if w else None
        print(render_card(card, ctx, ids, a.json, detected_for(w, a.id)))
    elif a.cards_cmd == "next":
        w = st.work()
        pend = pending_cards(w, cards)
        answered = st.cards_state()
        pend = [(c, ids) for c, ids in pend if not card_is_answered(answered, c["id"], ids)]
        if not pend:
            waiting = collections.OrderedDict()      # card id -> scans it waits for
            for it in active(w):
                cid = it["route"].get("card")
                if cid in by and not resolved(it) and not ready_for_interview(it, w):
                    waiting.setdefault(cid, set()).update(missing_scans(it, w))
            if waiting:
                scans = sorted({s for v in waiting.values() for s in v})
                hint = {"aws": "`scan-aws` (or `plan --skip aws`)", "repo": "`scan-repo` (or `plan --skip repo`)", "iac": "`scan-iac` (or `plan --skip iac`)"}
                print(f"No decision card is ready yet: {len(waiting)} card(s) are waiting for evidence collection ({', '.join(scans)}).")
                print("Run " + " and ".join(hint[s] for s in scans) + " first, so that the cards can show what was already detected. `plan` lists the next steps.")
            else:
                print("All decision cards are answered. Run `finalize` when you are ready to export (`plan` lists the next steps).")
            return
        for c, ids in pend[: a.n]:
            print(render_card(c, ctx, ids, a.json, detected_for(w, c["id"])))
            print()
    elif a.cards_cmd == "answer":
        cmd_card_answer(a, st, cards, ctx)
    elif a.cards_cmd == "prefill":
        w = st.work()
        n = 0
        for it in active(w):
            cid = it["route"].get("card")
            if not cid or it.get("verdict") is not None:
                continue
            r = by[cid]["recommend"]
            it["org_shape"] = i18n.prefix("org.proposal") + fill(by[cid]["org_shape"], ctx)
            it["recommendation"] = fill(r["label"], ctx) + " / " + " → ".join(fill(x, ctx) for x in r["steps"][:3])
            it["effort"], it["priority"], it["owner"] = r["effort"], r["priority"], fill(r["owner"], ctx)
            it["prefilled"] = True
            n += 1
        save_json(st.p_work, w)
        print(f"prefilled proposals for {n} unresolved items (verdicts stay undetermined; confirm the current state on the decision cards)")


def cmd_card_answer(a, st, cards, ctx):
    by = {c["id"]: c for c in cards["cards"]}
    card = by.get(a.id) or die(f"unknown card: {a.id}")
    cur = a.current
    if cur == "na" and not card.get("na_ok"):
        die("This card cannot be marked N/A as a whole. Use --override CHOICE_ID=N/A for individual items.")
    w = st.work()
    verdict = LADDER_VERDICT[cur]
    over = {}
    for o in a.override or []:
        k, _, v = o.partition("=")
        over[k.strip()] = norm_verdict(v)
    r = card["recommend"]
    choose = a.choose or ("recommend" if cur in ("partial", "none") else "current")
    if choose == "recommend":
        chosen, shape = r["label"], fill(card["org_shape"], ctx)
    elif choose.startswith("alt"):
        n = int(choose[3:] or 1) - 1
        al = card["alternatives"][n]
        chosen, shape = al["label"], i18n.prefix("org.alt") + al["label"]
    else:
        chosen, shape = "current", a.note or ""
    evidence = redact(a.evidence or "")
    conf = "M"
    n = 0
    for it in active(w):
        if it["route"].get("card") != a.id:
            continue
        v = over.get(it["choice_id"], verdict)
        if settled_by_evidence(it) and not a.force and it["choice_id"] not in over:
            continue  # AWS/repo evidence wins over self-reported answers
        it["verdict"], it["channel"], it["confidence"] = v, "interview", conf
        it.pop("basis", None)    # a verdict replaced by a person's answer is no longer "IaC only"
        it["evidence"] = evidence or (i18n.prefix("ev.self") + cur + ")" + (f": {redact(a.note)}" if a.note else ""))
        it["org_shape"] = fill(shape, ctx)
        it["card_choice"] = {"card": a.id, "current": cur, "chosen": chosen}
        if v in ("△", "✗"):
            it["recommendation"] = fill(r["label"], ctx) + " / " + " → ".join(fill(s, ctx) for s in r["steps"][:3])
            it["effort"], it["priority"], it["owner"] = r["effort"], r["priority"], fill(r["owner"], ctx)
        elif v == "N/A":
            it["na_reason"] = redact(a.note or card["ladder"].get("na") or i18n.system_text("na.default"))
            it["na_reason_code"] = "BUSINESS_PRIORITIES"
        it["status"] = "answered"
        it["evidence_at"] = now_iso()
        n += 1
    state = st.cards_state()
    state[a.id] = {"current": cur, "chosen": chosen, "note": redact(a.note or ""), "overrides": over, "answered_at": now_iso(), "items": n,
                   # the items the card covered when it was answered; if a lens update adds items, `cards next` shows the card again
                   "choice_ids": [it["choice_id"] for it in active(w) if it["route"].get("card") == a.id]}
    save_json(st.p_cards, state)
    save_json(st.p_work, w)
    print(f"card {a.id}: {n} items set to {verdict} (channel=interview, confidence=M); chosen={chosen}")


def cmd_business(a, st):
    cards = load_cards(st)
    ctx = st.ctx()
    by = {b["id"]: b for b in cards["business"]}
    if a.biz_cmd == "show":
        for b in cards["business"]:
            cur = get_path(ctx, b["sets"])
            shown = "unanswered" if cur in (None, "", []) else repr(cur)
            print(f"## {b['id']} — {b['title']}  (current value: {shown})")
            print("Question:", fill(b["ask"], ctx))
            for o in b["options"]:
                star = " ★ recommended" if o["key"] == b["recommend"] else ""
                print(f"   [{o['key']}] {o['label']}{star}")
            print(f"   Why recommended: {b['why']}\n   Impact: {b['implies']}\n")
    elif a.biz_cmd == "answer":
        b = by.get(a.id) or die(f"unknown business question: {a.id}")
        keys = [k for k in (a.option or "").split(",") if k]
        valid = {o["key"] for o in b["options"]}
        if not a.text:
            for k in keys:
                if k not in valid:
                    die(f"option must be one of {sorted(valid)}")
        multi = isinstance(get_path(CONTEXT_TEMPLATE, b["sets"]), list)
        value = a.text if a.text else (keys if multi else (keys[0] if keys else None))
        set_path(ctx, b["sets"], value)
        ctx.setdefault("answers", {}).setdefault("business", {})[a.id] = value
        if b["sets"] == "business.compliance" and isinstance(value, list) and "ftr" in value:
            set_path(ctx, "business.ftr_target", True)
        save_json(st.p_ctx, ctx)
        print(f"{b['sets']} = {value!r}")


def cmd_context(a, st):
    ctx = st.ctx()
    if a.ctx_cmd == "show":
        print(json.dumps(ctx, ensure_ascii=False, indent=1))
    else:
        for kv in a.pairs:
            k, _, v = kv.partition("=")
            try:
                val = json.loads(v)
            except ValueError:
                val = v
            if k.strip() == "language":
                lang = i18n.norm_lang(val)
                if lang is None:
                    die(i18n.unsupported_message(val))
                val = lang
            set_path(ctx, k.strip(), val)
        save_json(st.p_ctx, ctx)
        print("context updated")


# ------------------------------------------------------------------ plan / status
def missing_business(ctx):
    miss = []
    for k in ("criticality", "availability_target", "traffic", "budget_sensitivity"):
        if not get_path(ctx, f"business.{k}"):
            miss.append(k)
    for k in ("data_sensitivity", "compliance"):
        if not get_path(ctx, f"business.{k}"):
            miss.append(k)
    return miss


def cmd_plan(a, st):
    w = st.work()
    ctx = st.ctx()
    cards = load_cards(st)
    if a.skip:
        w["meta"].setdefault("scans", {})
        for ch in a.skip.split(","):
            w["meta"]["scans"][ch] = {"skipped": True, "at": now_iso()}
        save_json(st.p_work, w)
    items = active(w)
    tot = len(items)
    by_ch = collections.Counter(i["route"]["ch"][0] for i in items)
    res = sum(1 for i in items if resolved(i))
    sc = scans_done(w)
    answered = st.cards_state()
    pend = [(c, ids) for c, ids in pending_cards(w, cards) if not card_is_answered(answered, c["id"], ids)]
    waiting = [i for i in items if not resolved(i) and not ready_for_interview(i, w)]
    unresolved_noncard = [i for i in items if not resolved(i) and not i["route"].get("card")]
    steps = []
    miss = missing_business(ctx)
    if miss:
        steps.append(f"Business-requirement questions (`business show` → `business answer`): unanswered {miss}")
    if any("repo" in i["route"]["ch"] for i in items) and "repo" not in sc:
        steps.append("Collect repository evidence with `scan-repo --path . [--gh OWNER/REPO]` (includes GitHub activity; or `plan --skip repo`)")
    iac = review_mode(w) == "iac"
    if iac:
        if any("aws" in i["route"]["ch"] for i in items) and "iac" not in sc:
            steps.append("Static IaC scan with `scan-iac --path .` (the live AWS environment is not read: mode=iac; or `plan --skip iac`)")
    elif any("aws" in i["route"]["ch"] for i in items) and "aws" not in sc:
        steps.append("Read-only AWS scan with `scan-aws --profile P [--regions ap-northeast-1]` "
                     "(--regions defaults to `aws.regions` in the context; run `auth-check` first; or `plan --skip aws`)")
    scan_items = [i for i in items if not resolved(i) and i["route"]["ch"][0] != "interview"]
    if any(not v.get("skipped") for v in sc.values()) and scan_items:      # skipped scans produced nothing to interpret
        if iac:
            steps.append(f"Record verdicts from the scan results (references/iac-readonly-checks.md, repo-signals.md) with `apply answers.json` "
                         f"(verdicts read from IaC use channel=repo, confidence at most M; {len(scan_items)} item(s) on aws/repo routes still undetermined)")
        else:
            steps.append(f"Record verdicts from the scan results (references/aws-readonly-checks.md, repo-signals.md) with `apply answers.json` ({len(scan_items)} item(s) on aws/repo routes still undetermined)")
    if pend:
        steps.append(f"Decision cards: {len(pend)} (`cards next -n 5`): " + ", ".join(c["id"] for c, _ in pend[:6]) + (" ..." if len(pend) > 6 else ""))
    steps.append(f"`finalize` marks undetermined items as '{EN['ch']['assumed']}' (✗); then `export --format xlsx`")
    steps = [f"{n}. {t}" for n, t in enumerate(steps, 1)]
    tiers = collections.Counter(tier(i) for i in items)
    out = {"total": tot, "resolved": res, "primary_channel": dict(by_ch), "tiers": dict(tiers), "scans": sc, "mode": review_mode(w),
           "pending_cards": [c["id"] for c, _ in pend], "waiting_for_scan": len(waiting), "next_steps": steps}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    if iac:
        print(f"mode: {review_mode(w)} — {EN['mode']['iac']}")
    print(f"items {tot} / resolved {res} / primary channel: {dict(by_ch)}")
    print(f"routes: decided {tiers['decide']} / draft {tiers['draft']} / interview-only {tiers['ask']}")
    print("scans: " + (", ".join(f"{k} (skipped)" if v.get("skipped") else f"{k} ({str(v.get('at', ''))[:10]})" for k, v in sc.items()) or "none"))
    print("next steps:")
    print("\n".join("  " + s for s in steps))


def verdict_counts(items):
    c = collections.Counter()
    for i in items:
        c[i.get("verdict") or "undetermined"] += 1
    return c


def cmd_status(a, st):
    w = st.work()
    items = active(w)
    print("pillar".ljust(26), "○".rjust(4), "△".rjust(4), "✗".rjust(4), "N/A".rjust(4), "undetermined".rjust(12), "assumed".rjust(8))
    for p in PILLAR_ORDER:
        its = [i for i in items if i["pillar"] == p]
        if not its:
            continue
        c = verdict_counts(its)
        assumed = sum(1 for i in its if i.get("channel") == "assumed")
        print(p.ljust(26), str(c["○"]).rjust(4), str(c["△"]).rjust(4), str(c["✗"]).rjust(4), str(c["N/A"]).rjust(4), str(c["undetermined"]).rjust(12), str(assumed).rjust(8))
    r = rates(items)
    print(f"recorded {r['recorded']}/{r['total']} ({r['recorded_rate']:.0%}) / confirmed (excl. assumed and undetermined) {r['confirmed']}/{r['total']} ({r['confirmed_rate']:.0%})")
    stale = sum(1 for i in items if resolved(i) and is_stale(i))
    if stale:
        print(f"evidence older than {STALE_DAYS} days: {stale} item(s) (list them with `diff`)")
    if review_mode(w) == "iac":
        print(f"mode: iac — {EN['mode']['iac']}")
    n_iac = len(iac_only_items(w))
    if n_iac:
        print(f"{n_iac} item(s) judged from IaC only (live environment not verified); they appear in OpenItems (when the live environment can be read, run `mode both` then `scan-aws`)")


# ------------------------------------------------------------------ re-review: snapshot / diff
def history_dir(st):
    return os.path.join(st.dir, "history")


def snapshot_items(items):
    keys = ("pillar", "question_id", "best_practice", "verdict", "channel", "confidence", "evidence_at", "priority")
    return {i["choice_id"]: {k: i.get(k) for k in keys} for i in items}


def cmd_snapshot(a, st):
    w = st.work()
    items = active(w)
    label = re.sub(r"[^0-9A-Za-z_-]+", "-", a.label or "").strip("-")
    name = datetime.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "") + ".json"
    path = os.path.join(history_dir(st), name)
    if os.path.exists(path):
        die(f"a snapshot with this name already exists: {name}")
    meta = {k: w["meta"].get(k) for k in ("source", "lens", "lens_version", "fetched_at", "workload_id", "scans")}
    save_json(path, {"taken_at": now_iso(), "label": a.label or "", "meta": meta, "counts": dict(verdict_counts(items)),
                     "rates": rates(items), "items": snapshot_items(items)})
    print(f"saved {path} ({len(items)} items)")


def load_snapshot(st, ref):
    d = history_dir(st)
    files = sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []
    if not files:
        die("no snapshots found; run `snapshot` first.")
    if not ref:
        name = files[-1]
    else:
        hit = [f for f in files if f in (ref, ref + ".json")] or [f for f in files if ref in f]
        if len(hit) != 1:
            die(f"snapshot {ref!r} does not identify a unique snapshot (matches: {', '.join(hit) or 'none'} / recent: {', '.join(files[-5:])})")
        name = hit[0]
    return name, load_json(os.path.join(d, name))


def diff_items(old, cur, stale_days):
    """old/cur: choice_id -> snapshot_items entry. Returns lists of change records."""
    out = {k: [] for k in ("regressed", "improved", "changed", "confirmed", "added", "removed", "stale")}

    def rec(cid, n, **kw):
        return dict({"choice_id": cid, "pillar": n.get("pillar"), "best_practice": n.get("best_practice"),
                     "channel": n.get("channel"), "confidence": n.get("confidence")}, **kw)

    def settled(n):
        return n.get("verdict") is not None and n.get("channel") != "assumed"

    for cid, n in cur.items():
        o = old.get(cid)
        if o is None:
            out["added"].append(rec(cid, n, **{"from": "(added)", "to": n.get("verdict")}))
            continue
        if o.get("verdict") != n.get("verdict"):
            if o.get("verdict") in VERDICT_SCORE and n.get("verdict") in VERDICT_SCORE:
                kind = "improved" if VERDICT_SCORE[n["verdict"]] > VERDICT_SCORE[o["verdict"]] else "regressed"
            else:
                kind = "changed"
            out[kind].append(rec(cid, n, **{"from": o.get("verdict"), "to": n.get("verdict")}))
        if not settled(o) and settled(n):
            out["confirmed"].append(rec(cid, n, **{"from": o.get("verdict"), "to": n.get("verdict")}))
        if settled(n) and is_stale(n, stale_days):
            out["stale"].append(rec(cid, n, evidence_at=n.get("evidence_at")))
    for cid, o in old.items():
        if cid not in cur:
            out["removed"].append(rec(cid, o, **{"from": o.get("verdict"), "to": "(removed)"}))
    return out


def norm_counts(c):
    """Verdict counts with the key 未判定 that older snapshots stored renamed to "undetermined"."""
    c = dict(c)
    if "未判定" in c:
        c["undetermined"] = c.get("undetermined", 0) + c.pop("未判定")
    return c


def cmd_diff(a, st):
    w = st.work()
    name, snap = load_snapshot(st, a.since)
    cur = snapshot_items(active(w))
    d = diff_items(snap["items"], cur, a.stale_days)
    now_counts = dict(verdict_counts(active(w)))
    before_counts = norm_counts(snap["counts"]) if snap.get("counts") is not None else None
    lens_before, lens_now = (snap.get("meta") or {}).get("lens_version"), w["meta"].get("lens_version")
    if a.json:
        print(json.dumps({"since": name, "taken_at": snap.get("taken_at"), "lens_version": {"before": lens_before, "now": lens_now},
                          "counts": {"before": before_counts, "now": now_counts}, "stale_days": a.stale_days,
                          **{k: v for k, v in d.items()}}, ensure_ascii=False, indent=1))
        return
    fmt = lambda c: " ".join(f"{v}={(c or {}).get(v, 0)}" for v in ("○", "△", "✗", "N/A", "undetermined"))
    print(f"previous: {name}" + (f" ({snap['label']})" if snap.get("label") else "") + " → now")
    print(f"  {fmt(before_counts)}  →  {fmt(now_counts)}")
    if lens_before != lens_now:
        print(f"  lens version: {lens_before} → {lens_now}")
    print(f"  regressed {len(d['regressed'])} / improved {len(d['improved'])} / other verdict changes {len(d['changed'])} / newly confirmed (assumed or undetermined → confirmed) {len(d['confirmed'])}"
          f" / added by lens {len(d['added'])} / removed {len(d['removed'])} / stale evidence ({a.stale_days}+ days) {len(d['stale'])}")

    def show(title, rows, limit):
        if not rows:
            return
        print(f"\n{title}:")
        for r in rows[:limit]:
            tr = f" {r.get('from') or 'undetermined'}→{r.get('to') or 'undetermined'}" if ("from" in r or "to" in r) else (f" {r['evidence_at'][:10]}" if r.get("evidence_at") else "")
            print(f"  {str(r['pillar'])[:12]:<12} {r['choice_id']:<48}{tr}  ({EN['ch'].get(r['channel'], '-')}/{EN['conf'].get(r['confidence'], '-')}) {str(r['best_practice'])[:36]}")
        if len(rows) > limit:
            print(f"  ... {len(rows) - limit} more (all with --json)")
    show("Regressed (needs action)", d["regressed"], 10 ** 6)
    show("Improved", d["improved"], a.limit)
    show("Other verdict changes", d["changed"], a.limit)
    show("Added by lens update", d["added"], a.limit)
    show("Removed from lens", d["removed"], a.limit)
    show("Stale evidence (rescan / re-check)", d["stale"], a.limit)


def cmd_items(a, st):
    """List items for evidence recording / generic per-item interview."""
    w = st.work()
    rows = []
    for it in active(w):
        if a.unresolved and resolved(it):
            continue
        if a.channel and it["route"]["ch"][0] != a.channel:
            continue
        if a.pillar and PILLAR_ID.get(it["pillar"]) != a.pillar and it["pillar"] != a.pillar:
            continue
        if a.area and a.area not in it["route"]["areas"]:
            continue
        rows.append({"choice_id": it["choice_id"], "question_id": it["question_id"], "pillar": it["pillar"],
                     "best_practice": it["best_practice"], "description": it["description"] if a.full else it["description"][:160],
                     "channels": it["route"]["ch"], "areas": it["route"]["areas"], "card": it["route"].get("card"),
                     "verdict": it.get("verdict"), "channel": it.get("channel")})
    rows = rows[: a.limit] if a.limit else rows
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            print(f"{r['pillar'][:4]:<5}{r['choice_id']:<62} {'/'.join(c[0].upper() for c in r['channels']):<4} {','.join(r['areas']):<26} {r['best_practice'][:60]}")
        print(f"({len(rows)} items)")


# ------------------------------------------------------------------ coverage / generic routing
def cmd_coverage(a, st):
    w = st.work()
    items = active(w)
    only_no = [i for i in items if str(i.get("aws_checkable", "")).strip().lower().startswith("no")]
    scope_name = "AWSCheckable=No items" if only_no and not a.all else "all items"
    scope = only_no if only_no and not a.all else items
    rows = collections.OrderedDict()
    for p in PILLAR_ORDER:
        its = [i for i in scope if i["pillar"] == p]
        if its:
            c = collections.Counter(tier(i) for i in its)
            rows[p] = {"decide": c["decide"], "draft": c["draft"], "ask": c["ask"], "total": len(its)}
    tot = collections.Counter(tier(i) for i in scope)
    n = len(scope) or 1
    out = {"scope": scope_name, "total": len(scope), "decide": tot["decide"], "draft": tot["draft"], "ask": tot["ask"],
           "evidence_share": round((tot["decide"] + tot["draft"]) / n, 3), "by_pillar": rows}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"scope: {scope_name} ({len(scope)} items)")
    print("pillar".ljust(26), "decided".rjust(8), "draft".rjust(6), "interview".rjust(10), "total".rjust(6))
    for p, r in rows.items():
        print(p.ljust(26), str(r["decide"]).rjust(8), str(r["draft"]).rjust(6), str(r["ask"]).rjust(10), str(r["total"]).rjust(6))
    print("total".ljust(26), str(tot["decide"]).rjust(8), str(tot["draft"]).rjust(6), str(tot["ask"]).rjust(10), str(len(scope)).rjust(6))
    print(f"items reachable with evidence (decided + draft): {tot['decide'] + tot['draft']} / {len(scope)} ({out['evidence_share']:.0%})")
    print("decided = settled from AWS / repository evidence / draft = evidence is pre-filled on the decision card and a person confirms it / interview = only a person can answer")


def cmd_route_unknown(a, st):
    """Material for classifying choices that have no routing (another lens / new best practices)."""
    w = st.work()
    routing = load_routing(st)
    cards = load_cards(st)
    rows = []
    for it in active(w):
        k = it["route"].get("known")
        if k == "none" or (a.all and k == "question"):
            rows.append({"choice_id": it["choice_id"], "question_id": it["question_id"], "pillar": it["pillar"],
                         "question_title": it["question_title"], "best_practice": it["best_practice"],
                         "description": it["description"][:300], "current": it["route"]["ch"], "known": k})
    out = {"how_to": ("Classify each item following `vocab`, write {\"choices\": {CHOICE_ID: {\"ch\": [...], \"areas\": [...], "
                      "\"draft\": true|false, \"card\": CARD_ID|null}}} and run `route-apply FILE`. "
                      "In ch, put the most reliable route first and always put interview last. Use draft:false only when the live configuration alone can settle "
                      "whether the practice is in place; use draft:true when you are inferring it from the presence or absence of documents or activity. "
                      "Items only a person can answer get ch: [interview] only."),
           "vocab": routing.get("vocab"), "cards": [{"id": c["id"], "title": c["title"]} for c in cards["cards"]],
           "count": len(rows), "items": rows[: a.limit] if a.limit else rows}
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_route_apply(a, st):
    data = load_json(a.file)
    choices = data.get("choices", data) if isinstance(data, dict) else die("the JSON must look like {choices: {...}}")
    routing = load_routing(st)
    vocab = routing.get("vocab", {})
    card_ids = {c["id"] for c in load_cards(st)["cards"]}
    ok_ch = set(CHANNELS) - {"assumed", "imported"}
    local_p = os.path.join(st.dir, "routing.local.json")
    local = load_json(local_p, {"choices": {}})
    local.setdefault("choices", {})
    warns = []
    for cid, r in choices.items():
        chs = list(r.get("ch") or [])
        bad = [c for c in chs if c not in ok_ch]
        if not chs or bad:
            die(f"{cid}: invalid ch ({bad or 'empty'}); allowed values are {sorted(ok_ch)}")
        if chs[-1] != "interview":
            chs.append("interview")
        card = r.get("card")
        if card and card not in card_ids:
            die(f"{cid}: card {card!r} does not exist")
        areas = list(r.get("areas") or [])
        for x in areas:
            if x not in vocab.get("areas", {}):
                warns.append(f"{cid}: unknown area {x!r} (not linked to any check)")
        local["choices"][cid] = {"ch": chs, "areas": areas, "draft": bool(r.get("draft")), "card": card}
    save_json(local_p, local)
    w = st.work()
    apply_routing(w["items"], load_routing(st))
    save_json(st.p_work, w)
    print(f"route-apply: {len(choices)} choices → {local_p}")
    for x in warns[:10]:
        print("  warn:", x)


# ------------------------------------------------------------------ scans
def cmd_scan_repo(a, st):
    import kensa_scan
    paths = a.path or get_path(st.ctx(), "repo.paths") or ["."]
    gh = a.gh or get_path(st.ctx(), "repo.github")
    results = [kensa_scan.scan_repo(p, gh_repo=gh if n == 0 else None) for n, p in enumerate(paths)]
    os.makedirs(st.p_ev, exist_ok=True)
    save_json(os.path.join(st.p_ev, "repo-scan.json"), {"repos": results})
    w = st.work()
    w["meta"].setdefault("scans", {})["repo"] = {"at": now_iso(), "paths": paths}
    save_json(st.p_work, w)
    for r in results:
        s = r["signals"]
        print(f"[{r['path']}] files={r['files_scanned']} iac={s['iac']['tools']} workflows={len(s['ci']['workflows'])} "
              f"tests={s['tests']['test_files']} dependabot={s['deps']['dependabot']} codeowners={s['review']['codeowners']} "
              f"hardcoded_keys={s['hardcoded_secrets']['count']}")
    print(f"→ {os.path.join(st.p_ev, 'repo-scan.json')}")


def cmd_scan_iac(a, st):
    """Statically read IaC (Terraform / CloudFormation / `cdk synth` output). Never touches AWS (no credentials needed). Runs in any mode."""
    import kensa_scan
    w = st.work()
    paths = a.path or get_path(st.ctx(), "repo.paths") or ["."]
    results = [kensa_scan.scan_iac(p) for p in paths]
    os.makedirs(st.p_ev, exist_ok=True)
    save_json(os.path.join(st.p_ev, "iac-scan.json"), {"repos": results})
    w["meta"].setdefault("scans", {})["iac"] = {"at": now_iso(), "paths": paths}
    save_json(st.p_work, w)
    for r in results:
        found = {k: v["count"] for k, v in r["resources"].items()}
        print(f"[{r['path']}] files={r['files_scanned']} tools={r['tools']} resources={sum(found.values())} kinds={len(found)}")
        if found:
            print("  " + ", ".join(f"{k}={n}" for k, n in found.items()))
        for n in r["notes"]:
            print(f"  note: {n}")
    print(f"→ {os.path.join(st.p_ev, 'iac-scan.json')}")
    if review_mode(w) == "iac":
        print("mode=iac: not finding something in IaC does not mean it is absent in AWS (it may be in another stack, created by hand, or in another repository). "
              "Do not treat 'not found' as grounds for ✗; send it to the decision card (references/iac-readonly-checks.md)")


def cmd_mode(a, st):
    w = st.work()
    cur = review_mode(w)
    if a.value is None:
        print(f"mode: {cur} — {EN['mode'][cur]}")
        return
    if a.value == cur:
        print(f"mode is already {cur} — {EN['mode'][cur]}")
        return
    w["meta"]["mode"] = a.value
    save_json(st.p_work, w)
    print(f"mode: {cur} → {a.value} — {EN['mode'][a.value]}")
    if a.value == "iac":
        print("  The live AWS environment is not read (`scan-aws` is refused and no channel=aws verdict is recorded).")
        print("  Next: `scan-repo` and `scan-iac --path .`. Items judged from IaC only are capped at confidence M and appear in OpenItems with the source '" + EN["iac_only"] + "'.")
    else:
        n = len(iac_only_items(w))
        print("  To read the live environment: `auth-check` → `scan-aws`. Items with an aws route do not move to interviews until `scan-aws` has run (or `plan --skip aws`).")
        if n:
            print(f"  The {n} item(s) judged from IaC only are replaced when you `apply` a live-environment verdict (channel=aws) "
                  "(a mismatch is kept in notes as suspected drift). They stay in OpenItems until then.")


def aws_profile(a, ctx, kind="scan"):
    """The profile to use. --profile always wins.
    kind="scan": reading the AWS environment (scan-aws) → aws.profile; it should point at a read-only role.
    kind="wa":   touching the W-A Tool (seed --from-wa-tool / push / precheck / workload-create) → aws.wa_profile, else aws.profile.
                 Give write permission only to the W-A Tool role, never to the scan role (docs/aws-iam.md)."""
    if getattr(a, "profile", None):
        return a.profile
    if kind == "wa":
        return get_path(ctx, "aws.wa_profile") or get_path(ctx, "aws.profile")
    return get_path(ctx, "aws.profile")


def check_auth(a, st, regions=None, kind="scan"):
    """Before touching AWS, check the credentials and show the target. Returns (session, identity); stops with exit code 3 when they cannot be used.

    How to authenticate (SSO, environment variables, assume-role, instance role) is left to boto3's standard lookup order (--profile > AWS_PROFILE > environment variables > default).
    With kind="scan", if the context has aws.account_ids, it also checks that the target is one of them
    (a W-A Tool workload can live in another account, so kind="wa" does not compare; the user checks the account shown)."""
    import kensa_scan
    ctx = st.ctx()
    profile = aws_profile(a, ctx, kind)
    region = getattr(a, "region", None) or (regions[0] if regions else None)
    try:
        session = kensa_scan.make_session(profile, region)
        ident = kensa_scan.preflight(session, get_path(ctx, "aws.account_ids") or [] if kind == "scan" else [], profile=profile, region=region)
    except kensa_scan.AuthError as e:
        die(str(e), code=3)
    print(f"AWS target: account {ident['account']} / {ident['principal_type']} / {ident['arn']}" + (f" / profile={profile}" if profile else "")
          + (" [for W-A Tool]" if kind == "wa" else ""))
    if ident["principal_type"] == "root":
        if not getattr(a, "allow_root", False):
            die("Refusing to run with root user credentials. Use a read-only role (docs/aws-iam.md). "
                "Pass --allow-root only if you really have to.", code=3)
        print("warning: these are root user credentials. Use a read-only role (docs/aws-iam.md).", file=sys.stderr)
    return session, ident


def cmd_auth_check(a, st):
    _, ident = check_auth(a, st, kind=a.target)
    if a.target == "scan" and not get_path(st.ctx(), "aws.account_ids"):
        print("hint: aws.account_ids is not set in the context. Setting it makes kensa stop when it points at another account: "
              f"`context set aws.account_ids='[\"{ident['account']}\"]'` (if this account is the right one)")


def cmd_scan_aws(a, st):
    import kensa_scan
    if review_mode(st.work()) == "iac":
        die("mode=iac: the live AWS environment is not read. Use `scan-iac` for IaC; switch to `mode both` to read the live environment as well.")
    ctx = st.ctx()
    regions = a.regions.split(",") if a.regions else (get_path(ctx, "aws.regions") or [])
    if not regions:
        die("no Region given: pass --regions ap-northeast-1 (comma-separated), or set it once with `context set aws.regions='[\"ap-northeast-1\"]'`.")
    session, _ = check_auth(a, st, regions)
    res = kensa_scan.run_aws_scan(session, regions, limit=a.limit, only=set(a.only.split(",")) if a.only else None)
    os.makedirs(st.p_ev, exist_ok=True)
    save_json(os.path.join(st.p_ev, "aws-scan.json"), res)
    w = st.work()
    w["meta"].setdefault("scans", {})["aws"] = {"at": now_iso(), "regions": regions}
    save_json(st.p_work, w)
    c = collections.Counter(v["status"] for v in res["checks"].values())
    print(f"AWS scan done (read-only). checks: {dict(c)}  → {os.path.join(st.p_ev, 'aws-scan.json')}")
    denied = [k for k, v in res["checks"].items() if v["status"] == "denied"]
    if denied:
        print("Checks that lacked permission (treated as 'cannot verify' → interview):", ", ".join(denied))


# ------------------------------------------------------------------ finalize / export
def default_priority(it):
    pid = PILLAR_ID.get(it["pillar"], "")
    v = it.get("verdict")
    if v == "✗":
        return "P2" if pid in FTR_PILLARS else "P3"
    if v == "△":
        return "P3"
    return ""


def cmd_finalize(a, st):
    w = st.work()
    items = active(w)
    if not scans_done(w) and not any(resolved(i) for i in items) and not a.allow_empty:
        die(f"nothing has been scanned, recorded or answered yet: `finalize` would mark all {len(items)} items as assumed ✗ (needs confirmation). "
            "Run `plan` for the next steps (scan, `apply`, `cards next`); skip a scan on purpose with `plan --skip aws,repo`; or pass --allow-empty to finalize anyway.")
    todo = [i for i in items if i.get("verdict") is None]
    scans = sorted({s for i in todo for s in missing_scans(i, w)})
    if scans:
        print(f"note: the {' / '.join(scans)} scan has not been run (or skipped with `plan --skip`) yet; items that wait for it will become assumed ✗.")
    cards = load_cards(st)
    answered = st.cards_state()
    open_cards = [(c, ids) for c, ids in pending_cards(w, cards) if not card_is_answered(answered, c["id"], ids)]
    if open_cards:
        n_items = sum(len(ids) for _, ids in open_cards)
        print(f"note: {len(open_cards)} decision card(s) are still unanswered ({n_items} items): they will become assumed ✗. `cards next` asks them.")
    n = 0
    for it in active(w):
        if it.get("verdict") is None:
            it.update(verdict="✗", channel="assumed", confidence="L", status="needs_review",
                      evidence=i18n.system_text("ev.assumed"),
                      recommendation=it.get("recommendation") or i18n.system_text("rec.assumed"),
                      effort=it.get("effort") or "M")
            n += 1
        if it["verdict"] in ("△", "✗") and not it.get("priority"):
            it["priority"] = default_priority(it)
        if it["verdict"] in ("△", "✗") and not it.get("effort"):
            it["effort"] = "M"
    drafts = 0
    for it in active(w):
        if it.get("channel") in EVIDENCE_CH and it["route"].get("draft"):
            it["status"] = "needs_review"
            drafts += 1
    save_json(st.p_work, w)
    print(f"finalized: {n} unresolved items set to ✗ (assumed, confidence L, needs confirmation)")
    if drafts:
        print(f"  {drafts} items are evidence-based drafts (auto-detected). They appear in OpenItems.")


def estimate_risk(items):
    """Rough local estimate only (the WA Tool computes the authoritative risk after push)."""
    byq = collections.defaultdict(list)
    for i in items:
        byq[(i["pillar"], i["question_id"])].append(i)
    out = collections.defaultdict(lambda: collections.Counter())
    for (p, q), its in byq.items():
        if any(not resolved(i) for i in its):   # besides undetermined items, assumed placeholders (✗ set by finalize with no evidence or answer) are unresolved too
            out[p]["UNRESOLVED"] += 1
            continue
        app = [i for i in its if i["verdict"] != "N/A"]
        if not app:
            out[p]["NOT_APPLICABLE"] += 1
            continue
        sel = sum(1 for i in app if i["verdict"] == "○")
        out[p]["NONE" if sel == len(app) else "HIGH" if sel == 0 else "MEDIUM"] += 1
    return out


# Column headers and verdict / confidence / source / route / tier labels of the exports: kensa_i18n.LABELS[lang]


def tier(it):
    """decide = can be settled from AWS / repository evidence / draft = the evidence is a draft that a person confirms / ask = only a person can answer."""
    r = it["route"]
    ev = [c for c in r["ch"] if c != "interview"]
    if not ev:
        return "ask"
    if r.get("draft") or not any(c in ("aws", "repo") for c in ev):
        return "draft"
    return "decide"


_AREA_SCOPE = None


def area_scope():
    """area -> 'aws' | 'repo' (which scanner the area belongs to), from routing.json vocab."""
    global _AREA_SCOPE
    if _AREA_SCOPE is None:
        _AREA_SCOPE = (assets("routing.json").get("vocab") or {}).get("area_scope", {})
    return _AREA_SCOPE


def route_text(it, lang=None):
    t = i18n.labels(lang)
    r = it["route"]
    parts = []
    for c in r["ch"]:
        if c == "interview":
            tags = []
        else:
            tags = [a for a in r["areas"] if area_scope().get(a, c) == c]
        parts.append(t["route"][c] + (f"[{','.join(tags)}]" if tags else ""))
    return t["tier"][tier(it)] + t["tier_sep"] + " → ".join(parts) + (f" ({r['card']})" if r.get("card") else "")


def row_of(it, mask=False, lang=None):
    """One Checklist / CSV row in the labels of ``lang`` (default: the active language). Stored system texts are shown in that language."""
    t = i18n.labels(lang)

    def m(s):
        s = i18n.localize_stored(s, lang)
        return mask_accounts(s) if mask else s
    v = it.get("verdict") or t["undetermined"]
    notes = it.get("notes", "")
    if it.get("status") in ("new_in_lens",):
        notes = (t["added_in_lens"] + notes).strip()
    return [it["pillar"], it["question_id"], it["question_title"], it["choice_id"], it["best_practice"], it["description"],
            it.get("aws_checkable", ""), v, ch_label(it, lang), t["conf"].get(it.get("confidence"), ""),
            m(it.get("evidence", "")), m(it.get("org_shape", "")), m(it.get("recommendation", "")), it.get("effort", ""),
            it.get("priority", ""), it.get("owner", ""), it["route"].get("card") or "", route_text(it, lang), m(notes),
            (it.get("evidence_at") or "")[:10]]


def item_dict(it, mask=False, lang=None):
    """An item of the JSON export: codes only (verdict symbol, channel / confidence / action_status codes); stored system texts are shown in ``lang``."""
    m = mask_accounts if mask else (lambda s: s)
    d = {k: it.get(k) for k in ("pillar", "question_id", "question_title", "choice_id", "best_practice", "verdict", "channel", "confidence",
                                "evidence_at", "effort", "priority", "owner", "due", "action_status", "status", "basis")}
    if d.get("action_status"):
        d["action_status"] = i18n.norm_action_status(d["action_status"]) or d["action_status"]    # always the code, also for old Japanese values
    for k in ("evidence", "org_shape", "recommendation", "notes"):
        d[k] = m(i18n.localize_stored(it.get(k, ""), lang))
    d["route"] = {"tier": tier(it), "channels": it["route"]["ch"], "card": it["route"].get("card")}
    return d


def mask_deep(o):
    if isinstance(o, dict):
        return {k: mask_deep(v) for k, v in o.items()}
    if isinstance(o, list):
        return [mask_deep(v) for v in o]
    return mask_accounts(o)


def localize_deep(o, lang):
    """Show the stored system-generated texts inside a nested value in ``lang``."""
    if isinstance(o, dict):
        return {k: localize_deep(v, lang) for k, v in o.items()}
    if isinstance(o, list):
        return [localize_deep(v, lang) for v in o]
    return i18n.localize_stored(o, lang)


def cmd_export(a, st):
    w = st.work()
    ctx = st.ctx()
    lang = st.lang
    t = i18n.labels(lang)
    items = sorted(active(w), key=lambda i: (PILLAR_ORDER.index(i["pillar"]) if i["pillar"] in PILLAR_ORDER else 99, i["question_id"], i["choice_id"]))
    if a.format == "csv":
        with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(t["cols"])
            for it in items:
                wr.writerow([csv_safe(v) for v in row_of(it, a.mask_account_ids, lang)])
        print(f"wrote {a.out}")
        warn_unmasked_export(a)
        return
    if a.format == "json":
        ctx_out = localize_deep({k: v for k, v in ctx.items() if k != "answers"}, lang)
        doc = {"generated_at": now_iso(), "tool": "kensa", "language": lang,
               "meta": {**{k: w["meta"].get(k) for k in ("source", "lens", "lens_version", "fetched_at", "workload_id")}, "mode": review_mode(w)},
               "context": mask_deep(ctx_out) if a.mask_account_ids else ctx_out,
               "counts": dict(verdict_counts(items)), "rates": rates(items),
               "items": [item_dict(it, a.mask_account_ids, lang) for it in items]}
        save_json(a.out, doc)
        print(f"wrote {a.out} ({len(items)} items)")
        warn_unmasked_export(a)
        return
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
        from openpyxl.formatting.rule import CellIsRule
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        die("openpyxl is required for xlsx. Run through `uv run …/kensa.py` (it installs openpyxl automatically), or `python3 -m pip install openpyxl` "
            "(on a PEP 668 system: `python3 -m venv .venv && .venv/bin/pip install openpyxl boto3`). `--format csv` needs nothing.")
    cards = load_cards(st)
    wb = Workbook()
    hdr_fill = PatternFill("solid", fgColor="1F3864")
    hdr_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")
    F = lambda x: fill(x, ctx, lang)
    assumed_label = t["ch"]["assumed"]

    def header(ws, cols, widths):
        ws.append(cols)
        for c in ws[1]:
            c.fill, c.font, c.alignment, c.border = hdr_fill, hdr_font, Alignment(wrap_text=True, vertical="center"), border
        for i, wd in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = wd
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"

    # ---- Checklist (sheet 2; created first to know the range)
    ws_sum = wb.active
    ws_sum.title = "Summary"
    ws = wb.create_sheet("Checklist")
    header(ws, t["cols"], [18, 22, 34, 34, 36, 50, 11, 9, 16, 8, 44, 44, 52, 7, 8, 16, 20, 34, 30, 12])
    for it in items:
        ws.append(row_of(it, a.mask_account_ids, lang))
    last = ws.max_row
    for row in ws.iter_rows(min_row=2, max_row=last):
        for c in row:
            c.alignment, c.border = wrap, border
    rng = f"H2:H{last}"
    for val, color in (("○", "C6EFCE"), ("△", "FFEB9C"), ("✗", "FFC7CE"), ("N/A", "E7E6E6"), (t["undetermined"], "F8CBAD")):
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{val}"'], fill=PatternFill("solid", bgColor=color, fgColor=color)))
    ws.conditional_formatting.add(f"I2:I{last}", CellIsRule(operator="equal", formula=[f'"{assumed_label}"'], fill=PatternFill("solid", bgColor="F8CBAD", fgColor="F8CBAD")))

    # ---- Summary with live formulas (the criteria are the active locale's labels, as written in the Checklist)
    ws_sum["A1"] = t["sum_title"]
    ws_sum["A1"].font = Font(bold=True, size=14)
    meta = w["meta"]
    ws_sum["A2"] = t["sum_info"].format(service=get_path(ctx, 'service.name') or get_path(ctx, 'service.description') or '-',
                                        lens=f"{meta.get('lens')} {meta.get('lens_version') or ''}", at=now_iso())
    ws_sum.append([])
    ws_sum.append(t["sum_hdr"])
    for c in ws_sum[4]:
        c.fill, c.font, c.alignment, c.border = hdr_fill, hdr_font, Alignment(wrap_text=True, horizontal="center"), border
    est = estimate_risk(items)
    r0 = 5
    for n, p in enumerate(PILLAR_ORDER):
        if not any(i["pillar"] == p for i in items):
            continue
        r = ws_sum.max_row + 1
        ws_sum.append([p] + [f'=COUNTIFS(Checklist!$A:$A,$A{r},Checklist!$H:$H,"{v}")' for v in ("○", "△", "✗", "N/A", t["undetermined"])] +
                      [f"=SUM(B{r}:F{r})", f'=COUNTIFS(Checklist!$A:$A,$A{r},Checklist!$I:$I,"{assumed_label}")',
                       t["ftr_required"] if PILLAR_ID[p] in FTR_PILLARS else "-",
                       len({i["question_id"] for i in items if i["pillar"] == p}), est[p]["HIGH"], est[p]["UNRESOLVED"]])
        for col, f in ((13, f"=IF(G{r}=0,0,(G{r}-F{r})/G{r})"), (14, f"=IF(G{r}=0,0,(G{r}-F{r}-H{r})/G{r})")):
            ws_sum.cell(row=r, column=col, value=f).number_format = "0%"
    rl = ws_sum.max_row
    ws_sum.append([t["sum_total"]] + [f"=SUM({get_column_letter(c)}{r0}:{get_column_letter(c)}{rl})" for c in range(2, 9)])
    rt = ws_sum.max_row
    for col, f in ((13, f"=IF(G{rt}=0,0,(G{rt}-F{rt})/G{rt})"), (14, f"=IF(G{rt}=0,0,(G{rt}-F{rt}-H{rt})/G{rt})")):
        ws_sum.cell(row=rt, column=col, value=f).number_format = "0%"
    for c in ws_sum[ws_sum.max_row]:
        c.font = Font(bold=True)
    ws_sum.append([])
    ws_sum.append(t["tier_hdr"])
    for c in ws_sum[ws_sum.max_row]:
        c.font = Font(bold=True)
    for key in ("decide", "draft", "ask"):
        ws_sum.append([t["tier"][key], f'=COUNTIF(Checklist!$R:$R,"{t["tier"][key]}*")', t["tier_meaning"][key]])
    ws_sum.append([])
    ws_sum.append([t["notes_hdr"]])
    for key in ("note_hri", "note_assumed", "note_rates", "note_ftr"):
        ws_sum.append([t[key].format(assumed=assumed_label)])
    n_iac = len(iac_only_items(w))
    if n_iac or review_mode(w) == "iac":
        ws_sum.append([t["note_iac"].format(n=n_iac, label=t["iac_only"])])
    for i, wd in enumerate([26, 10, 10, 10, 8, 10, 8, 18, 14, 9, 18, 16, 14, 14], 1):
        ws_sum.column_dimensions[get_column_letter(i)].width = wd

    # ---- Questions (all interview cards that cover items of this review)
    wsd = wb.create_sheet("Questions")
    header(wsd, t["q_hdr"], [22, 9, 28, 50, 34, 34, 30, 60, 50, 50, 8, 12, 34, 30])
    state = st.cards_state()
    cover = collections.Counter(i["route"].get("card") for i in items if i["route"].get("card"))
    for c in cards["cards"]:
        if not cover.get(c["id"]):
            continue
        s_ = state.get(c["id"])
        r = c["recommend"]
        wsd.append([c["id"], t["q_answered"] if s_ else t["q_unanswered"], F(c["title"]), ask_text(c, ctx, lang), F(c["ladder"]["full"]),
                    F(c["ladder"]["partial"]), F(c["ladder"]["none"]),
                    F(r["label"]) + t["q_effort"].format(effort=r["effort"], priority=r["priority"]) + "\n" + "\n".join(f"{i}. {F(x)}" for i, x in enumerate(r["steps"], 1)),
                    "\n".join(f"- {F(al['label'])} … {F(al['when'])} / {F(al['tradeoff'])}" for al in c["alternatives"]),
                    F(c["org_shape"]), cover[c["id"]],
                    t["q_current"][s_["current"]] if s_ else "",
                    F(s_["chosen"]) if s_ else "", s_.get("note", "") if s_ else ""])
    for row in wsd.iter_rows(min_row=2):
        for c in row:
            c.alignment, c.border = wrap, border

    # ---- OpenItems
    wso = wb.create_sheet("OpenItems")
    header(wso, t["oi_hdr"], [18, 36, 40, 8, 16, 8, 60, 20])
    for it in items:
        reason = None
        if it.get("verdict") is None:
            reason = (t["oi_undetermined_card"].format(card=it["route"]["card"]) if it["route"].get("card") and it["route"]["ch"][0] == "interview"
                      else t["oi_undetermined_scan"])
        elif it.get("channel") == "assumed":
            reason = t["oi_assumed"]
        elif it.get("channel") == "imported":
            reason = t["oi_imported"]
        elif it["route"].get("draft") and it.get("channel") in ("aws", "repo"):
            reason = t["oi_draft"]
        elif it.get("basis") == "iac_only":
            reason = t["oi_iac_only"]
        elif it.get("channel") == "interview" and it.get("verdict") == "○" and not str(it.get("evidence", "")).startswith("http"):
            reason = t["oi_self_reported"]
        elif it.get("confidence") == "L":
            reason = t["oi_low_conf"]
        elif is_stale(it):
            reason = t["oi_stale"].format(days=age_days(it), limit=STALE_DAYS)
        if reason:
            wso.append([it["pillar"], it["choice_id"], it["best_practice"], it.get("verdict") or t["undetermined"], ch_label(it, lang),
                        t["conf"].get(it.get("confidence"), ""), reason, it["route"].get("card") or ""])
    for row in wso.iter_rows(min_row=2):
        for c in row:
            c.alignment, c.border = wrap, border

    # ---- ActionPlan
    wsa = wb.create_sheet("ActionPlan")
    header(wsa, t["ap_hdr"], [8, 18, 22, 36, 38, 8, 60, 44, 7, 16, 16, 12, 10])
    act = [i for i in items if i.get("verdict") in ("△", "✗") or (i.get("verdict") is None and i.get("prefilled"))]
    act.sort(key=lambda i: (i.get("priority") or "P9", PILLAR_ORDER.index(i["pillar"]) if i["pillar"] in PILLAR_ORDER else 99, i["question_id"]))
    for it in act:
        status = it.get("action_status")
        status_code = (i18n.norm_action_status(status) or status) if status else ACTION_STATUSES[0]    # stored code (or an old Japanese label)
        wsa.append([it.get("priority", ""), it["pillar"], it["question_id"], it["choice_id"], it["best_practice"], it.get("verdict") or t["ap_undetermined"],
                    (mask_accounts(i18n.localize_stored(it.get("recommendation", ""), lang)) if a.mask_account_ids else i18n.localize_stored(it.get("recommendation", ""), lang)),
                    i18n.localize_stored(it.get("org_shape", ""), lang), it.get("effort", ""), it.get("owner", ""), ch_label(it, lang),
                    it.get("due", ""), t["status"].get(status_code, status_code)])
    for row in wsa.iter_rows(min_row=2):
        for c in row:
            c.alignment, c.border = wrap, border
    dv = DataValidation(type="list", formula1='"' + ",".join(t["status"][s] for s in ACTION_STATUSES) + '"', allow_blank=True)
    wsa.add_data_validation(dv)
    dv.add(f"M2:M{max(wsa.max_row, 2)}")

    # ---- Context
    wsc = wb.create_sheet("Context")
    wsc.append(t["ctx_hdr"])
    for c in wsc[1]:
        c.fill, c.font = hdr_fill, hdr_font
    flat = []

    def walk(prefix, o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "answers":
                    continue
                walk(f"{prefix}.{k}" if prefix else k, v)
        else:
            flat.append((prefix, o))
    walk("", ctx)
    for k, v in flat:
        val = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else i18n.localize_stored(v, lang)
        wsc.append([k, mask_accounts(val) if a.mask_account_ids else val])
    wsc.append(["review.mode", f"{review_mode(w)} — {t['mode'][review_mode(w)]}"])
    wsc.column_dimensions["A"].width, wsc.column_dimensions["B"].width = 34, 70
    if a.mask_account_ids:
        # A shared export must not keep a 12-digit account ID on any sheet. Masking field by field would miss columns or sheets added later
        for sheet in wb.worksheets:
            for row in sheet.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and not (sheet.title == "Summary" and c.value.startswith("=")):
                        c.value = mask_accounts(c.value)
    # Formulas exist only in the Summary sheet, which the code builds. The other sheets hold evidence, answers, rationale and similar body text
    # that may contain external content, so a string starting with `=` is written as a string, not a formula (openpyxl turns `=...` into a formula).
    # Do this after the values have been assigned
    for sheet in wb.worksheets:
        if sheet.title == "Summary":
            continue
        for row in sheet.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    c.data_type = "s"
    wb.save(a.out)
    print(f"wrote {a.out} (sheets: {', '.join(wb.sheetnames)})")
    warn_unmasked_export(a)


# ------------------------------------------------------------------ WA Tool
def cmd_push(a, st):
    import kensa_wa
    w = st.work()
    ctx = st.ctx()
    wid = a.workload_id or get_path(ctx, "aws.workload_id") or die("--workload-id is required")
    if a.apply:
        refuse_demo(w, "push --apply")
    items = active(w)
    pending = [i for i in items if i.get("verdict") is None]
    if pending and not a.allow_unresolved:
        die(f"{len(pending)} item(s) are undetermined. Run `finalize` or pass --allow-unresolved.")
    if a.milestone is not None and not 3 <= len(a.milestone) <= 100:
        die("--milestone must be 3 to 100 characters long")
    assumed = [i for i in items if i.get("channel") == "assumed"]
    if assumed and a.exclude_assumed:
        print(f"{len(assumed)} assumed ✗ item(s) (no evidence, no answer) will not be sent; the matching choices stay unanswered in the W-A Tool")
    elif assumed:
        print(f"warning: {len(assumed)} assumed ✗ item(s) (no evidence, no answer) will be recorded as UNSELECTED. "
              "That does not mean they were confirmed as not implemented; confirm them before sending, or exclude them with --exclude-assumed")
    n_iac = sum(1 for i in items if i.get("basis") == "iac_only")
    if n_iac:
        print(f"warning: {n_iac} item(s) were judged from IaC only (live AWS environment not verified). The W-A Tool records them in the same form as verdicts confirmed in the live environment. "
              "Verify them in the live environment before FTR or any external submission (`mode both` → `scan-aws`)")
    if a.milestone and not a.apply:
        print(f"(milestone {a.milestone!r} is created only with --apply, and only when there are no errors)")
    if a.apply:   # a dry run touches no AWS, so it needs no credentials either
        check_auth(a, st, kind="wa")
    res = kensa_wa.push(wid, items, a.lens or get_path(ctx, "aws.lens") or "wellarchitected", apply=a.apply,
                       none_choices=w["meta"].get("none_choices"), region=a.region, profile=aws_profile(a, ctx, "wa"),
                       exclude_assumed=a.exclude_assumed, milestone=a.milestone if a.apply else None, lang=st.lang)
    c = collections.Counter(r["status"] for r in res)
    print(("APPLIED" if a.apply else "DRY-RUN (nothing was written; use --apply to run)"), dict(c))
    for r in res:
        if r["status"] in ("error", "milestone-error", "milestone-skipped"):
            print(f"  {r['status']}:", r["question_id"], r["error"])
    if not a.apply:
        for r in res[:8]:
            print(f"  {r['question_id']}: selected={r['selected']}/{r['total']} (N/A={r['na']})")
        if len(res) > 8:
            print(f"  ... {len(res) - 8} more question(s)")


def cmd_precheck(a, st):
    import kensa_wa
    ctx = st.ctx()
    wid = a.workload_id or get_path(ctx, "aws.workload_id") or die("--workload-id is required")
    check_auth(a, st, kind="wa")
    res = kensa_wa.precheck(wid, a.lens or get_path(ctx, "aws.lens") or "wellarchitected",
                           solution_name=a.solution_name or get_path(ctx, "aws.solution_name"), pdf_out=a.pdf_out,
                           region=a.region, profile=aws_profile(a, ctx, "wa"))
    bad = 0
    for r in res:
        mark = {True: "OK  ", False: "NG  ", None: "WARN"}[r["ok"]]
        bad += r["ok"] is False
        print(f"[{mark}] {r['check']}: {r['detail']}")
    sys.exit(1 if bad else 0)


def cmd_workload_create(a, st):
    import kensa_wa
    ctx = st.ctx()
    if a.apply:   # a dry run touches no AWS, so it needs no credentials either
        check_auth(a, st, kind="wa")
    r = kensa_wa.create_workload(a.name, a.description or get_path(ctx, "service.description") or a.name, a.environment, a.owner,
                                a.regions.split(",") if a.regions else get_path(ctx, "aws.regions") or [],
                                account_ids=a.account_ids.split(",") if a.account_ids else None, lens=a.lens.split(",") if a.lens else "wellarchitected",
                                apply=a.apply, region=a.region, profile=aws_profile(a, ctx, "wa"))
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if r.get("WorkloadId"):
        set_path(ctx, "aws.workload_id", r["WorkloadId"])
        save_json(st.p_ctx, ctx)


def cmd_iam_policy(a, st):
    import kensa_scan
    import kensa_wa
    doc = {"scan-deny": kensa_scan.scan_deny_policy,
           "wa-tool-read": lambda: kensa_wa.wa_policy(write=False),
           "wa-tool-write": lambda: kensa_wa.wa_policy(write=True)}[a.kind]()
    print(json.dumps(doc, ensure_ascii=False, indent=2))


def cmd_init(a, st):
    if a.demo:
        return init_demo(a, st)
    os.makedirs(st.dir, exist_ok=True)
    if os.path.exists(st.p_ctx) and not a.force:
        print(f"{st.p_ctx} already exists (use --force to overwrite).")
        w = load_json(st.p_work)
        if w:
            its = active(w)
            print(f"  A review is in progress here: {sum(1 for i in its if resolved(i))}/{len(its)} items resolved. "
                  "Run `plan` to continue it, `snapshot` + a rescan + `diff` for a re-review, or use another --dir for a new review.")
    else:
        ctx = copy.deepcopy(CONTEXT_TEMPLATE)
        if a.lang:
            ctx["language"] = a.lang      # `--lang ja init` records the language of the exports and cards
        save_json(st.p_ctx, ctx)
        print(f"created {st.p_ctx}")
    if ensure_gitignore(st.dir):
        print("created .kensa/.gitignore (its contents will not be committed)")


DEMO_CSV = "demo_checklist.csv"


def init_demo(a, st):
    """A sandbox state directory with a small synthetic checklist: the whole flow (cards, finalize, export) works without AWS."""
    if load_json(st.p_work):
        die(f"{st.dir} already has a worklist. Use a separate directory for the demo, e.g. `--dir .kensa-demo init --demo`.")
    os.makedirs(st.dir, exist_ok=True)
    if not os.path.exists(st.p_ctx):
        ctx = copy.deepcopy(CONTEXT_TEMPLATE)
        if a.lang:
            ctx["language"] = a.lang
        save_json(st.p_ctx, ctx)
    rows = parse_checklist_csv(os.path.join(ASSETS, DEMO_CSV), 0, False)
    items, _, _ = merge_items([], rows)
    apply_routing(items, load_routing(st))
    now = now_iso()
    meta = {"source": "demo", "file": DEMO_CSV, "lens": "wellarchitected", "lens_version": None, "fetched_at": now, "seeded_at": now,
            "demo": True, "none_choices": {},
            "scans": {"aws": {"skipped": True, "at": now}, "repo": {"skipped": True, "at": now}}}   # the cards are ready at once
    save_json(st.p_work, {"meta": meta, "items": items})
    ensure_gitignore(st.dir)
    print(f"demo state created in {st.dir}: {len(rows)} synthetic best practices (no AWS access; nothing here can be sent to the W-A Tool).")
    print(f"  Try: `--dir {st.dir} business show` / `plan` / `cards next -n 2` / `cards answer C-OPS-OWNERSHIP --current partial --choose recommend` / `finalize` / `export --format csv --out {st.dir}/demo.csv`")


def refuse_demo(w, what):
    if (w.get("meta") or {}).get("demo"):
        die(f"this state directory is a demo (synthetic data): `{what}` is disabled. Start a real review in another --dir (the demo never touches the W-A Tool).")


def tool_path(name):
    import shutil
    return shutil.which(name)


def cmd_doctor(a, st):
    """Offline environment check: what is installed, which AWS access level is configured, and the next step. Touches no network and no AWS."""
    import importlib.util
    rows = []     # (status, name, detail)

    def add(ok, name, detail, level="ok"):
        rows.append(("ok" if ok else level, name, detail))
    v = sys.version_info
    add(v >= (3, 11), "python", f"{v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 11) else " (3.11 or later is required)"), "NG")
    uv = tool_path("uv")
    add(bool(uv), "uv", uv or "not found. Recommended: `uv run kensa.py …` fetches boto3 / openpyxl by itself (https://docs.astral.sh/uv/)", "warn")
    for mod, need in (("boto3", "scan-aws, seed --from-wa-tool, push --apply, precheck"), ("openpyxl", "export --format xlsx")):
        have = importlib.util.find_spec(mod) is not None
        add(have, mod, "available" if have else f"not importable in this Python (needed for: {need}). " + ("`uv run` installs it on demand." if uv else f"`python3 -m pip install {mod}`, or install uv."), "warn")
    add(bool(tool_path("gh")), "gh", tool_path("gh") or "not found (optional: GitHub settings and activity in scan-repo; files are still read)", "info")
    add(bool(tool_path("aws")), "aws CLI", tool_path("aws") or "not found (optional: kensa uses boto3; the CLI is only needed for `aws sso login`)", "info")
    mark = {"ok": "[ok]  ", "warn": "[warn]", "NG": "[NG]  ", "info": "[info]"}
    print("kensa doctor (offline check)")
    for lvl, name, detail in rows:
        print(f"  {mark[lvl]} {name:<9} {detail}")
    ctx = st.ctx() if os.path.exists(st.p_ctx) else {}
    prof, wa = get_path(ctx, "aws.profile"), get_path(ctx, "aws.wa_profile")
    env_prof = os.environ.get("AWS_PROFILE") or ("AWS_ACCESS_KEY_ID set" if os.environ.get("AWS_ACCESS_KEY_ID") else None)
    level = 2 if wa else 1 if (prof or env_prof) else 0
    print("access level (from configuration only; `auth-check` verifies it against AWS):")
    print(f"  {'*' if level == 0 else ' '} Lv0 no AWS access : interview + repository (+ `mode iac`); `init --demo` to try the flow")
    print(f"  {'*' if level == 1 else ' '} Lv1 read-only AWS : `scan-aws` ({'aws.profile=' + prof if prof else 'env ' + env_prof if env_prof else 'set aws.profile'})")
    print(f"  {'*' if level == 2 else ' '} Lv2 + W-A Tool    : `seed --from-wa-tool` / `push` / `precheck` ({'aws.wa_profile=' + wa if wa else 'set aws.wa_profile (or reuse aws.profile)'})")
    w = load_json(st.p_work) if os.path.exists(st.p_work) else None
    if w:
        its = active(w)
        print(f"state: {st.dir} has a review in progress ({sum(1 for i in its if resolved(i))}/{len(its)} resolved{', DEMO' if (w.get('meta') or {}).get('demo') else ''}) → `plan`")
    elif os.path.exists(st.p_ctx):
        print(f"state: {st.dir} is initialized but has no checklist yet → `seed --from-wa-tool --workload-id …` (needs Lv2) or `seed --from-csv FILE`")
    else:
        print("state: none here → `init` (or `--dir .kensa-demo init --demo` to try without AWS)")
    if any(r[0] == "NG" for r in rows):
        sys.exit(1)


# ------------------------------------------------------------------ CLI
def _lang_arg(v):
    n = i18n.norm_lang(v)
    if n is None:
        raise argparse.ArgumentTypeError(i18n.unsupported_message(v))
    return n


def build_parser():
    p = argparse.ArgumentParser(prog="kensa.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default=os.environ.get("KENSA_DIR", ".kensa"), help="state directory (default: $KENSA_DIR or ./.kensa)")
    p.add_argument("--lang", type=_lang_arg, metavar="{en,ja}",
                   help="language of exports (xlsx/csv/json) and decision cards; default: $KENSA_LANG, then context.json \"language\", then en")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("init", help="create the state directory and context.json (--demo: a sandbox with a synthetic checklist, no AWS needed)")
    s.add_argument("--force", action="store_true"); s.add_argument("--demo", action="store_true", help="seed a small synthetic checklist so the whole flow can be tried offline (use a separate --dir)")
    s.set_defaults(fn=cmd_init)
    s = sp.add_parser("doctor", help="offline environment check (Python, uv, boto3, openpyxl, gh, aws) and the AWS access level in use; shows the next step")
    s.set_defaults(fn=cmd_doctor)

    s = sp.add_parser("context", help="show or set the review context (service, team, tools, AWS settings, language)"); cs = s.add_subparsers(dest="ctx_cmd", required=True)
    cs.add_parser("show")
    x = cs.add_parser("set"); x.add_argument("pairs", nargs="+", help="dotted.key=value (JSON allowed); language=en|ja")
    s.set_defaults(fn=cmd_context)

    s = sp.add_parser("business", help="business-requirement questions (Phase 0)"); bs = s.add_subparsers(dest="biz_cmd", required=True)
    bs.add_parser("show", help="print the 9 questions with their options and recommendations")
    x = bs.add_parser("answer", help="record an answer"); x.add_argument("id", help="e.g. B-CRITICALITY"); x.add_argument("--option", help="option key (comma-separated for several)"); x.add_argument("--text", help="free text instead of an option")
    s.set_defaults(fn=cmd_business)

    s = sp.add_parser("seed", help="import the checklist (latest lens from the W-A Tool, or a CSV)")
    s.add_argument("--from-csv"); s.add_argument("--sheet", default=0); s.add_argument("--import-verdicts", action="store_true")
    s.add_argument("--from-wa-tool", action="store_true"); s.add_argument("--workload-id"); s.add_argument("--lens")
    s.add_argument("--region"); s.add_argument("--profile"); s.add_argument("--allow-root", action="store_true", help="allow running with root user credentials (not recommended)")
    s.set_defaults(fn=cmd_seed)

    s = sp.add_parser("route", help="re-apply the routing table to the worklist"); s.set_defaults(fn=cmd_route)
    s = sp.add_parser("route-unknown", help="print, as JSON, the material for classifying items that have no route (another lens / new items)")
    s.add_argument("--all", action="store_true", help="also include items routed only by their question's default"); s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_route_unknown)
    s = sp.add_parser("route-apply", help="register the classification in .kensa/routing.local.json (the bundled routing is not changed)")
    s.add_argument("file"); s.set_defaults(fn=cmd_route_apply)
    s = sp.add_parser("coverage", help="breakdown into decided / draft / interview-only")
    s.add_argument("--all", action="store_true", help="all items, not only AWSCheckable=No"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_coverage)
    s = sp.add_parser("mode", help="how evidence is read: both (live environment and IaC; default) / iac (the live AWS environment is not read; IaC and repository only). Without a value, show the current one")
    s.add_argument("value", nargs="?", choices=list(MODES)); s.set_defaults(fn=cmd_mode)
    s = sp.add_parser("plan", help="show progress and the next steps"); s.add_argument("--skip", help="comma-separated scans to skip (aws, repo, iac)"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_plan)
    s = sp.add_parser("status", help="verdict counts per pillar"); s.set_defaults(fn=cmd_status)

    s = sp.add_parser("items", help="list items (for recording evidence or per-item interviews)")
    s.add_argument("--unresolved", action="store_true"); s.add_argument("--channel", choices=["aws", "repo", "interview"])
    s.add_argument("--pillar"); s.add_argument("--area"); s.add_argument("--limit", type=int); s.add_argument("--full", action="store_true")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_items)

    s = sp.add_parser("scan-repo", help="collect repository evidence (IaC, CI, documents, GitHub settings and activity)"); s.add_argument("--path", action="append"); s.add_argument("--gh", help="OWNER/REPO (needs the gh CLI)"); s.set_defaults(fn=cmd_scan_repo)
    s = sp.add_parser("scan-iac", help="statically read IaC (Terraform / CloudFormation / cdk synth output). Never touches AWS")
    s.add_argument("--path", action="append"); s.set_defaults(fn=cmd_scan_iac)
    s = sp.add_parser("auth-check", help="check the AWS credentials and the target account (STS GetCallerIdentity only; run it before touching AWS)")
    s.add_argument("--profile"); s.add_argument("--allow-root", action="store_true", help="allow running with root user credentials (not recommended)"); s.add_argument("--region")
    s.add_argument("--for", dest="target", choices=["scan", "wa"], default="scan", help="scan: the profile that reads AWS (aws.profile) / wa: the profile that touches the W-A Tool (aws.wa_profile)")
    s.set_defaults(fn=cmd_auth_check)
    s = sp.add_parser("scan-aws", help="read-only scan of the live AWS environment"); s.add_argument("--profile"); s.add_argument("--allow-root", action="store_true", help="allow running with root user credentials (not recommended)"); s.add_argument("--regions", help="comma-separated Regions (default: aws.regions in the context)"); s.add_argument("--only", help="comma-separated check ids"); s.add_argument("--limit", type=int, default=200); s.set_defaults(fn=cmd_scan_aws)

    s = sp.add_parser("apply", help="record verdicts (and management fields) from a JSON file"); s.add_argument("file"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_apply)

    s = sp.add_parser("cards", help="decision cards"); cs = s.add_subparsers(dest="cards_cmd", required=True)
    x = cs.add_parser("list", help="list all cards with their state (answered / pending / -)"); x.add_argument("--pending", action="store_true", help="only the cards that wait for an answer")
    x = cs.add_parser("show", help="print one card (question, detected evidence, options, recommendation)"); x.add_argument("id"); x.add_argument("--json", action="store_true")
    x = cs.add_parser("next", help="print the next cards to ask (only those whose evidence scans are done)"); x.add_argument("-n", type=int, default=3, help="how many cards (default 3)"); x.add_argument("--json", action="store_true")
    cs.add_parser("prefill", help="draft the recommendations as proposals (verdicts are not changed)")
    x = cs.add_parser("answer", help="record the current state of a card (and which option to adopt)"); x.add_argument("id"); x.add_argument("--current", required=True, choices=["full", "partial", "none", "na"], help="full=○ / partial=△ / none=✗ / na=N/A")
    x.add_argument("--choose", help="recommend | alt1 | alt2 | current"); x.add_argument("--note"); x.add_argument("--evidence")
    x.add_argument("--override", action="append", help="CHOICE_ID=○|△|✗|N/A"); x.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_cards)

    s = sp.add_parser("finalize", help="set undetermined items to the assumed placeholder (✗, confidence L)")
    s.add_argument("--allow-empty", action="store_true", help="finalize even if nothing has been scanned, recorded or answered yet")
    s.set_defaults(fn=cmd_finalize)
    s = sp.add_parser("export", help="write the deliverable (xlsx / csv / json) in the active language"); s.add_argument("--format", choices=["xlsx", "csv", "json"], default="xlsx"); s.add_argument("--out", required=True)
    s.add_argument("--mask-account-ids", action="store_true"); s.set_defaults(fn=cmd_export)

    s = sp.add_parser("snapshot", help="save the current verdicts to .kensa/history/ (the baseline for a re-review)")
    s.add_argument("--label", help="a label added to the snapshot name (e.g. 2026Q4)"); s.set_defaults(fn=cmd_snapshot)
    s = sp.add_parser("diff", help="differences from a snapshot (regressed, improved, newly confirmed, lens changes, stale evidence)")
    s.add_argument("--since", help="snapshot file name or part of it (default: the latest)")
    s.add_argument("--stale-days", type=int, default=STALE_DAYS); s.add_argument("--limit", type=int, default=10)
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_diff)

    s = sp.add_parser("push", help="record the answers in the W-A Tool (dry-run by default)")
    s.add_argument("--workload-id"); s.add_argument("--lens"); s.add_argument("--apply", action="store_true")
    s.add_argument("--exclude-assumed", action="store_true", help="do not send assumed ✗ items (no evidence, no answer)")
    s.add_argument("--milestone", help="with --apply, create a W-A Tool milestone with this name, only when every question was recorded successfully")
    s.add_argument("--allow-unresolved", action="store_true"); s.add_argument("--region"); s.add_argument("--profile"); s.add_argument("--allow-root", action="store_true", help="allow running with root user credentials (not recommended)"); s.set_defaults(fn=cmd_push)
    s = sp.add_parser("iam-policy", help="print an IAM policy JSON (scan-deny: extra Deny for value-returning reads / wa-tool-read|write: Allow for the W-A Tool)")
    s.add_argument("--kind", choices=["scan-deny", "wa-tool-read", "wa-tool-write"], default="scan-deny"); s.set_defaults(fn=cmd_iam_policy)
    s = sp.add_parser("precheck", help="FTR pre-check"); s.add_argument("--workload-id"); s.add_argument("--lens"); s.add_argument("--solution-name")
    s.add_argument("--pdf-out"); s.add_argument("--region"); s.add_argument("--profile"); s.add_argument("--allow-root", action="store_true", help="allow running with root user credentials (not recommended)"); s.set_defaults(fn=cmd_precheck)
    s = sp.add_parser("workload-create", help="create a W-A Tool workload (dry-run by default)"); s.add_argument("--name", required=True); s.add_argument("--description"); s.add_argument("--environment", default="PRODUCTION", choices=["PRODUCTION", "PREPRODUCTION"])
    s.add_argument("--owner", help="name + email (an FTR requirement)"); s.add_argument("--regions"); s.add_argument("--account-ids"); s.add_argument("--lens")
    s.add_argument("--apply", action="store_true"); s.add_argument("--region"); s.add_argument("--profile"); s.add_argument("--allow-root", action="store_true", help="allow running with root user credentials (not recommended)"); s.set_defaults(fn=cmd_workload_create)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    st = Store(a.dir, lang=a.lang)
    try:
        with i18n.using(st.lang):
            a.fn(a, st)
    finally:
        # whichever command creates the state directory (even `context set` or `scan-*` run before `init`), put a .gitignore in it
        ensure_gitignore(st.dir)


if __name__ == "__main__":
    main()
