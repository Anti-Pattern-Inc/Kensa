"""Locales for the files kensa produces (xlsx / csv / json exports) and a few user-facing defaults.

* Supported locales: ``en`` (default) and ``ja``. CLI messages, argparse help, docstrings and comments are English only;
  they are not translated here.
* Language resolution (``resolve_lang``): ``--lang`` > env ``KENSA_LANG`` > ``context.json`` key ``language`` > ``en``.
* ``LABELS[lang]`` holds every label that appears in a deliverable: column headers, verdict / confidence / source / route /
  tier / status labels, Summary notes, OpenItems reasons, Questions / Context sheet text, and the defaults used when a
  decision card's ``{placeholders}`` have no value. The ``ja`` table equals the strings that earlier versions wrote.
* Stored data (worklist.json, context.json, card_answers.json, snapshots) never holds a localized label. Status values are
  codes (``todo`` / ``doing`` / ``done``); system-generated texts are stored in English and mapped to the active locale
  on output by ``localize_stored``, which also recognises the Japanese texts older versions stored. Old Japanese values
  are accepted as aliases when reading (``norm_action_status``; ``import_header`` for CSV headers).
"""
import contextlib

SUPPORTED = ("en", "ja")
DEFAULT = "en"
ENV_VAR = "KENSA_LANG"

# ------------------------------------------------------------------ language resolution


def norm_lang(value):
    """'en' / 'ja' (case-insensitive; region suffixes such as ja_JP or en-US are accepted), or None when unsupported."""
    if not isinstance(value, str):
        return None
    primary = value.strip().lower().replace("_", "-").split("-")[0].split(".")[0]
    return primary if primary in SUPPORTED else None


def resolve_lang(cli=None, env=None, ctx_lang=None, warn=None):
    """First supported value of: --lang, $KENSA_LANG, context.json 'language'; otherwise 'en'.
    An unsupported value is skipped (reported through ``warn(message)`` when given), so a typo never blocks the commands
    that could fix it."""
    for source, v in (("--lang", cli), (ENV_VAR, env), ("context.json language", ctx_lang)):
        if v is None or v == "":
            continue
        n = norm_lang(v)
        if n:
            return n
        if warn:
            warn(f"ignoring unsupported {source} value {v!r} (supported: {', '.join(SUPPORTED)})")
    return DEFAULT


def unsupported_message(value):
    return f"unsupported language {value!r}; supported values: {', '.join(SUPPORTED)}"


_active = DEFAULT


def active():
    """The language of the command being run (set by kensa.main); ``en`` outside a command."""
    return _active


@contextlib.contextmanager
def using(lang):
    global _active
    prev, _active = _active, lang
    try:
        yield
    finally:
        _active = prev


