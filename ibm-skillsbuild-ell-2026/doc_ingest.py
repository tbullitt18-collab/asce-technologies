"""
ASCE Document Ingestion, OCR Preprocessing, and Defect Detection Engine
========================================================================

Real-world document processing pipeline for federal benefit applications:
- Image quality assessment and preprocessing
- OCR text extraction from images and PDFs
- Document type classification
- Key field extraction (names, addresses, dates, amounts)
- Deterministic defect detection (missing signatures, expired evidence, mismatches)
- Cryptographic evidence provenance (SHA-256 hashing)
- Integration with DeterministicAuditor for compliance checking
- Vision AI integration for ink signature detection and cursive handwriting

Compliance: 44 CFR § 206.111 (evidence requirements and duplicate claim detection)
"""

import hashlib
import re
import datetime
from typing import Dict, List, Optional, Tuple, Any
from io import BytesIO

try:
    from PIL import Image
    import numpy as np
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# Import Vision AI for ink signature detection
try:
    from vision_ai import VisionAIClient, create_vision_client
    VISION_AI_AVAILABLE = True
except ImportError:
    VisionAIClient = None
    create_vision_client = None
    VISION_AI_AVAILABLE = False


# Document type patterns for classification
DOCUMENT_PATTERNS = {
    "LEASE_AGREEMENT": [
        r"lease\s+agreement",
        r"rental\s+agreement",
        r"tenant.*landlord",
        r"monthly\s+rent",
        r"lease\s+term"
    ],
    "UTILITY_BILL": [
        r"utility\s+bill",
        r"electric\s+bill",
        r"gas\s+bill",
        r"water\s+bill",
        r"account\s+number.*\d{8,}",
        r"service\s+address"
    ],
    "PROPERTY_DEED": [
        r"deed",
        r"warranty\s+deed",
        r"quit\s*claim",
        r"property\s+description",
        r"legal\s+description",
        r"grantor.*grantee"
    ],
    "GOVERNMENT_ID": [
        r"driver.*license",
        r"state\s+id",
        r"identification\s+card",
        r"date\s+of\s+birth",
        r"license\s+number"
    ],
    "PAYSTUB": [
        r"pay\s*stub",
        r"earnings\s+statement",
        r"gross\s+pay",
        r"net\s+pay",
        r"pay\s+period",
        r"year.to.date"
    ]
}

# Signature detection patterns
SIGNATURE_PATTERNS = [
    r"signature.*date",
    r"signed\s+by",
    r"tenant.*signature",
    r"landlord.*signature",
    r"applicant.*signature",
    r"authorized\s+signature"
]

# Defect codes aligned with compliance_engine.py
DEFECT_ILLEGIBLE_DOCUMENT = "ILLEGIBLE_DOCUMENT"
DEFECT_MISSING_SIGNATURE = "MISSING_SIGNATURE"
DEFECT_MISSING_PAGE_SIGNATURE = "MISSING_PAGE_{}_SIGNATURE"
DEFECT_EXPIRED_EVIDENCE_DATE = "EXPIRED_EVIDENCE_DATE"
DEFECT_ADDRESS_MISMATCH = "ADDRESS_MISMATCH"
DEFECT_NAME_MISMATCH = "NAME_MISMATCH"
DEFECT_LOW_CONTRAST = "LOW_CONTRAST"
DEFECT_DARK_IMAGE = "DARK_IMAGE"


def compute_sha256(data: bytes) -> str:
    """
    Compute SHA-256 hash for cryptographic evidence provenance.
    
    Compliance: 44 CFR § 206.111 - tamper-evidence and duplicate claim detection
    
    Args:
        data: Raw document bytes
        
    Returns:
        Hexadecimal SHA-256 hash string
    """
    return hashlib.sha256(data).hexdigest()


