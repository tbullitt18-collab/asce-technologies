"""
ASCE GOVBEN CaaS v10.0 — Legacy Workstation & IBM Bob Caseworker Co-Pilot Sidecar
=================================================================================
Track #4: Government & Public Services | ASCE Technologies LLC (CEID: 10x6kg)

Demonstrates how the IBM Bob + watsonx Orchestrate Caseworker Co-Pilot attaches
non-invasively to 40-year-old legacy government systems (Win2000 thick clients,
state MMIS/COMPASS portals, and IBM z/OS CICS 3270 terminals) via:
  1. EHLLAPI (PCSHLL32.DLL) / Win32 UIA Presentation-Space Screen Sync (Read)
  2. TN3270E / IBM MQ Pre-Commit Statutory Interceptor — "Invisible Roof" (Hold)
  3. Deterministic Field Write-Back & 80-Column EBCDIC COBOL Copybook Stage (Write)

All legal & statutory decisions are executed deterministically by `DeterministicAuditor`
(`compliance_engine.py`); IBM Bob + watsonx Orchestrate orchestrate the 4-step workflow
and translate verified deterministic outputs into plain-English caseworker action.
"""

from flask import Flask, render_template, request, jsonify
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Any

from compliance_engine import DeterministicAuditor
from mainframe_adapter import COBOLCopybookGenerator

app = Flask(__name__)
copybook_gen = COBOLCopybookGenerator()


