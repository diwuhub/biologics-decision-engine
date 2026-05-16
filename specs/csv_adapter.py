"""
CSV Adapter -- Phase 1 concrete implementation of the Bridge interfaces.

This is what the current pipeline uses. When Phase 2 document parsers are
built, they will implement the same interfaces and can be swapped in.

Usage::

    adapter = CSVBridge()
    pipeline_input = adapter.ingest(["batch_data.csv"], product_name="mAb-X")

    from pipelines.comparability import run_comparability_assessment
    report = run_comparability_assessment(pipeline_input, product_name="mAb-X")
"""

from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional

from specs.cross_document_bridge import (
    AttributeExtractor,
    BridgeOrchestrator,
    DocumentParser,
    DocumentType,
    ExtractedAttribute,
)


# ---------------------------------------------------------------------------
# Column mapping: CSV header -> internal attribute schema
# ---------------------------------------------------------------------------

#: Default mapping from CSV column names to the attribute dict keys expected
#: by ``run_comparability_assessment``.  Keys are lowered + stripped.
DEFAULT_COLUMN_MAP: Dict[str, str] = {
    "attribute": "name",
    "attribute_name": "name",
    "name": "name",
    "category": "category",
    "pre_value": "pre_value",
    "pre": "pre_value",
    "post_value": "post_value",
    "post": "post_value",
    "unit": "unit",
    "units": "unit",
    "n_lots": "n_lots",
    "cv_pct": "cv_pct",
    "cv": "cv_pct",
    "n_methods": "n_methods",
    "has_functional_correlation": "has_functional_correlation",
    "prior_approvals": "prior_approvals",
    "lot_id": "lot_id",
}

#: Numeric fields that should be cast from string.
_NUMERIC_FIELDS = {"pre_value", "post_value", "n_lots", "cv_pct", "n_methods", "prior_approvals"}

#: Boolean fields.
_BOOL_FIELDS = {"has_functional_correlation"}


class CSVDocumentParser(DocumentParser):
    """Parse a CSV file into the common parsed-document schema.

    The CSV is treated as a single-table document with one row per attribute.
    """

    def __init__(self, column_map: Optional[Dict[str, str]] = None):
        self._column_map = column_map or DEFAULT_COLUMN_MAP

    def parse(self, document_path: str) -> Dict[str, Any]:
        """Read a CSV and return the parsed-document schema."""
        if not os.path.isfile(document_path):
            raise FileNotFoundError(f"CSV not found: {document_path}")

        with open(document_path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            raw_headers = list(reader.fieldnames or [])
            rows = list(reader)

        # Normalize headers
        header_map = {}
        for h in raw_headers:
            normalized = h.strip().lower().replace(" ", "_")
            if normalized in self._column_map:
                header_map[h] = self._column_map[normalized]

        # Build table representation
        mapped_rows = []
        for row in rows:
            mapped = {}
            for orig_col, mapped_col in header_map.items():
                val = row.get(orig_col, "").strip()
                if mapped_col in _NUMERIC_FIELDS:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        val = 0.0
                elif mapped_col in _BOOL_FIELDS:
                    val = val.lower() in ("true", "1", "yes")
                mapped[mapped_col] = val
            # Carry through unmapped columns as metadata
            for orig_col in row:
                if orig_col not in header_map:
                    mapped.setdefault("_extra", {})[orig_col] = row[orig_col]
            mapped_rows.append(mapped)

        return {
            "document_path": document_path,
            "document_type": DocumentType.OTHER,
            "pages": [
                {
                    "page_number": 1,
                    "text": "",
                    "tables": [
                        {
                            "id": "csv_table_1",
                            "headers": [header_map.get(h, h) for h in raw_headers],
                            "rows": mapped_rows,
                        }
                    ],
                }
            ],
            "metadata": {
                "source_format": "csv",
                "n_rows": len(mapped_rows),
                "original_headers": raw_headers,
            },
        }

    def supported_formats(self) -> List[str]:
        return [".csv"]


class CSVAttributeExtractor(AttributeExtractor):
    """Extract ``ExtractedAttribute`` objects from a parsed CSV."""

    def extract_attributes(
        self, parsed_content: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        attributes: List[ExtractedAttribute] = []
        doc_path = parsed_content.get("document_path", "unknown")

        for page in parsed_content.get("pages", []):
            for table in page.get("tables", []):
                for row in table.get("rows", []):
                    name = row.get("name", "")
                    if not name:
                        continue

                    # For CSV we emit two ExtractedAttributes per row
                    # (pre and post) so reconciliation can pair them.
                    for phase, key in [("pre", "pre_value"), ("post", "post_value")]:
                        val = row.get(key)
                        if val is None:
                            continue
                        attributes.append(
                            ExtractedAttribute(
                                name=name,
                                value=float(val) if not isinstance(val, float) else val,
                                unit=row.get("unit", ""),
                                source_document=doc_path,
                                source_page=1,
                                source_table=table.get("id", ""),
                                confidence=1.0,  # CSV input is assumed authoritative
                                context=f"{phase}-change value from CSV",
                                category=row.get("category", "physicochemical"),
                                lot_id=row.get("lot_id", f"{phase}_batch"),
                                metadata={"phase": phase, **row.get("_extra", {})},
                            )
                        )
        return attributes

    def supported_categories(self) -> List[str]:
        return [
            "physicochemical",
            "purity",
            "biological_activity",
            "potency",
            "identity",
            "stability",
        ]


class CSVBridge(BridgeOrchestrator):
    """Phase 1 bridge: CSV files -> pipeline-ready input dict.

    Composes ``CSVDocumentParser`` and ``CSVAttributeExtractor`` behind the
    ``BridgeOrchestrator`` interface so that callers use the same API
    regardless of whether input is CSV (Phase 1) or raw documents (Phase 2).
    """

    def __init__(
        self,
        column_map: Optional[Dict[str, str]] = None,
    ):
        self._parser = CSVDocumentParser(column_map=column_map)
        self._extractor = CSVAttributeExtractor()

    def ingest(
        self,
        sources: List[str],
        product_name: str = "",
        change_description: str = "",
    ) -> Dict[str, Any]:
        """Ingest CSV file(s) and return pipeline-ready input.

        If multiple CSVs are provided their rows are concatenated.
        Each CSV row is expected to contain both pre_value and post_value,
        so no cross-document reconciliation is needed in Phase 1.
        """
        all_rows: List[Dict[str, Any]] = []

        for source in sources:
            parsed = self._parser.parse(source)
            # In Phase 1 we shortcut extraction: the parsed rows already
            # contain pre_value/post_value pairs suitable for the pipeline.
            for page in parsed.get("pages", []):
                for table in page.get("tables", []):
                    for row in table.get("rows", []):
                        if row.get("name") and "pre_value" in row and "post_value" in row:
                            all_rows.append(row)

        # Build the dict that run_comparability_assessment expects
        attributes = []
        for row in all_rows:
            attr: Dict[str, Any] = {
                "name": row["name"],
                "category": row.get("category", "physicochemical"),
                "pre_value": float(row["pre_value"]),
                "post_value": float(row["post_value"]),
                "unit": row.get("unit", ""),
            }
            # Optional enrichment fields
            for opt_key in ("n_lots", "cv_pct", "n_methods",
                            "has_functional_correlation", "prior_approvals"):
                if opt_key in row:
                    attr[opt_key] = row[opt_key]
            attributes.append(attr)

        return {
            "attributes": attributes,
            "molecule_class": "mAb",  # default; override in CSV metadata
            "modality": "IV",
        }