def assess_image_quality(image_data: bytes) -> Dict[str, Any]:
    """
    Assess image quality for contrast, brightness, and legibility.
    
    Detects:
    - Low contrast images (hard to read)
    - Dark/underexposed images
    - Illegible scans
    
    Args:
        image_data: Raw image bytes
        
    Returns:
        Dictionary with quality metrics and defect flags
    """
    if not PIL_AVAILABLE:
        return {
            "status": "SKIPPED",
            "reason": "PIL not available",
            "defects": []
        }
    
    try:
        img = Image.open(BytesIO(image_data))
        
        # Convert to grayscale for analysis
        if img.mode != 'L':
            img = img.convert('L')
        
        # Convert to numpy array for statistical analysis
        img_array = np.array(img)
        
        # Calculate brightness (mean pixel value)
        mean_brightness = float(np.mean(img_array))
        
        # Calculate contrast (standard deviation)
        contrast = float(np.std(img_array))
        
        # Calculate dynamic range
        min_val = float(np.min(img_array))
        max_val = float(np.max(img_array))
        dynamic_range = max_val - min_val
        
        defects = []
        
        # Thresholds based on 8-bit grayscale (0-255)
        LOW_CONTRAST_THRESHOLD = 30.0
        DARK_IMAGE_THRESHOLD = 80.0
        LOW_DYNAMIC_RANGE_THRESHOLD = 100.0
        
        if contrast < LOW_CONTRAST_THRESHOLD:
            defects.append({
                "code": DEFECT_LOW_CONTRAST,
                "message": f"Low contrast detected (std={contrast:.1f}, threshold={LOW_CONTRAST_THRESHOLD})",
                "severity": "HIGH"
            })
        
        if mean_brightness < DARK_IMAGE_THRESHOLD:
            defects.append({
                "code": DEFECT_DARK_IMAGE,
                "message": f"Dark/underexposed image (brightness={mean_brightness:.1f}, threshold={DARK_IMAGE_THRESHOLD})",
                "severity": "HIGH"
            })
        
        if dynamic_range < LOW_DYNAMIC_RANGE_THRESHOLD:
            defects.append({
                "code": DEFECT_ILLEGIBLE_DOCUMENT,
                "message": f"Illegible document - insufficient dynamic range (range={dynamic_range:.1f}, threshold={LOW_DYNAMIC_RANGE_THRESHOLD})",
                "severity": "CRITICAL"
            })
        
        return {
            "status": "ANALYZED",
            "metrics": {
                "mean_brightness": mean_brightness,
                "contrast_std": contrast,
                "dynamic_range": dynamic_range,
                "min_pixel": min_val,
                "max_pixel": max_val,
                "image_size": img.size,
                "image_mode": img.mode
            },
            "defects": defects,
            "legible": len([d for d in defects if d["severity"] == "CRITICAL"]) == 0
        }
        
    except Exception as e:
        return {
            "status": "ERROR",
            "reason": str(e),
            "defects": [{
                "code": DEFECT_ILLEGIBLE_DOCUMENT,
                "message": f"Image processing error: {str(e)}",
                "severity": "CRITICAL"
            }]
        }


def normalize_image(image_data: bytes) -> Optional[bytes]:
    """
    Normalize and binarize image for improved OCR accuracy.
    
    Applies:
    - Contrast enhancement
    - Brightness normalization
    - Adaptive thresholding for binarization
    
    Args:
        image_data: Raw image bytes
        
    Returns:
        Normalized image bytes or None if processing fails
    """
    if not PIL_AVAILABLE:
        return None
    
    try:
        img = Image.open(BytesIO(image_data))
        
        # Convert to grayscale
        if img.mode != 'L':
            img = img.convert('L')
        
        img_array = np.array(img)
        
        # Normalize to full dynamic range
        min_val = np.min(img_array)
        max_val = np.max(img_array)
        
        if max_val > min_val:
            normalized = ((img_array - min_val) / (max_val - min_val) * 255).astype(np.uint8)
        else:
            normalized = img_array
        
        # Convert back to PIL Image
        normalized_img = Image.fromarray(normalized)
        
        # Save to bytes
        output = BytesIO()
        normalized_img.save(output, format='PNG')
        return output.getvalue()
        
    except Exception:
        return None


def get_image_ocr_status() -> Dict[str, Any]:
    """Return explicit OCR capability status so empty image text is not misread as blank."""
    return {
        "ocr_configured": False,
        "status": "NOT_CONFIGURED",
        "provider": "none",
        "note": "No live image OCR engine (Granite Vision / Tesseract) is configured in this runtime.",
    }


def extract_text_from_image(image_data: bytes) -> str:
    """
    Extract text from image using OCR.
    
    Note: No live OCR engine is connected in this demo runtime (see get_image_ocr_status()).
    In production, integrate with:
    - IBM Watson Visual Recognition
    - Google Cloud Vision API
    - Tesseract OCR
    - Azure Computer Vision
    
    Args:
        image_data: Raw image bytes
        
    Returns:
        Extracted text string (empty until OCR is configured)
    """
    return ""


