import os
import re
import json
import hashlib
import logging
import datetime
from flask import Flask, render_template, request, jsonify, session
from dotenv import load_dotenv
from compliance_engine import (
    ADAPTER_TENANTS,
    NON_LOCKING_FLAG_TYPES,
    DeterministicAuditor,
    approval_locked,
)
from templates import build_action_plan, TEMPLATE_LABEL
from flask import Response
import base64
import numpy as np
import cv2
from mainframe_adapter import MainframeAdapter, COBOLCopybookGenerator
from fed_auth import FederalAuthenticationManager, CardType
from vision_ai import VisionAIClient

load_dotenv()

app = Flask(__name__)
# Must be identical across all gunicorn workers and Code Engine instances, or sessions break between pages.
_flask_secret_key = os.environ.get("FLASK_SECRET_KEY")
if not _flask_secret_key:
    logging.getLogger("asce.app").warning(
        "FLASK_SECRET_KEY is unset; generating ephemeral secret key (use gunicorn --preload for single container, set FLASK_SECRET_KEY for multi-pod deployments)."
    )
    _flask_secret_key = os.urandom(24)
app.secret_key = _flask_secret_key
# Request body cap (the bulk JSON API is the only large client). Oversized bodies get a 413.
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH_MB", "25")) * 1024 * 1024

VALID_CFR_SECTIONS = {
    "FEMA_TITLE_44": ["206.110", "206.111", "206.113", "206.114", "206.115", "206.117", "206.119", "206.120"],
    "FEMA_SCENARIO_1_SBA": ["206.110", "206.111", "206.113", "206.114", "206.115", "206.117", "206.119", "206.120"],
    "FEMA_SCENARIO_2_REDUNDANCY": ["206.110", "206.111", "206.113", "206.114", "206.115", "206.117", "206.119", "206.120"],
    "FEMA_SCENARIO_3_INSPECTOR": ["206.110", "206.111", "206.113", "206.114", "206.115", "206.117", "206.119", "206.120"],
    "FEMA_SCENARIO_4_CTHA": ["206.110", "206.111", "206.113", "206.114", "206.115", "206.117", "206.119", "206.120"],
    "FEMA_FORUM_CASE_OCCUPANCY": ["206.110", "206.111", "206.115", "206.117", "206.120"],
    "VA_FORUM_PACT_ACT_NEXUS": ["3.109", "3.159", "3.203", "3.303", "3.304"],
    "MEDICAID_FORUM_PHE_UNWINDING": ["435.912", "435.916"],
    "SNAP_FORUM_SAR7_INCOME": ["273.2", "273.12"],
    "VA_TITLE_38": ["3.109", "3.159", "3.203", "3.303", "3.304", "3.310"],
    "SSA_TITLE_20": ["404.909", "404.968", "404.1512", "416.1450"],
    "MEDICAID_42_CFR": ["435.912", "435.916", "435.907"],
    "SNAP_7_CFR": ["273.2", "273.2(g)", "273.2(i)", "273.12"],
    "HUD_24_CFR": ["5.657", "5.659", "960.257", "982.516"],
    # Disaster Unemployment Assistance. Same CFR title (20) as SSA, so separation relies on this per-tenant list.
    # Only sections confirmed on eCFR (2026-10-01) and encoded by the engine.
    "DUA_20_CFR_625": ["625.2(f)", "625.4(a)", "625.6(e)", "625.7", "625.8(a)"]
}

DECISION_SUPPORT_DISCLAIMER = (
    "Decision support only. A trained human reviewer must independently "
    "verify facts, citations, and applicable policy before taking any action."
)

def _days_ago(n):
    return (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=n)).isoformat()

