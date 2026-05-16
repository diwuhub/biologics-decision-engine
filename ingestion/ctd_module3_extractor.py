"""
Phase 4B: CTD Module 3 Dispatcher / Extractor.

CTD Module 3 documents contain MULTIPLE sections (3.2.S.1 through 3.2.S.7
and 3.2.P.1 through 3.2.P.8). This extractor:

1. Detects CTD section headings (3.2.S.x, 3.2.P.x format)
2. Splits the document into sections
3. Routes each section to the appropriate extractor
4. Aggregates results

Section routing:
- 3.2.S.3 (Characterisation) -> CharacterizationExtractor
- 3.2.S.4 (Control of Drug Substance) -> AnalyticalMethodExtractor
- 3.2.S.7 (Stability) -> StabilityExtractor
- 3.2.P.5 (Control of Drug Product) -> AnalyticalMethodExtractor
- 3.2.P.8 (Stability) -> StabilityExtractor
- All other sections -> GenericCMCExtractor

Contract:
- extract_attributes() MUST NOT raise unhandled exceptions.
- extract_evidence() MUST NOT raise unhandled exceptions.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ingestion.base_extractor import BaseExtractor
from specs.cross_document_bridge import ExtractedAttribute

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CTD section heading patterns
# ---------------------------------------------------------------------------

# Match headings like "3.2.S.1", "3.2.S.3.1", "3.2.P.5", "3.2.P.8.2"
_CTD_HEADING_RE = re.compile(
    r"\b(3\.2\.[SP]\.\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# Section label mapping for known CTD Module 3 sections
_CTD_SECTION_LABELS = {
    "3.2.S.1": "General Information (Drug Substance)",
    "3.2.S.2": "Manufacture (Drug Substance)",
    "3.2.S.3": "Characterisation (Drug Substance)",
    "3.2.S.4": "Control of Drug Substance",
    "3.2.S.5": "Reference Standards (Drug Substance)",
    "3.2.S.6": "Container Closure System (Drug Substance)",
    "3.2.S.7": "Stability (Drug Substance)",
    "3.2.P.1": "Description and Composition (Drug Product)",
    "3.2.P.2": "Pharmaceutical Development",
    "3.2.P.3": "Manufacture (Drug Product)",
    "3.2.P.4": "Control of Excipients",
    "3.2.P.5": "Control of Drug Product",
    "3.2.P.6": "Reference Standards (Drug Product)",
    "3.2.P.7": "Container Closure System (Drug Product)",
    "3.2.P.8": "Stability (Drug Product)",
}

# Routing: which extractor handles which section
# Key: CTD section prefix (e.g., "3.2.S.3")
# Value: extractor class name
_SECTION_ROUTING = {
    "3.2.S.3": "characterization",
    "3.2.S.4": "analytical_method",
    "3.2.S.7": "stability",
    "3.2.P.5": "analytical_method",
    "3.2.P.8": "stability",
}


class CTDModule3Extractor(BaseExtractor):
    """Extract data from CTD Module 3 documents by splitting into sections.

    Contract:
    - extract_attributes() NEVER raises.
    - extract_evidence() NEVER raises.
    """

    def extract_attributes(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Split CTD Module 3 into sections, route each to appropriate extractor."""
        try:
            return self._extract_attributes_impl(parsed_doc)
        except Exception as e:
            logger.error("CTDModule3Extractor.extract_attributes failed: %s", e)
            return []

    def extract_evidence(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Section-by-section coverage assessment."""
        try:
            return self._extract_evidence_impl(parsed_doc)
        except Exception as e:
            logger.error("CTDModule3Extractor.extract_evidence failed: %s", e)
            return {"error": str(e), "extractor": "CTDModule3Extractor"}

    def supported_categories(self) -> List[str]:
        return [
            "physicochemical",
            "purity",
            "biological_activity",
            "potency",
            "identity",
            "stability",
            "analytical_method",
            "general",
        ]

    # ------------------------------------------------------------------
    # Section splitting
    # ------------------------------------------------------------------

    def split_into_sections(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """Split a CTD Module 3 document into sub-documents per section.

        Returns a dict mapping section number (e.g., "3.2.S.3") to a
        parsed_doc-like dict containing only that section's content.
        """
        sections: Dict[str, Dict[str, Any]] = {}

        # Strategy 1: Split by page text headings
        all_pages = parsed_doc.get("pages", [])
        if not all_pages:
            # No pages -- try paragraph-based splitting directly
            para_sections = self._split_paragraphs_into_sections(parsed_doc)
            for sec_id, sec_doc in para_sections.items():
                sections[sec_id] = sec_doc
            return sections

        # Scan through pages and accumulate them into sections
        current_section: Optional[str] = None
        current_pages: List[Dict[str, Any]] = []

        for page in all_pages:
            page_text = page.get("text", "")
            # Look for CTD section heading in the page text
            headings_found = _CTD_HEADING_RE.findall(page_text)

            if headings_found:
                # Flush previous section
                if current_section and current_pages:
                    parent = _get_parent_section(current_section)
                    sections[parent] = self._build_section_doc(
                        parsed_doc, current_pages, parent
                    )

                # Start new section with the first heading found
                current_section = headings_found[0]
                current_pages = [page]

                # If multiple headings on one page, that page goes to the first one
                # (subsections are grouped under parent)
            else:
                current_pages.append(page)

        # Flush last section
        if current_section and current_pages:
            parent = _get_parent_section(current_section)
            sections[parent] = self._build_section_doc(
                parsed_doc, current_pages, parent
            )

        # Also check paragraphs for section markers if page-level splitting
        # didn't find enough sections
        if len(sections) < 2:
            para_sections = self._split_paragraphs_into_sections(parsed_doc)
            for sec_id, sec_doc in para_sections.items():
                if sec_id not in sections:
                    sections[sec_id] = sec_doc

        return sections

    def _split_paragraphs_into_sections(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """Split paragraphs into sections based on CTD headings."""
        sections: Dict[str, Dict[str, Any]] = {}
        current_section: Optional[str] = None
        current_paras: List[Dict[str, Any]] = []

        for para in parsed_doc.get("paragraphs", []):
            text = para.get("text", "")
            headings = _CTD_HEADING_RE.findall(text)
            if headings:
                if current_section and current_paras:
                    parent = _get_parent_section(current_section)
                    if parent not in sections:
                        sections[parent] = {
                            "pages": [],
                            "paragraphs": current_paras,
                            "metadata": parsed_doc.get("metadata", {}),
                            "document_path": parsed_doc.get("document_path", ""),
                        }
                current_section = headings[0]
                current_paras = [para]
            else:
                current_paras.append(para)

        if current_section and current_paras:
            parent = _get_parent_section(current_section)
            if parent not in sections:
                sections[parent] = {
                    "pages": [],
                    "paragraphs": current_paras,
                    "metadata": parsed_doc.get("metadata", {}),
                    "document_path": parsed_doc.get("document_path", ""),
                }

        return sections

    def _build_section_doc(
        self,
        parsed_doc: Dict[str, Any],
        pages: List[Dict[str, Any]],
        section_id: str,
    ) -> Dict[str, Any]:
        """Build a parsed_doc-like dict for a single section."""
        return {
            "pages": pages,
            "paragraphs": [],
            "metadata": {
                **parsed_doc.get("metadata", {}),
                "ctd_section": section_id,
                "ctd_section_label": _CTD_SECTION_LABELS.get(section_id, ""),
            },
            "document_path": parsed_doc.get("document_path", ""),
        }

    # ------------------------------------------------------------------
    # Routing to sub-extractors
    # ------------------------------------------------------------------

    def _get_extractor_for_section(self, section_id: str) -> BaseExtractor:
        """Return the appropriate extractor for a CTD section."""
        route = _SECTION_ROUTING.get(section_id)
        try:
            if route == "characterization":
                from ingestion.characterization_extractor import CharacterizationExtractor
                return CharacterizationExtractor()
            elif route == "analytical_method":
                from ingestion.analytical_method_extractor import AnalyticalMethodExtractor
                return AnalyticalMethodExtractor()
            elif route == "stability":
                from ingestion.stability_extractor import StabilityExtractor
                return StabilityExtractor()
        except Exception as e:
            logger.warning(
                "Failed to load extractor for section %s: %s", section_id, e
            )

        from ingestion.generic_extractor import GenericCMCExtractor
        return GenericCMCExtractor()

    def get_section_extractor_type(self, section_id: str) -> str:
        """Return the extractor type name for a given section (for testing)."""
        return _SECTION_ROUTING.get(section_id, "generic")

    # ------------------------------------------------------------------
    # Attribute extraction
    # ------------------------------------------------------------------

    def _extract_attributes_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Split into sections, extract from each, aggregate."""
        sections = self.split_into_sections(parsed_doc)
        all_attributes: List[ExtractedAttribute] = []

        for section_id, section_doc in sections.items():
            extractor = self._get_extractor_for_section(section_id)
            try:
                attrs = extractor.extract_attributes(section_doc)
                # Tag attributes with CTD section
                for attr in attrs:
                    attr.context = (
                        f"CTD {section_id} "
                        f"({_CTD_SECTION_LABELS.get(section_id, '')}): "
                        f"{attr.context}"
                    )
                all_attributes.extend(attrs)
            except Exception as e:
                logger.warning(
                    "Extraction failed for section %s: %s", section_id, e
                )

        # If no sections detected, fall back to generic extraction
        if not sections:
            from ingestion.generic_extractor import GenericCMCExtractor
            fallback = GenericCMCExtractor()
            all_attributes = fallback.extract_attributes(parsed_doc)

        return all_attributes

    # ------------------------------------------------------------------
    # Evidence extraction
    # ------------------------------------------------------------------

    def _extract_evidence_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Section-by-section evidence coverage."""
        sections = self.split_into_sections(parsed_doc)

        section_evidence: Dict[str, Any] = {}
        sections_found: List[str] = []
        sections_with_data: List[str] = []

        for section_id, section_doc in sections.items():
            sections_found.append(section_id)
            extractor = self._get_extractor_for_section(section_id)
            try:
                evidence = extractor.extract_evidence(section_doc)
                section_evidence[section_id] = evidence
                # Check if section has meaningful data
                if evidence and not evidence.get("error"):
                    sections_with_data.append(section_id)
            except Exception as e:
                section_evidence[section_id] = {"error": str(e)}

        # Assess overall coverage
        all_expected = list(_CTD_SECTION_LABELS.keys())
        sections_missing = [s for s in all_expected if s not in sections_found]

        total_tables = sum(
            len(page.get("tables", []))
            for page in parsed_doc.get("pages", [])
        )

        return {
            "sections_found": sections_found,
            "sections_missing": sections_missing,
            "sections_with_data": sections_with_data,
            "section_evidence": section_evidence,
            "n_sections_found": len(sections_found),
            "n_sections_expected": len(all_expected),
            "coverage_score": (
                len(sections_found) / len(all_expected)
                if all_expected else 0.0
            ),
            "tables_found": total_tables,
            "extractor": "CTDModule3Extractor",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_parent_section(section_id: str) -> str:
    """Get the parent section ID (e.g., '3.2.S.3.1' -> '3.2.S.3')."""
    parts = section_id.split(".")
    # Standard format: 3.2.S.N or 3.2.P.N (4 dot-separated segments for parent)
    # "3.2.S.3.1" has 5 parts: ['3', '2', 'S', '3', '1']
    if len(parts) > 4:
        return ".".join(parts[:4])
    return section_id