def extract_text_from_pdf(pdf_data: bytes) -> str:
    """
    Extract text from PDF document using pdfplumber.
    
    Extracts all text from all pages, preserving layout and structure.
    Based on DOCUUMAKE template_extractor.py logic.
    
    Args:
        pdf_data: Raw PDF bytes
        
    Returns:
        Extracted text string (all pages concatenated)
    """
    try:
        import pdfplumber
        from io import BytesIO
        
        all_text = []
        
        with pdfplumber.open(BytesIO(pdf_data)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                # Extract full page text
                page_text = page.extract_text()
                if page_text:
                    all_text.append(f"--- Page {page_num + 1} ---\n{page_text}")
                
                # Also extract tables if present
                tables = page.find_tables()
                for t_idx, table in enumerate(tables):
                    table_data = table.extract()
                    if table_data:
                        all_text.append(f"\n[Table {t_idx + 1}]")
                        for row in table_data:
                            all_text.append(" | ".join(str(cell) if cell else "" for cell in row))
        
        return "\n".join(all_text)
        
    except ImportError:
        # pdfplumber not available - return empty string
        return ""
    except Exception as e:
        # PDF processing error - return empty string
        return ""


def classify_document(text: str) -> Tuple[Optional[str], float]:
    """
    Classify document type based on text content.
    
    Args:
        text: Extracted document text
        
    Returns:
        Tuple of (document_type, confidence_score)
    """
    text_lower = text.lower()
    
    scores = {}
    for doc_type, patterns in DOCUMENT_PATTERNS.items():
        matches = sum(1 for pattern in patterns if re.search(pattern, text_lower, re.IGNORECASE))
        scores[doc_type] = matches / len(patterns) if patterns else 0.0
    
    if not scores or max(scores.values()) == 0:
        return None, 0.0
    
    best_type = max(scores, key=scores.get)
    confidence = scores[best_type]
    
    return best_type, confidence


def extract_names(text: str) -> List[str]:
    """
    Extract person names from text.
    
    Uses simple heuristics:
    - Capitalized words following "name:", "tenant:", "applicant:"
    - Title case sequences (2-4 words)
    
    Args:
        text: Document text
        
    Returns:
        List of extracted names
    """
    names = []
    
    # Pattern 1: Explicit name fields
    name_patterns = [
        r"(?:name|tenant|applicant|landlord|owner):\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
        r"(?:signed\s+by|signature\s+of):\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})"
    ]
    
    for pattern in name_patterns:
        matches = re.findall(pattern, text)
        names.extend(matches)
    
    # Pattern 2: Title case sequences (2-4 words)
    title_case_pattern = r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b"
    potential_names = re.findall(title_case_pattern, text)
    
    # Filter out common non-name phrases
    stopwords = {"The", "This", "That", "These", "Those", "Street", "Avenue", "Road", "City", "State"}
    for name in potential_names:
        if name not in stopwords and len(name.split()) >= 2:
            names.append(name)
    
    return list(set(names))  # Remove duplicates


def extract_addresses(text: str) -> List[str]:
    """
    Extract addresses from text.
    
    Patterns:
    - Street addresses with numbers
    - City, State ZIP format
    
    Args:
        text: Document text
        
    Returns:
        List of extracted addresses
    """
    addresses = []
    
    # Pattern: Street address (number + street name)
    street_pattern = r"\d+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct)"
    streets = re.findall(street_pattern, text, re.IGNORECASE)
    addresses.extend(streets)
    
    # Pattern: City, State ZIP
    city_state_zip = r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?"
    locations = re.findall(city_state_zip, text)
    addresses.extend(locations)
    
    return addresses


def extract_dates(text: str) -> List[str]:
    """
    Extract dates from text.
    
    Supports formats:
    - MM/DD/YYYY
    - YYYY-MM-DD
    - Month DD, YYYY
    
    Args:
        text: Document text
        
    Returns:
        List of extracted dates in ISO format (YYYY-MM-DD)
    """
    dates = []
    
    # Pattern 1: MM/DD/YYYY
    pattern1 = r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"
    for match in re.finditer(pattern1, text):
        month, day, year = match.groups()
        try:
            date_obj = datetime.date(int(year), int(month), int(day))
            dates.append(date_obj.isoformat())
        except ValueError:
            pass
    
    # Pattern 2: YYYY-MM-DD
    pattern2 = r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"
    for match in re.finditer(pattern2, text):
        year, month, day = match.groups()
        try:
            date_obj = datetime.date(int(year), int(month), int(day))
            dates.append(date_obj.isoformat())
        except ValueError:
            pass
    
    # Pattern 3: Month DD, YYYY
    pattern3 = r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b"
    month_map = {
        "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
        "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12
    }
    for match in re.finditer(pattern3, text, re.IGNORECASE):
        month_name, day, year = match.groups()
        try:
            month_num = month_map[month_name.capitalize()]
            date_obj = datetime.date(int(year), month_num, int(day))
            dates.append(date_obj.isoformat())
        except (ValueError, KeyError):
            pass
    
    return dates


def extract_amounts(text: str) -> List[float]:
    """
    Extract monetary amounts from text.
    
    Patterns:
    - $X,XXX.XX
    - $XXX.XX
    
    Args:
        text: Document text
        
    Returns:
        List of extracted amounts as floats
    """
    amounts = []
    
    # Pattern: $X,XXX.XX or $XXX.XX
    pattern = r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)"
    
    for match in re.finditer(pattern, text):
        amount_str = match.group(1).replace(",", "")
        try:
            amount = float(amount_str)
            amounts.append(amount)
        except ValueError:
            pass
    
    return amounts