TENANTS = {
    "FEMA_SCENARIO_1_SBA": {
        "case_id": "CASE-FEMA-SBA-001",
        "label": "SYNTHETIC / FEMA (PRE-2024 SBA WORKFLOW REVIEW)",
        "applicant": {"age": 42},
        "incident": {
            "disaster_number": "DR-DEMO-0001",
            "declaration_date": "2024-03-01",
            "appeal_date": (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=92)).isoformat()
        },
        "residence": {"structure_status": "Red Tag - Condemned"},
        "evidence": {"uploaded": ["FEMA Registration Confirmation"], "missing": ["SBA Loan Determination"]}
    },
    "FEMA_SCENARIO_2_REDUNDANCY": {
        "case_id": "CASE-FEMA-RED-002",
        "label": "SYNTHETIC / FEMA (SENIOR + DOC REDUNDANCY)",
        "applicant": {"age": 72, "status": "Fixed Income"},
        "incident": {"disaster_number": "DR-DEMO-0001", "declaration_date": _days_ago(90)},
        "residence": {"structure_status": "Major Damage"},
        "evidence": {
            "uploaded": ["Driver's License", "Proof of Occupancy", "Insurance Denial"],
            "missing": []
        },
        "caseworker_action": {
            "intent": "Send Request for Information (RFI) Letter",
            "requesting_documents": ["Driver's License", "Utility Bill"]
        }
    },
    "FEMA_SCENARIO_3_INSPECTOR": {
        "case_id": "CASE-FEMA-INSP-003",
        "label": "SYNTHETIC / FEMA (INSPECTOR CONFLICT)",
        "applicant": {"age": 35},
        "incident": {"disaster_number": "DR-DEMO-0001", "declaration_date": _days_ago(90)},
        "residence": {
            "structure_status": "Red Tag - Condemned",
            "inspector_notes": "Minor roof damage observed from street. Structure intact."
        },
        "evidence": {"uploaded": ["Photo Documentation"], "missing": []}
    },
    "VA_TITLE_38": {
        "case_id": "CASE-DEMO-VA-002",
        "label": "SYNTHETIC / VA DEMONSTRATION ONLY",
        "claim": {
            "submission_date": _days_ago(410),
            "incomplete_application_notice_date": _days_ago(380),
            "incomplete_application_requested_evidence": ["DD214"],
            "evidence_time_limit_holidays": [],
            "condition": "PTSD"
        },
        "veteran": {"service_branch": "US Army"},
        "evidence": {"uploaded": ["Lay Statement"], "missing": ["DD214"]}
    },
    "SSA_TITLE_20": {
        "case_id": "CASE-DEMO-SSA-003",
        "label": "SYNTHETIC / SSA DEMONSTRATION ONLY",
        "applicant": {"age": 68},
        "deadline": {
            "label": "Reconsideration request filed after notice (60 days from receipt; receipt presumed 5 days after notice date; good cause may extend)",
            "start_date": _days_ago(75),
            "limit_days": 65,
            "citation": "20 CFR 404.909(a)(1), 404.901, 404.911"
        },
        "evidence": {"uploaded": ["Birth Certificate", "Medical Records"], "missing": []},
        "caseworker_action": {"intent": "Send evidence request", "requesting_documents": ["Birth Certificate"]}
    },
    "MEDICAID_42_CFR": {
        "case_id": "CASE-DEMO-MCD-004",
        "label": "SYNTHETIC / MEDICAID DEMONSTRATION ONLY",
        "applicant": {"age": 34},
        "deadline": {
            "label": "Eligibility determination pending since application (non-disability basis; 90-day limit applies to disability-based applications)",
            "start_date": _days_ago(52),
            "limit_days": 45,
            "citation": "42 CFR 435.912"
        },
        "evidence": {"uploaded": ["Application Form"], "missing": ["Income Verification"]}
    },
    "SNAP_7_CFR": {
        "case_id": "CASE-DEMO-SNAP-005",
        "label": "SYNTHETIC / SNAP DEMONSTRATION ONLY",
        "applicant": {"age": 29},
        "deadline": {
            "label": "Standard application processing",
            "start_date": _days_ago(34),
            "limit_days": 30,
            "citation": "7 CFR 273.2(g)"
        },
        "expedited": {
            "eligible": True,
            "start_date": _days_ago(12),
            "limit_days": 7,
            "citation": "7 CFR 273.2(i)(3)"
        },
        "evidence": {"uploaded": ["Photo ID", "Rent Receipt"], "missing": []},
        "caseworker_action": {"intent": "Send verification request", "requesting_documents": ["Photo ID"]}
    },
    "HUD_24_CFR": {
        "case_id": "CASE-DEMO-HUD-006",
        "label": "SYNTHETIC / HUD DEMONSTRATION ONLY",
        "applicant": {"age": 71},
        "deadline": {
            "label": "Annual reexamination overdue (modeling Section 8 project-based; public housing is 24 CFR 960.257)",
            "start_date": _days_ago(400),
            "limit_days": 365,
            "citation": "24 CFR 5.657"
        },
        "data_conflict": {
            "field": "Annual household income",
            "value_a": "$14,200", "source_a": "tenant-reported form",
            "value_b": "$22,900", "source_b": "third-party wage verification"
        },
        "evidence": {"uploaded": ["Lease", "Tenant Income Form"], "missing": []}
    },
    "FEMA_FORUM_CASE_OCCUPANCY": {
        "case_id": "CASE-FEMA-DR4782-019",
        "label": "REAL-WORLD FORUM CASE / FEMA DR-4782 (UNSIGNED LEASE PG 4)",
        "applicant": {"age": 49, "status": "Renter Disaster Survivor"},
        "incident": {
            "disaster_number": "DR-4782-GA",
            "declaration_date": _days_ago(65),
            "appeal_date": _days_ago(5)
        },
        "residence": {
            "structure_status": "Major Roof / Water Infiltration",
            "proof_of_occupancy": "Lease Agreement Uploaded (4 Pages)"
        },
        "evidence": {
            "uploaded": ["Photo ID", "Lease Agreement Pages 1-3"],
            "missing": ["Signed Page 4 Certification"]
        },
        "caseworker_action": {
            "intent": "Send generic 60-day Notice of Ineligibility",
            "requesting_documents": ["Proof of Occupancy"]
        }
    },
    "SNAP_FORUM_SAR7_INCOME": {
        "case_id": "CASE-SNAP-SAR7-882",
        "label": "REAL-WORLD FORUM CASE / SNAP 7 CFR § 273.12 (SAR-7 CHURN)",
        "applicant": {"age": 31, "household_size": 3},
        "deadline": {
            "label": "SAR-7 Semi-Annual Recertification Processing",
            "start_date": _days_ago(28),
            "limit_days": 30,
            "citation": "7 CFR 273.12"
        },
        "income": {
            "baseline_monthly": 1850,
            "one_time_overtime_spike": 420,
            "procedural_flag": "Non-recurring overtime flagged as permanent"
        },
        "evidence": {"uploaded": ["SAR-7 Report", "September Paystub"], "missing": []},
        "caseworker_action": {
            "intent": "Issue Notice of Denial for Exceeding Net Income",
            "requesting_documents": []
        }
    },
    "VA_FORUM_PACT_ACT_NEXUS": {
        "case_id": "CASE-VA-PACT-741",
        "label": "REAL-WORLD FORUM CASE / VA TITLE 38 CFR § 3.303 (PACT ACT NEXUS)",
        "claim": {
            "submission_date": _days_ago(180),
            "condition": "Constrictive Bronchiolitis (Presumptive Burn Pit)"
        },
        "veteran": {
            "service_branch": "USMC",
            "deployment": "Balad Air Base, Iraq (2007)",
            "pact_act_eligible": True
        },
        "evidence": {
            "uploaded": ["DD214", "Service Treatment Records"],
            "missing": ["Signed Medical Nexus Statement"]
        },
        "caseworker_action": {
            "intent": "Issue Proposed Rating Denial (No Nexus)",
            "requesting_documents": ["Medical Nexus Statement"]
        }
    },
    "MEDICAID_FORUM_PHE_UNWINDING": {
        "case_id": "CASE-MCD-PHE-904",
        "label": "REAL-WORLD FORUM CASE / MEDICAID 42 CFR § 435.916 (EX-PARTE UNWINDING)",
        "applicant": {"age": 27, "children_count": 2},
        "deadline": {
            "label": "Post-PHE Annual Eligibility Redetermination",
            "start_date": _days_ago(40),
            "limit_days": 45,
            "citation": "42 CFR 435.916"
        },
        "procedural_status": {
            "ex_parte_attempted": False,
            "returned_mail_flag": True,
            "state_wage_database_match": True
        },
        "evidence": {
            "uploaded": ["Electronic Wage Record (The Work Number)"],
            "missing": ["Paper Renewal Packet"]
        },
        "caseworker_action": {
            "intent": "Procedural Disenrollment for Failure to Return Packet",
            "requesting_documents": ["Paper Renewal Packet"]
        }
    },
    "DUA_20_CFR_625": {
        "case_id": "CASE-DEMO-DUA-007",
        "label": "SYNTHETIC / DUA DEMONSTRATION ONLY",
        "applicant": {"age": 47, "employment_type": "worker"},
        "incident": {
            "disaster_number": "DR-DEMO-0001",
            "disaster_began_date": _days_ago(63),
            "declaration_date": _days_ago(60),
            "announcement_date": _days_ago(57),
            "area_legal_holidays": []
        },
        "dua": {
            # Filing deadline is 27 days ago (25 at the latest after a weekend roll): filed late, no good cause.
            "initial_application_date": _days_ago(24),
            "good_cause_late_filing": False,
            # Statement-only weekly amount: proof was due 21 days after filing (3 days ago) and is missing.
            "weekly_amount_basis": "statement_only",
            "employment_proof_submitted_date": None,
            "weeks_claimed": [_days_ago(55), _days_ago(48)]
        },
        "evidence": {"uploaded": ["Photo ID", "DUA Initial Application"], "missing": []}
    },
    # Showcase of a real-world PATTERN, entirely synthetic: residency verified weeks earlier on a three-way call;
    # a second (CTHA housing-assistance) verifier reached a DIFFERENT tenant with the same first name and
    # recommended a fraud flag and lock; documents already on file re-requested; eviction notice; 40 days idle;
    # a 55+ disabled veteran in the household. Expected: no fraud flag, no lock, supervisor routing, human decides.
    "FEMA_SCENARIO_4_CTHA": {
        "case_id": "CASE-FEMA-CTHA-003",
        "label": "SYNTHETIC / FEMA + CTHA (VERIFIED RESIDENCY vs. WRONG-TENANT REPORT)",
        "applicant": {"age": 44, "full_name": "Synthetic Applicant Alpha", "unit": "Unit 12B",
                      "applicant_id": "SYN-APP-0003", "dob_year": 1982},
        "household_members": [
            {"member_id": "HH-1", "relationship": "applicant", "age": 44, "disability": False, "veteran": False},
            {"member_id": "HH-2", "relationship": "parent", "age": 58, "disability": True, "veteran": True},
        ],
        "incident": {"disaster_number": "DR-DEMO-0001", "declaration_date": _days_ago(90)},
        "residence": {"structure_status": "Major Damage"},
        "evidence": {
            "uploaded": ["FEMA Registration Confirmation", "SBA Loan Determination",
                         "Social Security Card \u2013 household", "Lease Agreement"],
            "missing": []
        },
        "caseworker_action": {
            "intent": "CTHA follow-up document request",
            "requesting_documents": ["Social Security cards", "SBA loan determination"]
        },
        "verified_facts": [
            {"fact": "residency", "value": "Current tenant of Unit 12B", "verified_by": "Caseworker CW-SYN-07",
             "method": "three-way call with property management", "date": _days_ago(70)}
        ],
        "third_party_reports": [
            {"report_id": "CTHA-RPT-0001", "fact": "residency", "finding": "CONFLICT",
             "reported_value": "Not a current tenant", "source": "CTHA housing-assistance verifier",
             "reported_by": "Verifier SYN-22", "date": _days_ago(45),
             "subject": {"first_name": "Synthetic", "full_name": "Synthetic Applicant Bravo", "unit": "Unit 4A"},
             "recommended_action": "FRAUD_FLAG_AND_LOCK"}
        ],
        "housing": {"eviction_notice_date": _days_ago(5), "rent_past_due": True},
        "last_action_date": _days_ago(40)
    }
}

