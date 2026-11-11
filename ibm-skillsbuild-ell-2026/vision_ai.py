"""
ASCE Vision AI Module
=====================

Addresses the "Visual Ink Signature Verification & Cursive Handwriting" gap.

Uses IBM Granite Vision (when configured) and local OpenCV contour geometry for:
1. Physical ink stroke detection in signature boxes
2. Cursive handwriting OCR status reporting (explicit when not configured)
3. Visual document quality assessment

This bridges the gap between text-based parsing and real-world handwritten documents
while enforcing strict anti-hallucination guardrails on blank, printed, and scribble inputs.
"""

import os
import base64
import cv2
import numpy as np
from typing import Dict, Any, Optional, Tuple
import requests
from dotenv import load_dotenv

load_dotenv()


class VisionAIClient:
    """
    IBM Granite Vision & OpenCV client for visual document analysis.

    Handles:
    - Ink signature detection (physical stroke presence)
    - Cursive handwriting OCR (honest unconfigured status when no Vision model is active)
    - Document quality assessment
    - Bounding box analysis
    """

    def __init__(self, api_key: str = None, project_id: str = None):
        """
        Initialize Vision AI client.

        Args:
            api_key: IBM Cloud API key
            project_id: Watsonx project ID
        """
        self.api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY")
        self.project_id = project_id or os.environ.get("IBM_WATSONX_PROJECT_ID")
        self.vision_endpoint = "https://us-south.ml.cloud.ibm.com/ml/v1/text/generation"
        self._cached_token = None

        # Granite Vision model for visual understanding
        self.vision_model = "ibm/granite-vision-3b"

    def generate_iam_token(self) -> str:
        """Generate IAM bearer token for IBM Cloud."""
        url = "https://iam.cloud.ibm.com/identity/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = f"grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={self.api_key}"
        response = requests.post(url, headers=headers, data=data, timeout=15)
        response.raise_for_status()
        self._cached_token = response.json()["access_token"]
        return self._cached_token

    @staticmethod
    def _mask_rotation_border_artifacts(gray: np.ndarray) -> np.ndarray:
        """
        Identify solid black (<= 3) border wedges introduced by affine rotation
        (cv2.warpAffine fills empty triangular borders with 0,0,0).
        Only masks connected very-dark components touching at least one outer corner
        so legitimate interior ink strokes are never removed.
        """
        h, w = gray.shape[:2]
        if h == 0 or w == 0:
            return np.zeros_like(gray, dtype=bool)
        near_black = (gray <= 3).astype(np.uint8)
        num_labels, labels = cv2.connectedComponents(near_black, connectivity=8)
        artifact_mask = np.zeros_like(gray, dtype=bool)
        if num_labels <= 1:
            return artifact_mask
        corners = {(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)}
        corner_labels = {int(labels[r, c]) for r, c in corners if labels[r, c] > 0}
        if not corner_labels:
            return artifact_mask
        for lbl in corner_labels:
            artifact_mask |= (labels == lbl)
        return artifact_mask

    @staticmethod
    def _max_angle_reversals(contour: np.ndarray) -> Tuple[int, float]:
        """
        Estimate sharp direction reversals and closed-contour path tortuosity.
        Genuine signatures sweep mostly left-to-right or in smooth loops; chaotic
        random zig-zag scribbles exhibit many sharp acute reversals and huge perimeter
        relative to horizontal span.
        """
        approx = cv2.approxPolyDP(contour, 4.0, False)
        pts = approx.reshape(-1, 2)
        reversals = 0
        if len(pts) >= 3:
            v = np.diff(pts.astype(np.float32), axis=0)
            norms = np.linalg.norm(v, axis=1)
            keep = norms >= 8.0
            v = v[keep]
            norms = norms[keep]
            for i in range(len(v) - 1):
                cos_theta = float(np.dot(v[i], v[i + 1]) / (norms[i] * norms[i + 1] + 1e-6))
                if cos_theta < -0.2:
                    reversals += 1
        x_c, y_c, w_c, h_c = cv2.boundingRect(contour)
        arc_len = float(cv2.arcLength(contour, True))
        span = float(max(w_c, 1))
        return reversals, arc_len / span

    def detect_ink_signature(
        self,
        image_data: bytes,
        signature_bbox: Optional[Tuple[int, int, int, int]] = None,
    ) -> Dict[str, Any]:
        """
        Detect physical ink presence in signature area using OpenCV.

        Distinguishes real handwritten/wet-ink signatures (including rotated and
        high-contrast black ink curves) from blank pages, printed typography,
        table rules, very faint artifacts, and random zigzag scribbles.

        Args:
            image_data: Image bytes (PNG, JPG, etc.)
            signature_bbox: Optional (x, y, width, height) bounding box for signature area

        Returns:
            Dictionary with:
            - signature_detected: bool
            - confidence: float (0-1)
            - ink_density: float (0-1)
            - stroke_count: int
            - method: str ("opencv_contour")
        """
        try:
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img is None:
                return {
                    "signature_detected": False,
                    "confidence": 0.0,
                    "error": "Failed to decode image",
                }

            if signature_bbox:
                x, y, w, h = signature_bbox
                signature_region = img[y:y + h, x:x + w]
            else:
                height = img.shape[0]
                signature_region = img[int(height * 0.8):, :]

            if signature_region.size == 0:
                return {
                    "signature_detected": False,
                    "confidence": 0.0,
                    "ink_density": 0.0,
                    "stroke_count": 0,
                    "method": "opencv_contour",
                    "bbox_used": signature_bbox is not None,
                }

            gray = cv2.cvtColor(signature_region, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(signature_region, cv2.COLOR_BGR2HSV)

            # Mask warpAffine black rotation wedges touching corners
            border_artifact = self._mask_rotation_border_artifacts(gray)
            valid_gray = gray[~border_artifact]
            bg_level = float(np.percentile(valid_gray, 75)) if valid_gray.size > 0 else 255.0

            # Contrast relative to paper background (rejects very faint marks like #dcdcdc on white)
            diff = bg_level - gray.astype(np.float32)
            min_contrast = max(55.0, bg_level * 0.24)
            sat = hsv[:, :, 1]
            val = hsv[:, :, 2]

            chromatic_ink = (sat >= 45) & (val >= 25) & (diff >= 28.0) & (~border_artifact)
            achromatic_ink = (~chromatic_ink) & (diff >= min_contrast) & (~border_artifact)

            # Suppress pure long straight horizontal/vertical rules ONLY on achromatic toner/lines,
            # preserving shallow chromatic pen curves even when rotated.
            achrom_u8 = (achromatic_ink.astype(np.uint8) * 255)
            h_len = max(140, min(200, signature_region.shape[1] // 3))
            v_len = max(55, min(120, signature_region.shape[0] // 2))
            horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
            vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))
            horiz_lines = cv2.morphologyEx(achrom_u8, cv2.MORPH_OPEN, horiz_kernel, iterations=1)
            vert_lines = cv2.morphologyEx(achrom_u8, cv2.MORPH_OPEN, vert_kernel, iterations=1)
            achrom_clean = cv2.subtract(achrom_u8, cv2.bitwise_or(horiz_lines, vert_lines))

            binary_no_lines = cv2.bitwise_or(achrom_clean, (chromatic_ink.astype(np.uint8) * 255))

            contours, _ = cv2.findContours(
                binary_no_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            # Check whole-cluster scribble characteristics using 4x downscaled closing so dense
            # overlapping random polylines (even if segmented slightly) are seen as one web.
            reg_h, reg_w = binary_no_lines.shape[:2]
            pad_h = (-reg_h) % 4
            pad_w = (-reg_w) % 4
            padded = np.pad(binary_no_lines, ((0, pad_h), (0, pad_w)), mode="constant", constant_values=0)
            small_bin = padded.reshape((reg_h + pad_h) // 4, 4, (reg_w + pad_w) // 4, 4).max(axis=(1, 3))
            close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
            small_closed = cv2.morphologyEx(small_bin, cv2.MORPH_CLOSE, close_k)
            cluster_contours, _ = cv2.findContours(
                small_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            multi_cluster_scribble = False
            for cc in cluster_contours:
                cx, cy, cw, ch = cv2.boundingRect(cc)
                if cw * 4 >= 90 and ch * 4 >= 32:
                    # Measure ink density and perimeter inside original binary_no_lines bbox
                    bx0, by0 = max(0, cx * 4), max(0, cy * 4)
                    bx1, by1 = min(reg_w, (cx + cw) * 4 + 4), min(reg_h, (cy + ch) * 4 + 4)
                    patch = binary_no_lines[by0:by1, bx0:bx1]
                    patch_cnts, _ = cv2.findContours(patch, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    total_arc = sum(cv2.arcLength(pc, True) for pc in patch_cnts)
                    patch_sat_mask = patch > 0
                    patch_hsv = hsv[by0:by1, bx0:bx1]
                    mean_cluster_sat = float(np.mean(patch_hsv[:, :, 1][patch_sat_mask])) if np.any(patch_sat_mask) else 0.0
                    bw = max(bx1 - bx0, 1)
                    if mean_cluster_sat < 40.0 and (total_arc / float(bw)) >= 4.2:
                        multi_cluster_scribble = True
                        break

            cursive_strokes = []
            chromatic_stroke_count = 0
            printed_glyph_count = 0
            cursive_ink_pixels = 0
            scribble_like_detected = multi_cluster_scribble

            for c in contours:
                area = cv2.contourArea(c)
                if area < 10:
                    continue
                x_c, y_c, w_c, h_c = cv2.boundingRect(c)
                aspect = w_c / float(max(h_c, 1))
                arc_len = cv2.arcLength(c, False)
                closed_arc = cv2.arcLength(c, True)

                roi_hsv = hsv[y_c:y_c + h_c, x_c:x_c + w_c]
                roi_mask = binary_no_lines[y_c:y_c + h_c, x_c:x_c + w_c] > 0
                mean_sat = float(np.mean(roi_hsv[:, :, 1][roi_mask])) if np.any(roi_mask) else 0.0

                # Machine-printed typography glyphs: uniform discrete letter boxes with low saturation
                if w_c <= 35 and h_c <= 32 and mean_sat < 40.0:
                    printed_glyph_count += 1
                    continue

                # Ignore purely thin horizontal/vertical table rules or underlines
                if h_c <= 5 or aspect > 25.0 or aspect < 0.08:
                    continue

                # Check scribble / chaotic polyline tortuosity on achromatic blobs.
                # Genuine black-ink signatures have smooth horizontal trajectories
                # (low tortuosity perim_to_span < 2.32, <= 2 sharp reversals, compact band),
                # whereas tall multi-reversal polylines or dense webs are scribbles.
                reversals, perim_to_span = self._max_angle_reversals(c)
                if mean_sat < 40.0 and (
                    (h_c >= 32 and (perim_to_span >= 4.0 or (reversals >= 5 and perim_to_span >= 3.0)))
                    or (w_c >= 100 and h_c >= 50 and (perim_to_span >= 2.32 or reversals >= 3))
                    or (w_c >= 100 and h_c >= 25 and reversals >= 4)
                ):
                    scribble_like_detected = True
                    continue

                extent = area / float(max(w_c * h_c, 1))
                is_chromatic_wet_ink = (mean_sat >= 45.0 and (w_c >= 18 or h_c >= 12) and arc_len >= 35.0)
                is_achromatic_curve = (
                    mean_sat < 40.0
                    and w_c >= 38
                    and h_c >= 10
                    and arc_len >= 85.0
                    and extent < 0.72
                )

                if is_chromatic_wet_ink or is_achromatic_curve:
                    cursive_strokes.append(c)
                    if is_chromatic_wet_ink:
                        chromatic_stroke_count += 1
                    # Multi-letter continuous stroke counts toward stroke richness ONLY when it forms
                    # a smooth, bounded-height signature sweep rather than a chaotic scribble.
                    if (
                        w_c >= 120
                        and arc_len >= 220.0
                        and (
                            mean_sat >= 45.0
                            or (h_c <= 46 and perim_to_span < 2.32 and reversals <= 2)
                        )
                    ):
                        cursive_strokes.append(c)
                    cursive_ink_pixels += int(np.sum(roi_mask))

            stroke_count = len(cursive_strokes)

            # Anti-false-positive guardrails
            if printed_glyph_count >= 20 and chromatic_stroke_count == 0 and stroke_count < 3:
                stroke_count = 0
                cursive_ink_pixels = 0

            if scribble_like_detected and chromatic_stroke_count == 0:
                stroke_count = 0
                cursive_ink_pixels = 0

            total_pixels = max(gray.size, 1)
            ink_density = cursive_ink_pixels / float(total_pixels)

            signature_detected = (stroke_count >= 2 and ink_density >= 0.0005)

            if not signature_detected:
                confidence = 0.0
            else:
                confidence = min(0.99, (stroke_count / 4.0) * 0.65 + min(ink_density * 50.0, 0.34))

            return {
                "signature_detected": signature_detected,
                "confidence": float(confidence),
                "ink_density": float(ink_density),
                "stroke_count": stroke_count,
                "printed_glyphs_filtered": printed_glyph_count,
                "scribble_filtered": scribble_like_detected,
                "method": "opencv_contour",
                "bbox_used": signature_bbox is not None,
            }

        except Exception as e:
            return {
                "signature_detected": False,
                "confidence": 0.0,
                "error": str(e),
                "method": "opencv_contour",
            }

    def analyze_handwriting(self, image_data: bytes) -> Dict[str, Any]:
        """
        Analyze cursive handwriting using IBM Granite Vision.

        Reports honest unconfigured status until live Granite Vision OCR integration
        is connected so callers never treat empty OCR output as verified blank text.

        Args:
            image_data: Image bytes containing handwritten text

        Returns:
            Dictionary with honest OCR configuration status
        """
        try:
            _ = base64.b64encode(image_data).decode("utf-8")
            return {
                "text": "",
                "confidence": 0.0,
                "is_handwritten": None,
                "ocr_configured": False,
                "status": "NOT_CONFIGURED",
                "legibility_score": 0.0,
                "method": "granite_vision_placeholder",
                "note": "Granite Vision OCR is not configured in this runtime — no OCR inference was executed.",
            }
        except Exception as e:
            return {
                "text": "",
                "confidence": 0.0,
                "is_handwritten": None,
                "ocr_configured": False,
                "status": "ERROR",
                "error": str(e),
                "method": "granite_vision",
            }

    def assess_document_quality(self, image_data: bytes) -> Dict[str, Any]:
        """
        Assess visual quality of document image.

        Checks for:
        - Blur/focus issues
        - Lighting problems
        - Resolution adequacy

        Args:
            image_data: Image bytes

        Returns:
            Dictionary with quality metrics
        """
        try:
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img is None:
                return {"quality_score": 0.0, "error": "Failed to decode image"}

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            blur_score = min(1.0, laplacian_var / 100.0)

            mean_brightness = np.mean(gray)
            brightness_score = 1.0 - abs(mean_brightness - 128) / 200.0

            contrast = gray.std()
            contrast_score = min(1.0, contrast / 30.0)

            height, width = img.shape[:2]
            min_resolution = 600
            resolution_score = min(1.0, min(height, width) / min_resolution)

            quality_score = (
                blur_score * 0.3
                + brightness_score * 0.2
                + contrast_score * 0.3
                + resolution_score * 0.2
            )

            return {
                "quality_score": float(quality_score),
                "blur_score": float(blur_score),
                "brightness_score": float(brightness_score),
                "contrast_score": float(contrast_score),
                "resolution_score": float(resolution_score),
                "dimensions": {"width": width, "height": height},
                "is_acceptable": quality_score >= 0.6,
            }

        except Exception as e:
            return {"quality_score": 0.0, "error": str(e)}

    def verify_signature_presence(
        self, pdf_page_image: bytes, expected_location: str = "bottom"
    ) -> Dict[str, Any]:
        """
        High-level signature verification for document ingestion.

        Combines ink detection with location heuristics.

        Args:
            pdf_page_image: Image bytes of PDF page
            expected_location: "bottom", "top", or "middle"

        Returns:
            Verification result with signature status
        """
        try:
            nparr = np.frombuffer(pdf_page_image, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img is None:
                return {
                    "signature_present": False,
                    "confidence": 0.0,
                    "error": "Failed to decode image",
                }

            height, width = img.shape[:2]

            if expected_location == "bottom":
                bbox = (0, int(height * 0.75), width, int(height * 0.25))
            elif expected_location == "top":
                bbox = (0, 0, width, int(height * 0.25))
            else:
                bbox = (0, int(height * 0.4), width, int(height * 0.2))

            result = self.detect_ink_signature(pdf_page_image, bbox)

            return {
                "signature_present": result.get("signature_detected", False),
                "confidence": result.get("confidence", 0.0),
                "ink_density": result.get("ink_density", 0.0),
                "stroke_count": result.get("stroke_count", 0),
                "search_location": expected_location,
                "bbox": bbox,
                "method": "opencv_ink_detection",
            }

        except Exception as e:
            return {
                "signature_present": False,
                "confidence": 0.0,
                "error": str(e),
            }


def create_vision_client() -> Optional[VisionAIClient]:
    """
    Factory function to create cloud-credentialed Vision AI client.

    Returns None if IBM Cloud / watsonx credentials are not configured.
    Note: Local OpenCV signature detection on VisionAIClient() does not
    require cloud credentials.
    """
    try:
        api_key = os.environ.get("IBM_CLOUD_API_KEY")
        project_id = os.environ.get("IBM_WATSONX_PROJECT_ID")

        if not api_key or not project_id:
            return None

        return VisionAIClient(api_key, project_id)
    except Exception:
        return None


if __name__ == "__main__":
    print("=== ASCE Vision AI Module ===")
    client = create_vision_client()
    if client:
        print("Vision AI client initialized with IBM Cloud credentials.")
    else:
        print("Local OpenCV VisionAIClient ready (IBM Granite Vision cloud OCR not configured).")