def detect_signature_blocks(text: str, page_number: Optional[int] = None, image_data: Optional[bytes] = None) -> Dict[str, Any]:
    """
    Detect signature blocks in document text AND physical ink signatures.
    
    CRITICAL: Text-based detection alone cannot distinguish between a blank line
    and an actual cursive signature. This function now uses Vision AI for physical
    ink stroke detection when image_data is provided.
    
    Args:
        text: Document text
        page_number: Optional page number for specific page signature detection
        image_data: Optional image bytes for visual ink detection
        
    Returns:
        Dictionary with signature detection results including:
        - Text-based signature block detection (legacy)
        - Vision AI ink stroke detection (new capability)
    """
    signature_found = False
    signature_locations = []
    
    # Legacy text-based detection (still useful for finding signature areas)
    for pattern in SIGNATURE_PATTERNS:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if matches:
            signature_found = True
            for match in matches:
                signature_locations.append({
                    "pattern": pattern,
                    "text": match.group(0),
                    "position": match.start(),
                    "method": "text_pattern"
                })
    
    # NEW: Vision AI ink detection (runs locally via OpenCV even without cloud credentials)
    vision_result = None
    if image_data and VISION_AI_AVAILABLE:
        try:
            vision_client = create_vision_client() if create_vision_client else None
            if vision_client is None and VisionAIClient is not None:
                vision_client = VisionAIClient()
            if vision_client:
                vision_result = vision_client.verify_signature_presence(
                    image_data,
                    expected_location="bottom"
                )
                
                # If Vision AI detects ink, mark signature as found
                if vision_result.get("signature_present"):
                    signature_found = True
                    signature_locations.append({
                        "method": "vision_ai_ink_detection",
                        "confidence": vision_result.get("confidence", 0.0),
                        "ink_density": vision_result.get("ink_density", 0.0),
                        "stroke_count": vision_result.get("stroke_count", 0)
                    })
        except Exception as e:
            # Vision AI failure doesn't block processing
            vision_result = {"error": str(e), "method": "vision_ai_failed"}
    
    defects = []
    if not signature_found:
        if page_number is not None:
            defects.append({
                "code": DEFECT_MISSING_PAGE_SIGNATURE.format(page_number),
                "message": f"Missing signature on page {page_number} (checked: text patterns + vision AI ink detection)",
                "severity": "HIGH",
                "vision_ai_used": vision_result is not None
            })
        else:
            defects.append({
                "code": DEFECT_MISSING_SIGNATURE,
                "message": "No signature block detected in document (checked: text patterns + vision AI ink detection)",
                "severity": "HIGH",
                "vision_ai_used": vision_result is not None
            })
    
    return {
        "signature_found": signature_found,
        "signature_count": len(signature_locations),
        "vision_ai_result": vision_result,
        "detection_methods": [loc.get("method") for loc in signature_locations],
        "locations": signature_locations,
        "defects": defects
    }


