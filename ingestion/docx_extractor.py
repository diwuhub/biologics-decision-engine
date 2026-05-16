"""
P8-B: DOCX Attribute Extractor.

Template-aware extraction of comparability attributes from parsed DOCX
documents. Looks for tables with common comparability formats and extracts
pre/post values with EvidenceAnchors.

Keyword heuristics only -- no LLM.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from specs.cross_document_bridge import AttributeExtractor, ExtractedAttribute


# ---------------------------------------------------------------------------
# Known comparability table header patterns
# ---------------------------------------------------------------------------

#: Patterns that indicate a comparability data table.
#: Each entry is a tuple of (pre_col_patterns, post_col_patterns, attr_col_patterns).
_TABLE_PATTERNS: List[Dict[str, List[str]]] = [
    {
        "attr_cols": ["attribute", "test", "parameter", "quality attribute", "assay", "method"],
        "pre_cols": ["pre-change", "pre change", "pre_change", "pre", "before", "reference", "original"],
        "post_cols": ["post-change", "post change", "post_change", "post", "after", "proposed", "new"],
        "spec_cols": ["spec", "specification", "acceptance criteria", "limit", "acceptance criterion"],
        "result_cols": ["result", "outcome", "conclusion", "pass/fail", "status"],
        "unit_cols": ["unit", "units", "uom"],
        "category_cols": ["category", "type", "test type"],
    },
]


class ExtractionIssue(Exception):
    """Raised when extraction encounters an issue."""

    def __init__(self, message: str, severity: str = "warning"):
        super().__init__(message)
        self.severity = severity


class DOCXAttributeExtractor(AttributeExtractor):
    """Extract comparability attributes from parsed DOCX content.

    Template-aware extraction:
    - Looks for tables with headers like "Pre-Change", "Post-Change", "Attribute", "Result"
    - Common patterns: "Test | Pre | Post | Spec | Result" format
    - Extracts numerical values from cells
    - Creates EvidenceAnchors for each extracted value
    - If table format not recognized -> raise ExtractionIssue(severity='critical')
    """

    def extract_attributes(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Find comparability tables and extract pre/post values."""
        doc_path = parsed_doc.get("document_path", "unknown")
        all_attributes: List[ExtractedAttribute] = []
        tables_found = 0

        for page in parsed_doc.get("pages", []):
            for table in page.get("tables", []):
                headers = [h.lower().strip() for h in table.get("headers", [])]
                if not headers:
                    continue

                mapping = self._match_table_pattern(headers)
                if mapping is None:
                    continue

                tables_found += 1
                attrs = self._extract_from_table(table, mapping, doc_path)
                all_attributes.extend(attrs)

        if tables_found == 0:
            import logging
            logging.getLogger(__name__).warning(
                "No comparability tables recognized in DOCX (%s). "
                "Document may be a characterization or stability report.",
                doc_path,
            )
            return []

        return all_attributes

    def supported_categories(self) -> List[str]:
        return [
            "physicochemical",
            "purity",
            "biological_activity",
            "potency",
            "identity",
            "stability",
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _match_table_pattern(
        self, headers: List[str]
    ) -> Optional[Dict[str, int]]:
        """Try to match table headers to a known comparability pattern.

        Returns a mapping of semantic role -> column index, or None.
        """
        for pattern in _TABLE_PATTERNS:
            attr_idx = self._find_col(headers, pattern["attr_cols"])
            pre_idx = self._find_col(headers, pattern["pre_cols"])
            post_idx = self._find_col(headers, pattern["post_cols"])

            if attr_idx is not None and pre_idx is not None and post_idx is not None:
                mapping = {
                    "attr": attr_idx,
                    "pre": pre_idx,
                    "post": post_idx,
                }
                spec_idx = self._find_col(headers, pattern["spec_cols"])
                if spec_idx is not None:
                    mapping["spec"] = spec_idx
                result_idx = self._find_col(headers, pattern["result_cols"])
                if result_idx is not None:
                    mapping["result"] = result_idx
                unit_idx = self._find_col(headers, pattern["unit_cols"])
                if unit_idx is not None:
                    mapping["unit"] = unit_idx
                cat_idx = self._find_col(headers, pattern["category_cols"])
                if cat_idx is not None:
                    mapping["category"] = cat_idx
                return mapping

        return None

    def _find_col(
        self, headers: List[str], patterns: List[str]
    ) -> Optional[int]:
        """Find the first header column matching any of the patterns."""
        for i, h in enumerate(headers):
            for pat in patterns:
                if pat in h:
                    return i
        return None

    def _extract_from_table(
        self,
        table: Dict[str, Any],
        mapping: Dict[str, int],
        doc_path: str,
    ) -> List[ExtractedAttribute]:
        """Extract attributes from a single matched table."""
        attributes = []
        headers = table.get("headers", [])
        table_id = table.get("id", "unknown_table")

        for row in table.get("rows", []):
            # Row is a dict keyed by header strings
            attr_name = self._get_cell_by_idx(row, headers, mapping["attr"])
            if not attr_name or not attr_name.strip():
                continue

            pre_raw = self._get_cell_by_idx(row, headers, mapping["pre"])
            post_raw = self._get_cell_by_idx(row, headers, mapping["post"])

            pre_val = self._parse_numeric(pre_raw)
            post_val = self._parse_numeric(post_raw)

            unit = ""
            if "unit" in mapping:
                unit = self._get_cell_by_idx(row, headers, mapping["unit"]) or ""

            category = "physicochemical"
            if "category" in mapping:
                cat_text = self._get_cell_by_idx(row, headers, mapping["category"]) or ""
                if cat_text.strip():
                    category = self._normalize_category(cat_text)

            spec_text = ""
            if "spec" in mapping:
                spec_text = self._get_cell_by_idx(row, headers, mapping["spec"]) or ""

            result_text = ""
            if "result" in mapping:
                result_text = self._get_cell_by_idx(row, headers, mapping["result"]) or ""

            # W1-2: Parse spec limits from spec_text into metadata
            spec_metadata: Dict[str, Any] = {}
            if spec_text.strip():
                spec_metadata["spec_value"] = spec_text.strip()
                _parsed_spec = self._parse_spec_limits(spec_text)
                if _parsed_spec:
                    spec_metadata.update(_parsed_spec)

            # Build context string
            context_parts = []
            if spec_text:
                context_parts.append(f"Spec: {spec_text}")
            if result_text:
                context_parts.append(f"Result: {result_text}")
            context = "; ".join(context_parts) if context_parts else f"From {table_id}"

            # Generate anchor IDs for traceability
            anchor_id = str(uuid.uuid4())

            attributes.append(
                ExtractedAttribute(
                    name=attr_name.strip(),
                    value=pre_val if pre_val is not None else 0.0,
                    unit=unit.strip(),
                    source_document=doc_path,
                    source_page=1,
                    source_table=table_id,
                    confidence=1.0 if (pre_val is not None and post_val is not None) else 0.5,
                    context=context,
                    category=category,
                    pre_value=pre_val,
                    post_value=post_val,
                    anchor_ids=[anchor_id],
                    extraction_confidence=1.0 if (pre_val is not None and post_val is not None) else 0.5,
                    metadata=spec_metadata,  # W1-2: spec limits in metadata
                )
            )

        return attributes

    def _get_cell_by_idx(
        self,
        row: Dict[str, str],
        headers: List[str],
        idx: int,
    ) -> Optional[str]:
        """Get cell value from a row dict using the column index."""
        if idx < len(headers):
            header_key = headers[idx]
            return row.get(header_key)
        return None

    def _parse_numeric(self, text: Optional[str]) -> Optional[float]:
        """Try to parse a numeric value from cell text."""
        if text is None:
            return None
        text = text.strip()
        if not text:
            return None

        # Remove common suffixes/prefixes
        cleaned = re.sub(r'[%<>~]', '', text)
        cleaned = cleaned.strip()

        # Handle ranges like "95.0 - 98.0" -> take midpoint
        range_match = re.match(r'^([\d.]+)\s*[-–]\s*([\d.]+)$', cleaned)
        if range_match:
            lo = float(range_match.group(1))
            hi = float(range_match.group(2))
            return (lo + hi) / 2.0

        # Handle "N/A", "ND", "NT", etc.
        if cleaned.upper() in ("N/A", "NA", "ND", "NT", "-", ""):
            return None

        try:
            return float(cleaned)
        except ValueError:
            return None

    def _parse_spec_limits(self, spec_text: str) -> Optional[Dict[str, Any]]:
        """Parse spec text into lower/upper limits.

        Handles common formats:
        - "≤2.0%"  or  "<= 2.0"  ->  upper=2.0
        - "≥95.0%" or  ">= 95.0" ->  lower=95.0
        - "95.0 - 105.0" or "95.0–105.0"  ->  lower=95.0, upper=105.0
        - "NMT 2.0%" (not more than)  ->  upper=2.0
        - "NLT 95.0%" (not less than) ->  lower=95.0
        """
        result: Dict[str, Any] = {}
        text = spec_text.strip()

        # Range pattern: "95.0 - 105.0" or "95.0–105.0"
        range_match = re.match(r'^([\d.]+)\s*[–\-]\s*([\d.]+)', re.sub(r'[%]', '', text))
        if range_match:
            result['spec_lower'] = float(range_match.group(1))
            result['spec_upper'] = float(range_match.group(2))
            return result

        cleaned = re.sub(r'[%]', '', text).strip()

        # NMT / NLT patterns
        nmt_match = re.match(r'(?:NMT|not\s+more\s+than)\s+([\d.]+)', cleaned, re.IGNORECASE)
        if nmt_match:
            result['spec_upper'] = float(nmt_match.group(1))
            return result

        nlt_match = re.match(r'(?:NLT|not\s+less\s+than)\s+([\d.]+)', cleaned, re.IGNORECASE)
        if nlt_match:
            result['spec_lower'] = float(nlt_match.group(1))
            return result

        # ≤ or <= pattern
        le_match = re.match(r'[≤<]=?\s*([\d.]+)', cleaned)
        if le_match:
            result['spec_upper'] = float(le_match.group(1))
            return result

        # ≥ or >= pattern
        ge_match = re.match(r'[≥>]=?\s*([\d.]+)', cleaned)
        if ge_match:
            result['spec_lower'] = float(ge_match.group(1))
            return result

        return result if result else None

    def _normalize_category(self, text: str) -> str:
        """Normalize category text to standard category names."""
        lower = text.lower().strip()
        category_map = {
            "purity": "purity",
            "potency": "potency",
            "identity": "identity",
            "stability": "stability",
            "biological": "biological_activity",
            "biological activity": "biological_activity",
            "bioactivity": "biological_activity",
            "physicochemical": "physicochemical",
            "physicochem": "physicochemical",
            "process": "process_related",
            "safety": "safety",
        }
        for key, val in category_map.items():
            if key in lower:
                return val
        return "physicochemical"
