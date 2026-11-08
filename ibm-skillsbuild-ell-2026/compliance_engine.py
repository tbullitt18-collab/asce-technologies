import datetime
import os
import re

# Import agency adapter system for plug-and-play agency support
try:
    from agencies import get_adapter, list_agencies
    AGENCY_ADAPTERS_AVAILABLE = True
except ImportError:
    AGENCY_ADAPTERS_AVAILABLE = False

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


def _parse_date(value):
    return datetime.datetime.strptime(value, "%Y-%m-%d").date()


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
ADVISORY_ENGINE_FLAG_TYPES = frozenset({"PRIORITY_EXPEDITE", "DUA_EMPLOYMENT_PROOF_PENDING",
                                        "VULNERABLE_HOUSEHOLD_MEMBER"})
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

    def _try_agency_adapter(self):
        """
        Try to route audit to agency adapter system (plug-and-play).
        
        Maps tenant_id to agency_id and delegates to adapter if available.
        Returns None if no adapter found (falls back to legacy system).
        """
        # Map tenant_id to agency_id
        tenant_to_agency = {
            "FEMA_TITLE_44": "FEMA",
            "VA_TITLE_38": "VA",
            "DUA_20_CFR_625": "DUA",
        }
        
        # Extract agency_id from tenant_id
        agency_id = tenant_to_agency.get(self.tenant_id)
        if not agency_id:
            # Check if tenant_id starts with known agency prefix
            for prefix, aid in [("FEMA", "FEMA"), ("VA", "VA"), ("DUA", "DUA")]:
                if self.tenant_id.startswith(prefix):
                    agency_id = aid
                    break
        
        if not agency_id:
            return None  # No adapter available, use legacy system
        
        # Get adapter
        adapter = get_adapter(agency_id)
        if not adapter:
            return None  # Adapter not found, use legacy system
        
        # Run agency-specific audit
        adapter_result = adapter.audit(self.case, self.today)
        
        # Merge adapter flags into our flags
        if adapter_result.get("flags"):
            self.flags.extend(adapter_result["flags"])
        
        # Update overall status
        if adapter_result.get("status") == "ESCALATE":
            self.overall_status = "ESCALATE"
        
        # Continue with common rules (redundant requests, vulnerable household, etc.)
        self._audit_redundant_requests()
        vulnerable = self._audit_vulnerable_household()
        housing = self._audit_housing_at_risk(vulnerable)
        if vulnerable:
            self.effective_stall_idle_days = min(self.effective_stall_idle_days, self.vulnerable_stall_idle_days)
        if housing:
            self.effective_stall_idle_days = min(self.effective_stall_idle_days, self.housing_stall_idle_days)
        self._audit_third_party_reports()
        self._audit_stall()
        
        return {
            "status": self.overall_status,
            "flags": self.flags,
            "hard_lock": self.overall_status == "ESCALATE" and approval_locked(self.flags),
            "stall_idle_threshold_days": self.effective_stall_idle_days,
            "agency": agency_id,
            "adapter_used": True
        }

    def audit(self):
        # Try agency adapter system first (plug-and-play)
        if AGENCY_ADAPTERS_AVAILABLE:
            adapter_result = self._try_agency_adapter()
            if adapter_result is not None:
                # Agency adapter handled the audit
                return adapter_result
        
        # Fall back to legacy tenant system for backward compatibility
        if self.tenant_id.startswith("FEMA"):
            self._audit_fema_44_cfr()
        elif self.tenant_id.startswith("VA"):
            self._audit_va_38_cfr()
        elif (self.tenant_id.startswith("SSA") or 
              self.tenant_id.startswith("MEDICAID") or 
              self.tenant_id.startswith("SNAP") or 
              self.tenant_id.startswith("HUD")):
            self._audit_generic_program()
        elif self.tenant_id == DUA_TENANT or self.tenant_id.startswith("DUA"):
            self._audit_dua_20_cfr_625()
            self._audit_generic_program()
        else:
            self.flags.append({"type": "TENANT_UNKNOWN", "message": f"Tenant {self.tenant_id} not recognized by gateway."})
            self.overall_status = "ESCALATE"
            return {"status": self.overall_status, "flags": self.flags, "hard_lock": True,
                    "stall_idle_threshold_days": None}

        # Applicant-protection rules apply to every recognized program.
        self._audit_redundant_requests()
        vulnerable = self._audit_vulnerable_household()
        housing = self._audit_housing_at_risk(vulnerable)
        if vulnerable:
            self.effective_stall_idle_days = min(self.effective_stall_idle_days, self.vulnerable_stall_idle_days)
        if housing:
            self.effective_stall_idle_days = min(self.effective_stall_idle_days, self.housing_stall_idle_days)
        self._audit_third_party_reports()

        # No applicant may sit in a silent hold: every recognized program gets the stall check.
        self._audit_stall()

        return {
            "status": self.overall_status,
            "flags": self.flags,
            "hard_lock": self.overall_status == "ESCALATE" and approval_locked(self.flags),
            "stall_idle_threshold_days": self.effective_stall_idle_days,
        }

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

    def _audit_fema_44_cfr(self):
        # Feature: 18-month statutory cliff for IHP (44 CFR 206.110(e))
        declaration_date_str = self.case.get("incident", {}).get("declaration_date")
        if declaration_date_str:
            declaration_date = datetime.datetime.strptime(declaration_date_str, "%Y-%m-%d").date()
            if (self.today - declaration_date).days >= 548:
                self.flags.append({"type": "SLA_CRITICAL", "message": f"SYSTEM LOCK TRIGGERED: Case exceeds statutory 18-month assistance period from declaration {declaration_date_str}. Action physically blocked per 44 CFR § 206.110(e). Mandatory supervisory override required."})
                self.overall_status = "ESCALATE"

        # Feature 1: 90-Day Appeal SLA
        appeal_date_str = self.case.get("incident", {}).get("appeal_date")
        if appeal_date_str:
            appeal_date = datetime.datetime.strptime(appeal_date_str, "%Y-%m-%d").date()
            today = self.today
            delta = (today - appeal_date).days

            if delta >= 90:
                self.flags.append({"type": "SLA_CRITICAL", "message": f"SYSTEM LOCK TRIGGERED: Case exceeds statutory 90-day appeal deadline ({delta} days). Action physically blocked per 44 CFR § 206.115(e). Mandatory supervisory override required."})
                self.overall_status = "ESCALATE"

        # Feature 2: Post-SBA Policy (Income vs SBA Requirement)
        missing = self.case.get("evidence", {}).get("missing", [])
        sba_missing = any("sba" in m.lower() for m in missing)
        if sba_missing:
            self.flags.append({"type": "POLICY_BLOCK", "message": "SYSTEM LOCK TRIGGERED: Mandatory SBA loan determination missing. Housing assistance locked per IA Equity Rule."})
            self.overall_status = "ESCALATE"

        # Feature 3: Senior / Vulnerable Population Priority
        age = self.case.get("applicant", {}).get("age", 0)
        if age >= 65:
            self.flags.append({"type": "PRIORITY_EXPEDITE", "message": f"EXPEDITE_PRIORITY_SENIOR (Age {age}): Mandated 48-hour expedited queue per 44 CFR § 206.110."})

        # Feature 4: Document Redundancy (Clawback Prevention) - now the shared every-program rule
        # (_audit_redundant_requests), which canonicalises document names so naming variants match.

        # Feature 5: Inspector vs. Caseworker Data Conflict
        inspector_status = self.case.get("residence", {}).get("inspector_notes", "").lower()
        claimed_status = self.case.get("residence", {}).get("structure_status", "").lower()
        if "minor" in inspector_status and ("condemned" in claimed_status or "destroyed" in claimed_status):
            self.flags.append({"type": "INSPECTOR_CONFLICT", "message": "SYSTEM LOCK TRIGGERED: DATA CONFLICT. Field inspector recorded 'Minor Damage' while claim status is 'Condemned'. Pre-commit lock engaged to prevent clawback."})
            self.overall_status = "ESCALATE"

    def _audit_va_38_cfr(self):
        claim_date_str = self.case.get("claim", {}).get("submission_date")
        if claim_date_str:
            claim_date = datetime.datetime.strptime(claim_date_str, "%Y-%m-%d").date()
            today = self.today
            if (today - claim_date).days >= 365:
                self.flags.append({"type": "SLA_CRITICAL", "message": "SLA CRITICAL: Claim exceeds 1-year window. Held for human review (see 38 CFR § 3.109; reviewer must verify)."})
                self.overall_status = "ESCALATE"

        missing = self.case.get("evidence", {}).get("missing", [])
        if any("dd214" in m.lower() for m in missing):
            self.flags.append({"type": "POLICY_BLOCK", "message": "POLICY BLOCK: DD214 missing. Cannot establish service connection."})
            self.overall_status = "ESCALATE"

    def _audit_generic_program(self):
        """Config-driven checks shared by programs whose rules are expressed as case fields.
        Citations are labeled for human verification; the engine only checks deterministic facts."""
        today = self.today

        # Rule 1: Statutory / regulatory processing or filing deadline
        dl = self.case.get("deadline")
        if dl:
            start = datetime.datetime.strptime(dl["start_date"], "%Y-%m-%d").date()
            elapsed = (today - start).days
            if elapsed > dl["limit_days"]:
                self.flags.append({"type": "SLA_CRITICAL", "message": f"SYSTEM LOCK TRIGGERED: {dl['label']} - {elapsed} days elapsed vs. statutory {dl['limit_days']}-day limit ({dl['citation']}). Pre-commit lock engaged."})
                self.overall_status = "ESCALATE"

        # Rule 2: Expedited-service clock
        ex = self.case.get("expedited")
        if ex and ex.get("eligible"):
            start = datetime.datetime.strptime(ex["start_date"], "%Y-%m-%d").date()
            elapsed = (today - start).days
            if elapsed > ex["limit_days"]:
                self.flags.append({"type": "SLA_CRITICAL", "message": f"SYSTEM LOCK TRIGGERED: EXPEDITED CLOCK BREACH. {elapsed} days elapsed vs. {ex['limit_days']}-day priority limit ({ex['citation']}). Escalate to supervisor."})
                self.overall_status = "ESCALATE"

        # Rule 3: Required documentation missing
        for m in self.case.get("evidence", {}).get("missing", []):
            self.flags.append({"type": "POLICY_BLOCK", "message": f"SYSTEM LOCK TRIGGERED: POLICY BLOCK. Mandatory statutory evidence missing - {m}. Case locked against premature determination."})
            self.overall_status = "ESCALATE"

        # Rule 4: Senior / vulnerable population priority
        age = self.case.get("applicant", {}).get("age", 0)
        if age >= 65:
            self.flags.append({"type": "PRIORITY_EXPEDITE", "message": f"EXPEDITE_PRIORITY_SENIOR (Age {age}): Flagged for priority routing queue."})

        # Rule 5: Document redundancy - now the shared every-program rule (_audit_redundant_requests).

        # Rule 6: Cross-source data conflict
        c = self.case.get("data_conflict")
        if c:
            self.flags.append({"type": "DATA_CONFLICT", "message": f"SYSTEM LOCK TRIGGERED: DATA CONFLICT. {c['field']} is {c['value_a']} per {c['source_a']} but {c['value_b']} per {c['source_b']}. Mandatory supervisory review required."})
            self.overall_status = "ESCALATE"

    def _audit_dua_20_cfr_625(self):
        """DUA timing rules (20 CFR 625.8(a), 625.6(e)(1), 625.2(f), 625.4(a), 625.7). Every flag carries the
        exact citation and the dates used, so templates and reviewers see the same numbers the engine used."""
        incident = self.case.get("incident", {})
        dua = self.case.get("dua", {})
        began = _parse_date(incident["disaster_began_date"])
        declared = _parse_date(incident["declaration_date"])
        announced = _parse_date(incident["announcement_date"])
        holidays = [_parse_date(h) for h in incident.get("area_legal_holidays") or []]
        explicit_end = incident.get("disaster_assistance_period_end")
        dap_end = dua_assistance_period_end(declared, _parse_date(explicit_end) if explicit_end else None)
        deadline = dua_filing_deadline(announced, holidays)
        filed = _parse_date(dua["initial_application_date"])

        # Rule D1: timely initial application (20 CFR 625.8(a))
        details = {"filed": filed.isoformat(), "deadline": deadline.isoformat(),
                   "announcement_date": announced.isoformat(), "dap_end": dap_end.isoformat()}
        if filed > dap_end:
            self.flags.append({"type": "DUA_APPLICATION_AFTER_DAP", "citation": "20 CFR 625.8(a)", "details": details,
                               "message": f"SYSTEM LOCK TRIGGERED: Initial DUA application filed {filed.isoformat()}, after the Disaster Assistance Period ended {dap_end.isoformat()}. 20 CFR 625.8(a): in no event shall such an application be accepted."})
            self.overall_status = "ESCALATE"
        elif filed > deadline:
            late_days = (filed - deadline).days
            details["days_late"] = late_days
            if dua.get("good_cause_late_filing"):
                self.flags.append({"type": "DUA_LATE_GOOD_CAUSE_REVIEW", "citation": "20 CFR 625.8(a)", "details": details,
                                   "message": f"SYSTEM LOCK TRIGGERED: Initial DUA application filed {late_days} day(s) after the {deadline.isoformat()} deadline (30 days after the {announced.isoformat()} announcement, business-day adjusted). Good cause is claimed; a human must decide good cause under 20 CFR 625.8(a)."})
            else:
                self.flags.append({"type": "DUA_APPLICATION_LATE", "citation": "20 CFR 625.8(a)", "details": details,
                                   "message": f"SYSTEM LOCK TRIGGERED: Initial DUA application filed {late_days} day(s) after the {deadline.isoformat()} deadline (30 days after the {announced.isoformat()} announcement, business-day adjusted) and no good cause is recorded (20 CFR 625.8(a))."})
            self.overall_status = "ESCALATE"

        # Rule D2: employment / self-employment documentation within 21 days (20 CFR 625.6(e)(1))
        if dua.get("weekly_amount_basis") == "statement_only":
            due = filed + datetime.timedelta(days=DUA_PROOF_WINDOW_DAYS)
            submitted_raw = dua.get("employment_proof_submitted_date")
            proof = {"filed": filed.isoformat(), "proof_due": due.isoformat()}
            if submitted_raw:
                submitted = _parse_date(submitted_raw)
                proof["submitted"] = submitted.isoformat()
                if submitted > due:
                    proof["days_late"] = (submitted - due).days
                    self.flags.append({"type": "DUA_EMPLOYMENT_PROOF_LATE", "citation": "20 CFR 625.6(e)(1)", "details": proof,
                                       "message": f"SYSTEM LOCK TRIGGERED: Employment documentation received {submitted.isoformat()}, {proof['days_late']} day(s) after the {due.isoformat()} due date (21 calendar days after filing, 20 CFR 625.6(e)(1)). Eligibility consequences under 625.6(e)(2) require human review."})
                    self.overall_status = "ESCALATE"
            elif self.today > due:
                proof["days_overdue"] = (self.today - due).days
                self.flags.append({"type": "DUA_EMPLOYMENT_PROOF_OVERDUE", "citation": "20 CFR 625.6(e)(1)", "details": proof,
                                   "message": f"SYSTEM LOCK TRIGGERED: Employment documentation not received; it was due {due.isoformat()} (21 calendar days after filing, 20 CFR 625.6(e)(1)). Eligibility consequences under 625.6(e)(2) require human review."})
                self.overall_status = "ESCALATE"
            else:
                proof["days_remaining"] = (due - self.today).days
                self.flags.append({"type": "DUA_EMPLOYMENT_PROOF_PENDING", "citation": "20 CFR 625.6(e)(1)", "details": proof,
                                   "message": f"ADVISORY: Employment documentation not yet received; due {due.isoformat()} ({proof['days_remaining']} day(s) remaining, 20 CFR 625.6(e)(1))."})

        # Rule D3: claimed weeks must begin during the Disaster Assistance Period (20 CFR 625.4(a), 625.7, 625.2(f))
        outside = sorted(w for w in (_parse_date(x) for x in dua.get("weeks_claimed") or [])
                         if not (began < w <= dap_end))
        if outside:
            self.flags.append({"type": "DUA_WEEK_OUTSIDE_DAP", "citation": "20 CFR 625.4(a)",
                               "details": {"weeks_outside": [w.isoformat() for w in outside],
                                           "disaster_began": began.isoformat(), "dap_end": dap_end.isoformat()},
                               "message": f"SYSTEM LOCK TRIGGERED: {len(outside)} claimed week(s) do not begin during the Disaster Assistance Period (after {began.isoformat()} through {dap_end.isoformat()}): {', '.join(w.isoformat() for w in outside)}. 20 CFR 625.4(a), 625.7, 625.2(f)."})
            self.overall_status = "ESCALATE"
