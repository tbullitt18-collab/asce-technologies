"""
ASCE Agency Adapters Package
=============================

Plug-and-play agency compliance adapters.

Usage:
    from agencies import get_adapter, list_agencies
    
    # Get specific adapter
    fema = get_adapter("FEMA")
    result = fema.audit(case_data, today)
    
    # List all available agencies
    agencies = list_agencies()
"""

# Import adapters to trigger auto-registration
from agencies.fema_adapter import FEMAAdapter
from agencies.va_adapter import VAAdapter
from agencies.dua_adapter import DUAAdapter

# Re-export registry functions for convenience
from agency_adapter import (
    get_adapter,
    list_agencies,
    get_registry,
    register_adapter,
    register_adapter_class
)

__all__ = [
    'FEMAAdapter',
    'VAAdapter', 
    'DUAAdapter',
    'get_adapter',
    'list_agencies',
    'get_registry',
    'register_adapter',
    'register_adapter_class'
]

# Load configuration on import
_registry = get_registry()
_registry.load_from_config("agencies.json")