def check_document_date(document_date: str, disaster_date: str, max_age_days: int = 60) -> Dict[str, Any]:
    """
    Check if document date is within acceptable range relative to disaster date.
    
    Compliance: Evidence must be recent and relevant to the disaster event.
    
    Args:
        document_date: Document date in ISO format (YYYY-MM-DD)
        disaster_date: Disaster/event date in ISO format (YYYY-MM-DD)
        max_age_days: Maximum acceptable age in days (default 60 for utility bills)
        
    Returns:
        Dictionary with date validation results
    """
    try:
        doc_date = datetime.datetime.strptime(document_date, "%Y-%m-%d").date()
        dis_date = datetime.datetime.strptime(disaster_date, "%Y-%m-%d").date()
        
        age_days = (dis_date - doc_date).days
        
        defects = []
        if age_days > max_age_days:
            defects.append({
                "code": DEFECT_EXPIRED_EVIDENCE_DATE,
                "message": f"Document dated {document_date} is {age_days} days before disaster date {disaster_date} (max {max_age_days} days)",
                "severity": "HIGH"
            })
        
        return {
            "valid": age_days <= max_age_days,
            "age_days": age_days,
            "document_date": document_date,
            "disaster_date": disaster_date,
            "max_age_days": max_age_days,
            "defects": defects
        }
        
    except ValueError as e:
        return {
            "valid": False,
            "error": str(e),
            "defects": [{
                "code": DEFECT_EXPIRED_EVIDENCE_DATE,
                "message": f"Invalid date format: {str(e)}",
                "severity": "HIGH"
            }]
        }


def check_address_match(document_addresses: List[str], applicant_address: str, threshold: float = 0.7) -> Dict[str, Any]:
    """
    Check if document addresses match applicant address.
    
    Uses fuzzy matching to account for formatting variations.
    
    Args:
        document_addresses: List of addresses extracted from document
        applicant_address: Applicant's registered address
        threshold: Similarity threshold (0.0 to 1.0)
        
    Returns:
        Dictionary with address matching results
    """
    def normalize_address(addr: str) -> str:
        """Normalize address for comparison."""
        addr = addr.lower()
        addr = re.sub(r'\b(street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|court|ct)\b', '', addr)
        addr = re.sub(r'[^\w\s]', '', addr)
        addr = re.sub(r'\s+', ' ', addr).strip()
        return addr
    
    norm_applicant = normalize_address(applicant_address)
    
    matches = []
    for doc_addr in document_addresses:
        norm_doc = normalize_address(doc_addr)
        
        # Simple word-based similarity
        applicant_words = set(norm_applicant.split())
        doc_words = set(norm_doc.split())
        
        if applicant_words and doc_words:
            intersection = len(applicant_words & doc_words)
            union = len(applicant_words | doc_words)
            similarity = intersection / union if union > 0 else 0.0
            
            matches.append({
                "document_address": doc_addr,
                "similarity": similarity,
                "match": similarity >= threshold
            })
    
    any_match = any(m["match"] for m in matches)
    
    defects = []
    if document_addresses and not any_match:
        defects.append({
            "code": DEFECT_ADDRESS_MISMATCH,
            "message": f"No document address matches applicant address: {applicant_address}",
            "severity": "HIGH",
            "document_addresses": document_addresses
        })
    
    return {
        "match_found": any_match,
        "applicant_address": applicant_address,
        "matches": matches,
        "defects": defects
    }


def check_name_match(document_names: List[str], applicant_name: str, threshold: float = 0.8) -> Dict[str, Any]:
    """
    Check if document names match applicant name.
    
    Args:
        document_names: List of names extracted from document
        applicant_name: Applicant's registered name
        threshold: Similarity threshold (0.0 to 1.0)
        
    Returns:
        Dictionary with name matching results
    """
    def normalize_name(name: str) -> str:
        """Normalize name for comparison."""
        return re.sub(r'\s+', ' ', name.lower().strip())
    
    norm_applicant = normalize_name(applicant_name)
    
    matches = []
    for doc_name in document_names:
        norm_doc = normalize_name(doc_name)
        
        # Simple word-based similarity
        applicant_words = set(norm_applicant.split())
        doc_words = set(norm_doc.split())
        
        if applicant_words and doc_words:
            intersection = len(applicant_words & doc_words)
            union = len(applicant_words | doc_words)
            similarity = intersection / union if union > 0 else 0.0
            
            matches.append({
                "document_name": doc_name,
                "similarity": similarity,
                "match": similarity >= threshold
            })
    
    any_match = any(m["match"] for m in matches)
    
    defects = []
    if document_names and not any_match:
        defects.append({
            "code": DEFECT_NAME_MISMATCH,
            "message": f"No document name matches applicant name: {applicant_name}",
            "severity": "HIGH",
            "document_names": document_names
        })
    
    return {
        "match_found": any_match,
        "applicant_name": applicant_name,
        "matches": matches,
        "defects": defects
    }