# ------------------------------------------------------------------ label tables
LABELS = {
    "en": {
        "undetermined": "Undetermined",
        "cols": ["Pillar", "QuestionId", "QuestionTitle", "ChoiceId", "BestPractice", "Description", "AWSCheckable", "Verdict",
                 "Source", "Confidence", "Evidence / rationale", "Org shape / tools", "Recommended action", "Effort", "Priority",
                 "Owner", "CardId", "Route", "Notes", "Confirmed on"],
        "ch": {"aws": "AWS (live environment)", "repo": "Repository / IaC", "interview": "Interview (self-reported)",
               "assumed": "assumed (needs confirmation)", "imported": "imported (needs re-check)"},
        "conf": {"H": "High", "M": "Medium", "L": "Low"},
        "route": {"aws": "AWS", "repo": "Repository", "interview": "Interview"},
        "tier": {"decide": "[Decided]", "draft": "[Draft]", "ask": "[Interview]"},
        "tier_sep": " ",    # between the tier label and the route text in the Route column
        "iac_only": "IaC (live environment not verified)",
        "mode": {"both": "Live environment (AWS) and IaC (default)", "iac": "IaC only (the live AWS environment is not read)"},
        "added_in_lens": "[Added in lens update] ",
        # Summary sheet
        "sum_title": "Well-Architected Review Summary",
        "sum_info": "Service: {service}  /  Lens: {lens}  /  Exported: {at}",
        "sum_hdr": ["Pillar", "○ Implemented", "△ Partial", "✗ Not implemented", "N/A", "Undetermined", "Total",
                    "Of which assumed (needs confirmation)", "FTR scope", "Questions", "Estimated HRI (decided only, indicative)",
                    "Questions not yet decided", "Recorded rate (excl. undetermined)", "Confirmed rate (also excl. assumed)"],
        "ftr_required": "Required (HRI=0)",
        "sum_total": "Total",
        "tier_hdr": ["Route (design-time classification, all items)", "Count", "Meaning"],
        "tier_meaning": {"decide": "Can be decided from AWS / repository evidence",
                         "draft": "Evidence is a draft; a person confirms it",
                         "ask": "Only a person can answer (interview)"},
        "notes_hdr": "Notes",
        "note_hri": "- Estimated HRI is an indicative count of the questions whose verdict is decided and that have no ○ at all. "
                    "Questions that contain undetermined or {assumed} items are counted as 'not yet decided' and left out of HRI. "
                    "For the official risk, use the value shown in the W-A Tool after you push.",
        "note_assumed": "- '{assumed}' marks items for which neither evidence nor an answer was obtained and that were set to ✗ "
                        "as a placeholder. Review them in the OpenItems sheet.",
        "note_rates": "- Recorded rate is the share of items that have a verdict; confirmed rate also leaves out the {assumed} items. "
                      "An assumed item does not mean it was confirmed as not implemented.",
        "note_ftr": "- The three FTR pillars (Security / Reliability / Operational Excellence) must have HRI=0.",
        "note_iac": "- The {n} item(s) whose source is '{label}' were judged only by reading IaC declarations; the live AWS environment "
                    "was not checked (manual changes and differences from other stacks are not visible). "
                    "They are listed in OpenItems. Verify them in the live environment before FTR or any external submission.",
        # Questions sheet
        "q_hdr": ["CardId", "Status", "Theme", "Question", "○ state", "△ state", "✗ state", "★ Recommendation (steps)", "Alternatives",
                  "Org shape / tools if adopted", "Items covered", "Current answer", "Chosen option", "Memo"],
        "q_answered": "Answered",
        "q_unanswered": "Unanswered",
        "q_effort": "  (effort {effort}/{priority})",
        "q_current": {"full": "○ Implemented", "partial": "△ Partial", "none": "✗ Not implemented", "na": "N/A"},
        "ask_technical": "\n(Technical wording: {ask})",
        # OpenItems sheet
        "oi_hdr": ["Pillar", "ChoiceId", "BestPractice", "Verdict", "Source", "Confidence", "What to confirm", "CardId"],
        "oi_undetermined_card": "Undetermined: confirm the current state in decision card {card}",
        "oi_undetermined_scan": "Undetermined: decide from the AWS / repository scan results (ask individually if scanning is not possible)",
        "oi_assumed": "Assumed because neither evidence nor an answer was obtained. Check the current state in AWS, the repository "
                      "or with the owner, then update.",
        "oi_imported": "Verdict imported from another environment. Re-check it in the live environment.",
        "oi_lens_changed": "The wording of this item changed in the lens ({fields}) after the verdict was recorded. Read it again and re-check the verdict.",
        "lens_fields": {"question_title": "question", "best_practice": "best practice", "description": "description"},
        "oi_draft": "Draft from evidence. Its existence was confirmed, but how it is actually operated and its quality were not. "
                    "The owner should confirm it and finalize.",
        "oi_iac_only": "Judged from IaC only (declarations were read; the live AWS environment was not checked). When the live "
                       "environment can be read, confirm with `mode both` then `scan-aws`. Watch for manual changes and drift "
                       "from other stacks.",
        "oi_self_reported": "Self-reported ○. Attach evidence (URL / configuration / document) to raise confidence.",
        "oi_low_conf": "Low confidence. Confirm with additional evidence.",
        "oi_stale": "{days} days since the evidence was recorded (threshold {limit} days). Rescan or re-check, then update.",
        # ActionPlan sheet
        "ap_hdr": ["Priority", "Pillar", "QuestionId", "ChoiceId", "BestPractice", "Verdict", "Recommended action", "Org shape / tools",
                   "Effort", "Owner", "Source", "Due", "Action status"],
        "ap_undetermined": "Undetermined (proposal)",
        "status": {"todo": "Not started", "doing": "In progress", "done": "Done"},
        # Context sheet
        "ctx_hdr": ["Item", "Value"],
        # defaults used when a card placeholder has no value
        "fill": {"po": "PO", "infra_owner": "Infra owner", "chat": "chat", "vcs": "Git", "ci": "CI", "ticket": "ticketing tool",
                 "docs": "documentation", "service": "this service"},
    },
    "ja": {
        "undetermined": "未判定",
        "cols": ["Pillar", "QuestionId", "QuestionTitle", "ChoiceId", "BestPractice", "Description", "AWSCheckable", "判定",
                 "確認元", "確信度", "根拠・証跡", "組織の形、ツール", "推奨アクション", "工数", "優先度", "担当", "CardId", "確認経路", "備考", "確認日"],
        "ch": {"aws": "AWS(実環境)", "repo": "リポジトリ/IaC", "interview": "面談(自己申告)",
               "assumed": "仮置き(要確認)", "imported": "取込み(要再確認)"},
        "conf": {"H": "高", "M": "中", "L": "低"},
        "route": {"aws": "AWS", "repo": "リポジトリ", "interview": "面談"},
        "tier": {"decide": "【確定】", "draft": "【下書き】", "ask": "【面談】"},
        "tier_sep": "",
        "iac_only": "IaC(実環境未確認)",
        "mode": {"both": "実環境(AWS)と IaC の両方(既定)", "iac": "IaC のみ(AWS の実環境は読まない)"},
        "added_in_lens": "[レンズ更新で追加] ",
        "sum_title": "Well-Architected レビュー サマリー",
        "sum_info": "サービス: {service}  /  レンズ: {lens}  /  出力: {at}",
        "sum_hdr": ["Pillar", "○ 実施", "△ 部分", "✗ 未実施", "N/A", "未判定", "合計", "うち仮置き(要確認)", "FTR対象", "質問数",
                    "推定HRI(確定分・参考)", "判定未確定の質問数", "記録済み率(未判定を除く)", "確認済み率(仮置きも除く)"],
        "ftr_required": "必須(HRI=0)",
        "sum_total": "合計",
        "tier_hdr": ["確認経路(設計上の分類・全項目)", "件数", "意味"],
        "tier_meaning": {"decide": "AWS/リポジトリの証拠で判定できる", "draft": "証拠は下書き。人が確認して確定",
                         "ask": "人にしか答えられない(面談)"},
        "notes_hdr": "注記",
        "note_hri": "・推定HRIは『判定が確定した質問のうち、○が1つも無い質問』の数による参考値です。未判定の項目や{assumed}の項目を含む質問は"
                    "『判定未確定』として数え、HRIには含みません。正式なリスクは WA Tool に push した後の値を使ってください。",
        "note_assumed": "・『{assumed}』は証跡も回答も得られず ✗ と仮置きした項目です。OpenItems シートで確認してください。",
        "note_rates": "・記録済み率は判定が付いた割合、確認済み率はそこから{assumed}も除いた割合です。仮置きは『未実施と確認できた』ことを意味しません。",
        "note_ftr": "・FTR対象の3ピラー(Security / Reliability / Operational Excellence)は HRI=0 が必須です。",
        "note_iac": "・確認元が『{label}』の {n} 件は、IaC の宣言を読んだだけで AWS の実環境は確認していません(手作業の変更や別スタックとの差は見えません)。"
                    "OpenItems に出ています。FTR や外部への提出の前に、実環境で確認してください。",
        "q_hdr": ["CardId", "状態", "テーマ", "質問", "○ の状態", "△ の状態", "✗ の状態", "★推奨(手順)", "代替案", "採用時の組織の形、ツール",
                  "対象項目数", "現状の回答", "採用した選択肢", "メモ"],
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
        "oi_lens_changed": "判定を記録した後に、レンズ側でこの項目の文言が変わった({fields})。読み直して判定を再確認",
        "lens_fields": {"question_title": "質問", "best_practice": "ベストプラクティス", "description": "説明"},
        "oi_draft": "証跡からの下書き。『ある』ことは確認できたが、運用の実態・品質は未確認。担当者が確認して確定",
        "oi_iac_only": "IaC だけで判定(宣言を読んだだけで、AWS の実環境は未確認)。実環境を読める時に `mode both` → `scan-aws` で確認。"
                       "手作業の変更・別スタックとの差(ドリフト)に注意",
        "oi_self_reported": "自己申告の○。証跡(URL/設定/ドキュメント)を添付して確信度を上げる",
        "oi_low_conf": "確信度が低い。追加の証跡で確認",
        "oi_stale": "証拠の記録から {days} 日経過(目安 {limit} 日)。再スキャン・再確認して更新",
        "ap_hdr": ["優先度", "Pillar", "QuestionId", "ChoiceId", "BestPractice", "判定", "推奨アクション", "組織の形、ツール", "工数", "担当",
                   "確認元", "期限", "実施状態"],
        "ap_undetermined": "未判定(提案)",
        "status": {"todo": "未着手", "doing": "対応中", "done": "完了"},
        "ctx_hdr": ["項目", "値"],
        "fill": {"po": "PO", "infra_owner": "インフラ担当", "chat": "チャット", "vcs": "Git", "ci": "CI", "ticket": "チケット管理",
                 "docs": "ドキュメント", "service": "本サービス"},
    },
}


