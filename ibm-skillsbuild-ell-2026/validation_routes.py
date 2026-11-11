"""Synthetic validation portal with deterministic, server-verifiable receipts."""

import hashlib
import io
import json
import time
from collections import Counter
from datetime import date, datetime, timezone

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from flask import Blueprint, jsonify, render_template, request, send_file

from compliance_engine import (
    DEFAULT_STALL_IDLE_DAYS,
    DEFAULT_STALL_IDLE_DAYS_HOUSING_AT_RISK,
    DEFAULT_STALL_IDLE_DAYS_VULNERABLE,
    DeterministicAuditor,
    RULE_PACK_VERSION,
)
from mainframe_adapter import COBOLCopybookGenerator


validation_bp = Blueprint("validation_bp", __name__)

BENCHMARK_CASE_COUNT = 5000
BENCHMARK_AS_OF = date(2026, 11, 11)

PACK_CONFIGS = {
    "HUD_24_CFR": {
        "label": "HUD annual reexamination controls",
        "agency_code": "HUD_PHA",
        "tenant_id": "HUD_24_CFR",
        "sample_flag": "HUD deadline, evidence, conflict, and duplicate-request controls.",
        "defects": (
            ("annual_deadline_breach", "SLA_CRITICAL"),
            ("missing_recertification_evidence", "EVIDENCE_REQUIRED"),
            ("income_data_conflict", "DATA_CONFLICT"),
            ("duplicate_verification_request", "REDUNDANT_REQUEST_BLOCKED"),
        ),
    },
    "FEMA_44_CFR": {
        "label": "FEMA Individual Assistance controls",
        "agency_code": "FEMA_IA",
        "tenant_id": "FEMA_TITLE_44",
        "sample_flag": "FEMA appeal, occupancy, inspector, and duplicate-request controls.",
        "defects": (
            ("appeal_91_days", "SLA_CRITICAL"),
            ("occupancy_evidence_incomplete", "EVIDENCE_REQUIRED"),
            ("inspector_case_conflict", "INSPECTOR_CONFLICT"),
            ("duplicate_occupancy_request", "REDUNDANT_REQUEST_BLOCKED"),
        ),
    },
    "LIHEAP_SNAP": {
        "label": "SNAP periodic-report and income controls",
        "agency_code": "SNAP",
        "tenant_id": "SNAP_FORUM_SAR7_INCOME",
        "sample_flag": "SNAP non-recurring overtime and periodic-report controls.",
        "defects": (
            ("non_recurring_overtime", "SNAP_NON_RECURRING_OVERTIME"),
            ("periodic_report_deadline", "SLA_CRITICAL"),
            ("missing_income_evidence", "EVIDENCE_REQUIRED"),
            ("duplicate_paystub_request", "REDUNDANT_REQUEST_BLOCKED"),
        ),
    },
    "MEDICAID_42_CFR": {
        "label": "Medicaid ex-parte renewal controls",
        "agency_code": "CMS_MED",
        "tenant_id": "MEDICAID_FORUM_PHE_UNWINDING",
        "sample_flag": "Medicaid ex-parte, deadline, and evidence controls.",
        "defects": (
            ("ex_parte_not_attempted", "EX_PARTE_RENEWAL_REQUIRED"),
            ("termination_despite_wage_match", "EX_PARTE_RENEWAL_REQUIRED"),
            ("paper_packet_despite_wage_match", "EX_PARTE_RENEWAL_REQUIRED"),
            ("missing_evidence_without_wage_match", "EVIDENCE_REQUIRED"),
        ),
    },
}