LEGACY_AGENCY_CASES: Dict[str, Dict[str, Any]] = {
    "FEMA_NEMIS": {
        "agency_key": "FEMA_NEMIS",
        "tenant_id": "FEMA_TITLE_44",
        "legacy_app_title": "National Emergency Management Information System (NEMIS-IA) v8.4.1 - [PROD]",
        "cics_header": "*** FEDERAL EMERGENCY MANAGEMENT AGENCY - CICS ADJUDICATION ***",
        "cics_trans": "NEMIS01",
        "mq_queue": "QM1/ASCE.FEMA.QUEUE",
        "ehllapi_coords": "PS_ROW:04,COL:18 (LEN:09) -> REG_ID",
        "case_id": "630596501",
        "program_code": "IHP-HA / 44 CFR § 206.115(e)",
        "statute_cfr": "44 CFR § 206.115(e)",
        "applicant_name": "BULLITT, TODD — BOYD COUNTY, KY",
        "incident_id": "DR-4782-KY",
        "declaration_date": "2026-05-22",
        "sla_banner": "STATUTORY APPEAL CLOCK: Day 41 of 90 (49 Days Remaining — 44 CFR § 206.115(e))",
        "legacy_bad_disp": "INEL - 01G (Insufficient Proof of Occupancy)",
        "remediated_disp": "ELIG - IHP Housing Assistance ($8,420.00)",
        "benefit_amount": "$8,420.00",
        "legacy_ocr_note": (
            "Applicant uploaded 24-page PDF bundle. Automated legacy OCR flagged Page 18 residential lease "
            "signature block at 14% ink density (BLANK). Queued Notice of Ineligibility (Letter 01G) and "
            "duplicate Proof of Occupancy request."
        ),
        "evault_doc_title": "STANDARD RESIDENTIAL LEASE AGREEMENT — PAGE 18 / 24 (200-DPI TIFF)",
        "evault_finding": "OpenCV Active Contour: 3 Continuous Wet-Ink Cursive Strokes Verified (99.4% Pass)",
        "case_payload": {
            "case_id": "630596501",
            "last_action_date": "2026-09-25",
            "incident": {
                "declaration_date": "2026-05-22",
                "appeal_date": "2026-09-01"
            },
            "applicant": {"age": 67},
            "household_members": [{"member_id": "APPLICANT-01", "age": 67, "veteran": True}],
            "evidence": {
                "uploaded": ["Residential_Lease_Agreement_Signed_Page18.pdf", "FEMA_Inspection_Report.pdf"],
                "missing": []
            },
            "caseworker_action": {
                "requesting_documents": ["Residential Lease Agreement"]
            },
            "residence": {
                "inspector_notes": "Major flood waterline 42 inches; primary residence verified",
                "structure_status": "Habitable with major repairs"
            }
        }
    },
    "GA_MEDICAID_MMIS": {
        "agency_key": "GA_MEDICAID_MMIS",
        "tenant_id": "MEDICAID_FORUM_PHE_UNWINDING",
        "legacy_app_title": "Georgia DCH Medicaid Management Info System (GAMMIS) v4.2 - [ATL-PROD]",
        "cics_header": "*** GEORGIA DEPT OF COMMUNITY HEALTH - GAMMIS CICS GATEWAY ***",
        "cics_trans": "GAMM42",
        "mq_queue": "QM.GA.DCH/MMIS.AUDIT.Q",
        "ehllapi_coords": "PS_ROW:03,COL:22 (LEN:12) -> MEDICAID_ID",
        "case_id": "GA-MMIS-884120",
        "program_code": "ABD / Family Medicaid — 42 CFR § 435.916",
        "statute_cfr": "42 CFR § 435.916(a)",
        "applicant_name": "CARTER, EVELYN — FULTON COUNTY, GA",
        "incident_id": "RENEWAL-FY27-Q1",
        "declaration_date": "2026-08-01",
        "sla_banner": "EX-PARTE MANDATE: 42 CFR § 435.916 — Mandatory Wage Match Before Procedural Termination",
        "legacy_bad_disp": "TERM - PROC-99 (Procedural Disenrollment: Packet Not Returned)",
        "remediated_disp": "RENEW - EX-PARTE AUTO-RENEWAL CONFIRMED (12 Months)",
        "benefit_amount": "CONTINUOUS COVERAGE",
        "legacy_ocr_note": (
            "Nightly batch job GAMM-TERM-04 flagged case for procedural termination: paper renewal packet "
            "unreturned at Day 48. Legacy system did not reconcile Georgia DOL quarterly wage tape."
        ),
        "evault_doc_title": "GEORGIA DOL WAGE INTERFACE & EX-PARTE VERIFICATION PACKET",
        "evault_finding": "GA DOL Wage Match Confirmed ($1,180/mo < 138% FPL) — Paper Packet Legally Unnecessary",
        "case_payload": {
            "case_id": "GA-MMIS-884120",
            "last_action_date": "2026-09-20",
            "deadline": {
                "label": "Medicaid Ex-Parte Redetermination Window",
                "start_date": "2026-08-20",
                "limit_days": 45,
                "citation": "42 CFR § 435.916"
            },
            "applicant": {"age": 68},
            "procedural_status": {
                "ex_parte_attempted": False,
                "returned_mail_flag": True,
                "state_wage_database_match": True
            },
            "evidence": {
                "uploaded": ["GA_DOL_Quarterly_Wage_Match_Q3.xml", "SSA_RSDI_Verification.xml"],
                "missing": ["Paper Renewal Packet"]
            },
            "caseworker_action": {
                "requesting_documents": ["Quarterly Wage Stubs"]
            }
        }
    },
    "GA_DFCS_SNAP": {
        "agency_key": "GA_DFCS_SNAP",
        "tenant_id": "SNAP_FORUM_SAR7_INCOME",
        "legacy_app_title": "Georgia DFCS Gateway / COMPASS Eligibility Client v6.9 - [DEKALB-02]",
        "cics_header": "*** GEORGIA DHS / DFCS - SNAP BENEFIT DETERMINATION CICS ***",
        "cics_trans": "SNAP07",
        "mq_queue": "QM.GA.DFCS/SNAP.HOLD.Q",
        "ehllapi_coords": "PS_ROW:05,COL:14 (LEN:11) -> AU_CASE_NBR",
        "case_id": "GA-SNAP-441092",
        "program_code": "SNAP Expedited & SAR-7 — 7 CFR § 273.12",
        "statute_cfr": "7 CFR § 273.12(a)(5)",
        "applicant_name": "JOHNSON, MARCUS — DEKALB COUNTY, GA",
        "incident_id": "PERIODIC-RPT-06M",
        "declaration_date": "2026-09-10",
        "sla_banner": "EXPEDITED & CHURN SHIELD: 7 CFR § 273.2(i) (7-Day SLA) & § 273.12 SAR-7 Receipt Verified",
        "legacy_bad_disp": "CLOSE - AUTO-CHURN (Day-30 Unindexed SAR-7 Closure)",
        "remediated_disp": "ACTIVE - EBT ISSUANCE AUTHORIZED ($684.00/mo)",
        "benefit_amount": "$684.00 / MO",
        "legacy_ocr_note": (
            "County mailroom scanned 6-month Periodic Report (SAR-7) into unindexed queue. Nightly COBOL "
            "batch scheduled automatic case closure despite timely timestamped receipt."
        ),
        "evault_doc_title": "DEKALB DFCS MAILROOM TIMESTAMPED SAR-7 PERIODIC REPORT",
        "evault_finding": "Barcode & Wet-Ink Signature Verified Uploaded Day 24 (6 Days Prior to Cutoff)",
        "case_payload": {
            "case_id": "GA-SNAP-441092",
            "last_action_date": "2026-09-28",
            "deadline": {
                "label": "SNAP SAR-7 Periodic Report Processing",
                "start_date": "2026-09-10",
                "limit_days": 30,
                "citation": "7 CFR § 273.12"
            },
            "income": {
                "baseline_monthly": 1850,
                "one_time_overtime_spike": 420,
                "procedural_flag": "Non-recurring overtime flagged as permanent"
            },
            "evidence": {
                "uploaded": ["Signed_SAR7_Periodic_Report_20260924.pdf", "Utility_Allowance_Bill.pdf"],
                "missing": []
            },
            "caseworker_action": {
                "requesting_documents": ["Signed SAR7 Periodic Report"]
            }
        }
    },
    "VA_VBMS": {
        "agency_key": "VA_VBMS",
        "tenant_id": "VA_TITLE_38",
        "legacy_app_title": "Veterans Benefits Management System (VBMS-Awards / BDN Bridge) v9.1",
        "cics_header": "*** DEPT OF VETERANS AFFAIRS - BDN / CICS COMPENSATION BRIDGE ***",
        "cics_trans": "VBMS38",
        "mq_queue": "QM.VA.VBA/AWARDS.AUDIT.Q",
        "ehllapi_coords": "PS_ROW:04,COL:20 (LEN:10) -> VET_FILE_NO",
        "case_id": "VA-38-9021445",
        "program_code": "Service-Connected Compensation — 38 CFR § 3.303 / § 3.159",
        "statute_cfr": "38 CFR § 3.159",
        "applicant_name": "RAMIREZ, CARLOS — SAVANNAH, GA",
        "incident_id": "PACT-ACT-SUPP-02",
        "declaration_date": "2025-09-15",
        "sla_banner": "DUTY TO ASSIST MANDATE: 38 U.S.C. § 5103A & 38 CFR § 3.159 — DD-214 Verified in eFolder",
        "legacy_bad_disp": "DENY - NO NEXUS / MISSING DD-214 (Premature Rating Closure)",
        "remediated_disp": "GRANT - 70% COMBINED SERVICE CONNECTION ($1,716.28/mo)",
        "benefit_amount": "$1,716.28 / MO",
        "legacy_ocr_note": (
            "Legacy supplemental merge script closed tracking line item and flagged DD-214 Member-4 copy as "
            "missing despite DPRIS archive match already present in Veteran eFolder."
        ),
        "evault_doc_title": "DPRIS / ARCHIVES DD-214 MEMBER-4 & C&P DBQ MEDICAL NEXUS",
        "evault_finding": "DD-214 Honorable Discharge & Southwest Asia Deployment Confirmed in eFolder",
        "case_payload": {
            "case_id": "VA-38-9021445",
            "last_action_date": "2026-09-18",
            "claim": {
                "submission_date": "2025-09-15",
                "incomplete_application_notice_date": "2025-10-01",
                "incomplete_application_requested_evidence": ["Medical Nexus Statement"],
                "evidence_time_limit_holidays": []
            },
            "household_members": [{"member_id": "VET-01", "age": 58, "veteran": True, "disability": True}],
            "evidence": {
                "uploaded": ["DD214_Member4_Verified.pdf", "CP_Exam_Medical_Nexus_DBQ.pdf"],
                "missing": ["Signed Medical Nexus Statement"]
            },
            "caseworker_action": {
                "requesting_documents": ["DD214 Member 4"]
            }
        }
    }
}


