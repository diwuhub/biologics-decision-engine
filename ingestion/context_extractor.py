"""
P8-C: Case Context Extractor.

Extracts case-level context from parsed DOCX documents using keyword
heuristics. No LLM -- pure pattern matching.

Detects:
- product_name: from title, header, or "Product:" field
- molecule_class: keyword detection (mAb, bispecific, ADC, fusion)
- change_type: keyword detection (process change, scale-up, site transfer)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ExtractedCaseContext:
    """Case-level context extracted from a document."""
    product_name: str = ""
    molecule_class: str = "unknown"
    change_type: str = "unknown"
    change_description: str = ""
    lifecycle_stage: str = "commercial"
    target_geography: str = "global"
    confidence: float = 0.0
    extraction_notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Keyword detection patterns
# ---------------------------------------------------------------------------

_MOLECULE_CLASS_PATTERNS: List[Dict[str, Any]] = [
    {"pattern": r"\b(monoclonal\s+antibod|mAb|IgG[1-4])\b", "class": "mAb"},
    {"pattern": r"\b(bispecific|bi-specific|biclonics)\b", "class": "bispecific"},
    {"pattern": r"\b(ADC|antibody[\s-]drug\s+conjugate)\b", "class": "ADC"},
    {"pattern": r"\b(fusion\s+protein|Fc[\s-]fusion)\b", "class": "fusion protein"},
    {"pattern": r"\b(enzyme|recombinant\s+enzyme)\b", "class": "enzyme"},
    {"pattern": r"\b(peptide)\b", "class": "peptide"},
    {"pattern": r"\b(biosimilar)\b", "class": "mAb"},  # biosimilars are typically mAbs
]

_CHANGE_TYPE_PATTERNS: List[Dict[str, Any]] = [
    {"pattern": r"\b(process\s+change|manufacturing\s+change|process\s+modification)\b", "type": "process change"},
    {"pattern": r"\b(scale[\s-]up|scale\s+up|scaling)\b", "type": "scale-up"},
    {"pattern": r"\b(site\s+transfer|site\s+change|manufacturing\s+site)\b", "type": "site transfer"},
    {"pattern": r"\b(cell\s+line\s+change|cell\s+bank\s+change|cell\s+culture)\b", "type": "cell line change"},
    {"pattern": r"\b(formulation\s+change|formulation\s+modification)\b", "type": "formulation change"},
    {"pattern": r"\b(analytical\s+method|method\s+change|method\s+transfer)\b", "type": "method change"},
    {"pattern": r"\b(column\s+change|resin\s+change|chromatography)\b", "type": "purification change"},
    {"pattern": r"\b(media\s+change|raw\s+material|excipient\s+change)\b", "type": "raw material change"},
    {"pattern": r"\b(comparability|comparability\s+study)\b", "type": "comparability study"},
]

_LIFECYCLE_PATTERNS: List[Dict[str, Any]] = [
    {"pattern": r"\b(phase\s+[123I]+|clinical\s+trial|IND)\b", "stage": "clinical"},
    {"pattern": r"\b(commercial|marketed|post-approval|post[\s-]approval)\b", "stage": "commercial"},
    {"pattern": r"\b(pre[\s-]?clinical|preclinical)\b", "stage": "preclinical"},
    {"pattern": r"\b(BLA|NDA|MAA|submission)\b", "stage": "submission"},
]

_PRODUCT_FIELD_PATTERNS = [
    r"Product\s*[:\-]\s*(.+?)(?:\n|$)",
    r"Product\s+Name\s*[:\-]\s*(.+?)(?:\n|$)",
    r"Drug\s+Substance\s*[:\-]\s*(.+?)(?:\n|$)",
    r"Drug\s+Product\s*[:\-]\s*(.+?)(?:\n|$)",
    r"Molecule\s*[:\-]\s*(.+?)(?:\n|$)",
]


class CaseContextExtractor:
    """Extract case-level context from a parsed DOCX document.

    Heuristics:
    - product_name: look for title, header, or "Product:" field
    - molecule_class: keyword detection (mAb, bispecific, ADC, fusion)
    - change_type: keyword detection (process change, scale-up, site transfer)
    """

    def extract_context(self, parsed_doc: Dict[str, Any]) -> ExtractedCaseContext:
        """Extract case context from parsed document."""
        ctx = ExtractedCaseContext()
        notes = []

        # Gather all searchable text
        all_text = self._gather_text(parsed_doc)
        metadata = parsed_doc.get("metadata", {})

        # 1. Product name
        ctx.product_name = self._detect_product_name(
            parsed_doc, metadata, all_text
        )
        if ctx.product_name:
            notes.append(f"Product detected: '{ctx.product_name}'")

        # 2. Molecule class
        ctx.molecule_class = self._detect_molecule_class(all_text)
        if ctx.molecule_class != "unknown":
            notes.append(f"Molecule class: {ctx.molecule_class}")

        # 3. Change type
        ctx.change_type = self._detect_change_type(all_text)
        if ctx.change_type != "unknown":
            notes.append(f"Change type: {ctx.change_type}")

        # 4. Change description (first relevant heading or paragraph)
        ctx.change_description = self._detect_change_description(parsed_doc, all_text)

        # 5. Lifecycle stage
        ctx.lifecycle_stage = self._detect_lifecycle_stage(all_text)

        # Confidence based on how much we could extract
        filled = sum(1 for v in [
            ctx.product_name, ctx.molecule_class != "unknown",
            ctx.change_type != "unknown", ctx.change_description,
        ] if v)
        ctx.confidence = filled / 4.0

        ctx.extraction_notes = notes
        return ctx

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def _gather_text(self, parsed_doc: Dict[str, Any]) -> str:
        """Combine all text from the document."""
        parts = []

        # Page text
        for page in parsed_doc.get("pages", []):
            text = page.get("text", "")
            if text:
                parts.append(text)

        # Paragraphs
        for para in parsed_doc.get("paragraphs", []):
            text = para.get("text", "")
            if text:
                parts.append(text)

        return "\n".join(parts)

    def _detect_product_name(
        self,
        parsed_doc: Dict[str, Any],
        metadata: Dict[str, Any],
        all_text: str,
    ) -> str:
        """Detect product name using multiple strategies."""

        # Strategy 1: Document title metadata
        title = metadata.get("title", "")
        if title and len(title) < 200:
            return title.strip()

        # Strategy 2: First heading (Title or Heading 1)
        for para in parsed_doc.get("paragraphs", []):
            if para.get("heading_level") in (0, 1) and para.get("text", "").strip():
                return para["text"].strip()

        # Strategy 3: "Product:" field in text
        for pattern in _PRODUCT_FIELD_PATTERNS:
            match = re.search(pattern, all_text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return ""

    def _detect_molecule_class(self, text: str) -> str:
        """Detect molecule class from text using keyword patterns."""
        for entry in _MOLECULE_CLASS_PATTERNS:
            if re.search(entry["pattern"], text, re.IGNORECASE):
                return entry["class"]
        return "unknown"

    def _detect_change_type(self, text: str) -> str:
        """Detect change type from text using keyword patterns."""
        for entry in _CHANGE_TYPE_PATTERNS:
            if re.search(entry["pattern"], text, re.IGNORECASE):
                return entry["type"]
        return "unknown"

    def _detect_change_description(
        self, parsed_doc: Dict[str, Any], all_text: str
    ) -> str:
        """Extract a brief change description."""
        # Look for heading containing "change", "comparability", "objective"
        for para in parsed_doc.get("paragraphs", []):
            if para.get("is_heading"):
                lower = para["text"].lower()
                if any(kw in lower for kw in ("change", "comparab", "objective", "purpose", "scope")):
                    # Take the next non-heading paragraph as description
                    idx = para["index"]
                    for p2 in parsed_doc.get("paragraphs", []):
                        if p2["index"] > idx and not p2.get("is_heading") and p2["text"].strip():
                            return p2["text"].strip()[:500]

        # Fallback: first non-heading paragraph with >20 chars
        for para in parsed_doc.get("paragraphs", []):
            if not para.get("is_heading") and len(para.get("text", "")) > 20:
                return para["text"].strip()[:500]

        return ""

    def _detect_lifecycle_stage(self, text: str) -> str:
        """Detect lifecycle stage from text."""
        for entry in _LIFECYCLE_PATTERNS:
            if re.search(entry["pattern"], text, re.IGNORECASE):
                return entry["stage"]
        return "commercial"
