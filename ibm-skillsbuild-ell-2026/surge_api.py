"""Stateless, deterministic-only JSON API for surge intake (/api/v1/*).

No model is called anywhere in this module. Every result comes from the ComplianceEngine
(compliance_engine.DeterministicAuditor) plus the fixed templates in templates.py, so identical input always
gives identical output and a large surge (disaster declaration + Disaster Unemployment Assistance wave) can be
processed without any AI-generated text.

Endpoints (all require header X-ASCE-API-Key == env ASCE_API_KEY; unset key -> 503 "API disabled"):
  GET  /api/v1/health  limits and mode
  POST /api/v1/batch   {"cases": [...], "include_templates": true}
  POST /api/v1/case    {"case": {...}}  (or the case object itself)

Environment:
  ASCE_API_KEY          shared API key (required to enable the API)
  BATCH_MAX_CASES       max cases per batch request (default 5000)
  MAX_CONTENT_LENGTH_MB request body cap, set on the Flask app (default 25)
"""
import datetime
import hmac
import logging
import math
import os
import re
import time
import uuid
from collections import Counter

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from compliance_engine import (
    ADAPTER_TENANTS,
    DUA_TENANT,
    ENGINE_PAYMENT_ACTION,
    GENERIC_PROGRAM_RULES,
    RULE_PACK_VERSION,
    DeterministicAuditor,
    document_request_decisions,
    fraud_referral_gate,
    identity_check,
)
from templates import DECISION_QUEUES as TEMPLATE_QUEUES
from templates import build_action_plan, engine_route, proposed_action_gate

logger = logging.getLogger("asce.surge_api")

API_KEY_HEADER = "X-ASCE-API-Key"
REQUEST_ID_HEADER = "X-Request-ID"
DEFAULT_BATCH_MAX_CASES = 5000
API_MODE = "DETERMINISTIC_ONLY"

_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQ_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CITATION_RE = re.compile(r"(\d+)\s*C\.?\s*F\.?\s*R\.?\s*§*\s*(\d+\.\d+(?:\([0-9A-Za-z]+\))*)", re.IGNORECASE)
MAX_LIST_ITEMS = 200
MAX_STR_LEN = 500
MAX_WEEKS_CLAIMED = 60
MAX_PROTECTION_ITEMS = 50
DUA_BASIS_VALUES = ("statement_only", "documented")
REPORT_FINDINGS = ("CONFLICT", "CONFIRMS")
ESCALATED_ROUTES = ("ESCALATE", "SUPERVISOR_ESCALATION", "HUMAN_RECONCILIATION")
LOAD_NOTE = ("Caseworker load removed = preparation work only (triage, document checks, drafted notices, routing). "
             "The engine never approves, denies or sets a fraud status: every case still requires a human decision.")


