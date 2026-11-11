"""
ASCE Mainframe Integration Adapter
===================================

Addresses Critical Gap #2: Mainframe "Last Mile" Protocol Adapter

Converts ASCE's modern JSON REST API outputs into legacy mainframe formats:
- COBOL copybook fixed-width records (80-column EBCDIC)
- IBM MQ message queue integration
- Batch staging files for CICS/DB2 ingestion

Target Systems:
- FEMA NEMIS (National Emergency Management Information System)
- State benefit databases (COBOL/DB2/CICS mainframes)
- Legacy 40-year-old systems that don't speak REST or JSON

Compliance: Maintains cryptographic evidence chain while translating formats
"""

import json
import struct
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
import hashlib


class COBOLCopybookGenerator:
    """
    Generates COBOL copybook fixed-width records from JSON data.
    
    COBOL mainframes expect 80-column fixed-width text files with:
    - EBCDIC encoding (not ASCII/UTF-8)
    - Fixed field positions and lengths
    - Packed decimal for numeric fields
    - No JSON, no REST, no modern protocols
    """
    
    # COBOL field type mappings
    FIELD_TYPES = {
        "alphanumeric": "X",  # PIC X(n)
        "numeric": "9",       # PIC 9(n)
        "decimal": "9V99",    # PIC 9(n)V99 (2 decimal places)
        "date": "X(10)",      # YYYY-MM-DD
        "boolean": "X(1)"     # Y/N
    }
    
    def __init__(self):
        """Initialize COBOL copybook generator."""
        self.record_layouts = self._define_record_layouts()
    
    def _define_record_layouts(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Define COBOL record layouts for ASCE audit results.
        
        Each field specifies:
        - name: COBOL field name (max 30 chars, uppercase, hyphens)
        - type: COBOL data type
        - length: Fixed field length
        - position: Starting position in 80-column record
        """
        return {
            "AUDIT_RESULT": [
                {"name": "CASE-ID", "type": "alphanumeric", "length": 20, "position": 1},
                {"name": "AUDIT-DATE", "type": "date", "length": 10, "position": 21},
                {"name": "AUDIT-STATUS", "type": "alphanumeric", "length": 10, "position": 31},
                {"name": "RISK-SCORE", "type": "numeric", "length": 3, "position": 41},
                {"name": "DEFECT-COUNT", "type": "numeric", "length": 2, "position": 44},
                {"name": "ESCALATION-FLAG", "type": "boolean", "length": 1, "position": 46},
                {"name": "EVIDENCE-HASH", "type": "alphanumeric", "length": 32, "position": 47},
                {"name": "FILLER", "type": "alphanumeric", "length": 2, "position": 79}
            ],
            "DEFECT_DETAIL": [
                {"name": "CASE-ID", "type": "alphanumeric", "length": 20, "position": 1},
                {"name": "DEFECT-CODE", "type": "alphanumeric", "length": 20, "position": 21},
                {"name": "DEFECT-SEVERITY", "type": "alphanumeric", "length": 10, "position": 41},
                {"name": "DEFECT-MESSAGE", "type": "alphanumeric", "length": 28, "position": 51},
                {"name": "FILLER", "type": "alphanumeric", "length": 2, "position": 79}
            ],
            "EVIDENCE_RECORD": [
                {"name": "CASE-ID", "type": "alphanumeric", "length": 20, "position": 1},
                {"name": "DOCUMENT-TYPE", "type": "alphanumeric", "length": 16, "position": 21},
                {"name": "DOCUMENT-HASH", "type": "alphanumeric", "length": 32, "position": 37},
                {"name": "UPLOAD-DATE", "type": "date", "length": 10, "position": 69},
                {"name": "FILLER", "type": "alphanumeric", "length": 2, "position": 79}
            ]
        }
    
    def generate_copybook_definition(self, record_type: str) -> str:
        """
        Generate COBOL copybook definition (source code).
        
        Args:
            record_type: Type of record (AUDIT_RESULT, DEFECT_DETAIL, etc.)
            
        Returns:
            COBOL copybook source code
        """
        if record_type not in self.record_layouts:
            raise ValueError(f"Unknown record type: {record_type}")
        
        layout = self.record_layouts[record_type]
        copybook = []
        
        # COBOL copybook header
        copybook.append(f"      * ASCE {record_type} COPYBOOK")
        copybook.append(f"      * Generated: {datetime.now(timezone.utc).isoformat()}")
        copybook.append(f"       01  {record_type}-RECORD.")
        
        # Generate field definitions
        for field in layout:
            name = field["name"]
            field_type = field["type"]
            length = field["length"]
            
            # COBOL PIC clause
            if field_type == "alphanumeric":
                pic = f"X({length})"
            elif field_type == "numeric":
                pic = f"9({length})"
            elif field_type == "decimal":
                pic = f"9({length-3})V99"
            elif field_type == "date":
                pic = "X(10)"
            elif field_type == "boolean":
                pic = "X(1)"
            else:
                pic = f"X({length})"
            
            copybook.append(f"           05  {name:<30} PIC {pic}.")
        
        return "\n".join(copybook)
    
    def json_to_fixed_width(self, data: Dict[str, Any], record_type: str) -> str:
        """
        Convert JSON data to COBOL fixed-width record.
        
        Args:
            data: JSON data dictionary
            record_type: Type of record layout to use
            
        Returns:
            Fixed-width ASCII string (80 columns)
        """
        if record_type not in self.record_layouts:
            raise ValueError(f"Unknown record type: {record_type}")
        
        layout = self.record_layouts[record_type]
        record = [" "] * 80  # 80-column fixed-width buffer
        
        for field in layout:
            name = field["name"]
            length = field["length"]
            position = field["position"] - 1  # Convert to 0-based
            
            # Map JSON field to COBOL field name
            json_key = name.lower().replace("-", "_")
            value = data.get(json_key, "")
            
            # Format value based on type
            if field["type"] == "numeric":
                formatted = str(value).zfill(length)[:length]
            elif field["type"] == "boolean":
                formatted = "Y" if value else "N"
            elif field["type"] == "date":
                if isinstance(value, str):
                    formatted = value[:length].ljust(length)
                else:
                    formatted = " " * length
            else:  # alphanumeric
                formatted = str(value)[:length].ljust(length)
            
            # Insert into fixed position
            for i, char in enumerate(formatted):
                if position + i < len(record):
                    record[position + i] = char
        
        return "".join(record[:80])  # Return exactly 80 chars
    
    def to_ebcdic(self, text: str, codepage: str = "cp037") -> bytes:
        """
        Convert ASCII/UTF-8 text to EBCDIC encoding.
        
        Args:
            text: ASCII/UTF-8 text string
            codepage: EBCDIC codepage (default: cp037 for US English)
            
        Returns:
            EBCDIC-encoded bytes
        """
        return text.encode(codepage)
    
    def from_ebcdic(self, ebcdic_bytes: bytes, codepage: str = "cp037") -> str:
        """
        Convert EBCDIC bytes to ASCII/UTF-8 text.
        
        Args:
            ebcdic_bytes: EBCDIC-encoded bytes
            codepage: EBCDIC codepage (default: cp037 for US English)
            
        Returns:
            ASCII/UTF-8 text string
        """
        return ebcdic_bytes.decode(codepage)
    
    def generate_batch_file(self, records: List[Dict[str, Any]], 
                           record_type: str, 
                           output_path: str,
                           use_ebcdic: bool = False,
                           codepage: str = "cp037") -> Dict[str, Any]:
        """
        Generate mainframe batch staging file.
        
        Args:
            records: List of JSON records
            record_type: Type of record layout
            output_path: Path to output file
            use_ebcdic: Whether to encode output in EBCDIC (default: False for ASCII)
            codepage: EBCDIC codepage if use_ebcdic=True (default: cp037)
            
        Returns:
            Metadata about generated file
        """
        lines = []
        
        for record in records:
            fixed_width = self.json_to_fixed_width(record, record_type)
            lines.append(fixed_width)
        
        # Write to file
        content = "\n".join(lines)
        
        if use_ebcdic:
            # Write as EBCDIC binary
            ebcdic_content = self.to_ebcdic(content, codepage)
            with open(output_path, "wb") as f:
                f.write(ebcdic_content)
            file_hash = hashlib.sha256(ebcdic_content).hexdigest()
            file_size = len(ebcdic_content)
            format_str = f"COBOL_FIXED_WIDTH_80_COLUMN_EBCDIC_{codepage.upper()}"
        else:
            # Write as ASCII text
            with open(output_path, "w", encoding="ascii") as f:
                f.write(content)
            file_hash = hashlib.sha256(content.encode("ascii")).hexdigest()
            file_size = len(content)
            format_str = "COBOL_FIXED_WIDTH_80_COLUMN_ASCII"
        
        return {
            "file_path": output_path,
            "record_type": record_type,
            "record_count": len(records),
            "file_size_bytes": file_size,
            "file_hash": file_hash,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "format": format_str
        }


class IBMMQAdapter:
    """
    IBM MQ (Message Queue) integration adapter.
    
    Sends ASCE audit results to mainframe message queues for:
    - Real-time processing
    - Asynchronous integration
    - Enterprise service bus (ESB) connectivity
    
    Note: Requires IBM MQ client libraries (pymqi) for production use.
    This implementation provides the interface and mock for testing.
    """
    
    def __init__(self, queue_manager: str, channel: str, 
                 host: str, port: int, queue_name: str):
        """
        Initialize IBM MQ connection parameters.
        
        Args:
            queue_manager: MQ queue manager name
            channel: MQ channel name
            host: MQ server hostname
            port: MQ server port (typically 1414)
            queue_name: Target queue name
        """
        self.queue_manager = queue_manager
        self.channel = channel
        self.host = host
        self.port = port
        self.queue_name = queue_name
        self.connected = False
        
        # Check if pymqi is available
        try:
            import pymqi
            self.pymqi = pymqi
            self.pymqi_available = True
        except ImportError:
            self.pymqi = None
            self.pymqi_available = False
    
    def connect(self) -> bool:
        """
        Connect to IBM MQ queue manager.
        
        Returns:
            True if connected successfully, False if pymqi not available
        """
        if not self.pymqi_available:
            # Cannot connect without pymqi library
            self.connected = False
            return False
        
        try:
            # Real IBM MQ connection
            conn_info = f"{self.host}({self.port})"
            self.qmgr = self.pymqi.connect(
                self.queue_manager,
                self.channel,
                conn_info
            )
            self.queue = self.pymqi.Queue(self.qmgr, self.queue_name)
            self.connected = True
            return True
        except Exception as e:
            self.connected = False
            return False
    
    def send_message(self, message: str, message_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Send message to IBM MQ queue.
        
        Args:
            message: Message content (typically fixed-width COBOL record)
            message_id: Optional message ID for tracking
            
        Returns:
            Send result metadata
        """
        if not self.connected:
            return {
                "success": False,
                "error": "Not connected to MQ",
                "message_id": message_id,
                "simulated": True
            }
        
        try:
            if self.pymqi_available:
                # Real MQ send
                md = self.pymqi.MD()
                if message_id:
                    md.MsgId = message_id.encode()
                
                self.queue.put(message.encode("ascii"), md)
                simulated = False
            else:
                # Simulated send (no pymqi library)
                simulated = True
            
            # Return success metadata
            return {
                "success": True,
                "message_id": message_id or hashlib.md5(message.encode()).hexdigest()[:16],
                "queue_name": self.queue_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message_size": len(message),
                "simulated": simulated
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message_id": message_id,
                "simulated": True
            }
    
    def disconnect(self):
        """Disconnect from IBM MQ."""
        if self.connected and self.pymqi_available:
            try:
                self.queue.close()
                self.qmgr.disconnect()
            except:
                pass
        self.connected = False


class MainframeAdapter:
    """
    High-level mainframe integration adapter.
    
    Orchestrates COBOL copybook generation and IBM MQ messaging
    to bridge ASCE's modern REST API with legacy mainframe systems.
    """
    
    def __init__(self, mq_config: Optional[Dict[str, Any]] = None):
        """
        Initialize mainframe adapter.
        
        Args:
            mq_config: Optional IBM MQ configuration
        """
        self.copybook_gen = COBOLCopybookGenerator()
        self.mq_adapter = None
        
        if mq_config:
            self.mq_adapter = IBMMQAdapter(
                queue_manager=mq_config.get("queue_manager", "QM1"),
                channel=mq_config.get("channel", "SYSTEM.DEF.SVRCONN"),
                host=mq_config.get("host", "localhost"),
                port=mq_config.get("port", 1414),
                queue_name=mq_config.get("queue_name", "ASCE.AUDIT.QUEUE")
            )
    
    def convert_audit_result(self, audit_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert ASCE audit result JSON to mainframe formats.
        
        Args:
            audit_result: ASCE audit result from compliance_engine
            
        Returns:
            Conversion result with multiple format options
        """
        result = {
            "original_json": audit_result,
            "mainframe_formats": {}
        }
        
        # Extract key fields for COBOL record
        case_id = audit_result.get("case_id", "UNKNOWN")
        audit_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        status = audit_result.get("status", "UNKNOWN")
        risk_score = audit_result.get("risk_score", 0)
        defects = audit_result.get("defects", [])
        evidence_hash = audit_result.get("evidence_hash", "")[:32]
        
        # Generate AUDIT_RESULT record
        audit_record = {
            "case_id": case_id,
            "audit_date": audit_date,
            "audit_status": status[:10],
            "risk_score": min(999, risk_score),
            "defect_count": min(99, len(defects)),
            "escalation_flag": audit_result.get("escalation_required", False),
            "evidence_hash": evidence_hash,
            "filler": " "
        }
        
        result["mainframe_formats"]["audit_record"] = {
            "json": audit_record,
            "fixed_width": self.copybook_gen.json_to_fixed_width(audit_record, "AUDIT_RESULT"),
            "copybook": self.copybook_gen.generate_copybook_definition("AUDIT_RESULT")
        }
        
        # Generate DEFECT_DETAIL records
        defect_records = []
        for defect in defects[:99]:  # Limit to 99 defects
            defect_record = {
                "case_id": case_id,
                "defect_code": defect.get("code", "UNKNOWN")[:30],
                "defect_severity": defect.get("severity", "UNKNOWN")[:10],
                "defect_message": defect.get("message", "")[:28],
                "filler": " "
            }
            defect_records.append({
                "json": defect_record,
                "fixed_width": self.copybook_gen.json_to_fixed_width(defect_record, "DEFECT_DETAIL")
            })
        
        result["mainframe_formats"]["defect_records"] = defect_records
        
        return result
    
    def send_to_mainframe(self, audit_result: Dict[str, Any], 
                         method: str = "batch") -> Dict[str, Any]:
        """
        Send audit result to mainframe system.
        
        Args:
            audit_result: ASCE audit result
            method: "batch" (file staging) or "mq" (message queue)
            
        Returns:
            Send result metadata
        """
        converted = self.convert_audit_result(audit_result)
        
        if method == "mq" and self.mq_adapter:
            # Send via IBM MQ
            if not self.mq_adapter.connected:
                self.mq_adapter.connect()
            
            # Send audit record
            audit_fixed = converted["mainframe_formats"]["audit_record"]["fixed_width"]
            mq_result = self.mq_adapter.send_message(
                audit_fixed,
                message_id=audit_result.get("case_id")
            )
            
            return {
                "method": "ibm_mq",
                "success": mq_result["success"],
                "mq_result": mq_result,
                "converted_data": converted
            }
        
        elif method == "batch":
            # Generate batch staging file
            case_id = audit_result.get("case_id", "UNKNOWN")
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            output_path = f"mainframe_staging_{case_id}_{timestamp}.txt"
            
            # Write audit record
            audit_record = converted["mainframe_formats"]["audit_record"]["json"]
            batch_result = self.copybook_gen.generate_batch_file(
                [audit_record],
                "AUDIT_RESULT",
                output_path
            )
            
            return {
                "method": "batch_file",
                "success": True,
                "batch_result": batch_result,
                "converted_data": converted
            }
        
        else:
            return {
                "method": method,
                "success": False,
                "error": f"Unsupported method: {method}"
            }


# Example usage and testing
if __name__ == "__main__":
    print("=== ASCE Mainframe Integration Adapter ===")
    print("\nThis module addresses Critical Gap #2:")
    print("Mainframe 'Last Mile' Protocol Adapter")
    print("\nCapabilities:")
    print("1. ✅ COBOL copybook generation (80-column fixed-width)")
    print("2. ✅ IBM MQ message queue integration")
    print("3. ✅ Batch file staging for CICS/DB2")
    print("4. ✅ JSON to EBCDIC format conversion")
    
    # Demo: Convert sample audit result
    sample_audit = {
        "case_id": "FEMA-2024-001234",
        "status": "ESCALATE",
        "risk_score": 85,
        "defects": [
            {"code": "MISSING_SIGNATURE", "severity": "HIGH", "message": "No signature detected"},
            {"code": "EXPIRED_EVIDENCE", "severity": "MEDIUM", "message": "Document expired"}
        ],
        "evidence_hash": "a1b2c3d4e5f6" * 3,
        "escalation_required": True
    }
    
    adapter = MainframeAdapter()
    result = adapter.convert_audit_result(sample_audit)
    
    print("\n\n=== Sample Conversion ===")
    print("\nCOBOL Fixed-Width Record (80 columns):")
    print(result["mainframe_formats"]["audit_record"]["fixed_width"])
    print("\nCOBOL Copybook Definition:")
    print(result["mainframe_formats"]["audit_record"]["copybook"][:200] + "...")
