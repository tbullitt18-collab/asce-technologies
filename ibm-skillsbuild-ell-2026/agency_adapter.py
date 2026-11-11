"""
ASCE Agency Adapter Plugin System
==================================

Plug-and-play architecture for agency-specific compliance rules.
No rip-and-replace - agencies are loaded dynamically from configuration.

Design Principles:
- Each agency is a separate adapter class
- Adapters implement a common interface (AgencyAdapter)
- Dynamic loading via agency registry
- Configuration-driven (agencies.json)
- Backward compatible with existing tenant system
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import datetime
import json
import os
from pathlib import Path


def _coerce_iso_date(value: Any, field: str) -> datetime.date:
    """Parse an ISO date or return a datetime.date unchanged; fail closed otherwise."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc
    raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)")


def evaluate_rule_effective_window(
    published_date: Any,
    effective_date: Any,
    as_of: Any,
    applicability_date: Any = None,
) -> Dict[str, Any]:
    """
    Determine whether a published CFR change has cleared its waiting window and
    governs the evaluated case date.

    A CFR final rule or interim final rule does not take legal effect on the day
    it is published unless effective_date == published_date:
    1. Waiting window (as_of < effective_date): staged only, never enforced early.
    2. Effective on or after effective_date: applies only if applicability_date is
       unset or on/after effective_date; older cases stay on the prior rule.
    """
    pub = _coerce_iso_date(published_date, "published_date")
    eff = _coerce_iso_date(effective_date, "effective_date")
    now = _coerce_iso_date(as_of, "as_of")
    app_date = (
        _coerce_iso_date(applicability_date, "applicability_date")
        if applicability_date is not None
        else None
    )
    if eff < pub:
        raise ValueError("effective_date cannot be earlier than published_date")

    waiting_days = (eff - pub).days
    if now < eff:
        return {
            "status": "PENDING_EFFECTIVE_WINDOW",
            "rule_in_force": False,
            "applies_to_case": False,
            "published_date": pub.isoformat(),
            "effective_date": eff.isoformat(),
            "as_of": now.isoformat(),
            "applicability_date": app_date.isoformat() if app_date else None,
            "waiting_window_days": waiting_days,
            "days_until_effective": (eff - now).days,
        }

    if app_date is not None and app_date < eff:
        return {
            "status": "PRIOR_RULE_GOVERNS_CASE",
            "rule_in_force": True,
            "applies_to_case": False,
            "published_date": pub.isoformat(),
            "effective_date": eff.isoformat(),
            "as_of": now.isoformat(),
            "applicability_date": app_date.isoformat(),
            "waiting_window_days": waiting_days,
            "days_until_effective": 0,
        }

    return {
        "status": "EFFECTIVE_FOR_CASE",
        "rule_in_force": True,
        "applies_to_case": True,
        "published_date": pub.isoformat(),
        "effective_date": eff.isoformat(),
        "as_of": now.isoformat(),
        "applicability_date": app_date.isoformat() if app_date else None,
        "waiting_window_days": waiting_days,
        "days_until_effective": 0,
    }


class AgencyAdapter(ABC):
    """
    Base interface for agency-specific compliance adapters.
    
    Each agency (FEMA, VA, SSA, etc.) implements this interface to provide
    custom compliance rules, validation logic, and defect detection.
    
    Plug-and-play: New agencies can be added without modifying core code.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the agency adapter.
        
        Args:
            config: Optional agency-specific configuration
        """
        self.config = config or {}
    
    @property
    @abstractmethod
    def agency_id(self) -> str:
        """Unique identifier for this agency (e.g., 'FEMA', 'VA', 'SSA')."""
        pass
    
    @property
    @abstractmethod
    def agency_name(self) -> str:
        """Human-readable agency name (e.g., 'Federal Emergency Management Agency')."""
        pass
    
    @property
    @abstractmethod
    def cfr_title(self) -> str:
        """CFR title number for this agency's regulations (e.g., '44' for FEMA)."""
        pass
    
    @property
    def cfr_parts(self) -> List[str]:
        """
        CFR part numbers owned by this agency under `cfr_title` (for example,
        FEMA owns 44 CFR Part 206; DUA owns 20 CFR Part 625 rather than SSA Part 404).
        """
        configured = (self.config or {}).get("cfr_parts")
        if isinstance(configured, list) and configured:
            return [str(part) for part in configured]
        sections = self.valid_cfr_sections
        parts: List[str] = []
        for section in sections:
            part = str(section).split(".", 1)[0]
            if part and part not in parts:
                parts.append(part)
        return parts

    @property
    def valid_cfr_sections(self) -> List[str]:
        """
        List of valid CFR sections for citation validation.
        
        Override this in subclasses to provide agency-specific sections.
        """
        return []
    
    @abstractmethod
    def audit(self, case_data: Dict[str, Any], today: datetime.date) -> Dict[str, Any]:
        """
        Perform agency-specific compliance audit.
        
        Args:
            case_data: Case data dictionary
            today: Current date for time-based checks
            
        Returns:
            Dictionary with:
            - flags: List of compliance flags/violations
            - status: Overall status (PASS, ESCALATE, etc.)
        """
        pass
    
    def validate_citation(self, citation: str) -> Dict[str, Any]:
        """
        Validate a CFR citation against this agency's whitelist.
        
        Args:
            citation: Citation text to validate
            
        Returns:
            Dictionary with validation result
        """
        # Default implementation - can be overridden
        if not citation or not citation.strip():
            return {"status": "REJECTED", "matched_section": None, "note": "Empty citation"}
        
        # Check if citation matches any valid section
        for section in self.valid_cfr_sections:
            if section in citation:
                return {"status": "VALIDATED", "matched_section": section, "note": "Matches whitelist"}
        
        return {"status": "UNVERIFIED", "matched_section": None, "note": "Not found in whitelist"}
    
    def get_metadata(self) -> Dict[str, Any]:
        """
        Get adapter metadata for introspection.
        
        Returns:
            Dictionary with adapter information
        """
        return {
            "agency_id": self.agency_id,
            "agency_name": self.agency_name,
            "cfr_title": self.cfr_title,
            "cfr_parts": self.cfr_parts,
            "valid_sections_count": len(self.valid_cfr_sections),
            "config": self.config
        }


