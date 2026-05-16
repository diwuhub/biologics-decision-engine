"""
D1: Analytical Method Extractor (Phase 3 Track D).

Extracts analytical method validation data per ICH Q2(R2).
Detects validation studies (specificity, linearity, range, accuracy,
precision, LOD, LOQ, robustness), assesses completeness, and
identifies gaps.

Contract:
- extract_attributes() MUST NOT raise unhandled exceptions.
- extract_evidence() MUST NOT raise unhandled exceptions.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ingestion.base_extractor import BaseExtractor
from specs.cross_document_bridge import ExtractedAttribute

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# D2: AnalyticalMethodEvidence dataclass
# ---------------------------------------------------------------------------

@dataclass
class AnalyticalMethodEvidence:
    """Evidence payload from an analytical method validation report."""
    method_name: Optional[str] = None
    validation_studies_found: List[str] = field(default_factory=list)  # accuracy, precision, linearity, etc.
    validation_studies_missing: List[str] = field(default_factory=list)
    completeness_score: float = 0.0
    accuracy_recovery: Optional[float] = None  # mean recovery %
    precision_rsd: Optional[float] = None  # %RSD
    linearity_r2: Optional[float] = None
    loq_reported: bool = False
    lod_reported: bool = False
    critical_gaps: List[str] = field(default_factory=list)
    reviewer_concerns: List[str] = field(default_factory=list)
    tables_found: int = 0
    extractor: str = "AnalyticalMethodExtractor"


# ---------------------------------------------------------------------------
# ICH Q2(R2) required validation studies
# ---------------------------------------------------------------------------

_ICH_Q2_STUDIES: Dict[str, Dict[str, Any]] = {
    "specificity": {
        "label": "Specificity",
        "patterns": [
            r"\bspecificity\b",
            r"\bselectivity\b",
            r"\bplacebo\s+interference\b",
        ],
    },
    "linearity": {
        "label": "Linearity",
        "patterns": [
            r"\blinearity\b",
            r"\blinear\s+range\b",
            r"\bcalibration\s+curve\b",
            r"\bR[²2]\b",
            r"\bR\s*-?\s*squared\b",
            r"\bcorrelation\s+coefficient\b",
        ],
    },
    "range": {
        "label": "Range",
        "patterns": [
            r"\brange\b",
            r"\bworking\s+range\b",
            r"\banalytical\s+range\b",
        ],
    },
    "accuracy": {
        "label": "Accuracy",
        "patterns": [
            r"\baccuracy\b",
            r"\brecovery\b",
            r"\btrueness\b",
            r"\bspiked?\s+recovery\b",
        ],
    },
    "precision_repeatability": {
        "label": "Precision (Repeatability)",
        "patterns": [
            r"\brepeatability\b",
            r"\bprecision\b",
            r"\b%?\s*RSD\b",
            r"\brelative\s+standard\s+deviation\b",
            r"\bintra[-\s]?(?:day|assay)\b",
        ],
    },
    "precision_intermediate": {
        "label": "Precision (Intermediate)",
        "patterns": [
            r"\bintermediate\s+precision\b",
            r"\binter[-\s]?(?:day|assay|analyst)\b",
            r"\bruggedness\b",
        ],
    },
    "lod": {
        "label": "LOD (Limit of Detection)",
        "patterns": [
            r"\bLOD\b",
            r"\blimit\s+of\s+detection\b",
            r"\bdetection\s+limit\b",
        ],
    },
    "loq": {
        "label": "LOQ (Limit of Quantitation)",
        "patterns": [
            r"\bLOQ\b",
            r"\blimit\s+of\s+quantit?ation\b",
            r"\bquantit?ation\s+limit\b",
        ],
    },
    "robustness": {
        "label": "Robustness",
        "patterns": [
            r"\brobustness\b",
            r"\brobust\b",
            r"\bdeliberate\s+(?:change|variation)\b",
        ],
    },
}

# Patterns for extracting specific numeric values
_RECOVERY_PATTERNS = [
    r"\brecovery\s*[:=]\s*([\d.]+)\s*%",
    r"\bmean\s+recovery\s*[:=]\s*([\d.]+)\s*%",
    r"\baccuracy\s*(?:\(recovery\))?\s*[:=]\s*([\d.]+)\s*%",
    r"\b([\d]{2,3}(?:\.[\d]+)?)\s*%\s*(?:recovery|mean\s+recovery)",
]

_RSD_PATTERNS = [
    r"\b%?\s*RSD\s*[:=]\s*([\d.]+)\s*%?",
    r"\bRSD\s*[:=]?\s*([\d.]+)\s*%",
    r"\brelative\s+standard\s+deviation\s*[:=]\s*([\d.]+)",
    r"\bprecision\s*(?:\(?\s*%?\s*RSD\s*\)?)?\s*[:=]\s*([\d.]+)",
]

_R2_PATTERNS = [
    r"\bR[²2]\s*[:=]\s*([\d.]+)",
    r"\bR\s*-?\s*squared\s*[:=]\s*([\d.]+)",
    r"\bcorrelation\s+coefficient\s*[:=]\s*([\d.]+)",
    r"\br[²2]\s*[:=]\s*(0\.[\d]+)",
]

_METHOD_NAME_PATTERNS = [
    r"\bmethod\s*[:=]\s*([^\n,;]+)",
    r"\banalytical\s+method\s*[:=]\s*([^\n,;]+)",
    r"\bmethod\s+name\s*[:=]\s*([^\n,;]+)",
    r"\bvalidation\s+of\s+([^\n,;]+?)\s+method\b",
    r"\bmethod\s+validation\s+(?:report|study)\s+(?:for|of)\s+([^\n,;]+)",
]

# Assessment thresholds from ICH Q2(R2) for biologics
_ACCURACY_RECOVERY_MIN = 98.0
_ACCURACY_RECOVERY_MAX = 102.0
_PRECISION_RSD_PHYSICOCHEMICAL = 2.0  # %RSD <= 2% physicochemical
_PRECISION_RSD_POTENCY = 5.0         # %RSD <= 5% potency
_LINEARITY_R2_MIN = 0.999


class AnalyticalMethodExtractor(BaseExtractor):
    """Extract analytical method validation data per ICH Q2(R2).

    Contract:
    - extract_attributes() NEVER raises.
    - extract_evidence() NEVER raises.
    """

    def extract_attributes(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Extract validation parameters from tables."""
        try:
            return self._extract_attributes_impl(parsed_doc)
        except Exception as e:
            logger.error("AnalyticalMethodExtractor.extract_attributes failed: %s", e)
            return []

    def extract_evidence(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assess ICH Q2(R2) validation completeness."""
        try:
            evidence = self._extract_evidence_impl(parsed_doc)
            return self._evidence_to_dict(evidence)
        except Exception as e:
            logger.error("AnalyticalMethodExtractor.extract_evidence failed: %s", e)
            return {"error": str(e), "extractor": "AnalyticalMethodExtractor"}

    def supported_categories(self) -> List[str]:
        return [
            "analytical_method",
            "validation",
            "method_performance",
        ]

    # ------------------------------------------------------------------
    # Internal: attribute extraction
    # ------------------------------------------------------------------

    def _extract_attributes_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Scan tables for method validation data."""
        doc_path = parsed_doc.get("document_path", "unknown")
        attributes: List[ExtractedAttribute] = []

        for page in parsed_doc.get("pages", []):
            page_num = page.get("page_number", 1)
            for table in page.get("tables", []):
                headers = [h.lower().strip() for h in table.get("headers", [])]
                if not headers:
                    continue

                name_idx = self._find_name_column(headers)
                value_idx = self._find_value_column(headers)
                unit_idx = self._find_unit_column(headers)
                criteria_idx = self._find_criteria_column(headers)

                if name_idx is None:
                    continue

                table_id = table.get("id", "unknown_table")

                for row in table.get("rows", []):
                    raw_headers = table.get("headers", [])
                    name = self._get_cell(row, raw_headers, name_idx)
                    if not name or not name.strip():
                        continue

                    value = None
                    if value_idx is not None:
                        raw_val = self._get_cell(row, raw_headers, value_idx)
                        value = self._try_parse_numeric(raw_val)

                    unit = ""
                    if unit_idx is not None:
                        unit = self._get_cell(row, raw_headers, unit_idx) or ""

                    criteria = ""
                    if criteria_idx is not None:
                        criteria = self._get_cell(row, raw_headers, criteria_idx) or ""

                    attributes.append(
                        ExtractedAttribute(
                            name=name.strip(),
                            value=value if value is not None else 0.0,
                            unit=unit.strip(),
                            source_document=doc_path,
                            source_page=page_num,
                            source_table=table_id,
                            confidence=0.7,
                            context=f"Method validation from {table_id}"
                            + (f" (criteria: {criteria})" if criteria else ""),
                            category="analytical_method",
                            anchor_ids=[str(uuid.uuid4())],
                            extraction_confidence=0.7,
                        )
                    )

        return attributes

    # ------------------------------------------------------------------
    # Internal: evidence extraction
    # ------------------------------------------------------------------

    def _extract_evidence_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> AnalyticalMethodEvidence:
        """Assess ICH Q2(R2) validation coverage."""
        all_text = self._gather_all_text(parsed_doc)
        all_table_text = self._gather_table_text(parsed_doc)
        combined = all_text + " " + all_table_text

        # Detect method name
        method_name = self._detect_method_name(combined)

        # Detect validation studies
        studies_found = []
        studies_missing = []
        for study_key, study_def in _ICH_Q2_STUDIES.items():
            found = False
            for pattern in study_def["patterns"]:
                if re.search(pattern, combined, re.IGNORECASE):
                    found = True
                    break
            if found:
                studies_found.append(study_def["label"])
            else:
                studies_missing.append(study_def["label"])

        total_required = len(_ICH_Q2_STUDIES)
        completeness_score = len(studies_found) / total_required if total_required > 0 else 0.0

        # Extract key numeric values
        accuracy_recovery = self._extract_first_numeric(combined, _RECOVERY_PATTERNS)
        precision_rsd = self._extract_first_numeric(combined, _RSD_PATTERNS)
        linearity_r2 = self._extract_first_numeric(combined, _R2_PATTERNS)

        # Also try from tables
        if accuracy_recovery is None:
            accuracy_recovery = self._extract_value_from_tables(parsed_doc, ["recovery", "accuracy"])
        if precision_rsd is None:
            precision_rsd = self._extract_value_from_tables(parsed_doc, ["rsd", "precision"])
        if linearity_r2 is None:
            linearity_r2 = self._extract_value_from_tables(parsed_doc, ["r2", "r-squared", "correlation"])

        # LOD/LOQ presence
        loq_reported = any(
            re.search(p, combined, re.IGNORECASE)
            for p in _ICH_Q2_STUDIES["loq"]["patterns"]
        )
        lod_reported = any(
            re.search(p, combined, re.IGNORECASE)
            for p in _ICH_Q2_STUDIES["lod"]["patterns"]
        )

        # Count tables
        tables_found = sum(
            len(page.get("tables", []))
            for page in parsed_doc.get("pages", [])
        )

        # Identify critical gaps
        critical_gaps = self._identify_critical_gaps(
            studies_found, studies_missing, accuracy_recovery,
            precision_rsd, linearity_r2, lod_reported, loq_reported,
            combined,
        )

        # Phase 4C: ICH Q2-specific reviewer concerns
        reviewer_concerns = self._predict_reviewer_concerns(
            studies_found, studies_missing, accuracy_recovery,
            precision_rsd, linearity_r2, lod_reported, loq_reported,
            combined,
        )

        return AnalyticalMethodEvidence(
            method_name=method_name,
            validation_studies_found=studies_found,
            validation_studies_missing=studies_missing,
            completeness_score=round(completeness_score, 3),
            accuracy_recovery=accuracy_recovery,
            precision_rsd=precision_rsd,
            linearity_r2=linearity_r2,
            loq_reported=loq_reported,
            lod_reported=lod_reported,
            critical_gaps=critical_gaps,
            reviewer_concerns=reviewer_concerns,
            tables_found=tables_found,
        )

    # ------------------------------------------------------------------
    # Gap identification
    # ------------------------------------------------------------------

    def _identify_critical_gaps(
        self,
        studies_found: List[str],
        studies_missing: List[str],
        accuracy_recovery: Optional[float],
        precision_rsd: Optional[float],
        linearity_r2: Optional[float],
        lod_reported: bool,
        loq_reported: bool,
        combined_text: str,
    ) -> List[str]:
        """Identify critical gaps in method validation."""
        gaps = []

        # Missing key studies
        critical_studies = ["Accuracy", "Linearity", "Specificity"]
        for study in critical_studies:
            if study in studies_missing:
                gaps.append(f"Missing {study} study -- required per ICH Q2(R2)")

        # Both precision types missing
        precision_labels = [
            _ICH_Q2_STUDIES["precision_repeatability"]["label"],
            _ICH_Q2_STUDIES["precision_intermediate"]["label"],
        ]
        if all(p in studies_missing for p in precision_labels):
            gaps.append("No precision data (repeatability or intermediate) -- required per ICH Q2(R2)")

        # Accuracy out of range for biologics
        if accuracy_recovery is not None:
            if accuracy_recovery < _ACCURACY_RECOVERY_MIN or accuracy_recovery > _ACCURACY_RECOVERY_MAX:
                gaps.append(
                    f"Accuracy recovery ({accuracy_recovery:.1f}%) outside {_ACCURACY_RECOVERY_MIN}-{_ACCURACY_RECOVERY_MAX}% range for biologics"
                )

        # Precision too high
        if precision_rsd is not None:
            # Determine if potency method
            is_potency = bool(re.search(r"\bpotency\b", combined_text, re.IGNORECASE))
            threshold = _PRECISION_RSD_POTENCY if is_potency else _PRECISION_RSD_PHYSICOCHEMICAL
            if precision_rsd > threshold:
                method_type = "potency" if is_potency else "physicochemical"
                gaps.append(
                    f"Precision RSD ({precision_rsd:.1f}%) exceeds {threshold}% limit for {method_type} methods"
                )

        # Linearity below threshold
        if linearity_r2 is not None:
            if linearity_r2 < _LINEARITY_R2_MIN:
                gaps.append(
                    f"Linearity R2 ({linearity_r2:.4f}) below {_LINEARITY_R2_MIN} threshold"
                )

        # Missing LOD/LOQ for impurity methods
        is_impurity = bool(re.search(r"\bimpurit", combined_text, re.IGNORECASE))
        if is_impurity:
            if not lod_reported:
                gaps.append("Missing LOD for impurity method -- major gap per ICH Q2(R2)")
            if not loq_reported:
                gaps.append("Missing LOQ for impurity method -- major gap per ICH Q2(R2)")

        # Low overall completeness
        total = len(studies_found) + len(studies_missing)
        if total > 0 and len(studies_found) / total < 0.5:
            gaps.append(
                f"Low validation completeness ({len(studies_found)}/{total} studies) "
                f"-- incomplete per ICH Q2(R2)"
            )

        return gaps

    # ------------------------------------------------------------------
    # Phase 4C: Reviewer concern prediction (ICH Q2-specific)
    # ------------------------------------------------------------------

    def _predict_reviewer_concerns(
        self,
        studies_found: List[str],
        studies_missing: List[str],
        accuracy_recovery: Optional[float],
        precision_rsd: Optional[float],
        linearity_r2: Optional[float],
        lod_reported: bool,
        loq_reported: bool,
        combined_text: str,
    ) -> List[str]:
        """Predict ICH Q2(R2)-specific reviewer concerns for method validation."""
        concerns = []

        # Missing robustness study
        robustness_label = _ICH_Q2_STUDIES["robustness"]["label"]
        if robustness_label in studies_missing:
            concerns.append(
                "Robustness study not reported. Reviewer will question method "
                "reliability under deliberate variation of parameters per ICH Q2(R2)."
            )

        # Precision above threshold
        if precision_rsd is not None:
            is_potency = bool(re.search(r"\bpotency\b", combined_text, re.IGNORECASE))
            threshold = _PRECISION_RSD_POTENCY if is_potency else _PRECISION_RSD_PHYSICOCHEMICAL
            if precision_rsd > threshold:
                method_type = "potency" if is_potency else "physicochemical"
                concerns.append(
                    f"Precision (%RSD = {precision_rsd:.1f}%) exceeds {threshold}% "
                    f"threshold for {method_type} methods. Reviewer will likely "
                    f"request method improvement or justification."
                )

        # Range not covering specification
        range_label = _ICH_Q2_STUDIES["range"]["label"]
        if range_label in studies_missing:
            concerns.append(
                "Analytical range not demonstrated. Reviewer will question whether "
                "the method covers the full specification range per ICH Q2(R2)."
            )

        # Missing intermediate precision
        intermediate_label = _ICH_Q2_STUDIES["precision_intermediate"]["label"]
        if intermediate_label in studies_missing:
            concerns.append(
                "Intermediate precision not assessed. Reviewer may require "
                "inter-day/inter-analyst variability data for method transfer."
            )

        # Accuracy outside acceptance range
        if accuracy_recovery is not None:
            if accuracy_recovery < _ACCURACY_RECOVERY_MIN or accuracy_recovery > _ACCURACY_RECOVERY_MAX:
                concerns.append(
                    f"Accuracy recovery ({accuracy_recovery:.1f}%) outside "
                    f"{_ACCURACY_RECOVERY_MIN}-{_ACCURACY_RECOVERY_MAX}% range. "
                    f"Reviewer will flag potential systematic bias."
                )

        # Linearity below threshold
        if linearity_r2 is not None and linearity_r2 < _LINEARITY_R2_MIN:
            concerns.append(
                f"Linearity R2 ({linearity_r2:.4f}) below {_LINEARITY_R2_MIN} threshold. "
                f"Reviewer will question method suitability for quantitative analysis."
            )

        # Low overall completeness
        total = len(studies_found) + len(studies_missing)
        if total > 0 and len(studies_found) / total < 0.5:
            concerns.append(
                f"Low validation completeness ({len(studies_found)}/{total} studies). "
                f"Reviewer will require comprehensive revalidation per ICH Q2(R2)."
            )

        return concerns

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def _detect_method_name(self, text: str) -> Optional[str]:
        """Extract the method name from text."""
        for pattern in _METHOD_NAME_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                name = m.group(1).strip()
                if len(name) > 3 and len(name) < 100:
                    return name
        return None

    def _extract_first_numeric(
        self, text: str, patterns: List[str]
    ) -> Optional[float]:
        """Extract first numeric match from text using patterns."""
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1))
                except (ValueError, IndexError):
                    continue
        return None

    def _extract_value_from_tables(
        self, parsed_doc: Dict[str, Any], name_keywords: List[str]
    ) -> Optional[float]:
        """Try to extract a numeric value from tables by attribute name keywords."""
        for page in parsed_doc.get("pages", []):
            for table in page.get("tables", []):
                for row in table.get("rows", []):
                    row_cells = []
                    if isinstance(row, dict):
                        row_cells = list(row.values())
                    elif isinstance(row, list):
                        row_cells = row

                    for i, cell in enumerate(row_cells):
                        cell_str = str(cell).lower()
                        if any(kw in cell_str for kw in name_keywords):
                            for j, other_cell in enumerate(row_cells):
                                if j == i:
                                    continue
                                val = self._try_parse_numeric(str(other_cell))
                                if val is not None:
                                    return val
        return None

    # ------------------------------------------------------------------
    # Column finding helpers
    # ------------------------------------------------------------------

    def _find_name_column(self, headers: List[str]) -> Optional[int]:
        """Find column with parameter/study names."""
        name_patterns = [
            "parameter", "study", "attribute", "test", "validation",
            "characteristic", "name", "criteria",
        ]
        for i, h in enumerate(headers):
            for pat in name_patterns:
                if pat in h:
                    return i
        if headers:
            return 0
        return None

    def _find_value_column(self, headers: List[str]) -> Optional[int]:
        """Find column with values/results."""
        value_patterns = [
            "value", "result", "observed", "measured", "mean",
            "actual", "data", "outcome",
        ]
        for i, h in enumerate(headers):
            for pat in value_patterns:
                if pat in h:
                    return i
        return None

    def _find_unit_column(self, headers: List[str]) -> Optional[int]:
        """Find column with units."""
        unit_patterns = ["unit", "uom", "units"]
        for i, h in enumerate(headers):
            for pat in unit_patterns:
                if pat in h:
                    return i
        return None

    def _find_criteria_column(self, headers: List[str]) -> Optional[int]:
        """Find column with acceptance criteria."""
        criteria_patterns = ["criteria", "acceptance", "specification", "limit", "requirement"]
        for i, h in enumerate(headers):
            for pat in criteria_patterns:
                if pat in h:
                    return i
        return None

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def _gather_all_text(self, parsed_doc: Dict[str, Any]) -> str:
        """Combine all text content."""
        parts = []
        for page in parsed_doc.get("pages", []):
            text = page.get("text", "")
            if text:
                parts.append(text)
        for para in parsed_doc.get("paragraphs", []):
            text = para.get("text", "")
            if text:
                parts.append(text)
        metadata = parsed_doc.get("metadata", {})
        title = metadata.get("title", "")
        if title:
            parts.append(title)
        return "\n".join(parts)

    def _gather_table_text(self, parsed_doc: Dict[str, Any]) -> str:
        """Collect all table cell text."""
        parts = []
        for page in parsed_doc.get("pages", []):
            for table in page.get("tables", []):
                for h in table.get("headers", []):
                    parts.append(h)
                for row in table.get("rows", []):
                    if isinstance(row, dict):
                        parts.extend(str(v) for v in row.values())
                    elif isinstance(row, list):
                        parts.extend(str(v) for v in row)
        return " ".join(parts)

    def _get_cell(
        self, row: Any, headers: List[str], idx: int
    ) -> Optional[str]:
        """Get cell value from a row dict or list by column index."""
        if isinstance(row, dict):
            if idx < len(headers):
                return row.get(headers[idx])
            return None
        elif isinstance(row, list):
            if idx < len(row):
                return str(row[idx])
            return None
        return None

    def _try_parse_numeric(self, text: Optional[str]) -> Optional[float]:
        """Try to parse a numeric value from cell text."""
        if text is None:
            return None
        text = text.strip()
        if not text:
            return None
        cleaned = re.sub(r'[%<>~]', '', text).strip()
        if cleaned.upper() in ("N/A", "NA", "ND", "NT", "-", ""):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _evidence_to_dict(self, evidence: AnalyticalMethodEvidence) -> Dict[str, Any]:
        """Convert AnalyticalMethodEvidence to dict."""
        return {
            "method_name": evidence.method_name,
            "validation_studies_found": evidence.validation_studies_found,
            "validation_studies_missing": evidence.validation_studies_missing,
            "completeness_score": evidence.completeness_score,
            "accuracy_recovery": evidence.accuracy_recovery,
            "precision_rsd": evidence.precision_rsd,
            "linearity_r2": evidence.linearity_r2,
            "loq_reported": evidence.loq_reported,
            "lod_reported": evidence.lod_reported,
            "critical_gaps": evidence.critical_gaps,
            "reviewer_concerns": evidence.reviewer_concerns,
            "tables_found": evidence.tables_found,
            "extractor": evidence.extractor,
        }
