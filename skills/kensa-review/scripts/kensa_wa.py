"""AWS Well-Architected Tool access (fetch the latest lens, record answers, FTR pre-check).

boto3 is imported lazily so that the offline subcommands of kensa.py work without it.
Nothing here is executed unless the user runs `fetch`/`push --apply`/`precheck`/`workload-create`.
Writes only ever target the Well-Architected Tool workload (never the user's other AWS resources).
"""
import base64
import datetime
import re
import time

import kensa_i18n as i18n

PILLAR_NAMES = {
    "operationalExcellence": "Operational Excellence", "security": "Security", "reliability": "Reliability",
    "performance": "Performance Efficiency", "costOptimization": "Cost Optimization", "sustainability": "Sustainability",
}
FTR_PILLARS = ("security", "reliability", "operationalExcellence")
# The "None of these" choice as the W-A Tool serves it; the Japanese alternatives match a lens served in Japanese.
NONE_TITLE = re.compile(r"none of these|これらのいずれにも|いずれにも該当しない", re.I)
NOTES_MAX = 250      # ChoiceUpdates.Notes
ANSWER_NOTES_MAX = 2084
PDF_MAX_BYTES = 3 * 1024 * 1024


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def client(region=None, profile=None):
    try:
        import boto3  # noqa: WPS433 (lazy on purpose)
    except ImportError:
        raise SystemExit("boto3 not found. Run through `uv run …/kensa.py` (it installs boto3 automatically), or `python3 -m pip install boto3` "
                         "(on a PEP 668 system: `python3 -m venv .venv && .venv/bin/pip install boto3 openpyxl`). The offline subcommands do not need boto3.")
    session = boto3.Session(profile_name=profile, region_name=region)
    return session.client("wellarchitected")


def fetch(workload_id, lens="wellarchitected", c=None, region=None, profile=None):
    """Return (items, meta) with every best practice of the lens review as the W-A Tool serves it for this workload.

    That is the lens version the workload is on, which is not always the newest one (a workload stays on its version until the lens review is
    upgraded), so meta["lens_status"] (CURRENT / NOT_CURRENT / DEPRECATED / DELETED) is returned for the caller to warn about.
    ListAnswers is called without PillarId so that a pillar added by a lens update is fetched too (a pillar kensa does not know keeps the name the W-A Tool gives it)."""
    c = c or client(region, profile)
    lr = c.get_lens_review(WorkloadId=workload_id, LensAlias=lens)["LensReview"]
    api_names = {s_["PillarId"]: s_.get("PillarName") for s_ in lr.get("PillarReviewSummaries", []) if s_.get("PillarId")}
    items, none_choices = [], {}
    token = None
    while True:
        kw = dict(WorkloadId=workload_id, LensAlias=lens, MaxResults=50)
        if token:
            kw["NextToken"] = token
        resp = c.list_answers(**kw)
        for a in resp.get("AnswerSummaries", []):
            pid = a.get("PillarId", "")
            for ch in a.get("Choices", []):
                if NONE_TITLE.search(ch.get("Title", "")):
                    none_choices[a["QuestionId"]] = ch["ChoiceId"]
                    continue
                items.append({
                    "choice_id": ch["ChoiceId"], "question_id": a["QuestionId"],
                    "pillar": PILLAR_NAMES.get(pid) or api_names.get(pid) or pid,
                    "question_title": a.get("QuestionTitle", ""), "best_practice": ch.get("Title", ""),
                    "description": ch.get("Description", ""), "aws_checkable": "",
                    "help_url": (ch.get("HelpfulResource") or {}).get("Url", ""),
                    "improvement_url": (ch.get("ImprovementPlan") or {}).get("Url", ""),
                    "question_risk": a.get("Risk", ""),
                })
        token = resp.get("NextToken")
        if not token:
            break
    meta = {"source": "wa-tool", "lens": lens, "lens_version": lr.get("LensVersion"), "lens_status": lr.get("LensStatus"), "workload_id": workload_id,
            "fetched_at": _now().isoformat(timespec="seconds"), "none_choices": none_choices}
    return items, meta