class AgencyRegistry:
    """
    Registry for managing agency adapters.
    
    Provides dynamic loading, registration, and retrieval of agency adapters.
    Supports both code-based and configuration-based registration.
    """
    
    def __init__(self):
        self._adapters: Dict[str, AgencyAdapter] = {}
        self._adapter_classes: Dict[str, type] = {}
    
    def register_adapter_class(self, adapter_class: type):
        """
        Register an adapter class for dynamic instantiation.
        
        Args:
            adapter_class: AgencyAdapter subclass
        """
        # Instantiate to get agency_id
        temp_instance = adapter_class()
        agency_id = temp_instance.agency_id
        self._adapter_classes[agency_id] = adapter_class
    
    def register_adapter(self, adapter: AgencyAdapter):
        """
        Register an adapter instance.
        
        Args:
            adapter: AgencyAdapter instance
        """
        self._adapters[adapter.agency_id] = adapter
    
    def get_adapter(self, agency_id: str) -> Optional[AgencyAdapter]:
        """
        Get an adapter by agency ID.
        
        Args:
            agency_id: Agency identifier (e.g., 'FEMA', 'VA')
            
        Returns:
            AgencyAdapter instance or None if not found
        """
        # Check if already instantiated
        if agency_id in self._adapters:
            return self._adapters[agency_id]
        
        # Try to instantiate from registered class
        if agency_id in self._adapter_classes:
            adapter = self._adapter_classes[agency_id]()
            self._adapters[agency_id] = adapter
            return adapter
        
        return None
    
    def list_agencies(self) -> List[str]:
        """
        List all registered agency IDs.
        
        Returns:
            List of agency identifiers
        """
        all_ids = set(self._adapters.keys()) | set(self._adapter_classes.keys())
        return sorted(all_ids)
    
    def get_all_metadata(self) -> Dict[str, Dict[str, Any]]:
        """
        Get metadata for all registered adapters.
        
        Returns:
            Dictionary mapping agency_id to metadata
        """
        metadata = {}
        for agency_id in self.list_agencies():
            adapter = self.get_adapter(agency_id)
            if adapter:
                metadata[agency_id] = adapter.get_metadata()
        return metadata
    
    def resolve_cfr_part(self, cfr_title: str, cfr_part: str) -> Optional[str]:
        """
        Route a (cfr_title, cfr_part) pair to its single owning agency_id.
        Fails closed (returns None) if unmapped or ambiguous.
        """
        title_norm = str(cfr_title or "").removeprefix("title-").strip()
        part_norm = str(cfr_part or "").strip()
        if not title_norm or not part_norm:
            return None
        matches: List[str] = []
        for agency_id in self.list_agencies():
            adapter = self.get_adapter(agency_id)
            if adapter is None:
                continue
            if str(adapter.cfr_title) == title_norm and part_norm in adapter.cfr_parts:
                matches.append(agency_id)
        if len(matches) == 1:
            return matches[0]
        return None

    def load_from_config(self, config_path: str = "agencies.json"):
        """
        Load agency configurations from JSON file.
        
        Args:
            config_path: Path to agencies.json configuration file
        """
        resolved_path = Path(config_path)
        if not resolved_path.is_absolute() and not resolved_path.exists():
            candidate = Path(__file__).resolve().parent / config_path
            if candidate.exists():
                resolved_path = candidate
        if not resolved_path.exists():
            return
        
        try:
            with resolved_path.open('r', encoding='utf-8') as f:
                config = json.load(f)
            
            for agency_config in config.get("agencies", []):
                agency_id = agency_config.get("agency_id")
                if agency_id and agency_id in self._adapter_classes:
                    # Instantiate with config
                    adapter = self._adapter_classes[agency_id](config=agency_config)
                    self._adapters[agency_id] = adapter
        except Exception as e:
            print(f"Warning: Failed to load agency config from {resolved_path}: {e}")


# Global registry instance
_global_registry = AgencyRegistry()


def get_registry() -> AgencyRegistry:
    """Get the global agency registry."""
    return _global_registry


def register_adapter(adapter: AgencyAdapter):
    """Register an adapter with the global registry."""
    _global_registry.register_adapter(adapter)


def register_adapter_class(adapter_class: type):
    """Register an adapter class with the global registry."""
    _global_registry.register_adapter_class(adapter_class)


def get_adapter(agency_id: str) -> Optional[AgencyAdapter]:
    """Get an adapter from the global registry."""
    return _global_registry.get_adapter(agency_id)


def list_agencies() -> List[str]:
    """List all registered agencies."""
    return _global_registry.list_agencies()
