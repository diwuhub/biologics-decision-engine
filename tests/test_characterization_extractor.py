"""
B6: Tests for CharacterizationExtractor (Phase 2 Track B).

Covers:
- test_classification_as_characterization
- test_section_detection (finds Q6B sections)
- test_completeness_scoring
- test_critical_gap_identification (missing potency)
- test_reviewer_concerns
- test_hmw_extraction
- test_potency_extraction
- test_dispatcher_routes_characterization
- test_extract_attributes_never_raises
- test_extract_evidence_never_raises
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)


# ==========================================================================
# Helpers
# ==========================================================================

def _get_fixture_path() -> str:
    """Return path to test characterization DOCX, creating if needed."""
    fixture_path = os.path.join(PROJECT_ROOT, "tests", "fixtures", "test_characterization_report.docx")
    if not os.path.exists(fixture_path):
        from tests.create_characterization_fixture import create_characterization_report_docx
        os.makedirs(os.path.dirname(fixture_path), exist_ok=True)
        create_characterization_report_docx(fixture_path)
    return fixture_path


def _parse_characterization_fixture() -> dict:
    """Parse the characterization DOCX fixture and return parsed doc."""
    from ingestion.docx_parser import DOCXDocumentParser
    fixture_path = _get_fixture_path()
    parser = DOCXDocumentParser()
    return parser.parse(fixture_path)


def _create_minimal_characterization_parsed() -> dict:
    """Create a minimal parsed doc dict that looks like a characterization report."""
    return {
        "document_path": "test_char.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Characterization Report -- mAb-Test IgG1\n"
                    "This document presents the physicochemical and biological characterization "
                    "of mAb-Test IgG1 per ICH Q6B guidelines.\n"
                    "Reference Standard Lot: RS-2026-001\n\n"
                    "1. Primary Structure\n"
                    "Peptide mapping by LC-MS/MS confirmed 100% sequence coverage.\n\n"
                    "2. Higher-Order Structure\n"
                    "Circular dichroism (CD) and DSC analysis confirmed IgG1 fold.\n"
                    "FTIR spectroscopy showed expected secondary structure.\n\n"
                    "3. Aggregation and Size Variants\n"
                    "SEC-HPLC analysis showed HMW: 1.2%, monomer: 98.2%.\n"
                    "AUC and DLS confirmed low aggregation.\n\n"
                    "4. Charge Heterogeneity\n"
                    "CEX and icIEF analysis. Main charge peak: 58.3%.\n\n"
                    "5. Glycosylation\n"
                    "N-glycan profiling by HILIC-MS. Afucosylation: 4.2%.\n"
                    "Post-translational modification analysis.\n\n"
                    "6. Biological Activity and Potency\n"
                    "Cell-based assay. Relative potency: 102.5%.\n"
                    "ADCC activity confirmed.\n\n"
                    "7. Immunochemical Properties\n"
                    "ELISA and SPR/Biacore binding analysis. Kd = 0.12 nM.\n\n"
                    "8. Purity and Impurities\n"
                    "rCE-SDS purity 98.5%. RP-HPLC purity 99.1%.\n"
                    "Host cell protein: 12.5 ppm."
                ),
                "tables": [
                    {
                        "id": "table_1",
                        "headers": ["Attribute", "Method", "Value", "Unit"],
                        "rows": [
                            {"Attribute": "Monomer", "Method": "SEC-HPLC", "Value": "98.2", "Unit": "%"},
                            {"Attribute": "HMW (Aggregates)", "Method": "SEC-HPLC", "Value": "1.2", "Unit": "%"},
                            {"Attribute": "Main Charge Peak", "Method": "CEX", "Value": "58.3", "Unit": "%"},
                            {"Attribute": "Relative Potency", "Method": "Cell-based Assay", "Value": "102.5", "Unit": "%"},
                            {"Attribute": "Afucosylation", "Method": "N-glycan HILIC-MS", "Value": "4.2", "Unit": "%"},
                            {"Attribute": "Purity (reduced)", "Method": "rCE-SDS", "Value": "98.5", "Unit": "%"},
                        ],
                    },
                ],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Characterization Report -- mAb-Test IgG1"},
    }


def _create_potency_missing_parsed() -> dict:
    """Create parsed doc WITHOUT potency/biological activity sections."""
    return {
        "document_path": "test_char_no_potency.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Characterization Report -- mAb-Test IgG1\n"
                    "Primary structure confirmed by peptide mapping LC-MS/MS.\n"
                    "Higher-order structure by CD and DSC.\n"
                    "SEC-HPLC monomer 98.2%, HMW: 1.2%.\n"
                    "Charge heterogeneity by CEX.\n"
                    "Glycosylation by N-glycan HILIC-MS.\n"
                    "Purity by rCE-SDS 98.5%.\n"
                    "This is an IgG1 monoclonal antibody."
                ),
                "tables": [
                    {
                        "id": "table_1",
                        "headers": ["Attribute", "Value", "Unit"],
                        "rows": [
                            {"Attribute": "HMW", "Value": "1.2", "Unit": "%"},
                            {"Attribute": "Purity", "Value": "98.5", "Unit": "%"},
                        ],
                    },
                ],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Characterization Report"},
    }


def _create_adcc_high_afuc_parsed() -> dict:
    """Create parsed doc with high afucosylation for ADCC mAb."""
    return {
        "document_path": "test_char_adcc.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Characterization Report -- mAb-ADCC IgG1\n"
                    "This ADCC-dependent antibody was characterized.\n"
                    "Primary structure confirmed by peptide mapping LC-MS/MS.\n"
                    "Higher-order structure by CD and DSC.\n"
                    "SEC-HPLC analysis. HMW: 0.8%.\n"
                    "Charge heterogeneity by CEX and icIEF.\n"
                    "N-glycan profiling by HILIC-MS.\n"
                    "Afucosylation: 35.0%.\n"
                    "Cell-based assay potency: 105%.\n"
                    "ADCC activity measured.\n"
                    "Immunochemical: ELISA and SPR binding.\n"
                    "Purity by rCE-SDS and RP-HPLC.\n"
                    "Reference Standard Lot: RS-2026-002."
                ),
                "tables": [
                    {
                        "id": "table_1",
                        "headers": ["Parameter", "Method", "Value", "Unit"],
                        "rows": [
                            {"Parameter": "Afucosylation", "Method": "N-glycan", "Value": "35.0", "Unit": "%"},
                            {"Parameter": "Relative Potency", "Method": "Cell-based", "Value": "105", "Unit": "%"},
                        ],
                    },
                ],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Characterization Report -- ADCC mAb"},
    }


# ==========================================================================
# Test: Classification
# ==========================================================================

class TestClassificationAsCharacterization:
    """test_classification_as_characterization: The characterization DOCX is classified correctly."""

    def test_classification_as_characterization(self, tmp_path):
        """Full DOCX fixture is classified as CHARACTERIZATION."""
        from ingestion.docx_parser import DOCXDocumentParser
        from ingestion.document_classifier import DocumentClassifier

        fixture_path = _get_fixture_path()
        parser = DOCXDocumentParser()
        parsed = parser.parse(fixture_path)

        classifier = DocumentClassifier()
        result = classifier.classify(parsed)

        assert result.document_type == "CHARACTERIZATION"
        assert result.confidence >= 0.5

    def test_classification_from_minimal_dict(self):
        """Minimal characterization parsed doc classifies as CHARACTERIZATION."""
        from ingestion.document_classifier import DocumentClassifier

        parsed = _create_minimal_characterization_parsed()
        classifier = DocumentClassifier()
        result = classifier.classify(parsed)

        assert result.document_type == "CHARACTERIZATION"
        assert result.confidence >= 0.5


# ==========================================================================
# Test: Section Detection
# ==========================================================================

class TestSectionDetection:
    """test_section_detection: Finds ICH Q6B sections in characterization report."""

    def test_section_detection_full_report(self):
        """All 8 Q6B sections are found in the full synthetic report."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        # All 8 sections should be found
        assert len(evidence["sections_found"]) == 8
        assert len(evidence["sections_missing"]) == 0

    def test_section_detection_with_docx_fixture(self):
        """Parsed DOCX fixture has all sections detected."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _parse_characterization_fixture()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        # Should detect all or nearly all sections
        assert len(evidence["sections_found"]) >= 7
        assert evidence["completeness_score"] >= 0.875  # 7/8

    def test_section_detection_partial_report(self):
        """Report missing potency has that section flagged as missing."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_potency_missing_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert "Biological Activity / Potency" in evidence["sections_missing"]
        assert "Immunochemical Properties" in evidence["sections_missing"]

    def test_section_labels_are_strings(self):
        """Sections found/missing are lists of strings."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        for s in evidence["sections_found"]:
            assert isinstance(s, str)
        for s in evidence["sections_missing"]:
            assert isinstance(s, str)


# ==========================================================================
# Test: Completeness Scoring
# ==========================================================================

class TestCompletenessScoring:
    """test_completeness_scoring: Score = sections_found / total_required."""

    def test_full_completeness(self):
        """Full report has completeness 1.0."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["completeness_score"] == 1.0

    def test_partial_completeness(self):
        """Report missing sections has lower completeness."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_potency_missing_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["completeness_score"] < 1.0
        assert evidence["completeness_score"] > 0.0

    def test_empty_doc_zero_completeness(self):
        """Empty doc has completeness 0.0."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = {"pages": [], "paragraphs": [], "metadata": {}}
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["completeness_score"] == 0.0


