"""
VA Agency Adapter
=================

Department of Veterans Affairs compliance adapter.
Implements VA-specific rules from 38 CFR (Veterans Benefits).

Plug-and-play: Can be loaded/unloaded without modifying core code.
"""

import datetime
import re
from typing import Dict, Any, List
from agency_adapter import AgencyAdapter


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class VAAdapter(AgencyAdapter):
    """
    VA (Department of Veterans Affairs) compliance adapter.
    
    Implements 38 CFR regulations for veterans benefits:
    - Disability compensation
    - Pension benefits
    - Education benefits
    - Healthcare eligibility
    """

    EVIDENCE_RESPONSE_YEARS = 1

    @staticmethod
    def _parse_date(value: Any, field: str) -> datetime.date:
        """Parse an ISO date and identify the field in fail-closed errors."""
        if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
            raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)")
        try:
            return datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc

    @staticmethod
    def _add_year(value: datetime.date) -> datetime.date:
        """Add one calendar year; a February 29 anniversary falls on February 28."""
        try:
            return value.replace(year=value.year + 1)
        except ValueError:
            return value.replace(year=value.year + 1, day=28)

    @classmethod
    def _evidence_deadline(
        cls, notice_date: datetime.date, legal_holidays
    ) -> datetime.date:
        """Apply the one-year period and § 3.110 weekend/holiday rollover."""
        deadline = cls._add_year(notice_date)
        holidays = set(legal_holidays)
        while deadline.weekday() >= 5 or deadline in holidays:
            deadline += datetime.timedelta(days=1)
        return deadline
    
    @property
    def agency_id(self) -> str:
        return "VA"
    
    @property
    def agency_name(self) -> str:
        return "Department of Veterans Affairs"
    
    @property
    def cfr_title(self) -> str:
        return "38"
    
    @property
    def valid_cfr_sections(self) -> List[str]:
        """Valid 38 CFR sections for VA programs."""
        return [
            "3.109",   # Time limits
            "3.159",   # Department of Veterans Affairs assistance in developing claims
            "3.203",   # Service records as evidence of service and character of discharge
            "3.303",   # Principles relating to service connection
            "3.304",   # Direct service connection
            "3.310",   # Ratings for service-connected disabilities
        ]
    
    def audit(self, case_data: Dict[str, Any], today: datetime.date) -> Dict[str, Any]:
        """
        Perform VA-specific compliance audit.
        
        Checks:
        1. Evidence response period following incomplete-application notice (38 CFR 3.109)
        2. DD214 service documentation requirement
        3. Service connection and duty-to-assist evidence review
        
        Args:
            case_data: Case data dictionary
            today: Current date for time-based checks
            
        Returns:
            Dictionary with flags and status
        """
        flags = []
        status = "PASS"

        if not isinstance(case_data, dict):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "case_data must be an object"}],
                "agency": self.agency_id,
            }
        raw_claim = case_data.get("claim")
        if "claim" in case_data and case_data["claim"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "claim must be an object (not null)"}],
                "agency": self.agency_id,
            }
        if raw_claim is not None and not isinstance(raw_claim, dict):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "claim must be an object"}],
                "agency": self.agency_id,
            }
        raw_evidence = case_data.get("evidence")
        if "evidence" in case_data and case_data["evidence"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "evidence must be an object (not null)"}],
                "agency": self.agency_id,
            }
        if raw_evidence is not None and not isinstance(raw_evidence, dict):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "evidence must be an object"}],
                "agency": self.agency_id,
            }
        raw_veteran = case_data.get("veteran")
        if "veteran" in case_data and case_data["veteran"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "veteran must be an object (not null)"}],
                "agency": self.agency_id,
            }
        if raw_veteran is not None and not isinstance(raw_veteran, dict):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "veteran must be an object"}],
                "agency": self.agency_id,
            }

        claim = raw_claim or {}
        evidence = raw_evidence or {}
        veteran = raw_veteran or {}

        if "submission_date" in claim:
            try:
                self._parse_date(claim["submission_date"], "claim.submission_date")
            except ValueError as exc:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": str(exc)}],
                    "agency": self.agency_id,
                }
        
        # Validate evidence.missing and evidence.uploaded element types
        if "missing" in evidence and evidence["missing"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "evidence.missing must be a list (not null)"}],
                "agency": self.agency_id,
            }
        missing_raw = evidence.get("missing")
        if missing_raw is not None:
            if not isinstance(missing_raw, list):
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": "evidence.missing must be a list"}],
                    "agency": self.agency_id,
                }
            for idx, item in enumerate(missing_raw):
                if not isinstance(item, str):
                    return {
                        "status": "INPUT_INVALID",
                        "flags": [{"type": "INPUT_INVALID", "message": f"evidence.missing[{idx}] must be a string"}],
                        "agency": self.agency_id,
                    }
        if "uploaded" in evidence and evidence["uploaded"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "evidence.uploaded must be a list (not null)"}],
                "agency": self.agency_id,
            }
        uploaded_raw = evidence.get("uploaded")
        if uploaded_raw is not None:
            if not isinstance(uploaded_raw, list):
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": "evidence.uploaded must be a list"}],
                    "agency": self.agency_id,
                }
            for idx, item in enumerate(uploaded_raw):
                if not isinstance(item, str):
                    return {
                        "status": "INPUT_INVALID",
                        "flags": [{"type": "INPUT_INVALID", "message": f"evidence.uploaded[{idx}] must be a string"}],
                        "agency": self.agency_id,
                    }
        
        missing = evidence.get("missing") or []

        pact_act_raw = veteran.get("pact_act_eligible")
        if pact_act_raw is not None and not isinstance(pact_act_raw, bool):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "veteran.pact_act_eligible must be a boolean"}],
                "agency": self.agency_id,
            }

        ext_raw = claim.get("evidence_time_extension")
        if ext_raw is not None and not isinstance(ext_raw, dict):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "claim.evidence_time_extension must be an object"}],
                "agency": self.agency_id,
            }
        extension = ext_raw or {}
        extension_approved_raw = extension.get("approved")
        if extension_approved_raw is not None and not isinstance(extension_approved_raw, bool):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "claim.evidence_time_extension.approved must be a boolean"}],
                "agency": self.agency_id,
            }
        extension_approved = extension_approved_raw is True
        extension_end_raw = extension.get("end_date")
        extension_end = None
        if extension_approved and extension_end_raw is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{
                    "type": "INPUT_INVALID",
                    "message": (
                        "claim.evidence_time_extension.end_date is required when an "
                        "evidence response extension is marked approved"
                    ),
                }],
                "agency": self.agency_id,
            }
        if extension_end_raw is not None:
            try:
                extension_end = self._parse_date(
                    extension_end_raw, "claim.evidence_time_extension.end_date"
                )
            except ValueError as exc:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": str(exc)}],
                    "agency": self.agency_id,
                }

        # Rule 1: section 3.109 runs from notice requesting evidence for an
        # incomplete application; it is not a clock from initial claim submission.
        notice_date_str = claim.get("incomplete_application_notice_date")
        if notice_date_str is not None:
            requested_after_notice = claim.get(
                "incomplete_application_requested_evidence"
            )
            holiday_values = claim.get("evidence_time_limit_holidays")
            if (
                not isinstance(requested_after_notice, list)
                or any(not isinstance(item, str) or not item.strip()
                       for item in requested_after_notice)
            ):
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{
                        "type": "INPUT_INVALID",
                        "message": (
                            "claim.incomplete_application_requested_evidence must be an "
                            "array of non-empty evidence names when an incomplete-application "
                            "notice date is present"
                        ),
                    }],
                    "agency": self.agency_id,
                }
            if not isinstance(holiday_values, list):
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{
                        "type": "INPUT_INVALID",
                        "message": (
                            "claim.evidence_time_limit_holidays must be an array of ISO "
                            "dates (use [] when no holiday affects the period)"
                        ),
                    }],
                    "agency": self.agency_id,
                }
            try:
                notice_date = self._parse_date(
                    notice_date_str, "claim.incomplete_application_notice_date"
                )
                legal_holidays = [
                    self._parse_date(value, "claim.evidence_time_limit_holidays[]")
                    for value in holiday_values
                ]
            except ValueError as exc:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": str(exc)}],
                    "agency": self.agency_id,
                }

            days_since_notice = (today - notice_date).days
            standard_deadline = self._evidence_deadline(
                notice_date, legal_holidays
            )
            if extension_approved and extension_end <= standard_deadline:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{
                        "type": "INPUT_INVALID",
                        "message": (
                            "claim.evidence_time_extension.end_date must be after the "
                            "standard evidence response deadline"
                        ),
                    }],
                    "agency": self.agency_id,
                }
            effective_deadline = (
                extension_end
                if extension_approved and extension_end is not None
                else standard_deadline
            )
            missing_keys = {" ".join(item.lower().split()) for item in missing}
            outstanding_requested = [
                item
                for item in requested_after_notice
                if " ".join(item.lower().split()) in missing_keys
            ]
            if outstanding_requested and today > effective_deadline:
                flags.append({
                    "type": "EVIDENCE_DEADLINE_REVIEW",
                    "message": (
                        "Requested evidence remains outstanding after the applicable response "
                        "period measured from the incomplete-application notice. Human review "
                        "must consider any good-cause extension under 38 CFR 3.109."
                    ),
                    "citation": "38 CFR 3.109",
                    "notice_date": notice_date.isoformat(),
                    "standard_deadline": standard_deadline.isoformat(),
                    "effective_deadline": effective_deadline.isoformat(),
                    "days_since_notice": days_since_notice,
                    "outstanding_evidence": outstanding_requested,
                    "good_cause_extension_recorded": extension_approved,
                })
                status = "ESCALATE"

        # Rule 2: DD214 service documentation requirement
        dd214_missing = any("dd214" in m.lower() for m in missing)
        if dd214_missing:
            flags.append({
                "type": "EVIDENCE_REQUIRED",
                "message": (
                    "DD214 is listed as missing. Route for evidence development and human "
                    "review; absence of this single document is not an automated denial."
                ),
                "citation": "38 CFR 3.203",
                "missing_document": "DD214",
            })
            status = "ESCALATE"

        # Rule 3: nexus evidence remains independently reviewable even when a
        # DD214 or other service records have already been uploaded.
        condition = claim.get("condition")
        nexus_missing = any("nexus" in item.lower() for item in missing)
        if nexus_missing:
            flags.append({
                "type": "DUTY_TO_ASSIST",
                "category": "EVIDENCE_REQUIRED",
                "message": (
                    "Medical nexus evidence is listed as missing. Before an adverse action, "
                    "route for duty-to-assist and presumptive-service-connection review."
                ),
                "citation": "38 CFR 3.159",
                "condition": condition,
                "missing_document": "Medical Nexus Statement",
                "pact_act_eligible": bool(
                    (case_data.get("veteran") or {}).get("pact_act_eligible")
                ),
            })
            status = "ESCALATE"
        elif condition and not evidence.get("uploaded"):
            flags.append({
                "type": "EVIDENCE_REQUIRED",
                "message": f"Service connection evidence required for condition: {condition}",
                "citation": "38 CFR 3.303",
                "condition": condition,
            })
            status = "ESCALATE"
        
        return {
            "status": status,
            "flags": flags,
            "agency": self.agency_id
        }


# Auto-register with global registry
from agency_adapter import register_adapter_class
register_adapter_class(VAAdapter)