PROMPT_TEMPLATE = """You are an ASCE compliance auditor reviewing a synthetic demonstration case for {tenant_id}.
Analyze the following SYNTHETIC case data. All data is fabricated for demonstration purposes only."""

def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()

TENANT_CFR_TITLE = {
    "FEMA_TITLE_44": "44",
    "FEMA_SCENARIO_1_SBA": "44", "FEMA_SCENARIO_2_REDUNDANCY": "44", "FEMA_SCENARIO_3_INSPECTOR": "44",
    "FEMA_SCENARIO_4_CTHA": "44",
    "VA_TITLE_38": "38", "SSA_TITLE_20": "20", "MEDICAID_42_CFR": "42", "SNAP_7_CFR": "7", "HUD_24_CFR": "24",
    "DUA_20_CFR_625": "20",
    "FEMA_FORUM_CASE_OCCUPANCY": "44",
    "SNAP_FORUM_SAR7_INCOME": "7",
    "VA_FORUM_PACT_ACT_NEXUS": "38",
    "MEDICAID_FORUM_PHE_UNWINDING": "42",
}

_SECTION_RE = re.compile(r"(\d+\.\d+)((?:\([0-9A-Za-z]+\))*)")
_TITLE_RE = re.compile(r"\b(\d+)\s*C\.?\s*F\.?\s*R\b", re.IGNORECASE)

def validate_citation(citation_text, tenant_id):
    """Whitelist check only. VALIDATED means the section is on the approved list; it does NOT confirm legal correctness."""
    citation_text = "" if citation_text is None else str(citation_text)
    if not citation_text.strip():
        return {"status": "REJECTED", "matched_section": None, "note": "Empty citation"}

    expected_title = TENANT_CFR_TITLE.get(tenant_id)
    wrong_titles = sorted({t for t in _TITLE_RE.findall(citation_text) if expected_title and t != expected_title})
    if wrong_titles:
        return {"status": "UNVERIFIED", "matched_section": None,
                "note": f"Cites {', '.join(wrong_titles)} CFR; this program is governed by {expected_title} CFR"}

    whitelist = VALID_CFR_SECTIONS.get(tenant_id, [])
    best = None
    for base, parens in _SECTION_RE.findall(citation_text):
        for entry in whitelist:
            e_base, e_parens = _SECTION_RE.match(entry).groups()
            if base == e_base and parens.startswith(e_parens):
                if best is None or len(entry) > len(best):
                    best = entry
    if best:
        return {"status": "VALIDATED", "matched_section": best, "note": "Matches whitelist"}
    return {"status": "UNVERIFIED", "matched_section": None, "note": "Not found in whitelist"}

NEW_FIXTURES = {
    "FEMA_TITLE_44": ("REVIEW", "MEDIUM", "44 CFR 206.110", "FEMA Individual Assistance case queued for deterministic rule evaluation and human caseworker review."),
    "SSA_TITLE_20": ("REVIEW", "MEDIUM", "20 CFR 404.909", "Reconsideration request appears outside the filing window and a requested document is already in the file. Human review required."),
    "MEDICAID_42_CFR": ("ESCALATE", "HIGH", "42 CFR 435.912", "Eligibility determination exceeds the timeliness standard and income verification is missing."),
    "SNAP_7_CFR": ("ESCALATE", "HIGH", "7 CFR 273.2(g)", "Standard and expedited processing clocks appear exceeded, and the caseworker is re-requesting an ID already on file."),
    "HUD_24_CFR": ("ESCALATE", "HIGH", "24 CFR 5.657", "Annual recertification is overdue and reported income conflicts with third-party verification."),
    "DUA_20_CFR_625": ("ESCALATE", "HIGH", "20 CFR 625.8(a)", "The initial DUA application appears to have been filed after the 30-day window following the announcement date, and employment documentation appears overdue. Human review required."),
    "FEMA_FORUM_CASE_OCCUPANCY": ("ESCALATE", "HIGH", "44 CFR 206.110", "Lease Agreement Pages 1-3 are verified; Page 4 signature block is missing. Occupancy evidence review defect DET-004 queued for caseworker review before issuing any adverse notice."),
    "SNAP_FORUM_SAR7_INCOME": ("REVIEW", "MEDIUM", "7 CFR 273.12", "Overtime wage spike flagged as one-time anomaly. Procedural churn blocked; caseworker review required to prevent wrongful benefit termination."),
    "VA_FORUM_PACT_ACT_NEXUS": ("ESCALATE", "HIGH", "38 CFR 3.303", "PACT Act presumptive condition identified with verified Iraq deployment. Missing nexus statement blocked from summary denial; VA Duty to Assist examination ordered."),
    "MEDICAID_FORUM_PHE_UNWINDING": ("ESCALATE", "HIGH", "42 CFR 435.916", "Procedural disenrollment blocked. State wage database match confirms income eligibility; ex-parte automatic renewal required before paper notice termination."),
    "FEMA_SCENARIO_4_CTHA": ("ESCALATE", "HIGH", "44 CFR 206.110", "Residency was already verified; a later third-party report appears to concern a different tenant. Documents already on file are being re-requested, the household faces eviction and the case has been idle for weeks. Supervisor attention and human reconciliation required; no fraud determination is made."),
}

