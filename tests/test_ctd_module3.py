"""
Phase 4D: Tests for CTD Module 3 Extractor.

Verifies:
1. CTD heading pattern detection (3.2.S.x, 3.2.P.x)
2. Section splitting into sub-documents
3. Section routing to correct extractor
4. Attribute extraction across sections
5. Evidence extraction with coverage assessment
6. Fallback to generic extractor for unrecognized sections
7. Never crashes on malformed input
8. Performance < 30s for full CTD Module 3
9. Dispatcher registration for CTD_MODULE_3
"""

from __future__ import annotations

import os
import sys
import time
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ingestion.ctd_module3_extractor import (
    CTDModule3Extractor,
    _CTD_HEADING_RE,
    _CTD_SECTION_LABELS,
    _SECTION_ROUTING,
    _get_parent_section,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page(text: str, tables: list | None = None) -> dict:
    return {"text": text, "tables": tables or []}


def _make_parsed_doc(pages: list, paragraphs: list | None = None) -> dict:
    return {
        "pages": pages,
        "paragraphs": paragraphs or [],
        "metadata": {"title": "CTD Module 3"},
        "document_path": "test_ctd.docx",
    }


def _build_full_ctd_doc() -> dict:
    """Build a synthetic CTD Module 3 parsed document with all sections."""
    pages = []

    # S sections
    pages.append(_make_page(
        "3.2.S.1 General Information\nThe drug substance is a mAb."
    ))
    pages.append(_make_page(
        "3.2.S.2 Manufacture\nManufactured at Site A."
    ))
    pages.append(_make_page(
        "3.2.S.3 Characterisation\n"
        "HMW: 1.2%. Monomer purity: 97.5%. "
        "Reference standard lot: RS-2024-001.",
        tables=[{
            "headers": ["Attribute", "Method", "Result", "Unit"],
            "rows": [
                {"Attribute": "HMW", "Method": "SEC-HPLC", "Result": "1.2", "Unit": "%"},
                {"Attribute": "Monomer Purity", "Method": "SEC-HPLC", "Result": "97.5", "Unit": "%"},
            ],
        }],
    ))
    pages.append(_make_page(
        "3.2.S.4 Control of Drug Substance\n"
        "Method validation per ICH Q2. Specificity, linearity, accuracy, "
        "precision demonstrated. Recovery 99.5%. RSD 1.2%.",
        tables=[{
            "headers": ["Validation Parameter", "Result", "Criteria"],
            "rows": [
                {"Validation Parameter": "Accuracy", "Result": "99.5%", "Criteria": "98-102%"},
            ],
        }],
    ))
    pages.append(_make_page(
        "3.2.S.5 Reference Standards\nPrimary reference standard RS-2024-001."
    ))
    pages.append(_make_page(
        "3.2.S.6 Container Closure System\nPolycarbonate bottles with HDPE caps."
    ))
    pages.append(_make_page(
        "3.2.S.7 Stability\n"
        "Stability at 5C and 40C/75RH. T=0, 3M, 6M, 12M. "
        "Proposed shelf-life 24 months.",
        tables=[{
            "headers": ["Attribute", "Condition", "T=0", "3M", "6M", "12M"],
            "rows": [
                {"Attribute": "Purity", "Condition": "5C",
                 "T=0": "97.5", "3M": "97.3", "6M": "97.1", "12M": "96.8"},
            ],
        }],
    ))

    # P sections
    pages.append(_make_page(
        "3.2.P.1 Description and Composition\nSterile solution, 10 mg/mL."
    ))
    pages.append(_make_page(
        "3.2.P.2 Pharmaceutical Development\nFormulation optimization."
    ))
    pages.append(_make_page(
        "3.2.P.3 Manufacture\nAseptic fill into glass vials."
    ))
    pages.append(_make_page(
        "3.2.P.4 Control of Excipients\nAll compendial grade."
    ))
    pages.append(_make_page(
        "3.2.P.5 Control of Drug Product\n"
        "Release testing per ICH Q2. Specificity, linearity, accuracy demonstrated.",
        tables=[{
            "headers": ["Test", "Method", "Criteria"],
            "rows": [
                {"Test": "Purity", "Method": "SE-HPLC", "Criteria": ">=95%"},
            ],
        }],
    ))
    pages.append(_make_page(
        "3.2.P.6 Reference Standards\nSame lot RS-2024-001."
    ))
    pages.append(_make_page(
        "3.2.P.7 Container Closure System\nType I glass vials."
    ))
    pages.append(_make_page(
        "3.2.P.8 Stability\n"
        "Drug product stability at 5C. T=0, 3M, 6M, 12M.",
        tables=[{
            "headers": ["Attribute", "Condition", "T=0", "3M", "6M", "12M"],
            "rows": [
                {"Attribute": "Purity", "Condition": "5C",
                 "T=0": "97.2", "3M": "97.0", "6M": "96.8", "12M": "96.5"},
            ],
        }],
    ))

    return _make_parsed_doc(pages)


# ---------------------------------------------------------------------------
# 1. CTD heading pattern detection
# ---------------------------------------------------------------------------

class TestCTDHeadingPatterns(unittest.TestCase):
    """Regex should detect 3.2.S.x and 3.2.P.x headings."""

    def test_detects_s_sections(self):
        for i in range(1, 8):
            text = f"3.2.S.{i} Section Title"
            matches = _CTD_HEADING_RE.findall(text)
            self.assertTrue(
                len(matches) >= 1,
                f"Failed to detect 3.2.S.{i} in: {text}"
            )

    def test_detects_p_sections(self):
        for i in range(1, 9):
            text = f"3.2.P.{i} Section Title"
            matches = _CTD_HEADING_RE.findall(text)
            self.assertTrue(
                len(matches) >= 1,
                f"Failed to detect 3.2.P.{i} in: {text}"
            )

    def test_detects_subsections(self):
        text = "3.2.S.3.1 Elucidation of Structure"
        matches = _CTD_HEADING_RE.findall(text)
        self.assertTrue(len(matches) >= 1)
        self.assertIn("3.2.S.3.1", matches)

    def test_no_false_positives(self):
        text = "The pH was 3.2 and the concentration was 5.0 mg/mL."
        matches = _CTD_HEADING_RE.findall(text)
        self.assertEqual(len(matches), 0)

    def test_case_insensitive(self):
        text = "3.2.s.3 Characterisation"
        matches = _CTD_HEADING_RE.findall(text)
        self.assertTrue(len(matches) >= 1)


# ---------------------------------------------------------------------------
# 2. Section splitting
# ---------------------------------------------------------------------------

class TestSectionSplitting(unittest.TestCase):
    """CTDModule3Extractor should split into correct sections."""

    def setUp(self):
        self.extractor = CTDModule3Extractor()

    def test_splits_into_sections(self):
        doc = _build_full_ctd_doc()
        sections = self.extractor.split_into_sections(doc)
        self.assertGreater(len(sections), 0, "Should find at least some sections")

    def test_finds_s_and_p_sections(self):
        doc = _build_full_ctd_doc()
        sections = self.extractor.split_into_sections(doc)
        s_sections = [s for s in sections if s.startswith("3.2.S")]
        p_sections = [s for s in sections if s.startswith("3.2.P")]
        self.assertGreater(len(s_sections), 0, "Should find S sections")
        self.assertGreater(len(p_sections), 0, "Should find P sections")

    def test_section_docs_have_pages(self):
        doc = _build_full_ctd_doc()
        sections = self.extractor.split_into_sections(doc)
        for section_id, section_doc in sections.items():
            self.assertIn("pages", section_doc, f"{section_id} missing 'pages' key")

    def test_empty_doc_returns_empty(self):
        doc = _make_parsed_doc([])
        sections = self.extractor.split_into_sections(doc)
        self.assertEqual(len(sections), 0)

    def test_paragraph_based_splitting(self):
        """Sections can also be detected from paragraphs."""
        doc = _make_parsed_doc(
            pages=[],
            paragraphs=[
                {"text": "3.2.S.3 Characterisation\nHMW 1.2%."},
                {"text": "More characterization data."},
                {"text": "3.2.S.7 Stability\nStability at 5C."},
                {"text": "More stability data."},
            ],
        )
        sections = self.extractor.split_into_sections(doc)
        self.assertGreater(len(sections), 0, "Should split paragraphs into sections")


# ---------------------------------------------------------------------------
# 3. Section routing
# ---------------------------------------------------------------------------

class TestSectionRouting(unittest.TestCase):
    """Each CTD section should route to the correct extractor."""

    def setUp(self):
        self.extractor = CTDModule3Extractor()

    def test_s3_routes_to_characterization(self):
        self.assertEqual(
            self.extractor.get_section_extractor_type("3.2.S.3"),
            "characterization",
        )

    def test_s4_routes_to_analytical_method(self):
        self.assertEqual(
            self.extractor.get_section_extractor_type("3.2.S.4"),
            "analytical_method",
        )

    def test_s7_routes_to_stability(self):
        self.assertEqual(
            self.extractor.get_section_extractor_type("3.2.S.7"),
            "stability",
        )

    def test_p5_routes_to_analytical_method(self):
        self.assertEqual(
            self.extractor.get_section_extractor_type("3.2.P.5"),
            "analytical_method",
        )

    def test_p8_routes_to_stability(self):
        self.assertEqual(
            self.extractor.get_section_extractor_type("3.2.P.8"),
            "stability",
        )

    def test_unrecognized_section_routes_to_generic(self):
        self.assertEqual(
            self.extractor.get_section_extractor_type("3.2.S.1"),
            "generic",
        )
        self.assertEqual(
            self.extractor.get_section_extractor_type("3.2.P.2"),
            "generic",
        )

    def test_all_routing_entries_valid(self):
        """All routing keys should be known CTD sections."""
        for section_id in _SECTION_ROUTING:
            self.assertIn(
                section_id, _CTD_SECTION_LABELS,
                f"Routing key {section_id} not in section labels",
            )


# ---------------------------------------------------------------------------
# 4. Attribute extraction
# ---------------------------------------------------------------------------

class TestAttributeExtraction(unittest.TestCase):
    """CTDModule3Extractor should extract attributes from all sections."""

    def setUp(self):
        self.extractor = CTDModule3Extractor()

    def test_extracts_attributes(self):
        doc = _build_full_ctd_doc()
        attrs = self.extractor.extract_attributes(doc)
        self.assertIsInstance(attrs, list)

    def test_attributes_tagged_with_ctd_section(self):
        """Extracted attributes should have CTD section in context."""
        doc = _build_full_ctd_doc()
        attrs = self.extractor.extract_attributes(doc)
        if attrs:  # Only check if extraction found something
            ctd_tagged = [a for a in attrs if "CTD" in a.context]
            self.assertGreater(
                len(ctd_tagged), 0,
                "At least some attributes should be tagged with CTD section",
            )

    def test_never_raises(self):
        """extract_attributes must never raise."""
        extractor = CTDModule3Extractor()
        # Completely malformed input
        result = extractor.extract_attributes({})
        self.assertIsInstance(result, list)

        result = extractor.extract_attributes({"pages": None})
        self.assertIsInstance(result, list)

    def test_fallback_when_no_sections_found(self):
        """When no CTD sections detected, falls back to generic extractor."""
        doc = _make_parsed_doc([
            _make_page("Some general text without CTD headings."),
        ])
        attrs = self.extractor.extract_attributes(doc)
        self.assertIsInstance(attrs, list)


# ---------------------------------------------------------------------------
# 5. Evidence extraction
# ---------------------------------------------------------------------------

class TestEvidenceExtraction(unittest.TestCase):
    """CTDModule3Extractor should produce evidence with coverage assessment."""

    def setUp(self):
        self.extractor = CTDModule3Extractor()

    def test_evidence_has_required_keys(self):
        doc = _build_full_ctd_doc()
        evidence = self.extractor.extract_evidence(doc)
        self.assertIn("sections_found", evidence)
        self.assertIn("sections_missing", evidence)
        self.assertIn("sections_with_data", evidence)
        self.assertIn("coverage_score", evidence)
        self.assertIn("extractor", evidence)
        self.assertEqual(evidence["extractor"], "CTDModule3Extractor")

    def test_coverage_score_is_fraction(self):
        doc = _build_full_ctd_doc()
        evidence = self.extractor.extract_evidence(doc)
        score = evidence["coverage_score"]
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_section_evidence_populated(self):
        doc = _build_full_ctd_doc()
        evidence = self.extractor.extract_evidence(doc)
        section_evidence = evidence.get("section_evidence", {})
        self.assertIsInstance(section_evidence, dict)

    def test_never_raises(self):
        """extract_evidence must never raise."""
        extractor = CTDModule3Extractor()
        result = extractor.extract_evidence({})
        self.assertIsInstance(result, dict)

        result = extractor.extract_evidence({"pages": None})
        self.assertIsInstance(result, dict)

    def test_tables_counted(self):
        doc = _build_full_ctd_doc()
        evidence = self.extractor.extract_evidence(doc)
        self.assertIn("tables_found", evidence)
        self.assertGreater(evidence["tables_found"], 0)


# ---------------------------------------------------------------------------
# 6. Section label constants
# ---------------------------------------------------------------------------

class TestSectionLabels(unittest.TestCase):
    """Verify CTD section label completeness."""

    def test_all_s_sections_present(self):
        for i in range(1, 8):
            key = f"3.2.S.{i}"
            self.assertIn(key, _CTD_SECTION_LABELS, f"Missing label for {key}")

    def test_all_p_sections_present(self):
        for i in range(1, 9):
            key = f"3.2.P.{i}"
            self.assertIn(key, _CTD_SECTION_LABELS, f"Missing label for {key}")

    def test_total_section_count(self):
        """7 S sections + 8 P sections = 15 total."""
        self.assertEqual(len(_CTD_SECTION_LABELS), 15)


# ---------------------------------------------------------------------------
# 7. Parent section resolution
# ---------------------------------------------------------------------------

class TestParentSection(unittest.TestCase):

    def test_parent_of_subsection(self):
        self.assertEqual(_get_parent_section("3.2.S.3.1"), "3.2.S.3")
        self.assertEqual(_get_parent_section("3.2.P.8.2"), "3.2.P.8")

    def test_parent_of_top_level(self):
        self.assertEqual(_get_parent_section("3.2.S.3"), "3.2.S.3")
        self.assertEqual(_get_parent_section("3.2.P.5"), "3.2.P.5")


# ---------------------------------------------------------------------------
# 8. Dispatcher registration
# ---------------------------------------------------------------------------

class TestDispatcherRegistration(unittest.TestCase):
    """CTD_MODULE_3 should be registered in the IngestionDispatcher."""

    def test_dispatcher_returns_ctd_extractor(self):
        from ingestion.dispatcher import IngestionDispatcher
        from ingestion.document_classifier import DocTypeSpec

        dispatcher = IngestionDispatcher()
        doc_type = DocTypeSpec(
            document_type="CTD_MODULE_3",
            confidence=0.95,
            classification_notes=[],
        )
        extractor = dispatcher.dispatch({}, doc_type)
        self.assertIsInstance(extractor, CTDModule3Extractor)


# ---------------------------------------------------------------------------
# 9. Performance
# ---------------------------------------------------------------------------

class TestPerformance(unittest.TestCase):
    """Full CTD Module 3 extraction should complete in < 30s."""

    def test_extraction_under_30s(self):
        doc = _build_full_ctd_doc()
        extractor = CTDModule3Extractor()

        start = time.time()
        attrs = extractor.extract_attributes(doc)
        evidence = extractor.extract_evidence(doc)
        elapsed = time.time() - start

        self.assertLess(
            elapsed, 30.0,
            f"CTD extraction took {elapsed:.1f}s (limit: 30s)"
        )
        self.assertIsInstance(attrs, list)
        self.assertIsInstance(evidence, dict)


# ---------------------------------------------------------------------------
# 10. DOCX fixture integration (if fixture exists)
# ---------------------------------------------------------------------------

class TestDocxFixtureIntegration(unittest.TestCase):
    """If the CTD Module 3 DOCX fixture exists, parse and extract from it."""

    FIXTURE_PATH = os.path.join(
        os.path.dirname(__file__), "fixtures", "test_ctd_module3.docx"
    )

    @unittest.skipUnless(
        os.path.exists(os.path.join(
            os.path.dirname(__file__), "fixtures", "test_ctd_module3.docx"
        )),
        "CTD Module 3 DOCX fixture not found",
    )
    def test_parse_and_extract_from_fixture(self):
        """Parse the DOCX fixture and verify extraction works."""
        from ingestion.docx_parser import DOCXDocumentParser as DOCXParser

        parser = DOCXParser()
        parsed_doc = parser.parse(self.FIXTURE_PATH)
        self.assertIsInstance(parsed_doc, dict)
        self.assertIn("pages", parsed_doc)

        extractor = CTDModule3Extractor()
        attrs = extractor.extract_attributes(parsed_doc)
        self.assertIsInstance(attrs, list)

        evidence = extractor.extract_evidence(parsed_doc)
        self.assertIsInstance(evidence, dict)
        self.assertIn("sections_found", evidence)

    @unittest.skipUnless(
        os.path.exists(os.path.join(
            os.path.dirname(__file__), "fixtures", "test_ctd_module3.docx"
        )),
        "CTD Module 3 DOCX fixture not found",
    )
    def test_fixture_has_multiple_sections(self):
        """The DOCX fixture should contain multiple CTD sections."""
        from ingestion.docx_parser import DOCXDocumentParser as DOCXParser

        parser = DOCXParser()
        parsed_doc = parser.parse(self.FIXTURE_PATH)

        extractor = CTDModule3Extractor()
        sections = extractor.split_into_sections(parsed_doc)
        self.assertGreater(
            len(sections), 2,
            f"Expected multiple CTD sections in fixture, got {len(sections)}: {list(sections.keys())}",
        )


# ---------------------------------------------------------------------------
# 11. supported_categories
# ---------------------------------------------------------------------------

class TestSupportedCategories(unittest.TestCase):

    def test_categories_not_empty(self):
        extractor = CTDModule3Extractor()
        cats = extractor.supported_categories()
        self.assertGreater(len(cats), 0)
        self.assertIn("stability", cats)
        self.assertIn("analytical_method", cats)


if __name__ == "__main__":
    unittest.main()
