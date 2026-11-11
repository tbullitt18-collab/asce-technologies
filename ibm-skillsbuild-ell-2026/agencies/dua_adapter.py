"""
DUA Agency Adapter
==================

Disaster Unemployment Assistance compliance adapter.
Implements DUA-specific rules from 20 CFR Part 625.

Plug-and-play: Can be loaded/unloaded without modifying core code.
"""

import datetime
import re
from typing import Dict, Any, List
from agency_adapter import AgencyAdapter


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DUAAdapter(AgencyAdapter):
    """
    DUA (Disaster Unemployment Assistance) compliance adapter.
    
    Implements 20 CFR Part 625 regulations:
    - Initial application deadlines (625.8(a))
    - Documentation requirements (625.6(e)(1))
    - Disaster Assistance Period (625.2(f))
    """
    
    # Constants from 20 CFR Part 625
    FILING_WINDOW_DAYS = 30
    PROOF_WINDOW_DAYS = 21
    DAP_WEEKS_AFTER_DECLARATION = 26

    @staticmethod
    def _parse_date(value: Any, field: str) -> datetime.date:
        """Parse an ISO date and identify the field in fail-closed errors."""
        if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
            raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)")
        try:
            return datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc

    def _invalid(self, messages: List[str]) -> Dict[str, Any]:
        """Return a consistent fail-closed result for unusable DUA input."""
        return {
            "status": "INPUT_INVALID",
            "flags": [
                {"type": "INPUT_INVALID", "message": message} for message in messages
            ],
            "agency": self.agency_id,
        }
    
    @property
    def agency_id(self) -> str:
        return "DUA"
    
    @property
    def agency_name(self) -> str:
        return "Disaster Unemployment Assistance"
    
    @property
    def cfr_title(self) -> str:
        return "20"
    
    @property
    def valid_cfr_sections(self) -> List[str]:
        """Valid 20 CFR sections for DUA programs."""
        return [
            "625.2",   # Definitions
            "625.4",   # Eligibility requirements
            "625.6",   # Weekly amount of DUA
            "625.7",   # Weeks for which DUA payable
            "625.8",   # Applications for DUA
            "625.9",   # Determinations
        ]
    
    def audit(self, case_data: Dict[str, Any], today: datetime.date) -> Dict[str, Any]:
        """
        Perform DUA-specific compliance audit.
        
        Checks:
        1. 30-day filing deadline (20 CFR 625.8(a))
        2. 21-day documentation deadline (20 CFR 625.6(e)(1))
        3. Disaster Assistance Period validity (20 CFR 625.2(f))
        4. Employment proof requirements
        
        Args:
            case_data: Case data dictionary
            today: Current date for time-based checks
            
        Returns:
            Dictionary with flags and status
        """
        flags = []
        status = "PASS"
        if not isinstance(case_data, dict):
            return self._invalid(["case_data must be an object"])
        raw_incident = case_data.get("incident")
        if "incident" in case_data and case_data["incident"] is None:
            return self._invalid(["incident must be an object (not null)"])
        if raw_incident is not None and not isinstance(raw_incident, dict):
            return self._invalid(["incident must be an object"])
        raw_dua = case_data.get("dua")
        if "dua" in case_data and case_data["dua"] is None:
            return self._invalid(["dua must be an object (not null)"])
        if raw_dua is not None and not isinstance(raw_dua, dict):
            return self._invalid(["dua must be an object"])
        raw_app = case_data.get("application")
        if "application" in case_data and case_data["application"] is None:
            return self._invalid(["application must be an object (not null)"])
        if raw_app is not None and not isinstance(raw_app, dict):
            return self._invalid(["application must be an object"])
        incident = raw_incident or {}
        dua = raw_dua or {}

        # Canonical application field, with an explicit legacy compatibility mapping.
        canonical_application = dua.get("initial_application_date")
        legacy_application = (raw_app or {}).get("submission_date")

        # Bug 5.1: Reject blank/null fallback - if initial_application_date is explicitly present, require valid date.
        if "initial_application_date" in dua:
            try:
                self._parse_date(canonical_application, "dua.initial_application_date")
            except ValueError as exc:
                return self._invalid([str(exc)])

        # Validate legacy application.submission_date whenever key is explicitly present.
        if raw_app is not None and "submission_date" in raw_app:
            try:
                self._parse_date(legacy_application, "application.submission_date")
            except ValueError as exc:
                return self._invalid([str(exc)])

        # Bug 5.2: Reject conflicting dual dates
        if (
            isinstance(canonical_application, str)
            and isinstance(legacy_application, str)
            and canonical_application != legacy_application
        ):
            return self._invalid([
                f"Conflicting application dates: dua.initial_application_date='{canonical_application}' vs "
                f"application.submission_date='{legacy_application}'. Only one should be provided."
            ])
        
        application_date_str = canonical_application or legacy_application
        required_fields = {
            "incident.announcement_date": incident.get("announcement_date"),
            "incident.declaration_date": incident.get("declaration_date"),
            "incident.disaster_began_date": incident.get("disaster_began_date"),
            "dua.initial_application_date": application_date_str,
            "dua.weekly_amount_basis": dua.get("weekly_amount_basis"),
        }
        missing_fields = [name for name, value in required_fields.items() if not value]
        if missing_fields:
            return self._invalid(
                [f"Missing required DUA field: {field}" for field in missing_fields]
            )
        if dua.get("weekly_amount_basis") not in {"statement_only", "documented"}:
            return self._invalid([
                "dua.weekly_amount_basis must be 'statement_only' or 'documented'"
            ])
        if not isinstance(dua.get("weeks_claimed"), list):
            return self._invalid(["dua.weeks_claimed must be an array of ISO dates"])

        good_cause_raw = dua.get("good_cause_late_filing")
        if good_cause_raw is not None and not isinstance(good_cause_raw, bool):
            return self._invalid(["dua.good_cause_late_filing must be a boolean"])

        raw_holidays = incident.get("area_legal_holidays")
        if raw_holidays is not None and not isinstance(raw_holidays, list):
            return self._invalid(["incident.area_legal_holidays must be an array of ISO dates"])

        try:
            announcement_date = self._parse_date(
                incident["announcement_date"], "incident.announcement_date"
            )
            declaration_date = self._parse_date(
                incident["declaration_date"], "incident.declaration_date"
            )
            disaster_began_date = self._parse_date(
                incident["disaster_began_date"], "incident.disaster_began_date"
            )
            application_date = self._parse_date(
                application_date_str, "dua.initial_application_date"
            )
            holidays = [
                self._parse_date(value, "incident.area_legal_holidays[]")
                for value in (raw_holidays or [])
            ]
        except ValueError as exc:
            return self._invalid([str(exc)])

        # Rule 1: 30-day filing deadline (20 CFR 625.8(a)).
        filing_deadline = self._calculate_filing_deadline(announcement_date, holidays)
        good_cause = good_cause_raw is True
        if application_date > filing_deadline:
            days_late = (application_date - filing_deadline).days
            flags.append({
                "type": (
                    "DUA_LATE_GOOD_CAUSE_REVIEW"
                    if good_cause
                        else "DUA_APPLICATION_LATE"
                ),
                "message": (
                    f"Initial application was filed {days_late} day(s) after the adjusted "
                    "30-day deadline. "
                    + (
                        "A human must decide the recorded good-cause basis."
                        if good_cause
                        else "No good cause is recorded; human review is required."
                    )
                ),
                "citation": "20 CFR 625.8(a)",
                "filing_deadline": filing_deadline.isoformat(),
                "application_date": application_date.isoformat(),
                "good_cause_recorded": good_cause,
            })
            status = "ESCALATE"

        # Rule 2: 21-day documentation deadline (20 CFR 625.6(e)(1)).
        proof_submitted_raw = dua.get("employment_proof_submitted_date")
        proof_submitted = None
        if proof_submitted_raw is not None:
            try:
                proof_submitted = self._parse_date(
                    proof_submitted_raw, "dua.employment_proof_submitted_date"
                )
            except ValueError as exc:
                return self._invalid([str(exc)])

        if dua.get("weekly_amount_basis") == "statement_only":
            proof_deadline = application_date + datetime.timedelta(
                days=self.PROOF_WINDOW_DAYS
            )
            if proof_submitted is not None:
                if proof_submitted > proof_deadline:
                    flags.append({
                        "type": "DUA_EMPLOYMENT_PROOF_LATE",
                        "message": (
                            "Employment proof was submitted after the 21-day documentation "
                            "period. Eligibility consequences require human review."
                        ),
                        "citation": "20 CFR 625.6(e)(1)",
                        "proof_deadline": proof_deadline.isoformat(),
                        "proof_submitted_date": proof_submitted.isoformat(),
                        "days_late": (proof_submitted - proof_deadline).days,
                    })
                    status = "ESCALATE"
            elif today > proof_deadline:
                flags.append({
                    "type": "DUA_EMPLOYMENT_PROOF_OVERDUE",
                    "message": (
                        "Employment proof remains outstanding after the 21-day documentation "
                        "period. Eligibility consequences require human review."
                    ),
                    "citation": "20 CFR 625.6(e)(1)",
                    "proof_deadline": proof_deadline.isoformat(),
                    "days_overdue": (today - proof_deadline).days,
                })
                status = "ESCALATE"

        # Rule 3: evaluate the application and claimed weeks against the DAP,
        # rather than treating the passage of today's date as a claimant defect.
        inc_dap_raw = incident.get("disaster_assistance_period_end")
        dua_dap_raw = dua.get("disaster_assistance_period_end")
        try:
            inc_dap_end = (
                self._parse_date(
                    inc_dap_raw, "incident.disaster_assistance_period_end"
                )
                if inc_dap_raw is not None
                else None
            )
            dua_dap_end = (
                self._parse_date(
                    dua_dap_raw, "dua.disaster_assistance_period_end"
                )
                if dua_dap_raw is not None
                else None
            )
            if (
                inc_dap_end is not None
                and dua_dap_end is not None
                and inc_dap_end != dua_dap_end
            ):
                return self._invalid([
                    "incident.disaster_assistance_period_end and dua.disaster_assistance_period_end conflict"
                ])
            explicit_dap_end = dua_dap_end if dua_dap_end is not None else inc_dap_end
            if explicit_dap_end is not None and explicit_dap_end < declaration_date:
                return self._invalid([
                    "disaster_assistance_period_end cannot be earlier than incident.declaration_date"
                ])
            dap_end = (
                explicit_dap_end
                if explicit_dap_end is not None
                else declaration_date
                + datetime.timedelta(weeks=self.DAP_WEEKS_AFTER_DECLARATION)
            )
            claimed_weeks = [
                self._parse_date(value, "dua.weeks_claimed[]")
                for value in dua.get("weeks_claimed", [])
            ]
        except ValueError as exc:
            return self._invalid([str(exc)])

        if application_date > dap_end:
            flags.append({
                "type": "DUA_APPLICATION_AFTER_DAP",
                "message": (
                    "Initial application date is after the Disaster Assistance Period. "
                    "Human review is required under 20 CFR 625.8(a)."
                ),
                "citation": "20 CFR 625.8(a)",
                "application_date": application_date.isoformat(),
                "dap_end": dap_end.isoformat(),
            })
            status = "ESCALATE"

        outside_weeks = sorted(
            week
            for week in claimed_weeks
            if not (disaster_began_date < week <= dap_end)
        )
        if outside_weeks:
            flags.append({
                "type": "DUA_WEEK_OUTSIDE_DAP",
                "message": (
                    f"{len(outside_weeks)} claimed week(s) fall outside the Disaster "
                    "Assistance Period and require human review."
                ),
                "citation": "20 CFR 625.4(a), 625.7, 625.2(f)",
                "weeks_outside": [value.isoformat() for value in outside_weeks],
                "disaster_began_date": disaster_began_date.isoformat(),
                "dap_end": dap_end.isoformat(),
            })
            status = "ESCALATE"
        
        return {
            "status": status,
            "flags": flags,
            "agency": self.agency_id
        }
    
    def _calculate_filing_deadline(
        self,
        announcement_date: datetime.date,
        area_legal_holidays=(),
    ) -> datetime.date:
        """
        Calculate filing deadline with weekend/holiday extension (20 CFR 625.8(a)).
        
        Args:
            announcement_date: Date disaster was announced
            
        Returns:
            Filing deadline date
        """
        deadline = announcement_date + datetime.timedelta(days=self.FILING_WINDOW_DAYS)
        
        holidays = set(area_legal_holidays or ())
        # Extend if the deadline falls on a weekend or legal holiday in the area.
        while deadline.weekday() >= 5 or deadline in holidays:  # Saturday=5, Sunday=6
            deadline += datetime.timedelta(days=1)
        
        return deadline


# Auto-register with global registry
from agency_adapter import register_adapter_class
register_adapter_class(DUAAdapter)