def load_fixture(tenant_id):
    if tenant_id in NEW_FIXTURES:
        rec, conf, cite, reason = NEW_FIXTURES[tenant_id]
        return {"recommendation": rec, "confidence_band": conf, "statutory_basis": cite,
                "reasoning": reason, "missing_evidence": [], "_source": "FIXTURE",
                "model_id": "none (pre-written synthetic fixture)"}
    if tenant_id == "FEMA_SCENARIO_1_SBA":
        return {
            "recommendation": "ESCALATE",
            "confidence_band": "HIGH",
            "statutory_basis": "44 CFR 206.115",
            "reasoning": "For this pre-March 22, 2024 declaration, legacy SBA loan determination workflow review applies (declarations on or after 2024-03-22 no longer require an SBA determination under the FEMA IA Equity Rule), and the appeal window has exceeded 90 days under 44 CFR 206.115.",
            "missing_evidence": ["SBA Loan Determination"],
            "_source": "FIXTURE",
            "model_id": "none (pre-written synthetic fixture)"
        }
    elif tenant_id == "FEMA_SCENARIO_2_REDUNDANCY":
        return {
            "recommendation": "REVIEW",
            "confidence_band": "MEDIUM",
            "statutory_basis": "44 CFR 206.117",
            "reasoning": "The applicant is 72 years old, triggering a priority queue. Caseworker action requires review due to potential redundancy in document requests.",
            "missing_evidence": [],
            "_source": "FIXTURE",
            "model_id": "none (pre-written synthetic fixture)"
        }
    elif tenant_id == "FEMA_SCENARIO_3_INSPECTOR":
        return {
            "recommendation": "ESCALATE",
            "confidence_band": "HIGH",
            "statutory_basis": "44 CFR 206.113",
            "reasoning": "A conflict exists between the field inspector's notes (Minor damage) and the applicant's claimed structure status (Condemned). Supervisory review is mandatory.",
            "missing_evidence": [],
            "_source": "FIXTURE",
            "model_id": "none (pre-written synthetic fixture)"
        }
    else:
        return {
            "recommendation": "ESCALATE",
            "confidence_band": "HIGH",
            "statutory_basis": "38 CFR 3.109",
            "reasoning": "Evidence requested after the incomplete-application notice remains outstanding beyond the 1-year response window under 38 CFR 3.109 (human review must evaluate good-cause extension), and the DD214 requires evidence development under 38 CFR 3.203.",
            "missing_evidence": ["DD214"],
            "_source": "FIXTURE",
            "model_id": "none (pre-written synthetic fixture)"
        }

DECISION_QUEUES = {
    "ESCALATE": "Senior Reviewer Queue",
    "RETURN": "Evidence Request Queue",
    "APPROVE_FOR_REVIEW": "Next-Stage Processing",
}

# Routing is owned by the rules engine, not the AI. Notice, checklist and next step come from templates.py.
DECISION_NEXT_STEPS = {
    "ESCALATE": "Hand the case to a senior reviewer for independent review before any action is taken.",
    "RETURN": "Prepare a request to the applicant for the missing evidence listed in this case.",
    "APPROVE_FOR_REVIEW": "Forward the case to next-stage processing.",
}

ORCHESTRATE_PASS_THROUGH_GATE = ("PASS-THROUGH: agent output not used; queue, notice and checklist "
                                 f"{TEMPLATE_LABEL}")
AGENT_REPLY_AUDIT_CHARS = 4000      # kept in the Cloudant audit document
AGENT_REPLY_SESSION_CHARS = 500     # kept in the session cookie / JSON export

HARD_LOCK_APPROVAL_ERROR = (
    "HARD LOCK: the rules engine blocked this case. 'Approve for Review' requires a supervisor override: tick the "
    "override box and enter the supervisor's initials and an override reason. 'Escalate' and 'Return' remain available."
)