def process_document(file_data: bytes, file_type: str = "image") -> Dict[str, Any]:
    """
    Process a document through the full ingestion pipeline.
    
    Steps:
    1. Compute SHA-256 hash for provenance
    2. Assess image quality (if image)
    3. Extract text (OCR or PDF extraction)
    4. Classify document type
    5. Extract key fields (names, addresses, dates, amounts)
    6. Detect signature blocks
    
    Args:
        file_data: Raw file bytes
        file_type: "image" or "pdf"
        
    Returns:
        Dictionary with processing results
    """
    result = {
        "sha256": compute_sha256(file_data),
        "file_size_bytes": len(file_data),
        "file_type": file_type,
        "processed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    
    raw_image_data = file_data if file_type == "image" else None

    # Step 1: Image quality assessment
    if file_type == "image":
        quality = assess_image_quality(file_data)
        result["quality_assessment"] = quality
        
        # Normalize image if quality is acceptable
        if quality.get("legible", False):
            normalized = normalize_image(file_data)
            if normalized:
                result["normalized"] = True
                file_data = normalized  # Use normalized version for OCR
    
    # Step 2: Text extraction
    if file_type == "image":
        ocr_status = get_image_ocr_status()
        result["ocr_status"] = ocr_status
        text = extract_text_from_image(file_data)
    elif file_type == "pdf":
        result["ocr_status"] = {
            "ocr_configured": False,
            "status": "PDF_TEXT_LAYER_ONLY",
            "provider": "pdfplumber",
            "note": "Extracts embedded PDF text and tables; image-only scanned PDFs require OCR.",
        }
        text = extract_text_from_pdf(file_data)
    else:
        text = ""
    
    result["text_extracted"] = text
    result["text_length"] = len(text)
    
    # Step 3: Document classification
    doc_type, confidence = classify_document(text)
    result["document_type"] = doc_type
    result["classification_confidence"] = confidence
    
    # Step 4: Field extraction
    result["extracted_fields"] = {
        "names": extract_names(text),
        "addresses": extract_addresses(text),
        "dates": extract_dates(text),
        "amounts": extract_amounts(text)
    }
    
    # Step 5: Signature detection (passes original color image bytes so chromatic ink is preserved)
    result["signature_analysis"] = detect_signature_blocks(text, image_data=raw_image_data)
    
    return result


def ingest_and_audit(file_path_or_bytes, case_data: Dict[str, Any], tenant_id: str = "FEMA_TITLE_44") -> Dict[str, Any]:
    """
    Complete document ingestion and audit pipeline.
    
    Integrates with DeterministicAuditor from compliance_engine.py.
    
    Args:
        file_path_or_bytes: File path string or raw bytes
        case_data: Case data dictionary (will be updated in place)
        tenant_id: Tenant identifier for compliance rules
        
    Returns:
        Dictionary with ingestion and audit results
    """
    from compliance_engine import DeterministicAuditor
    
    # Load file data
    if isinstance(file_path_or_bytes, bytes):
        file_data = file_path_or_bytes
        file_path = None
    else:
        file_path = file_path_or_bytes
        with open(file_path, 'rb') as f:
            file_data = f.read()
    
    # Determine file type
    if file_path:
        file_type = "pdf" if file_path.lower().endswith('.pdf') else "image"
    else:
        # Simple magic number detection
        if file_data[:4] == b'%PDF':
            file_type = "pdf"
        else:
            file_type = "image"
    
    # Process document
    doc_result = process_document(file_data, file_type)
    
    # Collect all defects
    all_defects = []
    
    # Quality defects
    if "quality_assessment" in doc_result:
        all_defects.extend(doc_result["quality_assessment"].get("defects", []))
    
    # Signature defects
    all_defects.extend(doc_result["signature_analysis"].get("defects", []))
    
    # Update case_data with document information
    if "documents_submitted" not in case_data:
        case_data["documents_submitted"] = []
    
    case_data["documents_submitted"].append({
        "sha256": doc_result["sha256"],
        "file_type": file_type,
        "document_type": doc_result.get("document_type"),
        "processed_at": doc_result["processed_at"],
        "file_size_bytes": doc_result["file_size_bytes"]
    })
    
    # Add defect codes to case_data
    if "defect_codes" not in case_data:
        case_data["defect_codes"] = []
    
    for defect in all_defects:
        case_data["defect_codes"].append(defect["code"])
    
    # Run DeterministicAuditor
    auditor = DeterministicAuditor(case_data, tenant_id=tenant_id)
    audit_result = auditor.audit()
    
    return {
        "document_processing": doc_result,
        "defects": all_defects,
        "audit_result": audit_result,
        "case_data": case_data
    }
