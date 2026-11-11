import datetime
import math
import os
import re

# Import agency adapter system for plug-and-play agency support
try:
    from agencies import get_adapter, list_agencies
    from agency_adapter import evaluate_rule_effective_window
    AGENCY_ADAPTERS_AVAILABLE = True
except ImportError:
    AGENCY_ADAPTERS_AVAILABLE = False
    evaluate_rule_effective_window = None

# ---------------------------------------------------------------------------
# Disaster Unemployment Assistance (DUA), 20 CFR Part 625.
# Only rules whose text was confirmed on eCFR (Title 20, up to date as of 2026-10-01) are encoded:
#   20 CFR 625.8(a)  initial application within 30 days after the announcement date; 30th day on a
#                    Saturday, Sunday or legal holiday in the major disaster area extends to the next
#                    business day; late filing accepted only for good cause; never after the DAP expires.
#   20 CFR 625.6(e)(1) statement-only weekly amount: documentation due within 21 calendar days of filing.
#   20 CFR 625.2(f)  Disaster Assistance Period: first week following the date the disaster began through
#                    the 26th week subsequent to the date the major disaster was declared.
#   20 CFR 625.4(a) / 625.7  DUA is payable only for weeks that begin during the Disaster Assistance Period.
# "Week" is defined by the applicable State law (625.2(v)); the derived DAP end (declaration + 26 weeks)
# is a modelling convention a reviewer must verify. A case may supply the announced DAP end explicitly.
# ---------------------------------------------------------------------------
DUA_TENANT = "DUA_20_CFR_625"
DUA_FILING_WINDOW_DAYS = 30            # 20 CFR 625.8(a)
DUA_PROOF_WINDOW_DAYS = 21             # 20 CFR 625.6(e)(1)
DUA_DAP_WEEKS_AFTER_DECLARATION = 26   # 20 CFR 625.2(f)

# Increment whenever a deterministic rule, canonical schema, or regulatory boundary changes.  Receipts and
# validation artifacts use this value to identify the exact rule pack that produced a result.
RULE_PACK_VERSION = "2026-11-11-wave2"

# Exact tenant routing is intentional.  Prefix matching allowed misspelled or invented tenants to fall through to
# a superficially similar ruleset and return PASS.  A tenant not listed here is invalid input.
ADAPTER_TENANTS = {
    "FEMA_TITLE_44": "FEMA",
    "FEMA_SCENARIO_1_SBA": "FEMA",
    "FEMA_SCENARIO_2_REDUNDANCY": "FEMA",
    "FEMA_SCENARIO_3_INSPECTOR": "FEMA",
    "FEMA_SCENARIO_4_CTHA": "FEMA",
    "FEMA_FORUM_CASE_OCCUPANCY": "FEMA",
    "VA_TITLE_38": "VA",
    "VA_FORUM_PACT_ACT_NEXUS": "VA",
    DUA_TENANT: "DUA",
}

# These are rule-pack constants, not caller-controlled limits.  A supplied limit is accepted only when it matches
# the applicable constant; otherwise the case is INPUT_INVALID.  Medicaid disability determinations use 90 days
# only when deadline.application_basis is explicitly "disability"; all other Medicaid applications use 45 days.
GENERIC_PROGRAM_RULES = {
    "SSA_TITLE_20": {
        "deadline_days": 65,
        "citation": "20 CFR 404.909(a)(1), 404.901, 404.911",
        "label": "SSA reconsideration filing period (60 days plus presumed 5-day receipt period)",
    },
    "MEDICAID_42_CFR": {
        "deadline_days": 45,
        "disability_deadline_days": 90,
        "citation": "42 CFR 435.912",
        "label": "Medicaid eligibility determination timeliness",
    },
    "MEDICAID_FORUM_PHE_UNWINDING": {
        "deadline_days": None,
        "citation": "42 CFR 435.916",
        "label": "Medicaid annual renewal and ex-parte redetermination",
    },
    "SNAP_7_CFR": {
        "deadline_days": 30,
        "expedited_days": 7,
        "citation": "7 CFR 273.2(g)",
        "expedited_citation": "7 CFR 273.2(i)(3)",
        "label": "SNAP application processing",
    },
    "SNAP_FORUM_SAR7_INCOME": {
        "deadline_days": 30,
        "citation": "7 CFR 273.12",
        "label": "SNAP periodic-report processing",
    },
    "HUD_24_CFR": {
        "deadline_days": 365,
        "citation": "24 CFR 5.657; 24 CFR 960.257; 24 CFR 982.516",
        "label": "HUD annual reexamination",
    },
}

# Canonical (CFR title, CFR part) ownership table. Routing requires BOTH title and part so shared
# CFR titles (for example 20 CFR Part 625 DUA vs. 20 CFR Part 404 SSA) never cross-contaminate.
CFR_PART_OWNERS = {
    ("44", "206"): {
        "agency_id": "FEMA",
        "owner_type": "ADAPTER",
        "tenants": (
            "FEMA_TITLE_44",
            "FEMA_SCENARIO_1_SBA",
            "FEMA_SCENARIO_2_REDUNDANCY",
            "FEMA_SCENARIO_3_INSPECTOR",
            "FEMA_SCENARIO_4_CTHA",
            "FEMA_FORUM_CASE_OCCUPANCY",
        ),
        "program_label": "FEMA Individual Assistance (44 CFR Part 206)",
    },
    ("38", "3"): {
        "agency_id": "VA",
        "owner_type": "ADAPTER",
        "tenants": ("VA_TITLE_38", "VA_FORUM_PACT_ACT_NEXUS"),
        "program_label": "VA Adjudication / Compensation & Pension (38 CFR Part 3)",
    },
    ("20", "625"): {
        "agency_id": "DUA",
        "owner_type": "ADAPTER",
        "tenants": (DUA_TENANT,),
        "program_label": "Disaster Unemployment Assistance (20 CFR Part 625)",
    },
    ("20", "404"): {
        "agency_id": "SSA",
        "owner_type": "PROGRAM",
        "tenants": ("SSA_TITLE_20",),
        "program_label": "Social Security Title II (20 CFR Part 404)",
    },
    ("42", "435"): {
        "agency_id": "MEDICAID",
        "owner_type": "PROGRAM",
        "tenants": ("MEDICAID_42_CFR", "MEDICAID_FORUM_PHE_UNWINDING"),
        "program_label": "Medicaid Eligibility (42 CFR Part 435)",
    },
    ("7", "273"): {
        "agency_id": "SNAP",
        "owner_type": "PROGRAM",
        "tenants": ("SNAP_7_CFR", "SNAP_FORUM_SAR7_INCOME"),
        "program_label": "SNAP Certification & Reporting (7 CFR Part 273)",
    },
    ("24", "5"): {
        "agency_id": "HUD",
        "owner_type": "PROGRAM",
        "tenants": ("HUD_24_CFR",),
        "program_label": "HUD General Income & Reexamination (24 CFR Part 5)",
    },
    ("24", "960"): {
        "agency_id": "HUD",
        "owner_type": "PROGRAM",
        "tenants": ("HUD_24_CFR",),
        "program_label": "HUD Public Housing Reexamination (24 CFR Part 960)",
    },
    ("24", "982"): {
        "agency_id": "HUD",
        "owner_type": "PROGRAM",
        "tenants": ("HUD_24_CFR",),
        "program_label": "HUD Housing Choice Voucher Program (24 CFR Part 982)",
    },
}