# The W-A Tool APIs this module calls (IAM action names are the API names in CamelCase). A test pins that they match the calls.
IAM_READ = ("GetWorkload", "GetLensReview", "GetLensReviewReport", "ListAnswers")
IAM_WRITE = ("UpdateAnswer", "CreateWorkload", "CreateMilestone")


def wa_policy(write=False):
    """The Allow policy printed by `kensa.py iam-policy --kind wa-tool-read|wa-tool-write`. Resource is `*` (narrow it to the workload ARN after checking in the live environment)."""
    acts = list(IAM_READ) + (list(IAM_WRITE) if write else [])
    return {"Version": "2012-10-17",
            "Statement": [{"Sid": "KensaWellArchitectedTool" + ("Write" if write else "Read"), "Effect": "Allow",
                           "Action": ["wellarchitected:" + a for a in sorted(acts)], "Resource": "*"}]}


def _note(it, limit=NOTES_MAX, lang="en"):
    from_kensa = "[{v}/{ch}/{cf}] ".format(v=it.get("verdict"), ch=it.get("channel") or "-", cf=it.get("confidence") or "-")
    body = i18n.localize_stored(it.get("evidence") or it.get("org_shape") or "", lang).replace("\n", " ")
    return (from_kensa + body)[:limit]


def plan_updates(items, none_choices=None, exclude_assumed=False, lang="en"):
    """Group verdict-bearing items by question and build update_answer kwargs (no AWS call).

    exclude_assumed: do not send assumed placeholders (channel=assumed: no evidence, no answer); the choice stays unanswered in the W-A Tool.
    lang: language of the system-generated texts that end up in the notes sent to the W-A Tool (stored texts are English; see kensa_i18n).
    """
    none_choices = none_choices or {}
    by_q = {}   # every item with a verdict, per question (before assumed ones are excluded)
    for it in items:
        if it.get("verdict") in ("○", "△", "✗", "N/A") and not it.get("removed"):
            by_q.setdefault(it["question_id"], []).append(it)
    plans = []
    for qid, all_its in by_q.items():
        # whether a whole question is "not applicable" is decided on all its items before exclusion (it is not N/A just because the items left after excluding assumed ones are all N/A)
        its = [i for i in all_its if not (exclude_assumed and i.get("channel") == "assumed")]
        if not its:
            continue   # a question with nothing to send stays unanswered in the W-A Tool
        excluded = len(all_its) - len(its)
        if all(i["verdict"] == "N/A" for i in all_its):
            plans.append({"QuestionId": qid, "IsApplicable": False, "Reason": "OTHER",
                          "Notes": "; ".join(sorted({i18n.localize_stored(i.get("na_reason") or i18n.system_text("na.default"), lang) for i in its}))[:ANSWER_NOTES_MAX],
                          "_summary": {"selected": 0, "total": len(its), "na": len(its)}})
            continue
        updates, selected = {}, 0
        for it in its:
            v = it["verdict"]
            if v == "○":
                st, selected = "SELECTED", selected + 1
            elif v == "N/A":
                st = "NOT_APPLICABLE"
            else:
                st = "UNSELECTED"
            u = {"Status": st, "Notes": _note(it, lang=lang)}
            if st == "NOT_APPLICABLE":
                u["Reason"] = it.get("na_reason_code") or "OTHER"
            updates[it["choice_id"]] = u
        none_id = none_choices.get(qid)
        # a question with excluded items gets no "None of these" (it would also settle the excluded items as "not implemented")
        if none_id and selected == 0 and not excluded:
            updates[none_id] = {"Status": "SELECTED", "Notes": "No best practice selected"}
        plans.append({"QuestionId": qid, "IsApplicable": True, "ChoiceUpdates": updates,
                      "_summary": {"selected": selected, "total": len(its), "na": sum(1 for i in its if i["verdict"] == "N/A")}})
    return sorted(plans, key=lambda p: p["QuestionId"])


