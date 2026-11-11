"""
FEMA Agency Adapter
===================

Federal Emergency Management Agency compliance adapter.
Implements FEMA-specific rules from 44 CFR (Disaster Assistance).

Plug-and-play: Can be loaded/unloaded without modifying core code.
"""

import datetime
import re
from typing import Dict, Any, List
from agency_adapter import AgencyAdapter, evaluate_rule_effective_window


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FEMAAdapter(AgencyAdapter):
    """
    FEMA (Federal Emergency Management Agency) compliance adapter.
    
    Implements 44 CFR regulations for disaster assistance programs:
    - Individual Assistance (IA)
    - Public Assistance (PA)
    - Hazard Mitigation
    """

    IA_EQUITY_RULE_PUBLISHED_DATE = datetime.date(2024, 1, 22)
    IA_EQUITY_RULE_EFFECTIVE_DATE = datetime.date(2024, 3, 22)

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
    def _add_months(value: datetime.date, months: int) -> datetime.date:
        """Add calendar months without approximating them as a fixed day count."""
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        month_lengths = (
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        )
        return datetime.date(year, month, min(value.day, month_lengths[month - 1]))
    
    @property
    def agency_id(self) -> str:
        return "FEMA"
    
    @property
    def agency_name(self) -> str:
        return "Federal Emergency Management Agency"
    
    @property
    def cfr_title(self) -> str:
        return "44"
    
    @property
    def valid_cfr_sections(self) -> List[str]:
        """Valid 44 CFR sections for FEMA programs."""
        return [
            "206.110",  # Individual Assistance
            "206.111",  # Duplication of benefits
            "206.113",  # Damaged property
            "206.114",  # Eligible costs
            "206.115",  # Appeals
            "206.117",  # Repair, restoration, and replacement
            "206.119",  # Temporary housing assistance
            "206.120",  # Other needs assistance
        ]
    
    def audit(self, case_data: Dict[str, Any], today: datetime.date) -> Dict[str, Any]:
        """
        Perform FEMA-specific compliance audit.
        
        Checks:
        1. 18-month IHP period and documented extension exception (44 CFR 206.110(e))
        2. 90-day appeal deadline (44 CFR 206.115(e))
        3. Pre-Equity-Rule SBA workflow only for declarations before 2024-03-22
        4. Internal ASCE senior/vulnerable population priority policy
        5. Internal data-integrity conflict review
        6. Occupancy evidence review, separate from the appeals rule
        
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
        raw_incident = case_data.get("incident")
        if "incident" in case_data and case_data["incident"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "incident must be an object (not null)"}],
                "agency": self.agency_id,
            }
        if raw_incident is not None and not isinstance(raw_incident, dict):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "incident must be an object"}],
                "agency": self.agency_id,
            }
        incident = raw_incident or {}

        # Validate applicant structure and age type
        raw_applicant = case_data.get("applicant")
        if "applicant" in case_data and case_data["applicant"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "applicant must be an object (not null)"}],
                "agency": self.agency_id,
            }
        if raw_applicant is not None and not isinstance(raw_applicant, dict):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "applicant must be an object"}],
                "agency": self.agency_id,
            }
        applicant = raw_applicant or {}
        if "age" in applicant and applicant["age"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "applicant.age must be an integer (not null)"}],
                "agency": self.agency_id,
            }
        age_raw = applicant.get("age")
        if age_raw is not None:
            if not isinstance(age_raw, int) or isinstance(age_raw, bool):
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": "applicant.age must be an integer"}],
                    "agency": self.agency_id,
                }
            if age_raw < 0 or age_raw > 130:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": "applicant.age must be between 0 and 130"}],
                    "agency": self.agency_id,
                }

        # Validate residence structure and field types
        raw_residence = case_data.get("residence")
        if "residence" in case_data and case_data["residence"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "residence must be an object (not null)"}],
                "agency": self.agency_id,
            }
        if raw_residence is not None and not isinstance(raw_residence, dict):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "residence must be an object"}],
                "agency": self.agency_id,
            }
        residence = raw_residence or {}
        if "inspector_notes" in residence and residence["inspector_notes"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "residence.inspector_notes must be a string (not null)"}],
                "agency": self.agency_id,
            }
        inspector_notes_raw = residence.get("inspector_notes")
        if inspector_notes_raw is not None and not isinstance(inspector_notes_raw, str):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "residence.inspector_notes must be a string"}],
                "agency": self.agency_id,
            }
        if "structure_status" in residence and residence["structure_status"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "residence.structure_status must be a string (not null)"}],
                "agency": self.agency_id,
            }
        structure_status_raw = residence.get("structure_status")
        if structure_status_raw is not None and not isinstance(structure_status_raw, str):
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "residence.structure_status must be a string"}],
                "agency": self.agency_id,
            }

        # Validate evidence structure and list types
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
        evidence = raw_evidence or {}
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

        extra_ext_raw = incident.get("extraordinary_circumstances_extension_approved")
        if extra_ext_raw is not None and not isinstance(extra_ext_raw, bool):
            return {
                "status": "INPUT_INVALID",
                "flags": [{
                    "type": "INPUT_INVALID",
                    "message": "incident.extraordinary_circumstances_extension_approved must be a boolean",
                }],
                "agency": self.agency_id,
            }
        ext_raw = incident.get("ihp_period_extension")
        if ext_raw is not None and not isinstance(ext_raw, dict):
            return {
                "status": "INPUT_INVALID",
                "flags": [{
                    "type": "INPUT_INVALID",
                    "message": "incident.ihp_period_extension must be an object",
                }],
                "agency": self.agency_id,
            }
        extension = ext_raw or {}
        ext_approved_raw = extension.get("approved")
        if ext_approved_raw is not None and not isinstance(ext_approved_raw, bool):
            return {
                "status": "INPUT_INVALID",
                "flags": [{
                    "type": "INPUT_INVALID",
                    "message": "incident.ihp_period_extension.approved must be a boolean",
                }],
                "agency": self.agency_id,
            }
        public_interest_determination = extension.get("public_interest_determination")
        if (
            public_interest_determination is not None
            and not isinstance(public_interest_determination, bool)
        ):
            return {
                "status": "INPUT_INVALID",
                "flags": [{
                    "type": "INPUT_INVALID",
                    "message": "incident.ihp_period_extension.public_interest_determination must be a boolean",
                }],
                "agency": self.agency_id,
            }
        basis_raw = extension.get("basis")
        if basis_raw is not None and not isinstance(basis_raw, str):
            return {
                "status": "INPUT_INVALID",
                "flags": [{
                    "type": "INPUT_INVALID",
                    "message": "incident.ihp_period_extension.basis must be a string",
                }],
                "agency": self.agency_id,
            }
        extension_approved = (ext_approved_raw is True) or (extra_ext_raw is True)
        extension_basis = str(basis_raw or "").strip().lower()
        extension_end_raw = extension.get("end_date")
        extension_end = None
        if extension_end_raw is not None:
            try:
                extension_end = self._parse_date(
                    extension_end_raw, "incident.ihp_period_extension.end_date"
                )
            except ValueError as exc:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": str(exc)}],
                    "agency": self.agency_id,
                }

        if "declaration_date" in incident and incident["declaration_date"] is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{"type": "INPUT_INVALID", "message": "incident.declaration_date must be an ISO date (YYYY-MM-DD)"}],
                "agency": self.agency_id,
            }
        declaration_date_str = incident.get("declaration_date")
        declaration_date = None
        if declaration_date_str is not None:
            try:
                declaration_date = self._parse_date(
                    declaration_date_str, "incident.declaration_date"
                )
            except ValueError as exc:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": str(exc)}],
                    "agency": self.agency_id,
                }

            standard_deadline = self._add_months(declaration_date, 18)
            if extension_approved and extension_end is None:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{
                        "type": "INPUT_INVALID",
                        "message": (
                            "incident.ihp_period_extension.end_date is required when an "
                            "IHP period extension is marked approved"
                        ),
                    }],
                    "agency": self.agency_id,
                }
            if extension_approved and (
                    extension_basis != "extraordinary_circumstances"
                    or public_interest_determination is not True):
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{
                        "type": "INPUT_INVALID",
                        "message": (
                            "An approved IHP period extension requires basis="
                            "'extraordinary_circumstances' and "
                            "public_interest_determination=true under 44 CFR 206.110(e)"
                        ),
                    }],
                    "agency": self.agency_id,
                }
            if extension_approved and extension_end <= standard_deadline:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{
                        "type": "INPUT_INVALID",
                        "message": (
                            "incident.ihp_period_extension.end_date must be after the "
                            "standard 18-month deadline"
                        ),
                    }],
                    "agency": self.agency_id,
                }

            effective_deadline = (
                extension_end
                if extension_approved and extension_end is not None
                else standard_deadline
            )
            if today > effective_deadline:
                flags.append({
                    "type": "SLA_CRITICAL",
                    "message": (
                        "IHP review period exceeds the standard 18-month period. "
                        "Human review must determine whether extraordinary circumstances "
                        "support an extension in the public interest."
                    ),
                    "citation": "44 CFR 206.110(e)",
                    "declaration_date": declaration_date.isoformat(),
                    "standard_deadline": standard_deadline.isoformat(),
                    "effective_deadline": effective_deadline.isoformat(),
                    "extension_exception_available": True,
                    "extension_exception_recorded": extension_approved,
                    "extension_exception_requirements": {
                        "basis": "extraordinary_circumstances",
                        "public_interest_determination": True,
                    },
                })
                status = "ESCALATE"
            elif today > standard_deadline and extension_approved:
                flags.append({
                    "type": "IHP_PERIOD_EXTENSION_RECORDED",
                    "message": (
                        "An approved IHP period extension is recorded for reviewer "
                        "verification."
                    ),
                    "citation": "44 CFR 206.110(e)",
                    "standard_deadline": standard_deadline.isoformat(),
                    "extension_end_date": extension_end.isoformat() if extension_end else None,
                    "extension_basis": extension_basis,
                    "public_interest_determination": public_interest_determination,
                })

        # Rule 2: 90-day appeal deadline (44 CFR 206.115(e))
        appeal_date_str = incident.get("appeal_date")
        if appeal_date_str is not None:
            try:
                appeal_date = self._parse_date(appeal_date_str, "incident.appeal_date")
            except ValueError as exc:
                return {
                    "status": "INPUT_INVALID",
                    "flags": [{"type": "INPUT_INVALID", "message": str(exc)}],
                    "agency": self.agency_id,
                }
            days_since_appeal = (today - appeal_date).days

            if days_since_appeal > 90:
                flags.append({
                    "type": "SLA_CRITICAL",
                    "message": (
                        f"Appeal decision is beyond the 90-day period ({days_since_appeal} days). "
                        "Escalated for human review under 44 CFR § 206.115(e)."
                    ),
                    "citation": "44 CFR 206.115(e)",
                    "days_since_appeal": days_since_appeal,
                })
                status = "ESCALATE"

        # Rule 3: the 2024 IA Equity Rule removed the SBA prerequisite for covered declarations.
        missing_raw = evidence.get("missing", [])
        missing = missing_raw if isinstance(missing_raw, list) else []
        sba_missing = any("sba" in m.lower() for m in missing if isinstance(m, str))
        if sba_missing and declaration_date is None:
            return {
                "status": "INPUT_INVALID",
                "flags": [{
                    "type": "INPUT_INVALID",
                    "message": (
                        "incident.declaration_date is required to determine whether the "
                        "2024-03-22 IA Equity Rule applies to an SBA-related item"
                    ),
                }],
                "agency": self.agency_id,
            }
        if sba_missing:
            equity_window = evaluate_rule_effective_window(
                self.IA_EQUITY_RULE_PUBLISHED_DATE,
                self.IA_EQUITY_RULE_EFFECTIVE_DATE,
                today,
                applicability_date=declaration_date,
            )
            if not equity_window["applies_to_case"]:
                flags.append({
                    "type": "POLICY_BLOCK",
                    "message": (
                        "Pre-2024 IA workflow contains a missing SBA determination. "
                        "A reviewer must apply the policy in effect on the evaluation and declaration dates."
                    ),
                    "citation": None,
                    "policy_id": "FEMA_PRE_2024_SBA_WORKFLOW",
                    "equity_rule_published_date": self.IA_EQUITY_RULE_PUBLISHED_DATE.isoformat(),
                    "equity_rule_effective_date": self.IA_EQUITY_RULE_EFFECTIVE_DATE.isoformat(),
                    "rule_window_status": equity_window["status"],
                    "declaration_date": declaration_date.isoformat(),
                    "missing_document": "SBA Loan Determination",
                })
                status = "ESCALATE"

        # Rule 4: internal triage policy; it is not represented as a CFR-mandated deadline.
        age = applicant.get("age", 0)
        if age is not None and isinstance(age, int) and age >= 65:
            flags.append({
                "type": "PRIORITY_EXPEDITE",
                "message": (
                    f"ASCE internal priority review for an older applicant (age {age}); "
                    "this is not a 44 CFR-mandated 48-hour deadline."
                ),
                "citation": None,
                "policy_id": "ASCE_INTERNAL_PRIORITY_POLICY",
                "applicant_age": age,
            })

        # Rule 5: internal data-integrity control, not a statutory eligibility decision.
        inspector_notes_raw = residence.get("inspector_notes", "")
        structure_status_raw = residence.get("structure_status", "")
        inspector_status = inspector_notes_raw.lower() if isinstance(inspector_notes_raw, str) else ""
        claimed_status = structure_status_raw.lower() if isinstance(structure_status_raw, str) else ""

        severe_claim = "condemned" in claimed_status or "destroyed" in claimed_status
        if "minor" in inspector_status and severe_claim:
            flags.append({
                "type": "INSPECTOR_CONFLICT",
                "message": (
                    "Data conflict: field notes describe minor damage while the case records "
                    "the structure as condemned or destroyed. Human reconciliation required."
                ),
                "citation": None,
                "policy_id": "ASCE_DATA_INTEGRITY_REVIEW",
                "inspector_notes": inspector_status,
                "claimed_status": claimed_status,
            })
            status = "ESCALATE"

        # Rule 6: evidence sufficiency review; 44 CFR 206.115 is reserved for appeals.
        occupancy_missing = any(
            "occupancy" in item.lower()
            or "page 4" in item.lower()
            or "lease" in item.lower()
            for item in missing
        )
        if occupancy_missing:
            flags.append({
                "type": "EVIDENCE_REQUIRED",
                "message": (
                    "Occupancy or lease evidence appears incomplete. Route for evidence review; "
                    "do not characterize this as an appeal violation under 44 CFR 206.115."
                ),
                "citation": None,
                "policy_id": "FEMA_IHP_OCCUPANCY_EVIDENCE_REVIEW",
                "missing_document": "Proof of Occupancy / Signed Lease Page 4",
            })
            status = "ESCALATE"
        
        return {
            "status": status,
            "flags": flags,
            "agency": self.agency_id
        }
    
    def get_scenario_config(self, scenario_id: str) -> Dict[str, Any]:
        """
        Get FEMA-specific scenario configuration.
        
        Args:
            scenario_id: Scenario identifier (e.g., 'SCENARIO_1_SBA')
            
        Returns:
            Scenario configuration dictionary
        """
        scenarios = {
            "SCENARIO_1_SBA": {
                "label": "SYNTHETIC / FEMA (PRE-2024 SBA WORKFLOW REVIEW)",
                "description": (
                    "SBA workflow review applies only to declarations before 2024-03-22"
                ),
            },
            "SCENARIO_2_REDUNDANCY": {
                "label": "SYNTHETIC / FEMA (SENIOR + DOC REDUNDANCY)",
                "description": "Senior applicant with redundant document request"
            },
            "SCENARIO_3_INSPECTOR": {
                "label": "SYNTHETIC / FEMA (INSPECTOR CONFLICT)",
                "description": "Inspector notes conflict with claimed damage"
            },
            "SCENARIO_4_CTHA": {
                "label": "SYNTHETIC / FEMA + CTHA (VERIFIED RESIDENCY vs. WRONG-TENANT REPORT)",
                "description": "Verified residency conflicts with third-party report"
            }
        }
        return scenarios.get(scenario_id, {})


# Auto-register with global registry
from agency_adapter import register_adapter_class
register_adapter_class(FEMAAdapter)