def labels(lang=None):
    """The label table of ``lang`` (default: the active language)."""
    return LABELS[lang or _active]


# ------------------------------------------------------------------ stored data: codes, aliases, system texts
# Stored status codes. The Japanese labels older versions stored are accepted when reading.
ACTION_STATUSES = ("todo", "doing", "done")
_STATUS_ALIASES = {"todo": "todo", "doing": "doing", "done": "done", "未着手": "todo", "対応中": "doing", "完了": "done"}
for _lang_table in LABELS.values():
    for _code, _label in _lang_table["status"].items():
        _STATUS_ALIASES.setdefault(_label.strip().lower(), _code)
        _STATUS_ALIASES.setdefault(_label.strip(), _code)


def norm_action_status(value):
    """Status code (todo / doing / done) for a code, an English or Japanese label; None when it is not one of them."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    return _STATUS_ALIASES.get(s) or _STATUS_ALIASES.get(s.lower())


# System-generated texts that end up inside stored values. They are stored in English; the Japanese forms are what older
# versions stored. ``localize_stored`` shows either one in the active language.
SYSTEM_TEXT = {
    "ev.imported": {"en": "Verdict from the imported sheet (may be from another environment; needs re-check)",
                    "ja": "取り込み元シートの判定(別環境の可能性。要再確認)"},
    "ev.assumed": {"en": "No evidence or answer was obtained, so this is assumed not implemented (needs confirmation)",
                   "ja": "証跡も回答も得られなかったため未実施と仮置き(要確認)"},
    "rec.assumed": {"en": "Needs confirmation: check the current state and update the verdict",
                    "ja": "要確認: 現状を確認し、判定を更新してください"},
    "na.default": {"en": "Not applicable", "ja": "該当なし"},
    "ctx.infra_owner": {"en": "Infra owner", "ja": "インフラ担当"},
    "ctx.docs": {"en": "GitHub (docs/ and README)", "ja": "GitHub(docs/ と README)"},
}
SYSTEM_PREFIX = {
    "org.proposal": {"en": "[Proposal] ", "ja": "【提案】"},
    "org.alt": {"en": "(alternative adopted) ", "ja": "(代替案採用) "},
    "ev.self": {"en": "Self-reported (", "ja": "自己申告("},
}
_EXACT = {text: code for code, per in SYSTEM_TEXT.items() for text in per.values()}
_PREFIXES = [(text, code) for code, per in SYSTEM_PREFIX.items() for text in per.values()]


def system_text(code, lang="en"):
    """The text to STORE for a system-generated value (English)."""
    return SYSTEM_TEXT[code][lang]


def prefix(code, lang="en"):
    return SYSTEM_PREFIX[code][lang]


def localize_stored(text, lang=None):
    """Show a stored value in ``lang``: known system-generated texts (English, or the Japanese older versions stored) are
    replaced by the locale's text; everything else (user-provided text, card text) is returned unchanged."""
    if not isinstance(text, str) or not text:
        return text
    lang = lang or _active
    code = _EXACT.get(text)
    if code:
        return SYSTEM_TEXT[code][lang]
    for variant, pcode in _PREFIXES:
        if text.startswith(variant):
            return SYSTEM_PREFIX[pcode][lang] + text[len(variant):]
    return text


# ------------------------------------------------------------------ CSV import: headers of any locale
VERDICT_COL, ORG_COL = 7, 11    # positions in LABELS[...]["cols"]


def import_header(row, index, default=""):
    """Value of the checklist column ``index`` (position in ``cols``) from a csv row, whichever locale's header it has."""
    for table in LABELS.values():
        name = table["cols"][index]
        if name in row and row[name] is not None:
            return row[name]
    return default