def _env_int(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------------------------------------------
# Schema validation. Mirrors exactly what the engine reads, so a case that passes can never crash the engine.
# ---------------------------------------------------------------------------------------------------------------
class _V:
    def __init__(self):
        self.errors = []

    def err(self, path, msg):
        self.errors.append(f"{path}: {msg}")

    def obj(self, value, path, required=False):
        if value is None:
            if required:
                self.err(path, "required object")
            return None
        if not isinstance(value, dict):
            self.err(path, "must be an object")
            return None
        return value

    def string(self, value, path, required=False):
        if value is None:
            if required:
                self.err(path, "required string")
            return None
        if not isinstance(value, str):
            self.err(path, "must be a string")
            return None
        if len(value) > MAX_STR_LEN:
            self.err(path, f"longer than {MAX_STR_LEN} characters")
            return None
        return value

    def str_list(self, value, path):
        if value is None:
            return
        if not isinstance(value, list):
            self.err(path, "must be a list of strings")
            return
        if len(value) > MAX_LIST_ITEMS:
            self.err(path, f"more than {MAX_LIST_ITEMS} items")
            return
        for i, item in enumerate(value):
            self.string(item, f"{path}[{i}]", required=True)

    def integer(self, value, path, lo, hi, required=False):
        if value is None:
            if required:
                self.err(path, "required integer")
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            self.err(path, "must be an integer")
            return None
        if not lo <= value <= hi:
            self.err(path, f"must be between {lo} and {hi}")
            return None
        return value

    def boolean(self, value, path):
        if value is not None and not isinstance(value, bool):
            self.err(path, "must be true or false")

    def date(self, value, path, required=False):
        if value is None:
            if required:
                self.err(path, "required date (YYYY-MM-DD)")
            return None
        if not isinstance(value, str) or not _DATE_RE.match(value):
            self.err(path, "must be a date string YYYY-MM-DD")
            return None
        try:
            parsed = datetime.datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            self.err(path, "is not a real calendar date")
            return None
        if not 1900 <= parsed.year <= 2100:
            self.err(path, "year must be between 1900 and 2100")
            return None
        return parsed


def validate_case(case, allowed_tenants):
    """Return a list of human-readable schema errors (empty list means the case is safe to run)."""
    v = _V()
    if not isinstance(case, dict):
        return ["case: must be a JSON object"]

    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not _CASE_ID_RE.match(case_id):
        v.err("case_id", "required; 1-128 characters of letters, digits, '.', '_', ':' or '-'")
    tenant_id = case.get("tenant_id")
    if not isinstance(tenant_id, str) or tenant_id not in allowed_tenants:
        v.err("tenant_id", f"required; one of {', '.join(sorted(allowed_tenants))}")
        return v.errors

    applicant = v.obj(case.get("applicant"), "applicant", required="applicant" in case)
    if applicant is not None and "age" in applicant:
        v.integer(applicant.get("age"), "applicant.age", 0, 130, required=True)
    if applicant is not None:
        _validate_identity(applicant, "applicant", v)

    if "evidence" in case:
        evidence = v.obj(case.get("evidence"), "evidence", required=True)
        if evidence is not None:
            for key in ("uploaded", "missing"):
                if key in evidence:
                    if evidence[key] is None:
                        v.err(f"evidence.{key}", "must be a list of strings")
                    v.str_list(evidence[key], f"evidence.{key}")

    if "caseworker_action" in case:
        action = v.obj(case.get("caseworker_action"), "caseworker_action", required=True)
        if action is not None:
            if "requesting_documents" in action:
                if action["requesting_documents"] is None:
                    v.err("caseworker_action.requesting_documents", "must be a list of strings")
                v.str_list(action["requesting_documents"], "caseworker_action.requesting_documents")
            v.string(action.get("intent"), "caseworker_action.intent")
            if action.get("proposed_decision") is not None and \
                    action.get("proposed_decision") not in ("APPROVE_FOR_REVIEW", "RETURN", "ESCALATE"):
                v.err("caseworker_action.proposed_decision", "must be APPROVE_FOR_REVIEW, RETURN or ESCALATE")
            if action.get("fraud_referral") is not None:
                ref = v.obj(action.get("fraud_referral"), "caseworker_action.fraud_referral", required=True)
                if ref is not None:
                    v.string(ref.get("initials"), "caseworker_action.fraud_referral.initials")
                    v.string(ref.get("reason"), "caseworker_action.fraud_referral.reason")
                    v.boolean(ref.get("prior_verifications_reviewed"),
                              "caseworker_action.fraud_referral.prior_verifications_reviewed")

    # Stall detector inputs (any program).
    if case.get("last_action_date"):
        v.date(case.get("last_action_date"), "last_action_date", required=True)
    if case.get("action_due_date"):
        v.date(case.get("action_due_date"), "action_due_date", required=True)

    # Applicant-protection inputs (any program).
    _validate_protection(case, v)

    agency_id = ADAPTER_TENANTS.get(tenant_id)
    if agency_id == "FEMA":
        _validate_fema(case, v)
    elif agency_id == "VA":
        _validate_va(case, v)
    elif agency_id == "DUA":
        _validate_dua(case, v)
    elif tenant_id in GENERIC_PROGRAM_RULES:
        _validate_generic(case, tenant_id, v)
    else:
        v.err("tenant_id", f"unsupported or unknown tenant rule pack: {tenant_id}")
    return v.errors


def _validate_identity(obj, path, v, first_name=False):
    for key in ("full_name", "unit", "applicant_id") + (("first_name",) if first_name else ()):
        if key in obj:
            v.string(obj[key], f"{path}.{key}")
    if "dob_year" in obj and obj["dob_year"] is not None:
        v.integer(obj["dob_year"], f"{path}.dob_year", 1900, 2100, required=True)


def _obj_list(value, path, v, max_items=MAX_PROTECTION_ITEMS):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > max_items:
        v.err(path, f"must be a list of at most {max_items} objects")
        return []
    out = []
    for i, item in enumerate(value):
        if isinstance(item, dict):
            out.append((i, item))
        else:
            v.err(f"{path}[{i}]", "must be an object")
    return out


def _validate_protection(case, v):
    for i, m in _obj_list(case.get("household_members"), "household_members", v):
        p = f"household_members[{i}]"
        if "age" in m:
            v.integer(m.get("age"), f"{p}.age", 0, 130, required=True)
        v.boolean(m.get("disability"), f"{p}.disability")
        v.boolean(m.get("veteran"), f"{p}.veteran")
        v.string(m.get("member_id"), f"{p}.member_id")
        v.string(m.get("relationship"), f"{p}.relationship")
    if "housing" in case:
        housing = v.obj(case.get("housing"), "housing", required=True)
        if housing is not None:
            v.date(housing.get("eviction_notice_date"), "housing.eviction_notice_date")
            v.boolean(housing.get("rent_past_due"), "housing.rent_past_due")
            v.boolean(housing.get("temporary_housing_lost"), "housing.temporary_housing_lost")
    for i, fact in _obj_list(case.get("verified_facts"), "verified_facts", v):
        p = f"verified_facts[{i}]"
        v.string(fact.get("fact"), f"{p}.fact", required=True)
        v.date(fact.get("date"), f"{p}.date", required=True)
        for key in ("value", "verified_by", "method"):
            v.string(fact.get(key), f"{p}.{key}")
    for i, rep in _obj_list(case.get("third_party_reports"), "third_party_reports", v):
        p = f"third_party_reports[{i}]"
        v.string(rep.get("fact"), f"{p}.fact", required=True)
        if rep.get("finding") not in REPORT_FINDINGS:
            v.err(f"{p}.finding", f"required; one of {', '.join(REPORT_FINDINGS)}")
        v.date(rep.get("date"), f"{p}.date", required=True)
        for key in ("report_id", "source", "reported_by", "reported_value", "recommended_action"):
            v.string(rep.get(key), f"{p}.{key}")
        if "subject" in rep:
            subject = v.obj(rep.get("subject"), f"{p}.subject", required=True)
            if subject is not None:
                _validate_identity(subject, f"{p}.subject", v, first_name=True)



def _validate_fema(case, v):
    incident = v.obj(case.get("incident"), "incident", required=True)
    if incident is not None:
        v.date(incident.get("declaration_date"), "incident.declaration_date", required=True)
        if incident.get("appeal_date") is not None:
            v.date(incident.get("appeal_date"), "incident.appeal_date", required=True)
        ext_raw = incident.get("ihp_period_extension")
        if ext_raw is not None:
            ext = v.obj(ext_raw, "incident.ihp_period_extension", required=True)
            if ext is not None:
                v.boolean(ext.get("approved"), "incident.ihp_period_extension.approved")
                v.boolean(
                    ext.get("public_interest_determination"),
                    "incident.ihp_period_extension.public_interest_determination",
                )
                v.string(ext.get("basis"), "incident.ihp_period_extension.basis")
                if ext.get("approved") is True or ext.get("end_date") is not None:
                    v.date(ext.get("end_date"), "incident.ihp_period_extension.end_date", required=True)
        v.boolean(
            incident.get("extraordinary_circumstances_extension_approved"),
            "incident.extraordinary_circumstances_extension_approved",
        )
    if "evidence" not in case:
        v.err("evidence", "required object")
    residence = v.obj(case.get("residence"), "residence", required="residence" in case)
    if residence is not None:
        for key in ("inspector_notes", "structure_status"):
            if key in residence:
                if residence[key] is None:
                    v.err(f"residence.{key}", "must be a string (not null)")
                else:
                    v.string(residence[key], f"residence.{key}", required=True)


def _validate_va(case, v):
    claim = v.obj(case.get("claim"), "claim", required=True)
    if "evidence" not in case:
        v.err("evidence", "required object")
    if claim is not None:
        v.date(claim.get("submission_date"), "claim.submission_date", required=True)
        if claim.get("incomplete_application_notice_date") is not None:
            v.date(claim.get("incomplete_application_notice_date"), "claim.incomplete_application_notice_date", required=True)
            req = claim.get("incomplete_application_requested_evidence")
            if not isinstance(req, list) or any(not isinstance(item, str) or not item.strip() for item in req):
                v.err("claim.incomplete_application_requested_evidence",
                      "required array of non-empty strings when incomplete_application_notice_date is set")
            holidays = claim.get("evidence_time_limit_holidays")
            if not isinstance(holidays, list):
                v.err("claim.evidence_time_limit_holidays",
                      "required array of ISO dates when incomplete_application_notice_date is set")
            else:
                for i, h in enumerate(holidays):
                    v.date(h, f"claim.evidence_time_limit_holidays[{i}]", required=True)
        if claim.get("condition") is not None:
            v.string(claim.get("condition"), "claim.condition")
        ext_raw = claim.get("evidence_time_extension")
        if ext_raw is not None:
            ext = v.obj(ext_raw, "claim.evidence_time_extension", required=True)
            if ext is not None:
                v.boolean(ext.get("approved"), "claim.evidence_time_extension.approved")
                if ext.get("approved") is True or ext.get("end_date") is not None:
                    v.date(ext.get("end_date"), "claim.evidence_time_extension.end_date", required=True)
    if "veteran" in case:
        vet = v.obj(case.get("veteran"), "veteran", required=True)
        if vet is not None:
            v.boolean(vet.get("pact_act_eligible"), "veteran.pact_act_eligible")


def _validate_generic(case, tenant_id, v):
    rule = GENERIC_PROGRAM_RULES[tenant_id]
    expected_days = rule["deadline_days"]
    dl = case.get("deadline")
    if expected_days is not None:
        dl = v.obj(dl, "deadline", required=True)
        if dl is not None:
            v.date(dl.get("start_date"), "deadline.start_date", required=True)
            if tenant_id == "MEDICAID_42_CFR" and dl.get("application_basis") is not None:
                basis = v.string(dl.get("application_basis"), "deadline.application_basis")
                if basis is not None and basis.strip().lower() == "disability":
                    expected_days = rule["disability_deadline_days"]
            if dl.get("limit_days") is not None:
                sup = v.integer(dl.get("limit_days"), "deadline.limit_days", 0, 36500, required=True)
                if sup is not None and sup != expected_days:
                    v.err("deadline.limit_days", f"must equal canonical {expected_days}-day rule-pack value")
            if dl.get("label") is not None:
                v.string(dl.get("label"), "deadline.label")
            if dl.get("citation") is not None:
                v.string(dl.get("citation"), "deadline.citation")
    elif dl is not None:
        dl = v.obj(dl, "deadline", required=True)
        if dl is not None and dl.get("start_date") is not None:
            v.date(dl.get("start_date"), "deadline.start_date", required=True)

    ex = case.get("expedited")
    if ex is not None:
        ex = v.obj(ex, "expedited", required=True)
        if ex is not None:
            v.boolean(ex.get("eligible"), "expedited.eligible")
            if ex.get("eligible") is True:
                if "expedited_days" not in rule:
                    v.err("expedited", "not defined for this tenant")
                else:
                    v.date(ex.get("start_date"), "expedited.start_date", required=True)
                    if ex.get("limit_days") is not None:
                        sup_exp = v.integer(ex.get("limit_days"), "expedited.limit_days", 0, 36500, required=True)
                        if sup_exp is not None and sup_exp != rule["expedited_days"]:
                            v.err("expedited.limit_days",
                                  f"must equal canonical {rule['expedited_days']}-day rule-pack value")
                    if ex.get("citation") is not None:
                        v.string(ex.get("citation"), "expedited.citation")

    if tenant_id == "MEDICAID_FORUM_PHE_UNWINDING":
        proc = v.obj(case.get("procedural_status"), "procedural_status", required=True)
        if proc is not None:
            for field in ("ex_parte_attempted", "state_wage_database_match"):
                if not isinstance(proc.get(field), bool):
                    v.err(f"procedural_status.{field}", "required boolean")
            if "returned_mail_flag" in proc:
                v.boolean(proc.get("returned_mail_flag"), "procedural_status.returned_mail_flag")

    if tenant_id == "SNAP_FORUM_SAR7_INCOME":
        inc = v.obj(case.get("income"), "income", required=True)
        if inc is not None:
            for field in ("baseline_monthly", "one_time_overtime_spike"):
                val = inc.get(field)
                if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0:
                    v.err(f"income.{field}", "required non-negative number")
                elif isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    v.err(f"income.{field}", "must be a finite number (not NaN or Inf)")
            flag_str = v.string(inc.get("procedural_flag"), "income.procedural_flag", required=True)
            if flag_str is not None and not flag_str.strip():
                v.err("income.procedural_flag", "required non-empty string")

    conflict = case.get("data_conflict")
    if conflict is not None:
        conflict = v.obj(conflict, "data_conflict", required=True)
        if conflict is not None:
            for key in ("field", "value_a", "source_a", "value_b", "source_b"):
                val = v.string(conflict.get(key), f"data_conflict.{key}", required=True)
                if val is not None and not val.strip():
                    v.err(f"data_conflict.{key}", "required non-empty string")


def _validate_dua(case, v):
    incident = v.obj(case.get("incident"), "incident", required=True)
    dua = v.obj(case.get("dua"), "dua", required=True)
    if incident is None or dua is None:
        return
    began = v.date(incident.get("disaster_began_date"), "incident.disaster_began_date", required=True)
    declared = v.date(incident.get("declaration_date"), "incident.declaration_date", required=True)
    announced = v.date(incident.get("announcement_date"), "incident.announcement_date", required=True)
    holidays = incident.get("area_legal_holidays")
    if holidays is not None:
        if not isinstance(holidays, list) or len(holidays) > 366:
            v.err("incident.area_legal_holidays", "must be a list of at most 366 dates")
        else:
            for i, h in enumerate(holidays):
                v.date(h, f"incident.area_legal_holidays[{i}]", required=True)
    inc_dap_end = v.date(incident.get("disaster_assistance_period_end"), "incident.disaster_assistance_period_end")
    dua_dap_end = v.date(dua.get("disaster_assistance_period_end"), "dua.disaster_assistance_period_end")
    if inc_dap_end and dua_dap_end and inc_dap_end != dua_dap_end:
        v.err("dua.disaster_assistance_period_end", "conflicts with incident.disaster_assistance_period_end")
    if began and declared and declared < began:
        v.err("incident.declaration_date", "is before disaster_began_date")
    if declared and announced and announced < declared:
        v.err("incident.announcement_date", "is before declaration_date")
    if declared and inc_dap_end and inc_dap_end < declared:
        v.err("incident.disaster_assistance_period_end", "is before declaration_date")
    if declared and dua_dap_end and dua_dap_end < declared:
        v.err("dua.disaster_assistance_period_end", "is before declaration_date")

    app_obj = (
        v.obj(case.get("application"), "application", required=True)
        if "application" in case
        else None
    )
    has_initial = "initial_application_date" in dua
    raw_initial = dua.get("initial_application_date")
    has_sub = isinstance(app_obj, dict) and "submission_date" in app_obj
    raw_sub = app_obj.get("submission_date") if isinstance(app_obj, dict) else None

    filed_initial = v.date(raw_initial, "dua.initial_application_date", required=True) if has_initial else None
    filed_sub = v.date(raw_sub, "application.submission_date", required=True) if has_sub else None
    if has_initial and has_sub and filed_initial and filed_sub and filed_initial != filed_sub:
        v.err(
            "application.submission_date",
            "conflicts with dua.initial_application_date",
        )
    if not has_initial and not has_sub:
        v.err("dua.initial_application_date", "required date (YYYY-MM-DD)")
    filed = filed_initial or filed_sub
    if filed and began and filed < began:
        v.err("dua.initial_application_date", "is before disaster_began_date")
    v.boolean(dua.get("good_cause_late_filing"), "dua.good_cause_late_filing")
    if dua.get("weekly_amount_basis") not in DUA_BASIS_VALUES:
        v.err("dua.weekly_amount_basis", f"required; one of {', '.join(DUA_BASIS_VALUES)}")
    proof = v.date(dua.get("employment_proof_submitted_date"), "dua.employment_proof_submitted_date")
    if proof and filed and proof < filed:
        v.err("dua.employment_proof_submitted_date", "is before initial_application_date")
    weeks = dua.get("weeks_claimed")
    if weeks is None:
        v.err("dua.weeks_claimed", "required; must be an array of ISO dates (may be empty)")
    elif not isinstance(weeks, list) or len(weeks) > MAX_WEEKS_CLAIMED:
        v.err("dua.weeks_claimed", f"must be a list of at most {MAX_WEEKS_CLAIMED} dates")
    else:
        for i, w in enumerate(weeks):
            v.date(w, f"dua.weeks_claimed[{i}]", required=True)


# ---------------------------------------------------------------------------------------------------------------
# Deterministic processing
# ---------------------------------------------------------------------------------------------------------------
def engine_citations(flags):
    """Citations the engine itself attached to its flags (explicit 'citation' key or 'NN CFR x.y' in the message)."""
    seen, out = set(), []
    for flag in flags:
        candidates = [flag["citation"]] if flag.get("citation") else []
        candidates += [f"{t} CFR {s}" for t, s in _CITATION_RE.findall(flag.get("message", ""))]
        for c in candidates:
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


def process_case(case, index, allowed_tenants, validate_citation, decision_queues, include_templates=True,
                 as_of=None):
    """Validate and run one case. Never raises: a bad case becomes a per-case error."""
    case_id = case.get("case_id") if isinstance(case, dict) and isinstance(case.get("case_id"), str) else None
    tenant_id = case.get("tenant_id") if isinstance(case, dict) and isinstance(case.get("tenant_id"), str) else None
    base = {"index": index, "case_id": case_id[:128] if case_id else None,
            "tenant_id": tenant_id[:64] if tenant_id else None}
    errors = validate_case(case, allowed_tenants)
    if errors:
        return {
            **base,
            "ok": False,
            "deterministic_status": "INPUT_INVALID",
            "hard_lock": True,
            "rule_pack_version": RULE_PACK_VERSION,
            "errors": errors[:20],
        }
    as_of = as_of or datetime.datetime.now(datetime.timezone.utc).date()
    try:
        result = DeterministicAuditor(case, tenant_id=tenant_id, as_of=as_of).audit()
    except Exception as exc:  # defensive: validation should make this unreachable
        return {
            **base,
            "ok": False,
            "deterministic_status": "ERROR",
            "hard_lock": True,
            "rule_pack_version": RULE_PACK_VERSION,
            "errors": [f"ENGINE_ERROR: {type(exc).__name__}"],
        }

    flags = result.get("flags") or []
    if result.get("status") == "INPUT_INVALID":
        issue_msgs = [f.get("message", "INPUT_INVALID") for f in flags] or ["INPUT_INVALID"]
        return {
            **base,
            "ok": False,
            "deterministic_status": "INPUT_INVALID",
            "hard_lock": True,
            "rule_pack_version": result.get("rule_pack_version", RULE_PACK_VERSION),
            "flags": flags,
            "errors": issue_msgs[:20],
        }

    flag_types = [f["type"] for f in flags]
    route = engine_route(result["status"], flags)
    plan = build_action_plan(route, flags, tenant_id, case, as_of=as_of)
    action = case.get("caseworker_action") or {}
    conflict_reports = {(f.get("details") or {}).get("report_id") for f in flags
                        if f["type"] == "VERIFIED_FACT_CONFLICT"}
    applicant = case.get("applicant") or {}
    reports = []
    for i, rep in enumerate(case.get("third_party_reports") or []):
        report_id = rep.get("report_id") or f"report-{i + 1}"
        idc = identity_check(applicant, rep.get("subject"))
        reports.append({"report_id": report_id, "fact": rep.get("fact"), "finding": rep.get("finding"),
                        "source": rep.get("source"), "date": rep.get("date"), "identity_check": idc["result"],
                        "reliability": ("IDENTITY_MATCHED" if idc["result"] == "MATCH" else
                                        "UNRELIABLE_UNTIL_HUMAN_CONFIRMS_IDENTITY"),
                        "conflicts_with_verified_fact": report_id in conflict_reports,
                        "recommended_action_applied_by_engine": False})
    out = {
        **base,
        "ok": True,
        "deterministic_status": result["status"],
        "hard_lock": result["hard_lock"],
        "rule_pack_version": result.get("rule_pack_version", RULE_PACK_VERSION),
        "flags": flags,
        "flag_types": flag_types,
        "engine_route": route,
        "work_queue": decision_queues[route],
        # Requirement 0: the engine prepares and routes; a person makes every decision.
        "human_decision_required": True,
        "final_decision_by_engine": None,
        "escalated_for_human_attention": route in ESCALATED_ROUTES,
        "priority": plan["priority"],
        "priority_rank": plan["priority_rank"],
        "stall_idle_threshold_days": result["stall_idle_threshold_days"],
        "citations": [{"citation": c, **{k: validate_citation(c, tenant_id)[k] for k in ("status", "matched_section")}}
                      for c in engine_citations(flags)],
        "proposed_action": proposed_action_gate(result["status"], flags, action.get("proposed_decision")),
        "document_requests": [{k: d[k] for k in ("document", "decision", "on_file_as")}
                              for d in document_request_decisions(case)],
        "verified_facts": [{"fact": f.get("fact"), "value": f.get("value"), "date": f.get("date"),
                            "verified_by": f.get("verified_by"), "method": f.get("method"),
                            "status": "VERIFIED (unchanged by the engine)"}
                           for f in case.get("verified_facts") or []],
        "third_party_reports": reports,
        "fraud_status_set_by_engine": any("FRAUD" in t for t in flag_types),
        "fraud_referral": fraud_referral_gate(action.get("fraud_referral"), flags),
        "engine_payment_action": ENGINE_PAYMENT_ACTION,
        "next_step": plan["next_step"],
        "unblock_plan": plan["unblock_plan"],
        "next_due_date": plan["next_due_date"],
        "template_version": plan["template_version"],
        "template_ids": plan["template_ids"],
        "as_of": plan["as_of"],
    }
    if include_templates:
        out["notice"] = plan["notice"]
        out["checklist"] = plan["checklist"]
    return out


def _get_or_create_request_id():
    rid = getattr(g, "asce_request_id", None)
    if rid:
        return rid
    incoming = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
    rid = incoming if _REQ_ID_RE.match(incoming) else uuid.uuid4().hex[:16]
    g.asce_request_id = rid
    return rid


def create_surge_blueprint(tenants, validate_citation, decision_queues, disclaimer):
    """Build the /api/v1 blueprint. Dependencies come from app.py (no circular import)."""
    bp = Blueprint("surge_api", __name__, url_prefix="/api/v1")
    allowed_tenants = frozenset(tenants)
    queues = {**TEMPLATE_QUEUES, **decision_queues}

    def error(status, title, detail=None, **extra):
        body = {
            "error": title,
            "status": status,
            "mode": API_MODE,
            "request_id": _get_or_create_request_id(),
        }
        if detail:
            body["detail"] = detail
        body.update(extra)
        return jsonify(body), status

    @bp.before_request
    def require_api_key():
        _get_or_create_request_id()
        expected = os.environ.get("ASCE_API_KEY")
        if not expected:
            return error(503, "API disabled", "ASCE_API_KEY is not configured on this server.")
        supplied = request.headers.get(API_KEY_HEADER, "")
        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return error(401, "Unauthorized", f"Missing or invalid {API_KEY_HEADER} header.")
        return None

    @bp.after_request
    def attach_request_headers(response):
        rid = _get_or_create_request_id()
        response.headers[REQUEST_ID_HEADER] = rid
        logger.info("%s %s status=%s request_id=%s", request.method, request.path, response.status_code, rid)
        return response

    @bp.errorhandler(RequestEntityTooLarge)
    def too_large(_exc):
        return error(413, "Payload too large", max_bytes=current_app.config.get("MAX_CONTENT_LENGTH"))

    @bp.errorhandler(HTTPException)
    def http_error(exc):
        return error(exc.code or 500, exc.name)

    def read_json():
        max_bytes = current_app.config.get("MAX_CONTENT_LENGTH")
        if max_bytes and request.content_length and request.content_length > max_bytes:
            return None, error(413, "Payload too large", max_bytes=max_bytes, received_bytes=request.content_length)
        body = request.get_json(force=True, silent=True)
        if body is None:
            return None, error(400, "Bad request", "Body must be valid JSON.")
        return body, None

    def run(case, index, include_templates=True, as_of=None):
        return process_case(case, index, allowed_tenants, validate_citation, queues, include_templates, as_of)

    @bp.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "mode": API_MODE, "ai_used": False,
                        "request_id": _get_or_create_request_id(),
                        "rule_pack_version": RULE_PACK_VERSION,
                        "batch_max_cases": _env_int("BATCH_MAX_CASES", DEFAULT_BATCH_MAX_CASES),
                        "max_content_length": current_app.config.get("MAX_CONTENT_LENGTH"),
                        "tenants": sorted(allowed_tenants)})

    @bp.route("/batch", methods=["POST"])
    def batch():
        started = time.perf_counter()
        body, err = read_json()
        if err:
            return err
        cases = body.get("cases") if isinstance(body, dict) else None
        if not isinstance(cases, list) or not cases:
            return error(400, "Bad request", "Body must be an object with a non-empty 'cases' list.")
        max_cases = _env_int("BATCH_MAX_CASES", DEFAULT_BATCH_MAX_CASES)
        if len(cases) > max_cases:
            return error(413, "Too many cases", f"This server accepts at most {max_cases} cases per batch.",
                         max_cases=max_cases, received=len(cases))
        include_templates = body.get("include_templates", True) is not False
        as_of = datetime.datetime.now(datetime.timezone.utc).date()   # one date for the whole batch
        results = [run(c, i, include_templates, as_of) for i, c in enumerate(cases)]
        ok = [r for r in results if r["ok"]]
        return jsonify({
            "api_version": "v1", "mode": API_MODE, "ai_used": False,
            "request_id": _get_or_create_request_id(),
            "rule_pack_version": RULE_PACK_VERSION,
            "as_of": as_of.isoformat(),
            "received": len(cases),
            "processed_ok": len(ok),
            "errors": len(results) - len(ok),
            "status_counts": dict(Counter(r["deterministic_status"] for r in results)),
            "queue_counts": dict(Counter(r["work_queue"] for r in ok)),
            "flag_type_counts": dict(Counter(t for r in ok for t in r["flag_types"])),
            "caseworker_load": {
                "cases_prepared_by_engine": len(ok),
                "human_decision_required": sum(1 for r in ok if r["human_decision_required"]),
                "prepared_for_standard_review": sum(1 for r in ok if not r["escalated_for_human_attention"]),
                "escalated_for_senior_supervisor_or_reconciliation":
                    sum(1 for r in ok if r["escalated_for_human_attention"]),
                "drafted_notices": len(ok) if include_templates else 0,
                "document_requests_checked": sum(len(r["document_requests"]) for r in ok),
                "note": LOAD_NOTE,
            },
            "priority_counts": dict(Counter(r["priority"] for r in ok)),
            "approvals_blocked": sum(1 for r in ok if (r["proposed_action"] or {}).get("decision") == "BLOCKED_HARD_LOCK"),
            "redundant_requests_blocked": sum(1 for r in ok for d in r["document_requests"]
                                              if d["decision"] == "BLOCKED_ALREADY_ON_FILE"),
            "stalled_escalated": sum(1 for r in ok if "STALLED_CASE_ESCALATION" in r["flag_types"]),
            "housing_at_risk_escalated": sum(1 for r in ok if "HOUSING_AT_RISK" in r["flag_types"]
                                             and r["engine_route"] == "SUPERVISOR_ESCALATION"),
            "verified_fact_conflicts_routed_to_human": sum(1 for r in ok
                                                           if "VERIFIED_FACT_CONFLICT" in r["flag_types"]),
            "automatic_fraud_flags": sum(1 for r in ok if r["fraud_status_set_by_engine"]),
            "processing_ms": round((time.perf_counter() - started) * 1000, 2),
            "disclaimer": disclaimer,
            "results": results,
        })

    @bp.route("/case", methods=["POST"])
    def single_case():
        started = time.perf_counter()
        body, err = read_json()
        if err:
            return err
        case = body.get("case", body) if isinstance(body, dict) else body
        result = run(case, 0)
        result.update({"api_version": "v1", "mode": API_MODE, "ai_used": False,
                       "request_id": _get_or_create_request_id(),
                       "disclaimer": disclaimer,
                       "processing_ms": round((time.perf_counter() - started) * 1000, 2)})
        return jsonify(result), (200 if result["ok"] else 422)

    return bp