def invoke_orchestrate_agent(payload):
    """
    PASS-THROUGH mode. Send the deterministic result for a decision a HUMAN has already made to the ASCE
    Caseworker Action Agent in IBM watsonx Orchestrate, and record only the call status, run id and model.
    The agent's reply is never parsed, rendered or used for routing, notices or checklists; it is kept
    (truncated, with a SHA-256 of the full text) in the audit trail as 'agent_reply_not_used'.
    Requires WXO_API_KEY, WXO_INSTANCE_URL, WXO_AGENT_ID.
    """
    key, url, agent = (os.environ.get(k) for k in ("WXO_API_KEY", "WXO_INSTANCE_URL", "WXO_AGENT_ID"))
    if not (key and url and agent):
        return {"status": "NOT CONFIGURED (watsonx Orchestrate credentials not set)"}
    import requests
    try:
        iam = requests.post("https://iam.cloud.ibm.com/identity/token",
                            data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": key},
                            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
        if iam.status_code != 200:
            return {"status": f"FAILED (IAM HTTP {iam.status_code})"}
        r = requests.post(f"{url.rstrip('/')}/v1/orchestrate/{agent}/chat/completions",
                          headers={"Authorization": f"Bearer {iam.json()['access_token']}",
                                   "Content-Type": "application/json", "Accept": "application/json"},
                          json={"messages": [{"role": "user", "content": json.dumps(payload)}], "stream": False},
                          timeout=45)
        if r.status_code != 200:
            return {"status": f"FAILED (HTTP {r.status_code})"}
        body = r.json()
        content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
        text = content if isinstance(content, str) else ("" if content is None else json.dumps(content))
        return {"status": "COMPLETED (pass-through; agent reply not used)",
                "run_id": body.get("run_id"), "thread_id": body.get("thread_id"), "model": body.get("model"),
                "gate": ORCHESTRATE_PASS_THROUGH_GATE,
                "agent_reply_not_used": text[:AGENT_REPLY_AUDIT_CHARS],
                "agent_reply_sha256": sha256(text)}
    except Exception as e:
        return {"status": f"FAILED ({type(e).__name__})"}

@app.route('/demo')
@app.route('/video')
def demo_video_page():
    return app.send_static_file('demo.html')

@app.route('/')
@app.route('/cobol')
@app.route('/simulation')
@app.route('/workstation')
def index():
    tenant_id = request.args.get("tenant", "FEMA_SCENARIO_1_SBA")
    if tenant_id not in TENANTS:
        tenant_id = "FEMA_SCENARIO_1_SBA"
    return render_template("index.html", case=TENANTS[tenant_id], tenant_id=tenant_id, disclaimer=DECISION_SUPPORT_DISCLAIMER)

@app.route("/analyze", methods=["POST"])
def analyze():
    tenant_id = request.form.get("tenant_id", "FEMA_SCENARIO_1_SBA")
    if tenant_id not in TENANTS:
        tenant_id = "FEMA_SCENARIO_1_SBA"
    case_data = TENANTS[tenant_id]
    
    model_id = "FIXTURE (no live model call)"
    try:
        from ibm_watsonx_client import WatsonxClient
        import os
        api_key = os.environ.get("IBM_CLOUD_API_KEY")
        project_id = os.environ.get("IBM_WATSONX_PROJECT_ID")
        
        if api_key and project_id:
            client = WatsonxClient(api_key=api_key, project_id=project_id)
            system_prompt = (
                "You are a federal benefits compliance analyst providing decision support to a human reviewer. "
                "You must respond with ONLY a single valid JSON object and no other text, no markdown, no code fences."
            )
            prompt = PROMPT_TEMPLATE.format(tenant_id=tenant_id) + "\n\n" + json.dumps(case_data, indent=2) + """
            
Respond in this exact JSON format:
{
  "recommendation": "APPROVE / DENY / ESCALATE / REVIEW",
  "confidence_band": "LOW / MEDIUM / HIGH",
  "statutory_basis": "cite the specific CFR section",
  "reasoning": "2-3 sentence explanation",
  "missing_evidence": ["list any missing documents"]
}
"""
            infer_result = client.chat(prompt, system_prompt=system_prompt, max_tokens=600, temperature=0.0)
            if infer_result["status"] == "success":
                try:
                    import re
                    text = infer_result["generated_text"]
                    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
                    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
                    result = json.loads(match.group() if match else text)
                    result["_source"] = "LIVE"
                    result["model_id"] = infer_result.get("model_id") or client.model_id
                    model_id = result["model_id"]
                    source = "IBM_WATSONX_LIVE"
                except Exception:
                    result = load_fixture(tenant_id)
                    source = "FIXTURE_JSON_PARSE_ERROR"
            else:
                result = load_fixture(tenant_id)
                source = f"FIXTURE_API_ERROR_{infer_result.get('http_code', 'UNKNOWN')}"
        else:
            result = load_fixture(tenant_id)
            source = "FIXTURE_MISSING_CREDENTIALS"
    except Exception as e:
        result = load_fixture(tenant_id)
        source = f"FIXTURE_EXCEPTION_{str(e)[:20]}"
    
    # Run Deterministic Python Rules Engine
    auditor = DeterministicAuditor(case_data, tenant_id=tenant_id)
    deterministic_result = auditor.audit()
    
    if "flags" not in result:
        result["flags"] = []
    
    result["deterministic_flags"] = deterministic_result["flags"]
    result["deterministic_status"] = deterministic_result["status"]
    result["hard_lock"] = deterministic_result.get("hard_lock")
    result["rule_pack_version"] = deterministic_result.get("rule_pack_version")
    if deterministic_result["status"] in ("ESCALATE", "INPUT_INVALID"):
        result["recommendation"] = "ESCALATE"

    # A new analysis starts a new audit record: drop any previous case's review so exports never mix cases.
    for stale in ("reviewer_initials", "reviewer_action", "reviewer_reason", "reviewed_at", "rationale_hash",
                  "wxo", "assigned_queue", "gatekeeper_status", "orchestrate_status", "cloudant_doc_id", "cloudant_rev",
                  "supervisor_override", "action_plan_ids"):
        session.pop(stale, None)

    citation = result.get("statutory_basis", "")
    citation_check = validate_citation(citation, tenant_id)
    result["citation_check"] = citation_check
    result["disclaimer"] = DECISION_SUPPORT_DISCLAIMER
    result["case_id"] = case_data["case_id"]

    session["analysis"] = result
    session["result_hash"] = sha256(json.dumps(result, sort_keys=True))
    session["prompt_hash"] = sha256(PROMPT_TEMPLATE)
    session["source"] = source
    session["tenant_id"] = tenant_id
    session["case_data"] = case_data
    session["analyzed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return render_template(
        "result.html", result=result, citation_check=citation_check, source=source,
        disclaimer=DECISION_SUPPORT_DISCLAIMER, case=case_data, tenant_id=tenant_id
    )

def _gatekeeper(analysis):
    """Deterministic gatekeeper. Fails closed: if the engine status is missing or INPUT_INVALID,
    or if any flag is not explicitly non-locking, approval is hard-locked.
    Returns (hard_lock, gatekeeper_status, det_status, det_flags)."""
    det_flags = analysis.get("deterministic_flags") or []
    det_status = analysis.get("deterministic_status")
    hard_lock = (
        (det_status in ("ESCALATE", "INPUT_INVALID") and approval_locked(det_flags))
        or (det_status == "INPUT_INVALID" and not det_flags)
        or (det_status is None and bool(det_flags))
    )
    if hard_lock:
        return True, f"HARD LOCK ENGAGED — PRE-COMMIT VETO ({len(det_flags)} deterministic flag(s))", det_status, det_flags
    if det_flags:
        return False, f"CLEAR — no blocking violations ({len(det_flags)} advisory flag(s))", det_status, det_flags
    return False, "CLEAR — no deterministic rule violations", det_status, det_flags

def _form_checked(value):
    return (value or "").strip().lower() in ("on", "yes", "true", "1")

@app.route("/review", methods=["POST"])
def review():
    initials = request.form.get("reviewer_initials", "").strip()
    action = request.form.get("reviewer_action", "").strip()
    reason = request.form.get("reviewer_reason", "").strip()
    
    tenant_id = session.get("tenant_id", "FEMA_SCENARIO_1_SBA")
    case_data = session.get("case_data", TENANTS["FEMA_SCENARIO_1_SBA"])

    if not session.get("analysis"):
        # Session expired or analysis never ran: never render a receipt without an analysis.
        return render_template("index.html", case=TENANTS[tenant_id] if tenant_id in TENANTS else TENANTS["FEMA_SCENARIO_1_SBA"],
                               tenant_id=tenant_id, disclaimer=DECISION_SUPPORT_DISCLAIMER), 409

    errors = []
    if not initials or action not in ("APPROVE_FOR_REVIEW", "RETURN", "ESCALATE") or not reason:
        errors.append("All fields (Initials, Action, Reason) are required to generate the audit receipt.")

    def rerender(errs, status=200):
        return render_template("result.html", result=session.get("analysis", {}),
                               citation_check=session.get("analysis", {}).get("citation_check", {}),
                               source=session.get("source", "FIXTURE"), disclaimer=DECISION_SUPPORT_DISCLAIMER,
                               case=case_data, tenant_id=tenant_id, errors=errs, form_data=request.form), status

    if errors:
        return rerender(errors)

    # Deterministic gatekeeper. Computed BEFORE any side effect (webhook, agent, persistence) so a blocked
    # approval never leaves the building, and so the stored record matches the receipt.
    analysis = session.get("analysis", {})
    hard_lock, gatekeeper_status, det_status, det_flags = _gatekeeper(analysis)

    # HARD LOCK actually blocks: approving a locked case needs a recorded supervisor override.
    supervisor_override = None
    if hard_lock and action == "APPROVE_FOR_REVIEW":
        sup_initials = request.form.get("supervisor_initials", "").strip()
        override_reason = request.form.get("override_reason", "").strip()
        if not (_form_checked(request.form.get("supervisor_override")) and sup_initials and override_reason):
            return rerender([HARD_LOCK_APPROVAL_ERROR], 409)
        overridden_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        supervisor_override = {
            "supervisor_initials": sup_initials[:10],
            "override_reason": override_reason[:1000],
            "overridden_at_utc": overridden_at,
            "reviewer_decision": action,
            "gatekeeper_status_at_override": gatekeeper_status,
            "override_sha256": sha256(f"{sup_initials[:10]}:{action}:{override_reason[:1000]}:{overridden_at}"),
        }

    session["reviewer_initials"] = initials
    session["reviewer_action"] = action
    session["reviewer_reason"] = reason
    session["reviewed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    session["gatekeeper_status"] = gatekeeper_status
    session["supervisor_override"] = supervisor_override
    
    rationale_hash = sha256(f"{initials}:{action}:{reason}")
    session["rationale_hash"] = rationale_hash
    # Not yet provisioned in this demo: shown as architecture design targets, never as committed records.
    NOT_PROVISIONED = "DESIGN TARGET — not provisioned in this demo"
    factsheet_id = f"FS-2026-{case_data['case_id']} ({NOT_PROVISIONED})"
    cos_vault_uri = f"cos://asce-regulatory-evidence-vault/{case_data['case_id']}.worm ({NOT_PROVISIONED})"
    db2_tx_id = f"TX-{sha256(case_data['case_id'] + session.get('analyzed_at',''))[:12].upper()} ({NOT_PROVISIONED})"
    
    # Caseworker action webhook. Status reflects the real HTTP result.
    orchestrate_webhook = os.environ.get("ORCHESTRATE_WEBHOOK_URL")
    if orchestrate_webhook:
        import requests
        try:
            w = requests.post(orchestrate_webhook, json={"case_id": case_data["case_id"], "action": action}, timeout=30)  # allows for scale-from-zero cold start (observed >15s)
            if 200 <= w.status_code < 300:
                orchestrate_status = f"DELIVERED (HTTP {w.status_code}) to ASCE action webhook"
            else:
                orchestrate_status = f"FAILED (HTTP {w.status_code})"
        except Exception as e:
            orchestrate_status = f"FAILED ({type(e).__name__})"
    else:
        orchestrate_status = "NOT CONFIGURED (no webhook URL set)"
    session["orchestrate_status"] = orchestrate_status

    # Queue, next step, applicant notice and checklist: fixed templates keyed by the HUMAN decision and the
    # engine flags (templates.py). No model writes any of it.
    assigned_queue = DECISION_QUEUES[action]
    action_plan = build_action_plan(action, det_flags, tenant_id, case_data,
                                    supervisor_override=supervisor_override is not None)
    session["assigned_queue"] = assigned_queue
    session["action_plan_ids"] = {"template_version": action_plan["template_version"],
                                  "template_ids": action_plan["template_ids"]}

    # watsonx Orchestrate agent in PASS-THROUGH mode: it receives the deterministic result only.
    wxo = invoke_orchestrate_agent({
        "case_id": case_data["case_id"],
        "tenant_id": tenant_id,
        "program": case_data.get("label", tenant_id),
        "reviewer_decision": action,
        "assigned_queue": assigned_queue,
        "required_next_step": DECISION_NEXT_STEPS[action],
        "deterministic_status": det_status,
        "deterministic_flag_types": [f.get("type") for f in det_flags],
        "gatekeeper_status": gatekeeper_status,
        "supervisor_override": supervisor_override is not None,
        "template_version": action_plan["template_version"],
        "template_ids": action_plan["template_ids"],
        "mode": "PASS_THROUGH_NO_AGENT_TEXT_USED",
    })
    session["wxo"] = {"status": wxo.get("status"), "run_id": wxo.get("run_id"), "model": wxo.get("model"),
                      "gate": wxo.get("gate"),
                      "agent_reply_not_used": (wxo.get("agent_reply_not_used") or "")[:AGENT_REPLY_SESSION_CHARS] or None,
                      "agent_reply_sha256": wxo.get("agent_reply_sha256")}

    # Live IBM Cloudant Persistence: only reports a revision if the write actually succeeded.
    cloudant_doc_id = f"case-{case_data['case_id']}"
    cloudant_rev = "NOT WRITTEN (Cloudant not configured)"
    cloudant_url = os.environ.get("IBM_CLOUDANT_URL")
    cloudant_key = os.environ.get("IBM_CLOUDANT_API_KEY")
    if cloudant_url and cloudant_key:
        cloudant_rev = "NOT WRITTEN (write failed)"
        try:
            import requests
            iam_resp = requests.post("https://iam.cloud.ibm.com/identity/token", 
                data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": cloudant_key},
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=5)
            if iam_resp.status_code == 200:
                c_token = iam_resp.json()["access_token"]
                c_headers = {"Authorization": f"Bearer {c_token}", "Content-Type": "application/json"}
                doc_payload = {
                    "_id": cloudant_doc_id,
                    "case_id": case_data["case_id"],
                    "tenant_id": tenant_id,
                    "reviewer_initials": initials,
                    "reviewer_action": action,
                    "reviewer_reason": reason,
                    "reviewed_at_utc": session["reviewed_at"],
                    "rationale_hash": rationale_hash,
                    "result_hash": session.get("result_hash"),
                    "prompt_hash": session.get("prompt_hash"),
                    "source": session.get("source"),
                    "assigned_queue": assigned_queue,
                    "action_plan": action_plan,
                    "supervisor_override": supervisor_override,
                    "orchestrate_status": wxo.get("status"),
                    "orchestrate_run_id": wxo.get("run_id"),
                    "orchestrate_gate": wxo.get("gate"),
                    "orchestrate_agent_reply_not_used": wxo.get("agent_reply_not_used"),
                    "orchestrate_agent_reply_sha256": wxo.get("agent_reply_sha256"),
                    "gatekeeper_status": gatekeeper_status,
                    "deterministic_status": det_status,
                    "deterministic_flags": det_flags,
                }
                g_res = requests.get(f"{cloudant_url}/asce_audit_dockets/{cloudant_doc_id}", headers=c_headers, timeout=3)
                if g_res.status_code == 200:
                    doc_payload["_rev"] = g_res.json()["_rev"]
                p_res = requests.put(f"{cloudant_url}/asce_audit_dockets/{cloudant_doc_id}", json=doc_payload, headers=c_headers, timeout=5)
                if p_res.status_code in (200, 201):
                    cloudant_rev = "COMMITTED rev " + p_res.json().get("rev", "?")
                else:
                    cloudant_rev = f"NOT WRITTEN (HTTP {p_res.status_code})"
            else:
                cloudant_rev = f"NOT WRITTEN (IAM HTTP {iam_resp.status_code})"
        except Exception as e:
            cloudant_rev = f"NOT WRITTEN ({type(e).__name__})"
    session["cloudant_doc_id"] = cloudant_doc_id
    session["cloudant_rev"] = cloudant_rev

    # The receipt only ever sees agent call metadata, never agent-written text.
    wxo_display = {k: wxo.get(k) for k in ("status", "run_id", "model", "gate")}
    return render_template("receipt.html", case=case_data, result=analysis,
                           reviewer_initials=initials, reviewer_action=action, reviewer_reason=reason,
                           analyzed_at=session.get("analyzed_at"), reviewed_at=session["reviewed_at"],
                           result_hash=session.get("result_hash"), prompt_hash=session.get("prompt_hash"),
                           rationale_hash=rationale_hash, factsheet_id=factsheet_id,
                           cos_vault_uri=cos_vault_uri, db2_tx_id=db2_tx_id,
                           cloudant_doc_id=cloudant_doc_id, cloudant_rev=cloudant_rev,
                           source=session.get("source", "FIXTURE_MISSING_CREDENTIALS"), disclaimer=DECISION_SUPPORT_DISCLAIMER,
                           tenant_id=tenant_id, orchestrate_status=orchestrate_status,
                           wxo=wxo_display, assigned_queue=assigned_queue, gatekeeper_status=gatekeeper_status,
                           action_plan=action_plan, supervisor_override=supervisor_override)

@app.route("/export", methods=["POST"])
def export():
    if not session.get("reviewed_at"):
        return jsonify({"error": "No reviewed case in this session. Run an analysis and complete human review first."}), 409
    case_data = session.get("case_data", {})
    analysis = session.get("analysis", {})
    not_provisioned = "DESIGN TARGET — not provisioned in this demo"
    # Templates are deterministic, so re-rendering from the stored inputs reproduces the receipt exactly.
    action_plan = None
    if session.get("reviewer_action") in DECISION_QUEUES:
        action_plan = build_action_plan(session["reviewer_action"], analysis.get("deterministic_flags") or [],
                                        session.get("tenant_id"), case_data,
                                        supervisor_override=session.get("supervisor_override") is not None)
    receipt = {
        "_stamp": "SYNTHETIC DEMONSTRATION — NOT A SYSTEM RECORD",
        "disclaimer": DECISION_SUPPORT_DISCLAIMER,
        "tenant_id": session.get("tenant_id"),
        "case_id": case_data.get("case_id"),
        "model_source": session.get("source"),
        "model_id": analysis.get("model_id"),
        "analyzed_at_utc": session.get("analyzed_at"),
        "reviewed_at_utc": session.get("reviewed_at"),
        "reviewer_action": session.get("reviewer_action"),
        "reviewer_initials": session.get("reviewer_initials"),
        "reviewer_reason": session.get("reviewer_reason"),
        "assigned_queue": session.get("assigned_queue"),
        "deterministic_gatekeeper": session.get("gatekeeper_status"),
        "supervisor_override": session.get("supervisor_override"),
        "action_plan": action_plan,
        "watsonx_orchestrate_agent": session.get("wxo"),
        "action_webhook_status": session.get("orchestrate_status"),
        "ibm_cloudant": {"doc_id": session.get("cloudant_doc_id"), "status": session.get("cloudant_rev")},
        "watsonx_governance_factsheet": not_provisioned,
        "ibm_cos_worm_vault": not_provisioned,
        "ibm_db2_ledger": not_provisioned,
        "cryptographic_hashes": {
            "prompt_template_sha256": session.get("prompt_hash"),
            "model_response_sha256": session.get("result_hash"),
            "supervisor_rationale_sha256": session.get("rationale_hash")
        }
    }
    return jsonify(receipt)

# Stateless, deterministic-only bulk JSON API (/api/v1/*). Dependencies are injected to avoid a circular import.
from surge_api import create_surge_blueprint  # noqa: E402
app.register_blueprint(create_surge_blueprint(
    tenants={**dict.fromkeys(ADAPTER_TENANTS), **TENANTS},
    validate_citation=validate_citation,
    decision_queues=DECISION_QUEUES,
    disclaimer=DECISION_SUPPORT_DISCLAIMER))


# ---------------------------------------------------------------------------
# Mainframe, Vision AI & Federal Smartcard Authentication Extensions
# ---------------------------------------------------------------------------
mainframe_adapter = MainframeAdapter(mq_config={
    "queue_manager": "QM1",
    "channel": "SYSTEM.DEF.SVRCONN",
    "host": "localhost",
    "port": 1414,
    "queue_name": "ASCE.AUDIT.QUEUE"
})

fed_auth_manager = FederalAuthenticationManager(
    sp_entity_id="https://asce-govben.agency.gov/saml",
    protocol="SAML"
)

vision_client = VisionAIClient()

def generate_sample_signature_image(has_signature=True):
    img = np.ones((400, 700, 3), dtype=np.uint8) * 255
    cv2.putText(img, "OFFICIAL CLAIM EVIDENCE VERIFICATION", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (40, 40, 40), 2)
    cv2.putText(img, "Form FEMA-010-0-11 (Individual Assistance Certification)", (50, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (90, 90, 90), 1)
    cv2.putText(img, "I declare under penalty of perjury that all statements submitted are true.", (50, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (70, 70, 70), 1)
    cv2.rectangle(img, (50, 240), (650, 385), (210, 210, 210), 1)
    cv2.putText(img, "APPLICANT SIGNATURE (PHYSICAL WET-INK REQUIRED):", (60, 265), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1)
    cv2.line(img, (70, 360), (630, 360), (160, 160, 160), 1)
    
    if has_signature:
        # Stroke 1: First name cursive
        pts1 = [(90, 350), (120, 330), (140, 355), (170, 335), (200, 355)]
        for i in range(len(pts1) - 1):
            cv2.line(img, pts1[i], pts1[i+1], (180, 40, 20), 3)

        # Stroke 2: Last name cursive
        pts2 = [(240, 350), (270, 325), (295, 360), (325, 335), (355, 350), (390, 325), (420, 355)]
        for i in range(len(pts2) - 1):
            cv2.line(img, pts2[i], pts2[i+1], (180, 40, 20), 3)

        # Stroke 3: Flourish cross
        cv2.line(img, (110, 330), (210, 330), (180, 40, 20), 2)

        # Stroke 4: Date notation
        pts3 = [(480, 355), (510, 335), (540, 355)]
        for i in range(len(pts3) - 1):
            cv2.line(img, pts3[i], pts3[i+1], (180, 40, 20), 2)
        
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()

def _build_mainframe_audit_payload(case_data, analysis):
    gatekeeper_status = session.get("gatekeeper_status") or ""
    defects = []
    for flag in analysis.get("deterministic_flags") or []:
        flag_type = str(flag.get("type") or "ERR")
        severity = "HIGH" if flag_type not in NON_LOCKING_FLAG_TYPES else "ADVISORY"
        rule_ref = str(flag.get("citation") or flag.get("policy_id") or "")
        msg = str(flag.get("message") or flag_type)
        summary = f"{rule_ref}: {msg}" if rule_ref else msg
        defects.append({
            "code": flag_type[:30],
            "rule_id": rule_ref,
            "severity": severity,
            "message": summary[:28],
        })
    return {
        "case_id": case_data.get("case_id", "UNKNOWN"),
        "status": session.get("reviewer_action", "CLEAR"),
        "risk_score": 150 if gatekeeper_status.startswith("CLEAR") else 850,
        "escalation_required": gatekeeper_status.startswith("HARD"),
        "evidence_hash": (session.get("result_hash") or ("0" * 32))[:32],
        "defects": defects,
    }


@app.route("/export/cobol", methods=["POST"])
def export_cobol():
    if not session.get("reviewed_at"):
        return jsonify({"error": "No reviewed case in this session. Run an analysis and complete human review first."}), 409
    case_data = session.get("case_data", {})
    analysis = session.get("analysis", {})

    audit_result = _build_mainframe_audit_payload(case_data, analysis)
    converted = mainframe_adapter.convert_audit_result(audit_result)
    fixed_width = converted["mainframe_formats"]["audit_record"]["fixed_width"]

    return Response(
        fixed_width,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename=COBOL_{case_data.get('case_id', 'REC')}.dat"}
    )


@app.route("/export/copybook", methods=["GET", "POST"])
def export_copybook():
    gen = COBOLCopybookGenerator()
    cb = gen.generate_copybook_definition("AUDIT_RESULT")
    return jsonify({"record_type": "AUDIT_RESULT", "copybook": cb})


@app.route("/export/mq", methods=["POST"])
def export_mq():
    if not session.get("reviewed_at"):
        return jsonify({"error": "No reviewed case in this session. Run an analysis and complete human review first."}), 409
    case_data = session.get("case_data", {})
    analysis = session.get("analysis", {})

    audit_result = _build_mainframe_audit_payload(case_data, analysis)
    mq_result = mainframe_adapter.send_to_mainframe(audit_result, method="mq")
    raw_details = dict(mq_result.get("mq_result") or {})
    if mainframe_adapter.mq_adapter:
        raw_details.setdefault("queue_manager", mainframe_adapter.mq_adapter.queue_manager)
        raw_details.setdefault("queue_name", mainframe_adapter.mq_adapter.queue_name)
    succeeded = bool(mq_result.get("success"))
    simulated = bool(raw_details.get("simulated", True))
    if succeeded and not simulated:
        msg = (
            f"Message transmitted to IBM MQ "
            f"({raw_details.get('queue_manager', 'QM1')}/{raw_details.get('queue_name', 'ASCE.AUDIT.QUEUE')})."
        )
    elif succeeded:
        msg = "Simulated IBM MQ preview only (pymqi not active; no live MQ put)."
    else:
        err_text = raw_details.get("error") or mq_result.get("error") or "Not connected to MQ"
        msg = f"IBM MQ transmission failed: {err_text} (no live MQ connection in this runtime)."
    return jsonify({
        "status": "SUCCESS" if succeeded else "FAILED",
        "simulated": simulated,
        "mq_connected": succeeded and not simulated,
        "method": mq_result.get("method"),
        "details": raw_details,
        "message": msg,
    })

@app.route("/api/verify_signature", methods=["POST"])
def verify_signature():
    sample_type = request.form.get("sample_type", "signed")
    uploaded_file = request.files.get("document")
    doc_label = "Form 010-0-11 Page 4"
    
    if uploaded_file and uploaded_file.filename:
        img_bytes = uploaded_file.read()
        doc_label = f"Uploaded Document ({uploaded_file.filename})"
    elif sample_type == "blank":
        img_bytes = generate_sample_signature_image(has_signature=False)
    else:
        img_bytes = generate_sample_signature_image(has_signature=True)
        
    res = vision_client.detect_ink_signature(img_bytes)
    b64_img = base64.b64encode(img_bytes).decode("utf-8")
    
    detected = bool(res.get("signature_detected", False))
    printed_filtered = int(res.get("printed_glyphs_filtered", 0))
    defect_code = "CLEARED_WET_INK" if detected else "DEFECT_MISSING_SIGNATURE"
    if detected:
        remedy = f"Wet-ink cursive signature verified ({res.get('stroke_count', 0)} continuous pen strokes). {doc_label} accepted."
    elif printed_filtered > 0:
        remedy = (f"No physical wet-ink signature found on {doc_label} ({printed_filtered} machine-printed typography glyphs filtered out; "
                  f"0 cursive pen strokes detected). Defect code DET-004 logged and queued on caseworker workbench.")
    else:
        remedy = f"Missing signature detected on {doc_label}. Defect code DET-004 logged and queued on caseworker workbench to prevent premature statutory denial."
              
    return jsonify({
        "success": True,
        "signature_detected": detected,
        "confidence": res.get("confidence", 0.0),
        "ink_density": res.get("ink_density", 0.0),
        "stroke_count": res.get("stroke_count", 0),
        "printed_glyphs_filtered": printed_filtered,
        "defect_code": defect_code,
        "remedy": remedy,
        "image_preview": f"data:image/png;base64,{b64_img}"
    })

@app.route("/api/auth/smartcard", methods=["POST"])
def auth_smartcard():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form
    pin = str(payload.get("pin") or "")
    card_type = str(payload.get("card_type") or "PIV").strip().upper()

    auth_res = fed_auth_manager.authenticate_caseworker(pin=pin, card_type=card_type)
    if auth_res.get("success"):
        session["fed_user"] = auth_res["user_attributes"]
        session["fed_assurance"] = auth_res["assurance_level"]
        session["fed_token"] = auth_res["session_token"]
    else:
        for key in ("fed_user", "fed_assurance", "fed_token"):
            session.pop(key, None)
    return jsonify(auth_res)


@app.route("/api/auth/status", methods=["GET"])
def auth_status():
    token = session.get("fed_token")
    validation = fed_auth_manager.validate_session(token) if token else {"valid": False}
    if not validation.get("valid"):
        for key in ("fed_user", "fed_assurance", "fed_token"):
            session.pop(key, None)
        return jsonify({
            "authenticated": False,
            "simulated": False,
            "real_idp_verified": False,
            "simulation_enabled": fed_auth_manager.simulation_enabled,
            "user": None,
            "error": validation.get("error") if token else None,
        })
    user_attrs = dict(validation.get("user_attributes") or {})
    user_attrs["assurance_level"] = validation.get("assurance_level")
    return jsonify({
        "authenticated": True,
        "simulated": bool(validation.get("simulated", True)),
        "real_idp_verified": bool(validation.get("real_idp_verified", False)),
        "warning": validation.get("warning"),
        "user": user_attrs,
    })


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    token = session.pop("fed_token", None)
    session.pop("fed_user", None)
    session.pop("fed_assurance", None)
    res = fed_auth_manager.logout(token) if token else {
        "success": True,
        "message": "No active session",
        "simulated": fed_auth_manager.simulation_enabled,
        "real_idp_verified": False,
    }
    return jsonify({"authenticated": False, **res})

# ============================================================================
# STEEL-WALL GUARDRAIL: PROHIBIT AUTONOMOUS CITIZEN CORRESPONDENCE (OMB M-24-10)
# ============================================================================
PROHIBITED_OUTBOUND_CORRESPONDENCE_PHRASES = (
    "instant upload link",
    "mobile upload link",
    "automated applicant cure",
    "proactive mobile cure",
    "automated pre-intake cure",
    "instant cure notification",
    "portal-against-portal",
)

@app.after_request
def enforce_hitl_correspondence_guardrail(response):
    """Hard runtime guardrail ensuring zero autonomous citizen/portal correspondence claims leave the gateway."""
    if (not getattr(response, "direct_passthrough", False)) and response.content_type and ("text/html" in response.content_type or "application/json" in response.content_type):
        body = response.get_data(as_text=True)
        lower_body = body.lower()
        modified = False
        for banned in PROHIBITED_OUTBOUND_CORRESPONDENCE_PHRASES:
            if banned in lower_body:
                import re
                body = re.sub(re.escape(banned), "caseworker workbench review queue", body, flags=re.IGNORECASE)
                lower_body = body.lower()
                modified = True
        if modified:
            response.set_data(body)
    return response

from validation_routes import validation_bp
app.register_blueprint(validation_bp)

# ============================================================================
# DOCKED LEGACY WORKSTATION + IBM BOB CASEWORKER CO-PILOT SIDECAR (/copilot)
# ============================================================================
from bob_conversational_demo import (
    LEGACY_AGENCY_CASES,
    audit_case as _bob_audit_case,
    bob_health as _bob_health,
    chat as _bob_chat,
    legacy_sync as _bob_legacy_sync,
)


@app.route("/static/ASCE_GovBen_Walkthrough_Todd_Voice.mp4", methods=["GET", "HEAD"])
def redirect_govben_walkthrough_video():
    from flask import redirect, send_from_directory

    local_path = os.path.join(app.static_folder or "static", "ASCE_GovBen_Walkthrough_Todd_Voice.mp4")
    if os.path.isfile(local_path):
        return send_from_directory(app.static_folder, "ASCE_GovBen_Walkthrough_Todd_Voice.mp4", mimetype="video/mp4")
    return redirect("https://ascetech.org/wp-content/uploads/2026/11/ASCE_GovBen_Walkthrough_Todd_Voice.mp4", code=302)


@app.route("/static/ASCE_3Min_Demo_Video.mp4", methods=["GET", "HEAD"])
def redirect_ell_judge_video():
    from flask import redirect, send_from_directory

    local_path = os.path.join(app.static_folder or "static", "ASCE_3Min_Demo_Video.mp4")
    if os.path.isfile(local_path):
        return send_from_directory(app.static_folder, "ASCE_3Min_Demo_Video.mp4", mimetype="video/mp4")
    return redirect(
        "https://github.com/tbullitt18-collab/asce-technologies/raw/master/ibm-skillsbuild-ell-2026/ASCE_3Min_Demo_Video.mp4",
        code=302,
    )


@app.route("/copilot", methods=["GET"])
def copilot_workstation():
    return render_template("bob_chat.html", agencies=LEGACY_AGENCY_CASES)


@app.route("/api/bob/legacy_sync", methods=["POST"])
def app_bob_legacy_sync():
    return _bob_legacy_sync()


@app.route("/api/bob/chat", methods=["POST"])
def app_bob_chat():
    return _bob_chat()


@app.route("/api/bob/audit", methods=["POST"])
def app_bob_audit():
    return _bob_audit_case()


@app.route("/api/bob/health", methods=["GET"])
def app_bob_health():
    return _bob_health()


if __name__ == "__main__":
    app.run(debug=False, port=5000)
