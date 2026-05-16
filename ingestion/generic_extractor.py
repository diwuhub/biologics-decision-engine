"""
A4: Generic CMC Extractor.

Best-effort extraction from any document type. Never crashes.
Used as the fallback extractor when document type is UNKNOWN or
when no specialized extractor is available for the detected type.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from ingestion.base_extractor import BaseExtractor
from specs.cross_document_bridge import ExtractedAttribute

logger = logging.getLogger(__name__)


class GenericCMCExtractor(BaseExtractor):
    """Best-effort attribute extraction from any CMC document.

    Scans all tables for columns that look like they contain quality
    attribute data (name, value, unit) and extracts what it can.

    Contract:
    - extract_attributes() NEVER raises.
    - extract_evidence() NEVER raises.
    """

    def extract_attributes(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Best-effort extraction from any document. Never crashes."""
        try:
            return self._extract_attributes_impl(parsed_doc)
        except Exception as e:
            logger.error("GenericCMCExtractor.extract_attributes failed: %s", e)
            return []

    def extract_evidence(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract whatever evidence is available. Never crashes."""
        try:
            return self._extract_evidence_impl(parsed_doc)
        except Exception as e:
            logger.error("GenericCMCExtractor.extract_evidence failed: %s", e)
            return {"error": str(e), "tables_found": 0, "text_snippets": []}

    def supported_categories(self) -> List[str]:
        return [
            "physicochemical",
            "purity",
            "biological_activity",
            "potency",
            "identity",
            "stability",
            "general",
        ]

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _extract_attributes_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Scan tables and try to extract named attributes with values."""
        doc_path = parsed_doc.get("document_path", "unknown")
        attributes: List[ExtractedAttribute] = []

        for page in parsed_doc.get("pages", []):
            page_num = page.get("page_number", 1)
            for table in page.get("tables", []):
                headers = [h.lower().strip() for h in table.get("headers", [])]
                if not headers:
                    continue

                # Try to identify name/value/unit columns
                name_idx = self._find_name_column(headers)
                value_idx = self._find_value_column(headers)
                unit_idx = self._find_unit_column(headers)

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

                    attributes.append(
                        ExtractedAttribute(
                            name=name.strip(),
                            value=value if value is not None else 0.0,
                            unit=unit.strip(),
                            source_document=doc_path,
                            source_page=page_num,
                            source_table=table_id,
                            confidence=0.5,  # lower confidence for generic extraction
                            context=f"Generic extraction from {table_id}",
                            category="general",
                            anchor_ids=[str(uuid.uuid4())],
                            extraction_confidence=0.5,
                        )
                    )

        return attributes

    def _extract_evidence_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Collect evidence: table count, text snippets, key phrases."""
        tables_found = 0
        text_snippets: List[str] = []
        key_phrases: List[str] = []

        for page in parsed_doc.get("pages", []):
            tables_found += len(page.get("tables", []))
            page_text = page.get("text", "")
            if page_text.strip():
                # Take first 500 chars per page
                text_snippets.append(page_text.strip()[:500])

        # Scan for common CMC phrases
        all_text = " ".join(text_snippets)
        cmc_patterns = [
            r"\b(quality attribute|CQA|critical quality)\b",
            r"\b(acceptance criteria|specification)\b",
            r"\b(comparability|characterization|stability)\b",
            r"\b(validation|verification|qualification)\b",
            r"\b(batch|lot|process)\b",
        ]
        for pattern in cmc_patterns:
            match = re.search(pattern, all_text, re.IGNORECASE)
            if match:
                key_phrases.append(match.group(0))

        return {
            "tables_found": tables_found,
            "text_snippets": text_snippets[:5],  # limit to 5 snippets
            "key_phrases": key_phrases,
            "n_pages": len(parsed_doc.get("pages", [])),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_name_column(self, headers: List[str]) -> Optional[int]:
        """Find a column that likely contains attribute names."""
        name_patterns = [
            "attribute", "parameter", "test", "assay", "analyte",
            "quality attribute", "name", "method",
        ]
        for i, h in enumerate(headers):
            for pat in name_patterns:
                if pat in h:
                    return i
        # Fallback: first column if it doesn't look numeric
        if headers:
            return 0
        return None

    def _find_value_column(self, headers: List[str]) -> Optional[int]:
        """Find a column that likely contains values."""
        value_patterns = [
            "value", "result", "measured", "mean", "average",
            "observed", "actual", "data",
        ]
        for i, h in enumerate(headers):
            for pat in value_patterns:
                if pat in h:
                    return i
        return None

    def _find_unit_column(self, headers: List[str]) -> Optional[int]:
        """Find a column that likely contains units."""
        unit_patterns = ["unit", "uom", "units"]
        for i, h in enumerate(headers):
            for pat in unit_patterns:
                if pat in h:
                    return i
        return None

    def _get_cell(
        self, row: Dict[str, str], headers: List[str], idx: int
    ) -> Optional[str]:
        """Get cell value from a row dict by column index."""
        if idx < len(headers):
            return row.get(headers[idx])
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