def _normalize_flag(flag: Dict[str, Any], default_cfr: str = None) -> Dict[str, Any]:
    del default_cfr  # Never overwrite internal-policy or schema flags with a statutory CFR.
    ftype = flag.get("type", "COMPLIANCE_ALERT")
    msg = flag.get("message", ftype)
    if any(k in ftype for k in ("SLA_CRITICAL", "LOCK", "CONFLICT", "BLOCK", "INPUT_INVALID")):
        severity = "CRITICAL"
    elif any(k in ftype for k in ("REDUNDANT", "STALLED", "HOUSING")):
        severity = "HIGH"
    else:
        severity = "ADVISORY"
    out: Dict[str, Any] = {
        "type": ftype,
        "severity": severity,
        "citation": flag.get("citation") if "citation" in flag else None,
        "message": msg,
    }
    for optional_key in ("policy_id", "category", "details"):
        if optional_key in flag and flag[optional_key] is not None:
            out[optional_key] = flag[optional_key]
    return out


def _safe_case_id(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict) and payload.get("case_id") is not None:
        return str(payload.get("case_id"))
    return fallback


def run_deterministic_legacy_audit(agency_key: str, override_payload: Any = None, decision_mode: str = "BLOK") -> Dict[str, Any]:
    t0 = time.perf_counter()
    if not isinstance(agency_key, str) or agency_key not in LEGACY_AGENCY_CASES:
        norm_flags = [{
            "type": "INPUT_INVALID",
            "severity": "CRITICAL",
            "citation": None,
            "details": {"issues": [{"field": "agency_key", "reason": f"unknown agency_key {agency_key!r}"}]},
            "message": f"INPUT INVALID: unknown agency_key {agency_key!r}; failing closed.",
        }]
        canonical = json.dumps({
            "case_id": "INVALID-AGENCY",
            "tenant_id": None,
            "agency_key": str(agency_key),
            "status": "INPUT_INVALID",
            "flags": ["INPUT_INVALID"],
            "decision_mode": "BLOK",
        }, sort_keys=True).encode("utf-8")
        sha256_hash = hashlib.sha256(canonical).hexdigest()
        cobol_input = {
            "case_id": "INVALID-AGENCY",
            "audit_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "audit_status": "INPUT-INV",
            "risk_score": 99,
            "defect_count": 1,
            "escalation_flag": True,
            "evidence_hash": sha256_hash[:32],
        }
        fixed_80_record = copybook_gen.json_to_fixed_width(cobol_input, "AUDIT_RESULT")
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "preset": None,
            "agency_key": agency_key,
            "audit_status": "INPUT_INVALID",
            "hard_lock": True,
            "flags": norm_flags,
            "sha256": sha256_hash,
            "fixed_80_record": fixed_80_record,
            "cobol_display": (
                f"01  AUDIT-RESULT-RECORD.\n"
                f"    05  ARR-CASE-ID           PIC X(16)   VALUE 'INVALID-AGENCY  '.\n"
                f"    05  ARR-DECISION-CODE     PIC X(04)   VALUE 'BLOK'.\n"
                f"    05  ARR-SEVERITY-LVL      PIC X(08)   VALUE 'CRITICAL'.\n"
                f"    05  ARR-STATUTE-CFR       PIC X(20)   VALUE 'NONE                '.\n"
                f"    05  ARR-INSPECTION-PASS   PIC X(01)   VALUE 'N'.\n"
                f"    05  ARR-SHA256-DIGEST     PIC X(32)   VALUE '{sha256_hash[:32]}'.\n"
                f"* RAW 80-COL ASCII BUFFER: [{fixed_80_record}]"
            ),
            "workflow_steps": [
                {
                    "step": 1,
                    "name": "Agency & Schema Validation",
                    "detail": f"Unknown agency_key={agency_key!r}; fail-closed block engaged.",
                    "latency": f"{elapsed_ms:.2f} ms (actual local validation)",
                    "status": "failed",
                }
            ],
            "elapsed_ms": elapsed_ms,
        }

    preset = LEGACY_AGENCY_CASES[agency_key]
    payload = preset["case_payload"] if override_payload is None else override_payload
    tenant_id = preset["tenant_id"]

    # Pass as_of date instead of including "today" in payload (schema compliance)
    from datetime import date
    auditor = DeterministicAuditor(payload, tenant_id=tenant_id, as_of=date(2026, 10, 12))
    raw_result = auditor.audit()
    raw_flags = raw_result.get("flags", [])
    norm_flags = [_normalize_flag(f, preset["statute_cfr"]) for f in raw_flags]

    case_id_str = _safe_case_id(payload, preset["case_id"] if isinstance(payload, dict) else "INVALID-PAYLOAD")
    audit_status = raw_result.get("status", "PASS")
    hard_lock = bool(raw_result.get("hard_lock"))
    effective_decision_mode = "BLOK" if (audit_status == "INPUT_INVALID" or hard_lock) else decision_mode

    # Compute deterministic cryptographic SHA-256 lineage receipt
    canonical = json.dumps({
        "case_id": case_id_str,
        "tenant_id": tenant_id,
        "status": audit_status,
        "flags": [f["type"] for f in norm_flags],
        "decision_mode": effective_decision_mode,
    }, sort_keys=True).encode("utf-8")
    sha256_hash = hashlib.sha256(canonical).hexdigest()

    # Format real 80-column fixed-width COBOL Copybook record via mainframe_adapter.py
    cobol_input = {
        "case_id": case_id_str,
        "audit_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "audit_status": (
            "INPUT-INV"
            if audit_status == "INPUT_INVALID"
            else ("APPROVED" if effective_decision_mode == "APRV" else ("HOLD-101" if norm_flags else "CLEARED"))
        ),
        "risk_score": 10 if effective_decision_mode == "APRV" else min(99, 35 + 20 * len(norm_flags)),
        "defect_count": 0 if effective_decision_mode == "APRV" else len(norm_flags),
        "escalation_flag": False if effective_decision_mode == "APRV" else bool(norm_flags),
        "evidence_hash": sha256_hash[:32],
    }
    fixed_80_record = copybook_gen.json_to_fixed_width(cobol_input, "AUDIT_RESULT")

    padded_case = case_id_str.ljust(16)[:16]
    padded_cfr = preset["statute_cfr"].ljust(20)[:20]
    sev_str = "CLEARED " if effective_decision_mode == "APRV" else "CRITICAL"
    pass_char = "Y" if audit_status != "INPUT_INVALID" else "N"
    ts_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cobol_display = (
        f"01  AUDIT-RESULT-RECORD.\n"
        f"    05  ARR-CASE-ID           PIC X(16)   VALUE '{padded_case}'.\n"
        f"    05  ARR-DECISION-CODE     PIC X(04)   VALUE '{effective_decision_mode}'.\n"
        f"    05  ARR-SEVERITY-LVL      PIC X(08)   VALUE '{sev_str}'.\n"
        f"    05  ARR-STATUTE-CFR       PIC X(20)   VALUE '{padded_cfr}'.\n"
        f"    05  ARR-INSPECTION-PASS   PIC X(01)   VALUE '{pass_char}'.\n"
        f"    05  ARR-SHA256-DIGEST     PIC X(32)   VALUE '{sha256_hash[:32]}'.\n"
        f"    05  ARR-MQ-TIMESTAMP      PIC X(20)   VALUE '{ts_utc}'.\n"
        f"* RAW 80-COL ASCII BUFFER: [{fixed_80_record}]"
    )

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

    # NOTE: Workflow steps 1, 3, and 4 below reflect DEMO SIMULATION UI/adapters.
    # Step 2 timing reflects local deterministic `compliance_engine.py` execution only.
    workflow_steps = [
        {
            "step": 1,
            "name": "EHLLAPI Screen Sync & E-Vault Ingest (Demo Sim)",
            "detail": f"{preset['ehllapi_coords']} • {preset['evault_finding']}",
            "latency": "4.2 ms (synthetic demo value - no live EHLLAPI)",
            "status": "simulated",
        },
        {
            "step": 2,
            "name": "ASCE Deterministic Statutory Guardrail",
            "detail": f"Evaluated {tenant_id} via compliance_engine.py ({audit_status})",
            "latency": f"{elapsed_ms:.2f} ms (actual local compliance_engine.py execution)",
            "status": "failed" if audit_status == "INPUT_INVALID" else "complete",
        },
        {
            "step": 3,
            "name": "Duplicate-Request & Ex-Parte Shield",
            "detail": f"{len(norm_flags)} deterministic rule flag(s) • CICS-HOLD-101 {'RELEASED (SIM)' if effective_decision_mode == 'APRV' else 'ARMED'}",
            "latency": "included in local engine run",
            "status": "complete" if audit_status != "INPUT_INVALID" else "blocked",
        },
        {
            "step": 4,
            "name": "80-Col COBOL Preview Stage (Demo Sim)",
            "detail": f"Formatted local 80-col preview for {preset['mq_queue']} (not sent) • SHA-256: {sha256_hash[:16]}...",
            "latency": "1.9 ms (synthetic demo value - no live MQ connection)",
            "status": "simulated",
        },
    ]

    return {
        "preset": preset,
        "agency_key": agency_key,
        "audit_status": audit_status,
        "hard_lock": hard_lock,
        "flags": norm_flags,
        "sha256": sha256_hash,
        "fixed_80_record": fixed_80_record,
        "cobol_display": cobol_display,
        "workflow_steps": workflow_steps,
        "elapsed_ms": elapsed_ms,
    }