def _canonical_bytes(value):
    """Return the one canonical JSON representation used by benchmark receipts."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _update_receipt(hasher, value):
    """Hash a length-framed canonical record so stream boundaries are unambiguous."""
    payload = _canonical_bytes(value)
    hasher.update(len(payload).to_bytes(8, byteorder="big", signed=False))
    hasher.update(payload)


def _base_generic_case(cfg, index):
    return {
        "case_id": f"{cfg['agency_code']}-{10000 + index}",
        "applicant": {"age": 40},
        "deadline": {
            "start_date": "2026-10-20",
            "limit_days": 30,
        },
        "evidence": {"uploaded": [], "missing": []},
        "caseworker_action": {"requesting_documents": []},
    }


def _hud_case(cfg, index, defect):
    case = _base_generic_case(cfg, index)
    case["deadline"]["limit_days"] = 365
    case["deadline"]["start_date"] = "2026-10-20"
    if defect == "annual_deadline_breach":
        case["deadline"]["start_date"] = "2025-11-10"
    elif defect == "missing_recertification_evidence":
        case["evidence"]["missing"] = ["Signed Annual Recertification"]
    elif defect == "income_data_conflict":
        case["data_conflict"] = {
            "field": "Annual household income",
            "value_a": "14200",
            "source_a": "tenant report",
            "value_b": "22900",
            "source_b": "wage verification",
        }
    elif defect == "duplicate_verification_request":
        case["evidence"]["uploaded"] = ["Income Verification"]
        case["caseworker_action"]["requesting_documents"] = ["Income Verification"]
    return case


def _fema_case(cfg, index, defect):
    case = {
        "case_id": f"{cfg['agency_code']}-{10000 + index}",
        "applicant": {"age": 40},
        "incident": {"declaration_date": "2026-07-01"},
        "residence": {
            "inspector_notes": "Damage consistent with the case",
            "structure_status": "Major damage",
        },
        "evidence": {"uploaded": [], "missing": []},
        "caseworker_action": {"requesting_documents": []},
    }
    if defect == "appeal_91_days":
        case["incident"]["appeal_date"] = "2026-08-12"
    elif defect == "occupancy_evidence_incomplete":
        case["evidence"]["missing"] = ["Signed Lease Page 4"]
    elif defect == "inspector_case_conflict":
        case["residence"]["inspector_notes"] = "Minor damage observed"
        case["residence"]["structure_status"] = "Condemned"
    elif defect == "duplicate_occupancy_request":
        case["evidence"]["uploaded"] = ["Proof of Occupancy"]
        case["caseworker_action"]["requesting_documents"] = ["Proof of Occupancy"]
    return case


def _snap_case(cfg, index, defect):
    case = _base_generic_case(cfg, index)
    case["income"] = {
        "baseline_monthly": 1850,
        "one_time_overtime_spike": 0,
        "procedural_flag": "No adverse income action pending",
    }
    if defect == "non_recurring_overtime":
        case["income"]["one_time_overtime_spike"] = 420
        case["income"]["procedural_flag"] = "Overtime projected as permanent income"
    elif defect == "periodic_report_deadline":
        case["deadline"]["start_date"] = "2026-09-01"
    elif defect == "missing_income_evidence":
        case["evidence"]["missing"] = ["Income Verification"]
    elif defect == "duplicate_paystub_request":
        case["evidence"]["uploaded"] = ["September Paystub"]
        case["caseworker_action"]["requesting_documents"] = ["September Paystub"]
    return case


def _medicaid_case(cfg, index, defect):
    case = _base_generic_case(cfg, index)
    case.pop("deadline")
    case["procedural_status"] = {
        "ex_parte_attempted": True,
        "state_wage_database_match": False,
    }
    case["caseworker_action"]["intent"] = "Continue renewal review"
    if defect == "ex_parte_not_attempted":
        case["procedural_status"] = {
            "ex_parte_attempted": False,
            "state_wage_database_match": True,
        }
        case["evidence"]["missing"] = ["Paper Renewal Packet"]
    elif defect == "termination_despite_wage_match":
        case["procedural_status"]["state_wage_database_match"] = True
        case["caseworker_action"]["intent"] = "Procedural termination"
    elif defect == "paper_packet_despite_wage_match":
        case["procedural_status"]["state_wage_database_match"] = True
        case["caseworker_action"]["requesting_documents"] = [
            "Paper Renewal Packet"
        ]
    elif defect == "missing_evidence_without_wage_match":
        case["evidence"]["missing"] = ["Income Verification"]
    return case


CASE_BUILDERS = {
    "HUD_24_CFR": _hud_case,
    "FEMA_44_CFR": _fema_case,
    "LIHEAP_SNAP": _snap_case,
    "MEDICAID_42_CFR": _medicaid_case,
}


def _verified_cobol_line(pack_key, cfg):
    """Verify local 80-column formatting and a CP037 encode/decode round-trip."""
    try:
        generator = COBOLCopybookGenerator()
        sample = {
            "case_id": f"VAL-{cfg['agency_code']}-{pack_key}",
            "audit_date": BENCHMARK_AS_OF.isoformat(),
            "audit_status": "VERIFIED",
            "risk_score": 0,
            "defect_count": len(cfg["defects"]),
            "escalation_flag": True,
            "evidence_hash": hashlib.sha256(
                f"{RULE_PACK_VERSION}:{pack_key}".encode("utf-8")
            ).hexdigest()[:32],
        }
        record = generator.json_to_fixed_width(sample, "AUDIT_RESULT")
        if len(record) != 80:
            raise RuntimeError(f"formatter returned {len(record)} columns, expected 80")
        ebcdic = generator.to_ebcdic(record, "cp037")
        if len(ebcdic) != 80:
            raise RuntimeError(f"CP037 encoding returned {len(ebcdic)} bytes, expected 80")
        if generator.from_ebcdic(ebcdic, "cp037") != record:
            raise RuntimeError("CP037 round-trip changed the fixed-width record")
        return f"80-COL ASCII+CP037 OK: {record}"
    except Exception as exc:
        raise RuntimeError(
            f"local COBOL format verification failed for {pack_key}: {exc}"
        ) from exc


def _synthetic_case(pack_key, index):
    cfg = PACK_CONFIGS[pack_key]
    defect, expected_flag = cfg["defects"][index % len(cfg["defects"])]
    case = CASE_BUILDERS[pack_key](cfg, index, defect)
    return case, defect, expected_flag


def _run_benchmark(pack_key):
    """Execute and hash every canonical input/result pair for one rule pack."""
    if pack_key not in PACK_CONFIGS:
        raise KeyError(pack_key)

    cfg = PACK_CONFIGS[pack_key]
    cobol_line = _verified_cobol_line(pack_key, cfg)
    hasher = hashlib.sha256()
    _update_receipt(
        hasher,
        {
            "rule_pack_version": RULE_PACK_VERSION,
            "pack_key": pack_key,
            "case_count": BENCHMARK_CASE_COUNT,
            "as_of": BENCHMARK_AS_OF.isoformat(),
        },
    )
    _update_receipt(hasher, {"local_format_verification": cobol_line})
    injected = Counter()
    fired = Counter()
    flag_counts = Counter()
    exceptions = 0
    schema_failures = 0
    started = time.perf_counter()

    for index in range(BENCHMARK_CASE_COUNT):
        case, defect, expected_flag = _synthetic_case(pack_key, index)
        result = DeterministicAuditor(
            case,
            tenant_id=cfg["tenant_id"],
            as_of=BENCHMARK_AS_OF,
            stall_idle_days=DEFAULT_STALL_IDLE_DAYS,
            housing_stall_idle_days=DEFAULT_STALL_IDLE_DAYS_HOUSING_AT_RISK,
            vulnerable_stall_idle_days=DEFAULT_STALL_IDLE_DAYS_VULNERABLE,
        ).audit()
        injected[defect] += 1
        observed_types = [flag.get("type") for flag in result.get("flags", [])]
        flag_counts.update(observed_types)
        if expected_flag in observed_types:
            fired[defect] += 1
        if result.get("status") == "INPUT_INVALID":
            schema_failures += 1
        if result.get("flags"):
            exceptions += 1
        _update_receipt(
            hasher,
            {
                "index": index,
                "defect": defect,
                "expected_flag": expected_flag,
                "input": case,
                "result": result,
            },
        )

    missed = {
        name: injected[name] - fired[name]
        for name in injected
        if injected[name] != fired[name]
    }
    if missed or schema_failures:
        raise RuntimeError(
            f"benchmark integrity failure: missed={missed}, "
            f"schema_failures={schema_failures}"
        )

    elapsed = max(time.perf_counter() - started, 0.000001)
    receipt = hasher.hexdigest()
    return {
        "reference_id": f"ASCE-VAL-{RULE_PACK_VERSION}-{receipt[:12].upper()}",
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule_pack_version": RULE_PACK_VERSION,
        "benchmark_as_of": BENCHMARK_AS_OF.isoformat(),
        "pack_key": pack_key,
        "pack_label": cfg["label"],
        "tenant_id": cfg["tenant_id"],
        "packets_evaluated": BENCHMARK_CASE_COUNT,
        "exceptions_intercepted": exceptions,
        "schema_failures": schema_failures,
        "defect_counts_injected": dict(sorted(injected.items())),
        "defect_counts_fired": dict(sorted(fired.items())),
        "flag_counts": dict(sorted(flag_counts.items())),
        "total_seconds": round(elapsed, 3),
        "per_packet_ms": round((elapsed * 1000.0) / BENCHMARK_CASE_COUNT, 4),
        "throughput_per_sec": int(BENCHMARK_CASE_COUNT / elapsed),
        "sample_flag": cfg["sample_flag"],
        "cobol_line": cobol_line,
        "sha256_receipt": receipt,
    }


@validation_bp.route("/api/health", methods=["GET"])
def api_health_check():
    return jsonify(
        {
            "status": "ok",
            "service": "asce-govben-caas",
            "version": "10.0.0-validation-portal",
            "rule_pack_version": RULE_PACK_VERSION,
            "routes": ["/", "/validate"],
        }
    )


@validation_bp.route("/validate", methods=["GET"])
def validate_portal_page():
    return render_template("validate_portal.html")


def _parse_json_object():
    """Require either an empty body or a valid JSON object payload."""
    raw = request.get_data(cache=True)
    if not raw or not raw.strip():
        return {}, None
    parsed = request.get_json(silent=True)
    if not isinstance(parsed, dict):
        return None, (jsonify({"error": "INVALID_JSON_OBJECT"}), 400)
    return parsed, None


@validation_bp.route("/api/validate/benchmark", methods=["POST"])
def api_validate_benchmark():
    data, err = _parse_json_object()
    if err:
        return err
    pack_key = data.get("pack", "HUD_24_CFR")
    if not isinstance(pack_key, str) or pack_key not in PACK_CONFIGS:
        return (
            jsonify(
                {
                    "error": "UNKNOWN_PACK",
                    "allowed_packs": sorted(PACK_CONFIGS),
                }
            ),
            400,
        )
    try:
        return jsonify(_run_benchmark(pack_key))
    except RuntimeError as exc:
        return jsonify({"error": "BENCHMARK_INTEGRITY_FAILURE", "detail": str(exc)}), 500


def _bounded_text(value, default, limit=300):
    if value is None or isinstance(value, (dict, list)):
        return default[:limit]
    text = str(value).strip()
    return (text or default)[:limit]


@validation_bp.route("/api/validate/docx", methods=["POST"])
def api_validate_docx():
    data, err = _parse_json_object()
    if err:
        return err
    raw_benchmark = data.get("benchmark")
    if raw_benchmark is not None and not isinstance(raw_benchmark, dict):
        return jsonify({"error": "INVALID_BENCHMARK_OBJECT"}), 400
    supplied = raw_benchmark if isinstance(raw_benchmark, dict) else {}
    
    # Bug 7: Fail closed on explicit null - use data["pack"] directly if present
    if "pack" in data:
        pack_key = data["pack"]
    elif "pack_key" in supplied:
        pack_key = supplied["pack_key"]
    else:
        pack_key = "HUD_24_CFR"
    
    if not isinstance(pack_key, str) or pack_key not in PACK_CONFIGS:
        return jsonify({"error": "UNKNOWN_PACK"}), 400

    supplied_receipt = supplied.get("sha256_receipt")
    if supplied_receipt is not None and not isinstance(supplied_receipt, str):
        return jsonify({"error": "INVALID_BENCHMARK_RECEIPT"}), 400

    try:
        verified = _run_benchmark(pack_key)
    except RuntimeError as exc:
        return jsonify({"error": "BENCHMARK_INTEGRITY_FAILURE", "detail": str(exc)}), 500

    if supplied_receipt and supplied_receipt != verified["sha256_receipt"]:
        return (
            jsonify(
                {
                    "error": "BENCHMARK_RECEIPT_MISMATCH",
                    "server_sha256_receipt": verified["sha256_receipt"],
                }
            ),
            409,
        )

    organization = _bounded_text(
        data.get("organization"),
        "[Agency / Authority / Institute Name]",
    )
    reviewer = _bounded_text(data.get("reviewer"), "[Printed Name & Official Title]")
    contact = _bounded_text(data.get("contact"), "[Official Email & Phone]")
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    document = Document()
    for section in document.sections:
        section.top_margin = Inches(0.55)
        section.bottom_margin = Inches(0.55)
        section.left_margin = Inches(0.65)
        section.right_margin = Inches(0.65)

    header = document.add_paragraph()
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = header.add_run("[PLACE ON OFFICIAL AGENCY LETTERHEAD]")
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor(100, 116, 139)

    title = document.add_paragraph()
    run = title.add_run("SYNTHETIC BENCHMARK VERIFICATION RECORD")
    run.bold = True
    run.font.size = Pt(13)
    run.font.color.rgb = RGBColor(15, 41, 66)

    metadata = document.add_paragraph()
    metadata.add_run(
        f"Date: {date_str}\n"
        f"Reference ID: {verified['reference_id']}\n"
        f"Organization: {organization}\n"
        f"Rule pack: {verified['pack_label']} ({verified['rule_pack_version']})\n"
        f"Cases: {verified['packets_evaluated']} synthetic records\n"
        f"SHA-256 receipt: {verified['sha256_receipt']}"
    ).font.size = Pt(8.8)

    observations = document.add_paragraph()
    observations.add_run("Server-verified observations").bold = True
    document.add_paragraph(
        "All named injected defect classes produced their expected deterministic flag. "
        f"Schema failures: {verified['schema_failures']}. "
        f"Injected counts: {json.dumps(verified['defect_counts_injected'], sort_keys=True)}. "
        f"Fired counts: {json.dumps(verified['defect_counts_fired'], sort_keys=True)}."
    )
    document.add_paragraph(
        f"Local format verification: {verified['cobol_line']}. This verifies only local "
        "80-column ASCII generation and a CP037 encode/decode round-trip; it does not "
        "verify live mainframe, CICS, IBM MQ, or host connectivity."
    )
    document.add_paragraph(
        "This record covers synthetic deterministic evaluation only. It does not verify "
        "mainframe interoperability, identity assurance, an authorization to operate, "
        "or legal sufficiency outside the identified rule-pack version."
    )

    signature = document.add_paragraph()
    signature.paragraph_format.space_before = Pt(8)
    signature.add_run(
        "Authorized reviewer signature: ________________________________\n\n"
        f"Printed name and title: {reviewer}\n\n"
        f"Organization: {organization}\n\n"
        f"Official contact: {contact}"
    ).font.size = Pt(9)

    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    response = send_file(
        output,
        as_attachment=True,
        download_name="ASCE_Synthetic_Benchmark_Verification.docx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )
    response.headers["X-ASCE-Benchmark-SHA256"] = verified["sha256_receipt"]
    return response


@validation_bp.route("/api/validate/submit", methods=["POST"])
def api_validate_submit():
    """Acknowledge without persisting caller payload or identity data."""
    _, err = _parse_json_object()
    if err:
        return err
    return jsonify(
        {
            "status": "accepted_without_persistence",
            "message": "Wave 2 does not retain attestation payloads.",
        }
    ), 202


@validation_bp.route("/api/validate/events", methods=["GET"])
def api_validate_events():
    """Do not expose event payloads from an unauthenticated endpoint."""
    return jsonify({"error": "EVENT_FEED_DISABLED"}), 404