# ConflictException: the W-A Tool briefly refuses a write while it is still processing the previous one
# (seen right after UpdateAnswer, when CreateMilestone followed at once). It clears on its own, so wait and retry.
RETRY_CODES = ("ThrottlingException", "TooManyRequestsException", "ConflictException")


def _retry(fn, tries=5):
    for n in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if code in RETRY_CODES and n < tries - 1:
                time.sleep(2 ** n)
                continue
            raise


def existing_answers(c, workload_id, lens="wellarchitected"):
    """How many questions of the workload already have an answer in the W-A Tool (a selected choice, or marked not applicable).
    `push --apply` overwrites them, so a non-zero count means a rollback milestone is taken first."""
    n, token = 0, None
    while True:
        kw = dict(WorkloadId=workload_id, LensAlias=lens, MaxResults=50)
        if token:
            kw["NextToken"] = token
        resp = _retry(lambda: c.list_answers(**kw))
        n += sum(1 for a in resp.get("AnswerSummaries", []) if a.get("SelectedChoices") or a.get("IsApplicable") is False)
        token = resp.get("NextToken")
        if not token:
            return n


def backup_milestone_name(now=None):
    return "kensa-before-push-" + (now or _now()).strftime("%Y%m%dT%H%M%SZ")


def push(workload_id, items, lens="wellarchitected", apply=False, none_choices=None, c=None, region=None, profile=None,
         exclude_assumed=False, milestone=None, lang="en", backup=True):
    """Dry-run by default. With apply=True calls UpdateAnswer per question. Returns a result list.

    backup: with apply=True, first count the answers the workload already has (ListAnswers); if there are any, create a milestone
    (`kensa-before-push-<UTC time>`) before overwriting them, so that they can be restored. If that milestone cannot be created, nothing is written.
    milestone: with apply=True, call CreateMilestone only when every question was updated successfully (not if even one failed).
    """
    plans = plan_updates(items, none_choices, exclude_assumed, lang)
    results = []
    if not apply:
        return [{"question_id": p["QuestionId"], "status": "dry-run", **p["_summary"]} for p in plans]
    c = c or client(region, profile)
    if backup:
        try:
            n = existing_answers(c, workload_id, lens)
            if n:
                name = backup_milestone_name()
                m = _retry(lambda: c.create_milestone(WorkloadId=workload_id, MilestoneName=name))
                results.append({"question_id": "(backup milestone)", "status": "backup-milestone", "milestone": name,
                                "number": m.get("MilestoneNumber"), "existing": n})
        except Exception as e:  # noqa: BLE001
            return [{"question_id": "(backup milestone)", "status": "backup-milestone-error",
                     "error": f"nothing was written: could not save the existing answers as a milestone first ({str(e)[:240]})"}]
    for p in plans:
        kw = {k: v for k, v in p.items() if not k.startswith("_") and k != "QuestionId"}
        try:
            _retry(lambda: c.update_answer(WorkloadId=workload_id, LensAlias=lens, QuestionId=p["QuestionId"], **kw))
            results.append({"question_id": p["QuestionId"], "status": "updated", **p["_summary"]})
        except Exception as e:  # noqa: BLE001
            results.append({"question_id": p["QuestionId"], "status": "error", "error": str(e)[:300], **p["_summary"]})
    if milestone:
        failed = sum(1 for r in results if r["status"] == "error")
        if failed:
            results.append({"question_id": "(milestone)", "status": "milestone-skipped",
                            "error": f"milestone not created because {failed} question(s) had errors"})
        else:
            try:
                m = _retry(lambda: c.create_milestone(WorkloadId=workload_id, MilestoneName=milestone))
                results.append({"question_id": "(milestone)", "status": "milestone", "milestone": milestone, "number": m.get("MilestoneNumber")})
            except Exception as e:  # noqa: BLE001
                results.append({"question_id": "(milestone)", "status": "milestone-error", "error": str(e)[:300]})
    return results