# ==========================================================================
# Test: Critical Gap Identification
# ==========================================================================

class TestCriticalGapIdentification:
    """test_critical_gap_identification: Missing potency is always critical."""

    def test_missing_potency_is_critical_gap(self):
        """When potency section is missing, critical gap is flagged."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_potency_missing_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert len(evidence["critical_gaps"]) > 0
        potency_gap = [g for g in evidence["critical_gaps"] if "potency" in g.lower()]
        assert len(potency_gap) > 0, f"No potency gap in: {evidence['critical_gaps']}"

    def test_no_critical_gaps_when_complete(self):
        """Full report has no critical gaps (or minimal ones)."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        # With all sections + ref standard, should be 0 critical gaps
        assert len(evidence["critical_gaps"]) == 0

    def test_missing_ref_standard_is_gap(self):
        """No reference standard triggers a gap."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = {
            "document_path": "test.docx",
            "pages": [{
                "page_number": 1,
                "text": (
                    "Characterization report. Primary structure by peptide mapping LC-MS/MS. "
                    "Higher-order structure by CD and DSC. SEC-HPLC for aggregation. "
                    "Charge heterogeneity by CEX. N-glycan by HILIC-MS. "
                    "Potency by cell-based assay. "
                    "ELISA and SPR immunochemical. "
                    "Purity by rCE-SDS."
                ),
                "tables": [],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        ref_gaps = [g for g in evidence["critical_gaps"] if "reference standard" in g.lower()]
        assert len(ref_gaps) > 0


# ==========================================================================
# Test: Reviewer Concerns
# ==========================================================================

class TestReviewerConcerns:
    """test_reviewer_concerns: Characterization-specific concerns."""

    def test_missing_potency_concern(self):
        """Missing potency generates a CRITICAL reviewer concern."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_potency_missing_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        potency_concerns = [c for c in evidence["reviewer_concerns"] if "potency" in c.lower()]
        assert len(potency_concerns) > 0
        assert any("CRITICAL" in c for c in potency_concerns)

    def test_high_afucosylation_adcc_concern(self):
        """Afucosylation > 30% for ADCC mAb triggers FcgammaRIIIa concern."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_adcc_high_afuc_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        afuc_concerns = [c for c in evidence["reviewer_concerns"] if "fucosylation" in c.lower() or "fcgamma" in c.lower()]
        assert len(afuc_concerns) > 0

    def test_no_ref_standard_concern(self):
        """Missing reference standard generates traceability concern."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_potency_missing_parsed()
        # This parsed doc doesn't mention reference standard
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        ref_concerns = [c for c in evidence["reviewer_concerns"] if "reference standard" in c.lower()]
        assert len(ref_concerns) > 0

    def test_complete_report_has_fewer_concerns(self):
        """Full report should have fewer/no critical concerns."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        critical = [c for c in evidence["reviewer_concerns"] if "CRITICAL" in c]
        assert len(critical) == 0


# ==========================================================================
# Test: HMW Extraction
# ==========================================================================

class TestHMWExtraction:
    """test_hmw_extraction: Extract HMW percentage from text and tables."""

    def test_hmw_from_text(self):
        """HMW value extracted from narrative text."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["hmw_pct"] is not None
        assert abs(evidence["hmw_pct"] - 1.2) < 0.01

    def test_hmw_from_table(self):
        """HMW value extracted from table data."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = {
            "document_path": "test.docx",
            "pages": [{
                "page_number": 1,
                "text": "Characterization report. SEC-HPLC aggregation analysis.",
                "tables": [{
                    "id": "t1",
                    "headers": ["Attribute", "Value", "Unit"],
                    "rows": [
                        {"Attribute": "HMW", "Value": "2.5", "Unit": "%"},
                    ],
                }],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["hmw_pct"] is not None
        assert abs(evidence["hmw_pct"] - 2.5) < 0.01


# ==========================================================================
# Test: Potency Extraction
# ==========================================================================

class TestPotencyExtraction:
    """test_potency_extraction: Extract relative potency from text and tables."""

    def test_potency_from_text(self):
        """Potency value extracted from narrative text."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["potency_relative_pct"] is not None
        assert abs(evidence["potency_relative_pct"] - 102.5) < 0.1

    def test_potency_from_table(self):
        """Potency value extracted from table data."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = {
            "document_path": "test.docx",
            "pages": [{
                "page_number": 1,
                "text": "Characterization report. Biological activity potency. Cell-based assay.",
                "tables": [{
                    "id": "t1",
                    "headers": ["Attribute", "Value", "Unit"],
                    "rows": [
                        {"Attribute": "Relative Potency", "Value": "98.3", "Unit": "%"},
                    ],
                }],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["potency_relative_pct"] is not None
        assert abs(evidence["potency_relative_pct"] - 98.3) < 0.1

    def test_potency_none_when_absent(self):
        """No potency data returns None."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = {
            "document_path": "test.docx",
            "pages": [{
                "page_number": 1,
                "text": "Some report with no potency data.",
                "tables": [],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["potency_relative_pct"] is None


# ==========================================================================
# Test: Dispatcher Routing
# ==========================================================================

class TestDispatcherRouting:
    """test_dispatcher_routes_characterization: CHARACTERIZATION -> CharacterizationExtractor."""

    def test_dispatcher_routes_characterization(self):
        """Dispatcher routes CHARACTERIZATION type to CharacterizationExtractor."""
        from ingestion.dispatcher import IngestionDispatcher
        from ingestion.document_classifier import DocTypeSpec
        from ingestion.characterization_extractor import CharacterizationExtractor

        dispatcher = IngestionDispatcher()
        doc_type = DocTypeSpec(
            document_type="CHARACTERIZATION",
            confidence=0.85,
            classification_notes=["Test"],
        )
        parsed = _create_minimal_characterization_parsed()

        extractor = dispatcher.dispatch(parsed, doc_type)
        assert isinstance(extractor, CharacterizationExtractor)


# ==========================================================================
# Test: Error Safety
# ==========================================================================

class TestErrorSafety:
    """Extract methods never raise unhandled exceptions."""

    def test_extract_attributes_never_raises(self):
        """extract_attributes returns empty list on bad input."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        extractor = CharacterizationExtractor()

        # None-ish
        result = extractor.extract_attributes({})
        assert isinstance(result, list)

        # Missing keys
        result = extractor.extract_attributes({"pages": None})
        assert isinstance(result, list)

        # Completely wrong type
        result = extractor.extract_attributes({"pages": "not a list"})
        assert isinstance(result, list)

    def test_extract_evidence_never_raises(self):
        """extract_evidence returns dict with error key on bad input."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        extractor = CharacterizationExtractor()

        result = extractor.extract_evidence({})
        assert isinstance(result, dict)

        result = extractor.extract_evidence({"pages": None})
        assert isinstance(result, dict)

        result = extractor.extract_evidence({"pages": "not a list"})
        assert isinstance(result, dict)


# ==========================================================================
# Test: Attribute Extraction
# ==========================================================================

class TestAttributeExtraction:
    """Test that extract_attributes produces ExtractedAttribute objects."""

    def test_extracts_attributes_from_tables(self):
        """Attributes are extracted from characterization tables."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        attrs = extractor.extract_attributes(parsed)

        assert len(attrs) > 0
        names = [a.name for a in attrs]
        # Should find at least some of the table entries
        assert any("Monomer" in n or "HMW" in n or "Potency" in n for n in names)

    def test_attribute_categories_assigned(self):
        """Extracted attributes have meaningful categories."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        attrs = extractor.extract_attributes(parsed)

        categories = set(a.category for a in attrs)
        # Should have multiple categories, not all "general"
        assert len(categories) >= 2

    def test_attributes_from_docx_fixture(self):
        """DOCX fixture produces attributes."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _parse_characterization_fixture()
        extractor = CharacterizationExtractor()
        attrs = extractor.extract_attributes(parsed)

        assert len(attrs) > 0

    def test_supported_categories(self):
        """supported_categories returns expected list."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        extractor = CharacterizationExtractor()
        cats = extractor.supported_categories()

        assert "biological_activity" in cats
        assert "purity" in cats
        assert "glycosylation" in cats
        assert "aggregation" in cats


# ==========================================================================
# Test: Reference Standard
# ==========================================================================

class TestReferenceStandard:
    """Reference standard detection and lot extraction."""

    def test_reference_standard_identified(self):
        """Reference standard is identified from text."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["reference_standard_identified"] is True

    def test_reference_standard_lot_extracted(self):
        """Reference standard lot number is extracted."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["reference_standard_lot"] != ""
        assert "RS-2026-001" in evidence["reference_standard_lot"]


# ==========================================================================
# Test: Afucosylation Extraction
# ==========================================================================

class TestAfucosylationExtraction:
    """Extract afucosylation percentage."""

    def test_afucosylation_from_text(self):
        """Afucosylation value extracted from text."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_minimal_characterization_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["afucosylation_pct"] is not None
        assert abs(evidence["afucosylation_pct"] - 4.2) < 0.1

    def test_high_afucosylation(self):
        """High afucosylation value detected."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        parsed = _create_adcc_high_afuc_parsed()
        extractor = CharacterizationExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["afucosylation_pct"] is not None
        assert evidence["afucosylation_pct"] > 30.0