class ASCEBobCaseworkerCopilot:
    """
    Demonstrator Caseworker Co-Pilot grounded in ASCE's local deterministic engine
    (`compliance_engine.py`), with simulated EHLLAPI/MQ/orchestration views.
    """

    def process_message(self, user_message: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        if context is None:
            context = {}
        if not isinstance(context, dict):
            return {
                "audit_status": "INPUT_INVALID",
                "response": "**INPUT_INVALID**\n\nContext must be a JSON object.",
                "flags": [{"type": "INPUT_INVALID", "severity": "CRITICAL", "citation": None, "message": "context must be an object"}],
                "workflow_steps": [],
                "actions": ["review_payload"],
                "writeback": {"applied": False, "reason": "INPUT_INVALID - context must be an object"},
                "metrics": self._metrics(1),
            }

        agency_key = context["agency_key"] if "agency_key" in context else "FEMA_NEMIS"
        has_case_data = "case_data" in context
        override_payload = context["case_data"] if has_case_data else None
        msg_lower = str(user_message or "").lower()

        audit_bundle = run_deterministic_legacy_audit(
            agency_key=agency_key,
            override_payload=override_payload,
        )
        preset = audit_bundle["preset"]
        flags = audit_bundle["flags"]
        sha_short = audit_bundle["sha256"][:16]
        elapsed_ms = audit_bundle["elapsed_ms"]

        is_writeback_intent = any(
            k in msg_lower
            for k in ("write-back", "writeback", "remediat", "stage 80-column", "apply remediation", "approve")
        )
        tech_bob_keywords = (
            "tech stack",
            "technology",
            "tech",
            "stack",
            "ibm bob",
            "bob usage",
            "how bob",
            "how was bob",
            "mcp",
            "opencv",
            "flask",
            "framework",
            "architecture",
        )
        denial_keywords = ("deny", "01g", "intercept", "block", "hold", "adverse")
        is_tech_bob_intent = any(k in msg_lower for k in tech_bob_keywords) and not any(
            k in msg_lower for k in denial_keywords
        )

        # FAIL-CLOSED: Reject INPUT_INVALID across ALL paths (write-back and triage/chat).
        if audit_bundle["audit_status"] == "INPUT_INVALID" or preset is None:
            issue_lines = "\n".join([f"• **[{f['type']}]** {f['message']}" for f in flags]) or "• Payload failed canonical schema validation."
            out: Dict[str, Any] = {
                "audit_status": audit_bundle["audit_status"],
                "response": (
                    f"**{'WRITE-BACK BLOCKED: ' if is_writeback_intent else ''}INPUT_INVALID (FAIL-CLOSED)**\n\n"
                    f"Cannot proceed — canonical input or agency validation failed.\n"
                    f"{issue_lines}\n\n"
                    f"Fix the input data and re-run the audit before proceeding."
                ),
                "flags": flags,
                "workflow_steps": audit_bundle["workflow_steps"],
                "cobol_display": audit_bundle["cobol_display"],
                "sha256": audit_bundle["sha256"],
                "actions": ["review_payload"],
                "metrics": self._metrics(len(flags), elapsed_ms),
            }
            if is_writeback_intent:
                out["writeback"] = {
                    "applied": False,
                    "reason": "INPUT_INVALID - payload validation failed",
                }
            return out

        if is_writeback_intent:
            # FAIL-CLOSED: Refuse write-back if compliance_engine.approval_locked() / hard_lock is True
            if audit_bundle.get("hard_lock"):
                return {
                    "audit_status": audit_bundle["audit_status"],
                    "response": (
                        f"**WRITE-BACK BLOCKED: HARD LOCK ENGAGED**\n\n"
                        f"Cannot apply simulated remediation — deterministic locking flags require human review.\n"
                        f"Flags: {[f['type'] for f in flags]}"
                    ),
                    "flags": flags,
                    "workflow_steps": audit_bundle["workflow_steps"],
                    "actions": ["escalate_to_supervisor"],
                    "writeback": {
                        "applied": False,
                        "reason": "HARD_LOCK - requires supervisor/caseworker evidence review",
                    },
                    "metrics": self._metrics(len(flags), elapsed_ms),
                }

            # Proceed with simulated write-back only when no INPUT_INVALID or hard_lock is active
            approved_bundle = run_deterministic_legacy_audit(
                agency_key=agency_key,
                override_payload=override_payload,
                decision_mode="APRV",
            )
            case_label = _safe_case_id(override_payload, preset["case_id"]) if has_case_data else preset["case_id"]
            if has_case_data:
                rationale = (
                    f"[ASCE DETERMINISTIC AUDIT VERIFIED — DEMO WRITE-BACK] "
                    f"Case {case_label} passed hard-lock check (status={approved_bundle['audit_status']}); "
                    f"advisory escalation retained for human sign-off."
                )
                disp_text = f"SIMULATED APPROVAL STAGED ({case_label})"
            else:
                rationale = (
                    f"[ASCE DEMO VERIFIED {preset['statute_cfr']}] {preset['evault_finding']}. "
                    f"Advisory escalation retained; simulated preset disposition {preset['remediated_disp']}."
                )
                disp_text = preset["remediated_disp"]

            return {
                "audit_status": approved_bundle["audit_status"],
                "response": (
                    f"**SIMULATED LEGACY WRITE-BACK (`ARR-DECISION-CODE = 'APRV'`)**\n\n"
                    f"• **Demo Simulation:** Disposition would switch to `{disp_text}`.\n"
                    f"• **Rationale (Demo):** `{rationale}`\n"
                    f"• **Mainframe Queue (Demo):** Local 80-column ASCII preview formatted for `{preset['mq_queue']}` (not transmitted).\n"
                    f"• **Illustrative Target (Unmeasured Demo Scenario):** 42m → 28s workflow example.\n"
                    f"• **Note:** Local demonstration without live EHLLAPI, MQ, or IBM Bob/watsonx connections."
                ),
                "flags": approved_bundle["flags"],
                "workflow_steps": approved_bundle["workflow_steps"],
                "actions": ["toggle_3270_view", "next_agency_case"],
                "writeback": {
                    "applied": True,
                    "disposition": disp_text,
                    "rationale": rationale,
                    "cobol_display": approved_bundle["cobol_display"],
                    "sha256": approved_bundle["sha256"],
                },
                "metrics": self._metrics(len(approved_bundle["flags"]), approved_bundle["elapsed_ms"]),
            }

        elif is_tech_bob_intent:
            case_label = _safe_case_id(override_payload, preset["case_id"]) if has_case_data else preset["case_id"]
            response = (
                f"**ASCE GOVBEN TECH STACK & IBM BOB USAGE (`{case_label}` • `{preset['program_code']}`)**\n\n"
                f"• **Core Stack (Live Locally Here):** **Python 3.11+** + **Flask 3.0.0** serving the deterministic statutory engine (`compliance_engine.py`, `agencies/*.py`, `surge_api.py`, `templates.py` — **30 CFR sections** across 7 programs, evaluated in `{elapsed_ms} ms`), **80-col ASCII/`cp037` EBCDIC** COBOL formatter (`mainframe_adapter.py`), **OpenCV 4.8** contour wet-ink verifier (`vision_ai.py`), `pdfplumber 0.10.3`/`Pillow`, `SHA-256` canonical audit receipts, and `ecfr_check.py` regulatory watcher.\n"
                f"• **Security, Packaging & Verification:** `SQLite WAL` session store (`fed_auth.py` simulated PIV/CAC + SAML/OIDC pattern), **Red Hat UBI 9** non-root container (`Dockerfile`) + OpenShift YAML, `pytest` (`350+` checks) + `10,000`-case synthetic benchmark (`0` mismatches).\n"
                f"• **IBM Bob Usage — Build & IV&V Co-Developer:** Used **IBM Bob** with local MCP servers (`filesystem`, `memory`, `pdf`, `sequential-thinking`, `chrome-devtools`, `puppeteer` via `bob-mcp-config.json`) to scaffold this 3-pane sidecar UI, build the COBOL/EBCDIC & OpenCV modules, and enforce fail-closed IV&V hardening (`INPUT_INVALID`, `hard_lock`, honest telemetry).\n"
                f"• **IBM Bob Usage — Bounded Runtime Co-Pilot:** Plain-English assistant (*\"Bob explains, deterministic engine decides\"*) grounded behind `DeterministicAuditor` and mandatory human sign-off (`hard_lock={audit_bundle['hard_lock']}`).\n"
                f"• **Enterprise Integration & Sandbox Boundary:** Designed for non-invasive **EHLLAPI / UIA** screen read, **IBM MQ** pre-commit hold, credential-gated **watsonx.ai** (`ibm/granite-4-h-small` / `granite-3-8b-instruct`), **watsonx Orchestrate** pass-through (`PASS_THROUGH_NO_AGENT_TEXT_USED`), and Cloudant (`app.py`). Live external IBM/MQ/EHLLAPI transports are **offline-simulated** in this standalone demo (`SHA-256: {sha_short}...`)."
            )
            actions = ["toggle_3270_view", "sync_active_screen", "writeback_remediation"]

        elif any(k in msg_lower for k in ("deny", "01g", "intercept", "block", "why", "hold")):
            bullet_lines = "\n".join([f"• **[{f['type']}]** {f['message']}" for f in flags]) or "• Statutory hold verified."
            case_label = _safe_case_id(override_payload, preset["case_id"]) if has_case_data else preset["case_id"]
            response = (
                f"**PRE-COMMIT INTERCEPT (`CICS-HOLD-101`) — DEMO SIMULATION**\n\n"
                f"Case **`{case_label}`** ({preset['program_code']}) evaluation (`status={audit_bundle['audit_status']}`):\n"
                f"• **Preset Context (Demo Fixture):** {preset['evault_finding']}\n"
                f"• **Statutory Reference:** `{preset['sla_banner']}`\n"
                f"{bullet_lines}\n\n"
                f"**Next Step (Demo):** Review deterministic flags before any simulated write-back preview."
            )
            actions = ["writeback_remediation", "toggle_3270_view", "export_receipt"]

        elif any(k in msg_lower for k in ("legacy", "cobol", "attach", "hook", "ehllapi", "3270", "install", "deploy")):
            case_label = _safe_case_id(override_payload, preset["case_id"]) if has_case_data else preset["case_id"]
            response = (
                f"**Demo: Zero-Touch Legacy Integration Architecture ({preset['cics_trans']} / {preset['mq_queue']}):**\n\n"
                f"1. **Workstation Screen Sync (Demo Concept):** Would read `{case_label}` from terminal presentation memory (`{preset['ehllapi_coords']}`)—requires live EHLLAPI adapter.\n"
                f"2. **Pre-Commit Network Interceptor (Demo Concept):** Would intercept outbound commits on `{preset['mq_queue']}`—requires live IBM MQ connection.\n"
                f"3. **Bidirectional Write-Back (`mainframe_adapter.py`):** Generates local 80-column ASCII COBOL Copybook records (`SHA-256: {sha_short}...`) for preview.\n\n"
                f"**Projected Target (Illustrative):** 0 lines of legacy COBOL modified; example 42 min → 28 sec scenario.\n"
                f"**Current Status:** Local demo simulation without live EHLLAPI/MQ/watsonx connections."
            )
            actions = ["sync_active_screen", "writeback_remediation", "toggle_3270_view"]

        else:
            # Default: Full Caseworker Triage & Audit Brief
            bullet_lines = "\n".join([f"• **[{f['severity']} • {f['type']}]** {f['message']}" for f in flags]) or "• No deterministic escalation flags raised."
            case_label = _safe_case_id(override_payload, preset["case_id"]) if has_case_data else preset["case_id"]
            response = (
                f"**CASEWORKER CO-PILOT BRIEF (DEMO SIMULATION) — CASE `{case_label}` (`{preset['program_code']}`)**\n\n"
                f"Local deterministic engine (`compliance_engine.py`) evaluated rules in **`{elapsed_ms} ms`** *(orchestration & E-Vault views are demo fixtures)*:\n"
                f"• **Preset Scenario Screen:** `{preset['legacy_bad_disp']}`\n"
                f"• **Preset Demo Context:** {preset['evault_finding']}\n"
                f"{bullet_lines}\n\n"
                f"**Cryptographic Audit Receipt:** `SHA-256 {sha_short}...` (demo simulation).\n"
                f"Ready for human review (`hard_lock={audit_bundle['hard_lock']}`)."
            )
            actions = ["writeback_remediation", "simulate_denial_intercept", "toggle_3270_view"]

        return {
            "audit_status": audit_bundle["audit_status"],
            "response": response,
            "flags": flags,
            "workflow_steps": audit_bundle["workflow_steps"],
            "cobol_display": audit_bundle["cobol_display"],
            "sha256": audit_bundle["sha256"],
            "actions": actions,
            "metrics": self._metrics(len(flags), elapsed_ms),
        }

    @staticmethod
    def _metrics(flag_count: int, elapsed_ms: float = 0.0) -> Dict[str, Any]:
        return {
            "simulated": True,
            "measured_compliance_engine_ms": elapsed_ms,
            "projected_demo_time_saved": "85% (illustrative 42m -> 28s demo target)",
            "accuracy_basis": "Deterministic rule evaluation (local compliance_engine.py)",
            "illustrative_benchmark_note": "Use /api/validate/benchmark for measured 5,000-case timing",
            "flags_detected": flag_count,
        }


copilot = ASCEBobCaseworkerCopilot()


def _parse_json_object():
    """Require either an empty body or a valid JSON object payload."""
    raw = request.get_data(cache=True)
    if not raw or not raw.strip():
        return {}, None
    parsed = request.get_json(silent=True)
    if not isinstance(parsed, dict):
        return None, (jsonify({"error": "INVALID_JSON_OBJECT"}), 400)
    return parsed, None


@app.route('/')
def index():
    return render_template('bob_chat.html', agencies=LEGACY_AGENCY_CASES)


@app.route('/api/bob/legacy_sync', methods=['POST'])
def legacy_sync():
    data, err = _parse_json_object()
    if err:
        return err
    agency_key = data["agency_key"] if "agency_key" in data else "FEMA_NEMIS"
    if not isinstance(agency_key, str) or agency_key not in LEGACY_AGENCY_CASES:
        return jsonify({"error": "UNKNOWN_AGENCY", "allowed_agencies": sorted(LEGACY_AGENCY_CASES)}), 400
    bundle = run_deterministic_legacy_audit(agency_key=agency_key)
    chat_res = copilot.process_message("audit active screen", {"agency_key": agency_key})
    return jsonify({
        "preset": bundle["preset"],
        "audit": {
            "status": bundle["audit_status"],
            "hard_lock": bundle["hard_lock"],
            "flags": bundle["flags"],
            "sha256": bundle["sha256"],
            "cobol_display": bundle["cobol_display"],
            "workflow_steps": bundle["workflow_steps"],
            "elapsed_ms": bundle["elapsed_ms"],
        },
        "copilot": chat_res,
    })


@app.route('/api/bob/chat', methods=['POST'])
def chat():
    data, err = _parse_json_object()
    if err:
        return err
    user_message = data.get("message", "")
    if not isinstance(user_message, str):
        return jsonify({"error": "INVALID_MESSAGE"}), 400
    context = data.get("context") if "context" in data else {}
    if not isinstance(context, dict):
        return jsonify({"error": "INVALID_CONTEXT"}), 400
    if "agency_key" in context:
        ak = context["agency_key"]
        if not isinstance(ak, str) or ak not in LEGACY_AGENCY_CASES:
            return jsonify({"error": "UNKNOWN_AGENCY", "allowed_agencies": sorted(LEGACY_AGENCY_CASES)}), 400
    return jsonify(copilot.process_message(user_message, context))


@app.route('/api/bob/audit', methods=['POST'])
def audit_case():
    data, err = _parse_json_object()
    if err:
        return err
    agency_key = data["agency_key"] if "agency_key" in data else "FEMA_NEMIS"
    if not isinstance(agency_key, str) or agency_key not in LEGACY_AGENCY_CASES:
        return jsonify({"error": "UNKNOWN_AGENCY", "allowed_agencies": sorted(LEGACY_AGENCY_CASES)}), 400

    context: Dict[str, Any] = {"agency_key": agency_key}
    if "case_data" in data:
        if not isinstance(data["case_data"], dict):
            return jsonify({"error": "INVALID_CASE_DATA"}), 400
        context["case_data"] = data["case_data"]
    else:
        payload_fields = {k: v for k, v in data.items() if k != "agency_key"}
        if payload_fields:
            context["case_data"] = payload_fields

    return jsonify(copilot.process_message("audit this case", context))


@app.route('/api/bob/health')
def bob_health():
    return jsonify({
        "status": "healthy",
        "simulated": True,
        "live_ibm_bob_connected": False,
        "live_watsonx_orchestrate": False,
        "live_mainframe_connected": False,
        "service": "ASCE GOVBEN CaaS — Legacy Workstation & Caseworker Co-Pilot (Demo Simulation)",
        "entity": "ASCE Technologies LLC (CEID: 10x6kg)",
        "track": "Track #4: Government & Public Services",
        "version": "10.0.0-enterprise-legacy-bridge",
        "ibm_integration": {
            "ibm_bob_copilot": "simulated_demo",
            "watsonx_orchestrate_4step": "simulated_demo",
            "asce_deterministic_engine": "active_local",
            "cobol_ebcdic_80col_bridge": "local_formatter_only",
        },
        "tech_stack": {
            "runtime": "Python 3.11+ / Flask 3.0.0 / Gunicorn 21.2.0",
            "statutory_engine": "compliance_engine.py + agencies/*.py (30 CFR sections across 7 programs)",
            "vision_and_docs": "OpenCV 4.8.1 (wet-ink contour geometry), pdfplumber 0.10.3, Pillow 10.1, NumPy 1.26, python-docx 1.1",
            "mainframe_bridge": "mainframe_adapter.py (80-col ASCII COBOL copybooks + cp037 EBCDIC; IBM MQ/EHLLAPI simulated here)",
            "security_and_crypto": "SHA-256 canonical receipts; fed_auth.py SQLite WAL simulated PIV/CAC + SAML/OIDC",
            "container_and_ops": "Red Hat UBI 9 non-root container (USER 1001), OpenShift YAML, ecfr_check.py Federal Register monitor",
            "ibm_cloud_optional": "watsonx.ai Granite (ibm/granite-4-h-small / granite-3-8b-instruct), granite-vision-3b, watsonx Orchestrate pass-through, Cloudant (unconfigured/simulated here)",
            "verification": "pytest suite (350+ tests) + 10,000-case synthetic deterministic benchmark (0 mismatches)",
        },
        "ibm_bob_usage": {
            "sdlc_engineering_and_ivv": (
                "Used IBM Bob with local MCP servers (filesystem, memory, pdf, sequential-thinking, chrome-devtools, puppeteer "
                "via bob-mcp-config.json) to build the 3-pane Caseworker Co-Pilot UI, COBOL/EBCDIC & OpenCV adapters, and "
                "fail-closed schema/honesty hardening."
            ),
            "runtime_copilot_pattern": (
                "Bounded sidecar conversational layer translating deterministic CFR flags into plain-English caseworker steps; "
                "never overrides DeterministicAuditor, SLA clocks, or human-in-the-loop hard_lock gates."
            ),
            "sandbox_boundary": "IBM Bob API calls are offline-simulated in this standalone demo; local rules, COBOL formatting, and SHA-256 hashing run live.",
        },
    })


# Register validation blueprint before app.run
from validation_routes import validation_bp
app.register_blueprint(validation_bp)

if __name__ == '__main__':
    app.run(debug=False, host="0.0.0.0", port=5001)