def create_workload(name, description, environment, owner, regions, account_ids=None, lens="wellarchitected",
                    apply=False, c=None, region=None, profile=None):
    kw = {"WorkloadName": name, "Description": description, "Environment": environment,
          "Lenses": list(lens) if isinstance(lens, (list, tuple)) else [lens],
          "AwsRegions": regions or []}
    if owner:
        kw["ReviewOwner"] = owner
    if account_ids:
        kw["AccountIds"] = account_ids
    if not apply:
        return {"status": "dry-run", "request": kw}
    c = c or client(region, profile)
    resp = c.create_workload(**kw)
    return {"status": "created", "WorkloadId": resp.get("WorkloadId"), "WorkloadArn": resp.get("WorkloadArn")}


def precheck(workload_id, lens="wellarchitected", solution_name=None, pdf_out=None, c=None, region=None,
             profile=None, now=None):
    """FTR-oriented checks. Returns list of {check, ok, detail}. ok=None means a warning/unknown."""
    c = c or client(region, profile)
    now = now or _now()
    w = c.get_workload(WorkloadId=workload_id)["Workload"]
    lr = c.get_lens_review(WorkloadId=workload_id, LensAlias=lens)["LensReview"]
    out = []

    name = w.get("WorkloadName", "")
    if solution_name:
        out.append({"check": "workload name contains solution name", "ok": solution_name.lower() in name.lower(),
                    "detail": f"WorkloadName={name!r}, solution={solution_name!r}"})
    else:
        out.append({"check": "workload name contains solution name", "ok": None, "detail": "pass --solution-name to evaluate this check"})
    owner = (w.get("ReviewOwner") or "").strip()
    has_email = bool(re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", owner))
    out.append({"check": "review owner has name and email", "ok": bool(owner) and has_email and len(owner.split()) >= 2,
                "detail": f"ReviewOwner={owner!r} (both a name and an email address are required)"})

    pillars = {p["PillarId"]: p.get("RiskCounts", {}) for p in lr.get("PillarReviewSummaries", [])}
    for pid in FTR_PILLARS:
        rc = pillars.get(pid, {})
        high = rc.get("HIGH", 0)
        out.append({"check": f"{PILLAR_NAMES[pid]}: HRI == 0", "ok": high == 0 if pid in pillars else None,
                    "detail": f"HIGH={high}, MEDIUM={rc.get('MEDIUM', 0)}, UNANSWERED={rc.get('UNANSWERED', 0)}"})
    unanswered = sum(rc.get("UNANSWERED", 0) for rc in pillars.values())
    out.append({"check": "all questions answered (UNANSWERED == 0)", "ok": unanswered == 0, "detail": f"UNANSWERED total={unanswered}"})

    upd = lr.get("UpdatedAt")
    if upd:
        age = (now - upd).days if hasattr(upd, "tzinfo") else None
        out.append({"check": "review updated within 1 year", "ok": (age is not None and age < 365), "detail": f"UpdatedAt={upd}, age_days={age}"})
    try:
        rep = c.get_lens_review_report(WorkloadId=workload_id, LensAlias=lens)["LensReviewReport"]
        raw = base64.b64decode(rep["Base64String"])
        size = len(raw)
        if pdf_out:
            with open(pdf_out, "wb") as f:
                f.write(raw)
        out.append({"check": "report PDF <= 3 MB", "ok": size <= PDF_MAX_BYTES,
                    "detail": f"size={size} bytes" + (f", saved to {pdf_out}" if pdf_out else "")})
    except Exception as e:  # noqa: BLE001
        out.append({"check": "report PDF <= 3 MB", "ok": None, "detail": f"could not fetch the report PDF: {str(e)[:150]}"})
    return out