def resolve_cfr_owner(cfr_title, cfr_part):
    """Return the strict owner descriptor for (cfr_title, cfr_part), or None (fail closed)."""
    title_norm = str(cfr_title or "").removeprefix("title-").strip()
    part_norm = str(cfr_part or "").strip()
    owner = CFR_PART_OWNERS.get((title_norm, part_norm))
    if owner is None:
        return None
    return {
        "cfr_title": title_norm,
        "cfr_part": part_norm,
        **owner,
    }


_SECTION_PART_RE = re.compile(r"^(?:§\s*)?(\d+)\.\d+(?:\([0-9A-Za-z]+\))*$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def route_and_stage_cfr_update(update, as_of=None):
    """
    Route a CFR regulatory change strictly to its owning agency and hold it in a staged
    waiting window until `effective_date` arrives.

    Returns an explicit status without activating unmapped parts, section/part mismatches,
    or future-effective rules.
    """
    if not isinstance(update, dict):
        raise ValueError("update must be an object")
    title = str(update.get("cfr_title") or update.get("title") or "").removeprefix("title-").strip()
    part = str(update.get("cfr_part") or update.get("part") or "").strip()
    section = str(update.get("section") or "").strip()
    owner = resolve_cfr_owner(title, part)
    if owner is None:
        return {
            "status": "UNROUTABLE_CFR_PART",
            "routed": False,
            "active_in_rule_pack": False,
            "cfr_title": title,
            "cfr_part": part,
            "section": section or None,
            "owner": None,
            "reason": f"No canonical agency/program owner registered for {title} CFR Part {part}.",
        }
    if section:
        sec_match = _SECTION_PART_RE.match(section)
        section_part = sec_match.group(1) if sec_match else None
        if section_part != owner["cfr_part"]:
            return {
                "status": "SECTION_PART_MISMATCH",
                "routed": False,
                "active_in_rule_pack": False,
                "cfr_title": title,
                "cfr_part": part,
                "section": section,
                "owner": None,
                "reason": (
                    f"CFR section '{section}' does not belong to {title} CFR Part {part}."
                ),
            }

    eval_as_of = as_of or datetime.datetime.now(datetime.timezone.utc).date()
    published_date = update.get("published_date")
    effective_date = update.get("effective_date")
    applicability_date = update.get("applicability_date")

    if evaluate_rule_effective_window is None:
        pub = published_date if isinstance(published_date, datetime.date) else _parse_date(str(published_date))
        eff = effective_date if isinstance(effective_date, datetime.date) else _parse_date(str(effective_date))
        now = eval_as_of if isinstance(eval_as_of, datetime.date) else _parse_date(str(eval_as_of))
        app_d = None if applicability_date is None else (
            applicability_date if isinstance(applicability_date, datetime.date) else _parse_date(str(applicability_date))
        )
        if eff < pub:
            raise ValueError("effective_date cannot be earlier than published_date")
        if now < eff:
            window = {
                "status": "PENDING_EFFECTIVE_WINDOW",
                "rule_in_force": False,
                "applies_to_case": False,
                "published_date": pub.isoformat(),
                "effective_date": eff.isoformat(),
                "as_of": now.isoformat(),
                "applicability_date": app_d.isoformat() if app_d else None,
                "waiting_window_days": (eff - pub).days,
                "days_until_effective": (eff - now).days,
            }
        elif app_d is not None and app_d < eff:
            window = {
                "status": "PRIOR_RULE_GOVERNS_CASE",
                "rule_in_force": True,
                "applies_to_case": False,
                "published_date": pub.isoformat(),
                "effective_date": eff.isoformat(),
                "as_of": now.isoformat(),
                "applicability_date": app_d.isoformat(),
                "waiting_window_days": (eff - pub).days,
                "days_until_effective": 0,
            }
        else:
            window = {
                "status": "EFFECTIVE_FOR_CASE",
                "rule_in_force": True,
                "applies_to_case": True,
                "published_date": pub.isoformat(),
                "effective_date": eff.isoformat(),
                "as_of": now.isoformat(),
                "applicability_date": app_d.isoformat() if app_d else None,
                "waiting_window_days": (eff - pub).days,
                "days_until_effective": 0,
            }
    else:
        window = evaluate_rule_effective_window(
            published_date,
            effective_date,
            eval_as_of,
            applicability_date=applicability_date,
        )

    staged = not window["rule_in_force"]
    return {
        "status": "STAGED_PENDING_EFFECTIVE_DATE" if staged else window["status"],
        "routed": True,
        "staged": staged,
        "active_in_rule_pack": bool(window["applies_to_case"]),
        "agency_id": owner["agency_id"],
        "tenants": owner["tenants"],
        "owner": owner,
        "section": section or None,
        "window": window,
        "summary": update.get("summary"),
    }

# Rule-bearing objects are strict: an unrecognized key is usually a misspelled legal input and must not be ignored
# into a false PASS.  Flexible descriptive objects (for example applicant and veteran demographics) are not
# restricted here because they can carry program metadata that is irrelevant to the deterministic rules.
CANONICAL_RULE_OBJECT_FIELDS = {
    "incident": frozenset({
        "announcement_date", "appeal_date", "area_legal_holidays", "declaration_date",
        "disaster_assistance_period_end", "disaster_began_date", "disaster_date", "disaster_number",
        "extraordinary_circumstances_extension_approved", "ihp_period_extension",
    }),
    "claim": frozenset({
        "condition", "evidence_time_extension", "evidence_time_limit_holidays",
        "incomplete_application_notice_date",
        "incomplete_application_requested_evidence", "submission_date",
    }),
    "dua": frozenset({
        "disaster_assistance_period_end", "employment_proof_submitted_date", "good_cause_late_filing",
        "initial_application_date", "weekly_amount_basis", "weeks_claimed",
    }),
    "application": frozenset({"submission_date"}),
    "deadline": frozenset({"application_basis", "citation", "label", "limit_days", "start_date"}),
    "expedited": frozenset({"citation", "eligible", "limit_days", "start_date"}),
    "evidence": frozenset({"missing", "uploaded"}),
    "caseworker_action": frozenset({
        "fraud_referral", "intent", "proposed_decision", "requesting_documents",
    }),
    "procedural_status": frozenset({
        "ex_parte_attempted", "returned_mail_flag", "state_wage_database_match",
    }),
    "income": frozenset({"baseline_monthly", "one_time_overtime_spike", "procedural_flag"}),
    "data_conflict": frozenset({"field", "source_a", "source_b", "value_a", "value_b"}),
}

CANONICAL_EXTENSION_FIELDS = {
    "incident.ihp_period_extension": frozenset({
        "approved", "basis", "end_date", "public_interest_determination",
    }),
    "claim.evidence_time_extension": frozenset({"approved", "end_date"}),
    "caseworker_action.fraud_referral": frozenset({
        "initials", "prior_verifications_reviewed", "reason",
    }),
}


def _parse_date(value):
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise ValueError("must be an ISO date (YYYY-MM-DD)")
    return datetime.date.fromisoformat(value)


def dua_filing_deadline(announcement_date, area_legal_holidays=()):
    """Last timely day to file an initial DUA application (20 CFR 625.8(a))."""
    holidays = set(area_legal_holidays or ())
    deadline = announcement_date + datetime.timedelta(days=DUA_FILING_WINDOW_DAYS)
    while deadline.weekday() >= 5 or deadline in holidays:
        deadline += datetime.timedelta(days=1)
    return deadline


def dua_assistance_period_end(declaration_date, explicit_end=None):
    """Last day of the Disaster Assistance Period (20 CFR 625.2(f)); an announced end date takes precedence."""
    if explicit_end is not None:
        return explicit_end
    return declaration_date + datetime.timedelta(weeks=DUA_DAP_WEEKS_AFTER_DECLARATION)


# Stall detector: ASCE operating policy (not a regulation). A case idle longer than this is escalated.
DEFAULT_STALL_IDLE_DAYS = 10


def stall_idle_days_from_env():
    try:
        return max(1, int(os.environ.get("STALL_IDLE_DAYS", DEFAULT_STALL_IDLE_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_STALL_IDLE_DAYS


# ---------------------------------------------------------------------------
# Applicant-protection rules. ASCE INTERNAL OPERATING POLICY (DRAFT), not CFR mandates; no citation is claimed.
#   VERIFIED_FACT_CONFLICT       a later third-party report conflicting with a fact a caseworker already verified
#                                never overrides it, never locks the case, never sets a fraud status and never
#                                suspends payments; it goes to human reconciliation with both records side by side.
#   POSSIBLE_IDENTITY_MISMATCH   a third-party report whose subject does not match the applicant on more than a
#                                first name is treated as unreliable until a human confirms identity.
#   HOUSING_AT_RISK              eviction notice / rent past due / loss of temporary housing: shorter stall
#                                threshold, supervisor escalation, top of queue.
#   VULNERABLE_HOUSEHOLD_MEMBER  any household member aged 55+, with a disability, or a veteran: higher queue
#                                priority and a shorter stall threshold (advisory: never blocks anything).
#   REDUNDANT_REQUEST_BLOCKED    (every program) a request for a document already in the evidence vault is blocked;
#                                document names are canonicalised so naming variants match.
# The engine NEVER sets a fraud status, never suspends payments and never makes the final decision.
# ---------------------------------------------------------------------------
DEFAULT_STALL_IDLE_DAYS_HOUSING_AT_RISK = 3
DEFAULT_STALL_IDLE_DAYS_VULNERABLE = 5
VULNERABLE_MEMBER_MIN_AGE = 55
IDENTITY_FIELDS = ("applicant_id", "full_name", "unit", "dob_year")
ENGINE_PAYMENT_ACTION = "NONE"   # the engine has no code path that suspends, stops or changes a payment

# Flag types that route a case to a human (or only block one specific action) but never lock approval.
ADVISORY_ENGINE_FLAG_TYPES = frozenset({
    "PRIORITY_EXPEDITE",
    "DUA_EMPLOYMENT_PROOF_PENDING",
    "VULNERABLE_HOUSEHOLD_MEMBER",
    "IHP_PERIOD_EXTENSION_RECORDED",
})
NON_LOCKING_FLAG_TYPES = ADVISORY_ENGINE_FLAG_TYPES | frozenset({
    "REDUNDANT_REQUEST_BLOCKED",     # the duplicate request is blocked, not the case
    "STALLED_CASE_ESCALATION",       # escalation, never a hold
    "HOUSING_AT_RISK",               # escalation, never a hold
    "VERIFIED_FACT_CONFLICT",        # verified facts stand; human reconciliation
    "POSSIBLE_IDENTITY_MISMATCH",    # report unreliable until a human confirms identity
})


def _stall_env(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def approval_locked(flags):
    """HARD LOCK on approval: any flag that is not explicitly non-locking (unknown flag types lock: fail closed)."""
    return any(f.get("type") not in NON_LOCKING_FLAG_TYPES for f in flags or [])


_DOC_STOPWORDS = frozenset({"a", "an", "and", "the", "of", "for", "copy", "copies", "household", "applicant",
                            "applicants", "all", "member", "members", "s"})
_DOC_ALIASES = {
    "ssn card": "social security card",
    "ss card": "social security card",
    "social security number card": "social security card",
    "sba determination": "sba loan determination",
    "sba decision": "sba loan determination",
    "sba loan decision": "sba loan determination",
    "sba loan determination letter": "sba loan determination",
}


def canonical_document(name):
    """Normalise a document name so naming variants compare equal ('SSN card', 'Social Security Card(s)',
    'Social Security Card - household' -> 'social security card'). Deterministic; no fuzzy matching."""
    text = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower().replace("(s)", ""))
    tokens = []
    for tok in text.split():
        if tok in _DOC_STOPWORDS:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
            tok = tok[:-1]
        tokens.append(tok)
    key = " ".join(tokens)
    return _DOC_ALIASES.get(key, key)


def document_request_decisions(case):
    """Per requested document: BLOCKED_ALREADY_ON_FILE (with the vault copy it matched) or ALLOWED."""
    vault = {}
    for doc in ((case.get("evidence") or {}).get("uploaded") or []):
        key = canonical_document(doc)
        if key:
            vault.setdefault(key, doc)
    out = []
    for req in ((case.get("caseworker_action") or {}).get("requesting_documents") or []):
        key = canonical_document(req)
        if key and key in vault:
            out.append({"document": req, "decision": "BLOCKED_ALREADY_ON_FILE", "on_file_as": vault[key],
                        "document_key": key})
        else:
            out.append({"document": req, "decision": "ALLOWED", "on_file_as": None, "document_key": key})
    return out


def _norm(value):
    return " ".join(str(value).lower().split()) if value is not None else ""


def identity_check(applicant, subject):
    """Compare a third-party report's subject with the applicant on strong identifiers. A first-name-only match is
    NOT a match. Returns result MATCH or POSSIBLE_IDENTITY_MISMATCH plus the fields compared."""
    applicant = applicant if isinstance(applicant, dict) else {}
    subject = subject if isinstance(subject, dict) else {}
    compared, mismatched = [], []
    for field in IDENTITY_FIELDS:
        a, s = applicant.get(field), subject.get(field)
        if a in (None, "") or s in (None, ""):
            continue
        compared.append(field)
        if _norm(a) != _norm(s):
            mismatched.append(field)
    a_first = _norm(applicant.get("full_name")).split()[:1]
    s_first = (_norm(subject.get("first_name")) or _norm(subject.get("full_name"))).split()[:1]
    return {"result": "POSSIBLE_IDENTITY_MISMATCH" if (mismatched or not compared) else "MATCH",
            "compared_fields": compared, "matched_fields": [f for f in compared if f not in mismatched],
            "mismatched_fields": mismatched, "first_name_matches": bool(a_first) and a_first == s_first}


def fraud_referral_gate(referral, flags=None):
    """The engine never sets a fraud status. A fraud referral is only ever a HUMAN action and is recorded only
    with the person's initials, a reason and an acknowledgment that prior verifications were reviewed."""
    if not referral:
        return None
    referral = referral if isinstance(referral, dict) else {}
    missing = [k for k in ("initials", "reason") if not str(referral.get(k) or "").strip()]
    if referral.get("prior_verifications_reviewed") is not True:
        missing.append("prior_verifications_reviewed")
    open_conflicts = sum(1 for f in flags or [] if f.get("type") == "VERIFIED_FACT_CONFLICT")
    return {"decision": "BLOCKED_INCOMPLETE_HUMAN_REFERRAL" if missing else "RECORDED_AS_HUMAN_REFERRAL",
            "missing": missing, "open_verified_fact_conflicts": open_conflicts,
            "fraud_status_set_by_engine": False}


class DeterministicAuditor:
    def __init__(self, case_data, tenant_id="FEMA_TITLE_44", as_of=None, stall_idle_days=None,
                 housing_stall_idle_days=None, vulnerable_stall_idle_days=None):
        self.case = case_data
        self.tenant_id = tenant_id
        self.flags = []
        self.overall_status = "PASS"
        # as_of pins "today" for reproducible tests; production callers leave it unset (current UTC date).
        self.today = as_of or datetime.datetime.now(datetime.timezone.utc).date()
        self.stall_idle_days = stall_idle_days if stall_idle_days is not None else stall_idle_days_from_env()
        self.housing_stall_idle_days = (housing_stall_idle_days if housing_stall_idle_days is not None else
                                        _stall_env("STALL_IDLE_DAYS_HOUSING_AT_RISK",
                                                   DEFAULT_STALL_IDLE_DAYS_HOUSING_AT_RISK))
        self.vulnerable_stall_idle_days = (vulnerable_stall_idle_days if vulnerable_stall_idle_days is not None else
                                           _stall_env("STALL_IDLE_DAYS_VULNERABLE", DEFAULT_STALL_IDLE_DAYS_VULNERABLE))
        self.effective_stall_idle_days = self.stall_idle_days

    def _result(self, agency=None, adapter_used=False):
        """Return one result shape for every tenant and every evaluator."""
        return {
            "status": self.overall_status,
            "flags": self.flags,
            "hard_lock": self.overall_status in {"ESCALATE", "INPUT_INVALID"} and approval_locked(self.flags),
            "stall_idle_threshold_days": (None if self.overall_status == "INPUT_INVALID" else
                                           self.effective_stall_idle_days),
            "agency": agency,
            "adapter_used": adapter_used,
            "rule_pack_version": RULE_PACK_VERSION,
        }

    def _input_invalid(self, issues, agency=None, adapter_used=False):
        """Fail closed on malformed or incomplete input without pretending a legal rule was evaluated."""
        self.overall_status = "INPUT_INVALID"
        self.flags.append({
            "type": "INPUT_INVALID",
            "details": {"issues": issues, "tenant_id": self.tenant_id},
            "message": "INPUT INVALID: canonical case schema validation failed; no compliance PASS was issued.",
        })
        return self._result(agency=agency, adapter_used=adapter_used)

    @staticmethod
    def _valid_iso_date(value):
        if not isinstance(value, str):
            return False
        try:
            _parse_date(value)
        except (TypeError, ValueError):
            return False
        return True

    def _validate_common_schema(self):
        issues = []
        if not isinstance(self.case, dict):
            return [{"field": "$", "reason": "case must be an object"}]
        mapping_fields = ("applicant", "incident", "claim", "dua", "application", "veteran", "residence",
                          "evidence", "caseworker_action", "housing", "data_conflict", "deadline", "expedited",
                          "verified_facts", "procedural_status", "income")
        for field in mapping_fields:
            value = self.case.get(field)
            if value is not None and field != "verified_facts" and not isinstance(value, dict):
                issues.append({"field": field, "reason": "must be an object"})
        for field, allowed in CANONICAL_RULE_OBJECT_FIELDS.items():
            value = self.case.get(field)
            if not isinstance(value, dict):
                continue
            for unknown in sorted(set(value) - allowed):
                issues.append({
                    "field": f"{field}.{unknown}",
                    "reason": "unknown canonical schema field",
                })
        for path, allowed in CANONICAL_EXTENSION_FIELDS.items():
            parent_name, child_name = path.split(".", 1)
            parent = self.case.get(parent_name)
            child = parent.get(child_name) if isinstance(parent, dict) else None
            if child is not None and not isinstance(child, dict):
                issues.append({"field": path, "reason": "must be an object"})
            elif isinstance(child, dict):
                for unknown in sorted(set(child) - allowed):
                    issues.append({
                        "field": f"{path}.{unknown}",
                        "reason": "unknown canonical schema field",
                    })
        for field in ("household_members", "verified_facts", "third_party_reports"):
            value = self.case.get(field)
            if value is not None and not isinstance(value, list):
                issues.append({"field": field, "reason": "must be an array"})
            elif isinstance(value, list) and any(not isinstance(item, dict) for item in value):
                issues.append({"field": field, "reason": "must contain only objects"})
        applicant = self.case.get("applicant")
        if "applicant" in self.case and self.case["applicant"] is None:
            issues.append({"field": "applicant", "reason": "must be an object (not null)"})
        if isinstance(applicant, dict) and "age" in applicant:
            age = applicant["age"]
            if age is None or isinstance(age, bool) or not isinstance(age, int):
                issues.append({"field": "applicant.age",
                               "reason": "must be an integer"})
            elif age < 0 or age > 130:
                issues.append({"field": "applicant.age",
                               "reason": "must be between 0 and 130"})
        evidence = self.case.get("evidence")
        if "evidence" in self.case and self.case["evidence"] is None:
            issues.append({"field": "evidence", "reason": "must be an object (not null)"})
        if isinstance(evidence, dict):
            for field in ("uploaded", "missing"):
                if field in evidence and evidence[field] is None:
                    issues.append({"field": f"evidence.{field}", "reason": "must be a list (not null)"})
                value = evidence.get(field)
                if value is not None and (not isinstance(value, list) or
                                          any(not isinstance(item, str) for item in value)):
                    issues.append({"field": f"evidence.{field}", "reason": "must be an array of strings"})
        action = self.case.get("caseworker_action")
        if "caseworker_action" in self.case and action is None:
            issues.append({"field": "caseworker_action", "reason": "must be an object (not null)"})
        if isinstance(action, dict):
            intent = action.get("intent")
            if intent is not None and not isinstance(intent, str):
                issues.append({"field": "caseworker_action.intent", "reason": "must be a string"})
            proposed = action.get("proposed_decision")
            if proposed is not None and (
                not isinstance(proposed, str)
                or proposed not in ("APPROVE_FOR_REVIEW", "RETURN", "ESCALATE")
            ):
                issues.append({
                    "field": "caseworker_action.proposed_decision",
                    "reason": "must be APPROVE_FOR_REVIEW, RETURN, or ESCALATE",
                })
            fraud_referral = action.get("fraud_referral")
            if isinstance(fraud_referral, dict):
                for str_field in ("initials", "reason"):
                    val = fraud_referral.get(str_field)
                    if val is not None and not isinstance(val, str):
                        issues.append({
                            "field": f"caseworker_action.fraud_referral.{str_field}",
                            "reason": "must be a string",
                        })
                reviewed = fraud_referral.get("prior_verifications_reviewed")
                if reviewed is not None and not isinstance(reviewed, bool):
                    issues.append({
                        "field": "caseworker_action.fraud_referral.prior_verifications_reviewed",
                        "reason": "must be a boolean",
                    })
            requested = action.get("requesting_documents")
            if "requesting_documents" in action and action["requesting_documents"] is None:
                issues.append({"field": "caseworker_action.requesting_documents",
                               "reason": "must be a list (not null)"})
            if requested is not None and (not isinstance(requested, list) or
                                          any(not isinstance(item, str) for item in requested)):
                issues.append({"field": "caseworker_action.requesting_documents",
                               "reason": "must be an array of strings"})
        housing = self.case.get("housing")
        if "housing" in self.case and housing is None:
            issues.append({"field": "housing", "reason": "must be an object (not null)"})
        if isinstance(housing, dict):
            eviction = housing.get("eviction_notice_date")
            if eviction is not None and not self._valid_iso_date(eviction):
                issues.append({"field": "housing.eviction_notice_date",
                               "reason": "must be an ISO date (YYYY-MM-DD)"})
            for bool_field in ("rent_past_due", "temporary_housing_lost"):
                bval = housing.get(bool_field)
                if bval is not None and not isinstance(bval, bool):
                    issues.append({"field": f"housing.{bool_field}", "reason": "must be a boolean"})
        members = self.case.get("household_members")
        if isinstance(members, list):
            for idx, member in enumerate(members):
                if not isinstance(member, dict):
                    continue
                if "age" in member:
                    m_age = member["age"]
                    if m_age is None or isinstance(m_age, bool) or not isinstance(m_age, int):
                        issues.append({"field": f"household_members[{idx}].age",
                                       "reason": "must be an integer"})
                    elif m_age < 0 or m_age > 130:
                        issues.append({"field": f"household_members[{idx}].age",
                                       "reason": "must be between 0 and 130"})
                for bool_field in ("disability", "veteran"):
                    bval = member.get(bool_field)
                    if bval is not None and not isinstance(bval, bool):
                        issues.append({"field": f"household_members[{idx}].{bool_field}",
                                       "reason": "must be a boolean"})
        residence = self.case.get("residence")
        if "residence" in self.case and self.case["residence"] is None:
            issues.append({"field": "residence", "reason": "must be an object (not null)"})
        if isinstance(residence, dict):
            for field in ("inspector_notes", "structure_status"):
                if field in residence and residence[field] is None:
                    issues.append({"field": f"residence.{field}",
                                   "reason": "must be a string (not null)"})
                value = residence.get(field)
                if value is not None and not isinstance(value, str):
                    issues.append({"field": f"residence.{field}",
                                   "reason": "must be a string"})
        conflict = self.case.get("data_conflict")
        if isinstance(conflict, dict):
            for field in ("field", "value_a", "source_a", "value_b", "source_b"):
                if not str(conflict.get(field) or "").strip():
                    issues.append({"field": f"data_conflict.{field}",
                                   "reason": "required non-empty value"})
        for field in ("last_action_date", "action_due_date"):
            value = self.case.get(field)
            if value is not None and not self._valid_iso_date(value):
                issues.append({"field": field, "reason": "must be an ISO date (YYYY-MM-DD)"})
        return issues

    def _validate_adapter_schema(self, agency_id):
        """Validate the minimum canonical envelope needed to run an agency rule pack."""
        issues = []
        if agency_id == "FEMA":
            incident = self.case.get("incident")
            evidence = self.case.get("evidence")
            if not isinstance(incident, dict):
                issues.append({"field": "incident", "reason": "required object"})
            else:
                if not self._valid_iso_date(incident.get("declaration_date")):
                    issues.append({"field": "incident.declaration_date",
                                   "reason": "required ISO date for FEMA rule-version selection"})
                appeal_date = incident.get("appeal_date")
                if appeal_date is not None and not self._valid_iso_date(appeal_date):
                    issues.append({"field": "incident.appeal_date",
                                   "reason": "must be an ISO date (YYYY-MM-DD)"})
                extra_ext = incident.get("extraordinary_circumstances_extension_approved")
                if extra_ext is not None and not isinstance(extra_ext, bool):
                    issues.append({
                        "field": "incident.extraordinary_circumstances_extension_approved",
                        "reason": "must be a boolean",
                    })
                inc_ext = incident.get("ihp_period_extension")
                if isinstance(inc_ext, dict):
                    approved = inc_ext.get("approved")
                    if approved is not None and not isinstance(approved, bool):
                        issues.append({
                            "field": "incident.ihp_period_extension.approved",
                            "reason": "must be a boolean",
                        })
                    pid = inc_ext.get("public_interest_determination")
                    if pid is not None and not isinstance(pid, bool):
                        issues.append({
                            "field": "incident.ihp_period_extension.public_interest_determination",
                            "reason": "must be a boolean",
                        })
                    basis = inc_ext.get("basis")
                    if basis is not None and not isinstance(basis, str):
                        issues.append({
                            "field": "incident.ihp_period_extension.basis",
                            "reason": "must be a string",
                        })
                    ext_end = inc_ext.get("end_date")
                    if ext_end is not None and not self._valid_iso_date(ext_end):
                        issues.append({
                            "field": "incident.ihp_period_extension.end_date",
                            "reason": "must be an ISO date (YYYY-MM-DD)",
                        })
            if not isinstance(evidence, dict):
                issues.append({"field": "evidence", "reason": "required object"})
        elif agency_id == "VA":
            claim = self.case.get("claim")
            evidence = self.case.get("evidence")
            if not isinstance(claim, dict):
                issues.append({"field": "claim", "reason": "required object"})
            else:
                if not self._valid_iso_date(claim.get("submission_date")):
                    issues.append({"field": "claim.submission_date",
                                   "reason": "required ISO date (YYYY-MM-DD)"})
                notice = claim.get("incomplete_application_notice_date")
                if notice is not None and not self._valid_iso_date(notice):
                    issues.append({"field": "claim.incomplete_application_notice_date",
                                   "reason": "must be an ISO date (YYYY-MM-DD)"})
                requested = claim.get("incomplete_application_requested_evidence")
                if notice is not None and (
                        not isinstance(requested, list)
                        or any(not isinstance(item, str) or not item.strip() for item in requested)):
                    issues.append({
                        "field": "claim.incomplete_application_requested_evidence",
                        "reason": "required array of non-empty strings when notice date is present",
                    })
                holidays = claim.get("evidence_time_limit_holidays")
                if notice is not None and (
                        not isinstance(holidays, list)
                        or any(not self._valid_iso_date(item) for item in holidays)):
                    issues.append({
                        "field": "claim.evidence_time_limit_holidays",
                        "reason": "required array of ISO dates when notice date is present",
                    })
                ext = claim.get("evidence_time_extension")
                if isinstance(ext, dict):
                    approved = ext.get("approved")
                    if approved is not None and not isinstance(approved, bool):
                        issues.append({
                            "field": "claim.evidence_time_extension.approved",
                            "reason": "must be a boolean",
                        })
                    ext_end = ext.get("end_date")
                    if ext_end is not None and not self._valid_iso_date(ext_end):
                        issues.append({
                            "field": "claim.evidence_time_extension.end_date",
                            "reason": "must be an ISO date (YYYY-MM-DD)",
                        })
            veteran = self.case.get("veteran")
            if "veteran" in self.case and veteran is None:
                issues.append({"field": "veteran", "reason": "must be an object (not null)"})
            if isinstance(veteran, dict):
                pact = veteran.get("pact_act_eligible")
                if pact is not None and not isinstance(pact, bool):
                    issues.append({
                        "field": "veteran.pact_act_eligible",
                        "reason": "must be a boolean",
                    })
            if not isinstance(evidence, dict):
                issues.append({"field": "evidence", "reason": "required object"})
        elif agency_id == "DUA":
            incident = self.case.get("incident")
            dua = self.case.get("dua")
            if not isinstance(incident, dict):
                issues.append({"field": "incident", "reason": "required object"})
            if not isinstance(dua, dict):
                issues.append({"field": "dua", "reason": "required object"})
            if isinstance(incident, dict) and isinstance(dua, dict):
                good_cause = dua.get("good_cause_late_filing")
                if good_cause is not None and not isinstance(good_cause, bool):
                    issues.append({
                        "field": "dua.good_cause_late_filing",
                        "reason": "must be a boolean",
                    })
                inc_dap = incident.get("disaster_assistance_period_end")
                dua_dap = dua.get("disaster_assistance_period_end")
                if inc_dap is not None and not self._valid_iso_date(inc_dap):
                    issues.append({
                        "field": "incident.disaster_assistance_period_end",
                        "reason": "must be an ISO date (YYYY-MM-DD)",
                    })
                if dua_dap is not None and not self._valid_iso_date(dua_dap):
                    issues.append({
                        "field": "dua.disaster_assistance_period_end",
                        "reason": "must be an ISO date (YYYY-MM-DD)",
                    })
                if (
                    isinstance(inc_dap, str)
                    and isinstance(dua_dap, str)
                    and self._valid_iso_date(inc_dap)
                    and self._valid_iso_date(dua_dap)
                    and inc_dap != dua_dap
                ):
                    issues.append({
                        "field": "dua.disaster_assistance_period_end",
                        "reason": "conflicts with incident.disaster_assistance_period_end",
                    })
                proof_date = dua.get("employment_proof_submitted_date")
                if proof_date is not None and not self._valid_iso_date(proof_date):
                    issues.append({
                        "field": "dua.employment_proof_submitted_date",
                        "reason": "must be an ISO date (YYYY-MM-DD)",
                    })
        return issues

    def _validate_generic_schema(self, rule):
        issues = []
        if not isinstance(self.case.get("evidence"), dict):
            issues.append({"field": "evidence", "reason": "required object"})
        deadline = self.case.get("deadline")
        expected_days = rule["deadline_days"]
        if expected_days is not None:
            if not isinstance(deadline, dict):
                return ([{"field": "deadline", "reason": "required object"}], None)
            if not self._valid_iso_date(deadline.get("start_date")):
                issues.append({"field": "deadline.start_date",
                               "reason": "required ISO date (YYYY-MM-DD)"})
            if (self.tenant_id == "MEDICAID_42_CFR" and
                    str(deadline.get("application_basis") or "").strip().lower()
                    == "disability"):
                expected_days = rule["disability_deadline_days"]
            supplied_days = deadline.get("limit_days")
            if supplied_days is not None and (
                    isinstance(supplied_days, bool) or supplied_days != expected_days):
                issues.append({
                    "field": "deadline.limit_days",
                    "reason": f"must equal canonical {expected_days}-day rule-pack value",
                })

        expedited = self.case.get("expedited")
        if expedited is not None:
            if not isinstance(expedited, dict):
                issues.append({"field": "expedited", "reason": "must be an object"})
            else:
                eligible = expedited.get("eligible")
                if eligible is not None and not isinstance(eligible, bool):
                    issues.append({"field": "expedited.eligible", "reason": "must be a boolean"})
                elif eligible is True:
                    if "expedited_days" not in rule:
                        issues.append({"field": "expedited", "reason": "not defined for this tenant"})
                    else:
                        if not self._valid_iso_date(expedited.get("start_date")):
                            issues.append({"field": "expedited.start_date",
                                           "reason": "required ISO date when expedited.eligible is true"})
                        supplied_expedited = expedited.get("limit_days")
                        if (supplied_expedited is not None and
                                (isinstance(supplied_expedited, bool) or
                                 supplied_expedited != rule["expedited_days"])):
                            issues.append({"field": "expedited.limit_days",
                                           "reason": (f"must equal canonical {rule['expedited_days']}-day "
                                                      "rule-pack value")})

        if self.tenant_id == "MEDICAID_FORUM_PHE_UNWINDING":
            procedural = self.case.get("procedural_status")
            if not isinstance(procedural, dict):
                issues.append({"field": "procedural_status", "reason": "required object"})
            else:
                for field in ("ex_parte_attempted", "state_wage_database_match"):
                    if not isinstance(procedural.get(field), bool):
                        issues.append({"field": f"procedural_status.{field}", "reason": "required boolean"})
                returned_mail = procedural.get("returned_mail_flag")
                if returned_mail is not None and not isinstance(returned_mail, bool):
                    issues.append({"field": "procedural_status.returned_mail_flag", "reason": "must be a boolean"})

        if self.tenant_id == "SNAP_FORUM_SAR7_INCOME":
            income = self.case.get("income")
            if not isinstance(income, dict):
                issues.append({"field": "income", "reason": "required object"})
            else:
                for field in ("baseline_monthly", "one_time_overtime_spike"):
                    value = income.get(field)
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or value < 0
                    ):
                        issues.append({"field": f"income.{field}", "reason": "required non-negative finite number"})
                if not str(income.get("procedural_flag") or "").strip():
                    issues.append({"field": "income.procedural_flag", "reason": "required non-empty string"})
        return issues, expected_days

    def _audit_adapter(self, agency_id):
        if not AGENCY_ADAPTERS_AVAILABLE:
            return self._input_invalid([{"field": "tenant_id", "reason": "agency adapters unavailable"}],
                                       agency=agency_id)
        adapter = get_adapter(agency_id)
        if adapter is None:
            return self._input_invalid([{"field": "tenant_id",
                                         "reason": f"canonical {agency_id} adapter unavailable"}],
                                       agency=agency_id)
        adapter_result = adapter.audit(self.case, self.today)
        self.flags.extend(adapter_result.get("flags") or [])
        status = adapter_result.get("status")
        if status not in {"PASS", "ESCALATE", "INPUT_INVALID"}:
            return self._input_invalid([{"field": "adapter.status",
                                         "reason": f"unexpected adapter status {status!r}"}],
                                       agency=agency_id, adapter_used=True)
        self.overall_status = status
        if status == "INPUT_INVALID":
            return self._result(agency=agency_id, adapter_used=True)
        self._audit_common_rules()
        return self._result(agency=agency_id, adapter_used=True)

    def _audit_common_rules(self):
        self._audit_redundant_requests()
        vulnerable = self._audit_vulnerable_household()
        housing = self._audit_housing_at_risk(vulnerable)
        if vulnerable:
            self.effective_stall_idle_days = min(self.effective_stall_idle_days, self.vulnerable_stall_idle_days)
        if housing:
            self.effective_stall_idle_days = min(self.effective_stall_idle_days, self.housing_stall_idle_days)
        self._audit_third_party_reports()
        self._audit_stall()

    def audit(self):
        """Evaluate exactly one canonical tenant path; malformed or unknown input never returns PASS."""
        common_issues = self._validate_common_schema()
        if common_issues:
            return self._input_invalid(common_issues)

        agency_id = ADAPTER_TENANTS.get(self.tenant_id)
        if agency_id:
            adapter_issues = self._validate_adapter_schema(agency_id)
            if adapter_issues:
                return self._input_invalid(adapter_issues, agency=agency_id)
            return self._audit_adapter(agency_id)

        rule = GENERIC_PROGRAM_RULES.get(self.tenant_id)
        if rule is None:
            return self._input_invalid([{"field": "tenant_id",
                                         "reason": f"unknown tenant {self.tenant_id!r}"}])

        issues, expected_days = self._validate_generic_schema(rule)
        if issues:
            return self._input_invalid(issues)
        self._audit_generic_program(rule, expected_days)
        if self.tenant_id == "MEDICAID_FORUM_PHE_UNWINDING":
            self._audit_medicaid_ex_parte_renewal()
        elif self.tenant_id == "SNAP_FORUM_SAR7_INCOME":
            self._audit_snap_non_recurring_overtime()
        self._audit_common_rules()
        return self._result()

    def _audit_stall(self):
        """STALLED_CASE_ESCALATION when the case has had no action for longer than the idle threshold, or its
        action due date has passed with nothing done since. Needs last_action_date; action_due_date is optional.
        The idle threshold is shortened for households with housing at risk or a vulnerable member."""
        last_raw = self.case.get("last_action_date")
        if not last_raw:
            return
        threshold = self.effective_stall_idle_days
        last = _parse_date(last_raw)
        idle = (self.today - last).days
        due_raw = self.case.get("action_due_date")
        due = _parse_date(due_raw) if due_raw else None
        reasons = []
        if idle > threshold:
            reasons.append(f"no action for {idle} days (idle threshold {threshold} days)")
        if due is not None and due < self.today and last <= due:
            reasons.append(f"action was due {due.isoformat()} and nothing has happened since {last.isoformat()}")
        if reasons:
            self.flags.append({"type": "STALLED_CASE_ESCALATION",
                               "details": {"last_action_date": last.isoformat(), "idle_days": idle,
                                           "idle_threshold_days": threshold,
                                           "action_due_date": due.isoformat() if due else None,
                                           "reason": "; ".join(reasons)},
                               "message": f"STALLED CASE: {'; '.join(reasons)}. Routed to a supervisor so the applicant is not left in a hold."})
            self.overall_status = "ESCALATE"

    def _audit_redundant_requests(self):
        """Every program: a request for a document already in the evidence vault is blocked (names canonicalised)."""
        for d in document_request_decisions(self.case):
            if d["decision"] != "BLOCKED_ALREADY_ON_FILE":
                continue
            self.flags.append({"type": "REDUNDANT_REQUEST_BLOCKED",
                               "details": {"requested": d["document"], "on_file_as": d["on_file_as"],
                                           "document_key": d["document_key"]},
                               "message": f"REQUEST BLOCKED: REDUNDANT_REQUEST_BLOCKED. Caseworker is requesting '{d['document']}', which is already in the evidence vault as '{d['on_file_as']}'. The duplicate request is blocked to prevent an administrative delay loop; the case itself is not locked."})
            self.overall_status = "ESCALATE"

    def _audit_vulnerable_household(self):
        """Scan ALL household members (age 55+, disability, veteran). Advisory: raises priority, shortens the stall
        threshold, never blocks. Internal ASCE priority rule (DRAFT), not a CFR mandate."""
        hits = []
        for i, member in enumerate(self.case.get("household_members") or []):
            member = member if isinstance(member, dict) else {}
            criteria = []
            age = member.get("age")
            if isinstance(age, int) and not isinstance(age, bool) and age >= VULNERABLE_MEMBER_MIN_AGE:
                criteria.append(f"age {age} ({VULNERABLE_MEMBER_MIN_AGE}+)")
            if member.get("disability") is True:
                criteria.append("disability")
            if member.get("veteran") is True:
                criteria.append("veteran")
            if criteria:
                hits.append({"member_id": member.get("member_id") or f"member-{i + 1}",
                             "relationship": member.get("relationship"), "criteria": criteria})
        if not hits:
            return False
        summary = "; ".join(f"{h['member_id']}" + (f" ({h['relationship']})" if h["relationship"] else "") +
                            f": {', '.join(h['criteria'])}" for h in hits)
        threshold = min(self.stall_idle_days, self.vulnerable_stall_idle_days)
        self.flags.append({"type": "VULNERABLE_HOUSEHOLD_MEMBER",
                           "details": {"members": hits, "member_summary": summary,
                                       "stall_idle_threshold_days": threshold,
                                       "basis": "ASCE internal priority rule (DRAFT), not a CFR mandate"},
                           "message": f"PRIORITY: VULNERABLE HOUSEHOLD MEMBER(S) - {summary}. Queue priority raised and stall threshold shortened to {threshold} days; review accommodations."})
        return True

    def _audit_housing_at_risk(self, vulnerable=False):
        """Eviction notice, rent past due or loss of temporary housing: supervisor escalation, top of queue."""
        housing = self.case.get("housing") or {}
        reasons = []
        if housing.get("eviction_notice_date"):
            reasons.append(f"eviction notice dated {housing['eviction_notice_date']}")
        if housing.get("rent_past_due") is True:
            reasons.append("rent past due")
        if housing.get("temporary_housing_lost") is True:
            reasons.append("loss of temporary housing")
        if not reasons:
            return False
        threshold = min(self.stall_idle_days, self.housing_stall_idle_days,
                        self.vulnerable_stall_idle_days if vulnerable else self.housing_stall_idle_days)
        immediate = bool(vulnerable)
        self.flags.append({"type": "HOUSING_AT_RISK",
                           "details": {"housing_reasons": reasons, "stall_idle_threshold_days": threshold,
                                       "combined_with_vulnerable_member": immediate,
                                       "escalation": "IMMEDIATE supervisor escalation" if immediate else
                                                     "supervisor escalation",
                                       "policy_due_key": "IMMEDIATE" if immediate else "URGENT",
                                       "basis": "ASCE internal priority rule (DRAFT), not a CFR mandate"},
                           "message": f"HOUSING AT RISK: {', '.join(reasons)}. {'IMMEDIATE supervisor escalation (vulnerable household member)' if immediate else 'Supervisor escalation'}; top of queue; stall threshold {threshold} days. The case is escalated, not held."})
        self.overall_status = "ESCALATE"
        return True

    def _audit_third_party_reports(self):
        """Verified-fact protection and identity check for third-party verification reports.
        A report never overrides a verified fact, never locks the case, never sets a fraud status and never suspends
        payments. Reports dated before the verification were already superseded by it and are not re-raised."""
        applicant = self.case.get("applicant") or {}
        verified = {}
        for fact in self.case.get("verified_facts") or []:
            key = _norm((fact or {}).get("fact"))
            if key and key not in verified:
                verified[key] = fact
        for i, report in enumerate(self.case.get("third_party_reports") or []):
            report = report if isinstance(report, dict) else {}
            report_id = report.get("report_id") or f"report-{i + 1}"
            source = report.get("source") or "third party"
            idc = identity_check(applicant, report.get("subject"))
            if idc["result"] != "MATCH":
                why = (f"mismatched on {', '.join(idc['mismatched_fields'])}" if idc["mismatched_fields"] else
                       "no identifier beyond a first name could be compared")
                self.flags.append({"type": "POSSIBLE_IDENTITY_MISMATCH",
                                   "details": {"report_id": report_id, "report_source": source,
                                               "fact": report.get("fact"), **idc,
                                               "treated_as": "UNRELIABLE until a human confirms it is the same person"},
                                   "message": f"POSSIBLE IDENTITY MISMATCH: report {report_id} from {source} {why}. The report is treated as unreliable until a human confirms it concerns this applicant."})
                self.overall_status = "ESCALATE"
            prior = verified.get(_norm(report.get("fact")))
            if not prior or _norm(report.get("finding")) != "conflict":
                continue
            if report.get("date") and prior.get("date") and report["date"] < prior["date"]:
                continue
            ignored = report.get("recommended_action")
            self.flags.append({"type": "VERIFIED_FACT_CONFLICT",
                               "details": {"fact": prior.get("fact"),
                                           "verified_value": prior.get("value"),
                                           "verified_by": prior.get("verified_by"),
                                           "verified_method": prior.get("method"),
                                           "verified_date": prior.get("date"),
                                           "report_id": report_id, "report_source": source,
                                           "reported_by": report.get("reported_by"),
                                           "report_date": report.get("date"),
                                           "reported_value": report.get("reported_value"),
                                           "report_identity_check": idc["result"],
                                           "report_recommended_action_not_applied": ignored,
                                           "verified_fact_status": "VERIFIED (unchanged by the engine)",
                                           "engine_actions": "no lock, no fraud status, no payment suspension; "
                                                             "routed to human reconciliation"},
                               "message": f"VERIFIED FACT PROTECTED: {prior.get('fact')} was verified on {prior.get('date')} by {prior.get('verified_by')} ({prior.get('method')}). Report {report_id} from {source} dated {report.get('date')} conflicts with it. The verified fact stands; the report does not lock the case, set a fraud status or suspend payments. Routed to human reconciliation." + (f" The report's recommended action ('{ignored}') was NOT applied." if ignored else "")})
            self.overall_status = "ESCALATE"

    def _audit_generic_program(self, rule, expected_days):
        """Apply canonical rule-pack values; labels, citations, and limits never come from the caller."""
        if expected_days is not None:
            deadline = self.case["deadline"]
            started = _parse_date(deadline["start_date"])
            elapsed = (self.today - started).days
            if elapsed > expected_days:
                self.flags.append({
                    "type": "SLA_CRITICAL",
                    "citation": rule["citation"],
                    "details": {
                        "start_date": started.isoformat(),
                        "elapsed_days": elapsed,
                        "limit_days": expected_days,
                    },
                    "message": (
                        f"{rule['label']}: {elapsed} days elapsed against the canonical "
                        f"{expected_days}-day rule-pack limit ({rule['citation']}). "
                        "Human review required."
                    ),
                })
                self.overall_status = "ESCALATE"

        expedited = self.case.get("expedited") or {}
        if expedited.get("eligible") is True:
            expedited_start = _parse_date(expedited["start_date"])
            expedited_elapsed = (self.today - expedited_start).days
            expedited_days = rule["expedited_days"]
            if expedited_elapsed > expedited_days:
                self.flags.append({
                    "type": "SLA_CRITICAL",
                    "citation": rule["expedited_citation"],
                    "details": {
                        "start_date": expedited_start.isoformat(),
                        "elapsed_days": expedited_elapsed,
                        "limit_days": expedited_days,
                    },
                    "message": (
                        f"Expedited processing clock: {expedited_elapsed} days elapsed against "
                        f"the canonical {expedited_days}-day limit ({rule['expedited_citation']})."
                    ),
                })
                self.overall_status = "ESCALATE"

        suppress_paper_packet = False
        if self.tenant_id == "MEDICAID_FORUM_PHE_UNWINDING":
            procedural = self.case["procedural_status"]
            suppress_paper_packet = procedural["state_wage_database_match"] is True

        for missing_item in (self.case.get("evidence") or {}).get("missing") or []:
            if suppress_paper_packet and "paper renewal packet" in missing_item.lower():
                continue
            self.flags.append({
                "type": "EVIDENCE_REQUIRED",
                "citation": rule["citation"],
                "details": {"missing_document": missing_item},
                "message": f"Evidence review required for missing item: {missing_item}.",
            })
            self.overall_status = "ESCALATE"

        age = (self.case.get("applicant") or {}).get("age", 0)
        if isinstance(age, (int, float)) and not isinstance(age, bool) and age >= 65:
            self.flags.append({
                "type": "PRIORITY_EXPEDITE",
                "citation": None,
                "policy_id": "ASCE_INTERNAL_PRIORITY_POLICY",
                "message": f"ASCE internal priority routing for an older applicant (age {age}).",
            })

        conflict = self.case.get("data_conflict")
        if conflict:
            self.flags.append({
                "type": "DATA_CONFLICT",
                "citation": None,
                "policy_id": "ASCE_DATA_INTEGRITY_REVIEW",
                "details": dict(conflict),
                "message": "Cross-source data conflict requires human reconciliation.",
            })
            self.overall_status = "ESCALATE"

    def _audit_medicaid_ex_parte_renewal(self):
        """Apply §435.916 ordering: reliable wage data is considered before a paper-packet termination."""
        procedural = self.case["procedural_status"]
        intent = str((self.case.get("caseworker_action") or {}).get("intent") or "").lower()
        requested = [
            item.lower()
            for item in (
                (self.case.get("caseworker_action") or {}).get("requesting_documents")
                or []
            )
            if isinstance(item, str)
        ]
        paper_packet_requested = any("paper renewal packet" in item for item in requested)
        if procedural["state_wage_database_match"] is True and (
                procedural["ex_parte_attempted"] is False or
                "disenroll" in intent or "terminat" in intent or
                paper_packet_requested):
            self.flags.append({
                "type": "EX_PARTE_RENEWAL_REQUIRED",
                "category": "PROCEDURAL_PROTECTION",
                "citation": "42 CFR 435.916",
                "details": {
                    "state_wage_database_match": True,
                    "ex_parte_attempted": procedural["ex_parte_attempted"],
                    "paper_packet_required": False,
                },
                "message": (
                    "Reliable state wage data is available. Complete the ex-parte renewal determination "
                    "before requesting a paper packet or taking procedural termination action."
                ),
            })
            self.overall_status = "ESCALATE"

    def _audit_snap_non_recurring_overtime(self):
        """Separate a reported one-time overtime amount from ongoing projected SNAP income."""
        income = self.case["income"]
        spike = income["one_time_overtime_spike"]
        if spike <= 0:
            return
        self.flags.append({
            "type": "SNAP_NON_RECURRING_OVERTIME",
            "category": "INCOME_EXCLUSION_REVIEW",
            "citation": "7 CFR 273.12",
            "details": {
                "baseline_monthly": income["baseline_monthly"],
                "one_time_overtime_spike": spike,
                "exclude_from_ongoing_projection": True,
            },
            "message": (
                "The reported overtime spike is identified as non-recurring and must not be silently "
                "projected as ongoing monthly income; route the proposed adverse action for human review."
            ),
        })
        self.overall_status = "ESCALATE"
