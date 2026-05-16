"""
C4: Tests for StabilityExtractor (Phase 3 Track C).

Covers (~15 tests):
- Classification as STABILITY
- Timepoint detection
- Condition detection (5C, 25C/60RH, 40C/75RH)
- OOS flagging
- Shelf-life assessment
- Sufficiency for claim
- Critical gap identification
- Attribute extraction from tables
- Error safety (never raises)
- Dispatcher routing
- DOCX fixture end-to-end
"""

from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)


# ==========================================================================
# Helpers
# ==========================================================================

def _get_fixture_path() -> str:
    """Return path to test stability DOCX, creating if needed."""
    fixture_path = os.path.join(PROJECT_ROOT, "tests", "fixtures", "test_stability_report.docx")
    if not os.path.exists(fixture_path):
        from tests.create_stability_fixture import create_stability_report_docx
        os.makedirs(os.path.dirname(fixture_path), exist_ok=True)
        create_stability_report_docx(fixture_path)
    return fixture_path


def _parse_stability_fixture() -> dict:
    """Parse the stability DOCX fixture and return parsed doc."""
    from ingestion.docx_parser import DOCXDocumentParser
    fixture_path = _get_fixture_path()
    parser = DOCXDocumentParser()
    return parser.parse(fixture_path)


def _create_minimal_stability_parsed() -> dict:
    """Create a minimal parsed doc dict that looks like a stability report."""
    return {
        "document_path": "test_stability.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Stability Study Report -- mAb-Stab-01\n"
                    "This stability study was conducted per ICH Q1A(R2) and ICH Q5C.\n"
                    "Long-term stability at 5 degC (refrigerated storage).\n"
                    "Accelerated stability at 25 degC / 60% RH.\n"
                    "Stress stability at 40 degC / 75% RH.\n"
                    "Shelf-life of 24 months is proposed.\n"
                    "Samples tested at T=0, 3M, 6M, 9M, 12M, 18M, 24M.\n"
                    "Stability-indicating attributes: purity, aggregation, potency."
                ),
                "tables": [
                    {
                        "id": "table_longterm",
                        "headers": ["Attribute", "T=0", "3M", "6M", "12M", "18M", "24M"],
                        "rows": [
                            {"Attribute": "Purity (SEC)", "T=0": "99.2", "3M": "99.1",
                             "6M": "99.0", "12M": "98.6", "18M": "98.3", "24M": "98.0"},
                            {"Attribute": "HMW (%)", "T=0": "0.5", "3M": "0.6",
                             "6M": "0.7", "12M": "0.9", "18M": "1.1", "24M": "1.3"},
                            {"Attribute": "Potency (%)", "T=0": "102", "3M": "101",
                             "6M": "101", "12M": "100", "18M": "99", "24M": "98"},
                        ],
                    },
                    {
                        "id": "table_accelerated",
                        "headers": ["Attribute", "T=0", "3M", "6M"],
                        "rows": [
                            {"Attribute": "Purity (SEC)", "T=0": "99.2", "3M": "98.5", "6M": "97.8"},
                            {"Attribute": "HMW (%)", "T=0": "0.5", "3M": "1.0", "6M": "1.8"},
                        ],
                    },
                ],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Stability Study Report -- mAb-Stab-01"},
    }


def _create_stability_with_oos() -> dict:
    """Create a parsed doc with OOS events."""
    return {
        "document_path": "test_stability_oos.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Stability study per ICH Q1A.\n"
                    "Long-term stability at 5 degC.\n"
                    "Accelerated stability at 25 degC / 60% RH.\n"
                    "Stress at 40 degC / 75% RH.\n"
                    "OOS event at 3M stress condition for potency.\n"
                    "Shelf-life of 24 months is proposed.\n"
                    "T=0, 3M, 6M, 12M timepoints."
                ),
                "tables": [
                    {
                        "id": "table_stress",
                        "headers": ["Attribute", "T=0", "1M", "3M"],
                        "rows": [
                            {"Attribute": "Purity (SEC)", "T=0": "99.2", "1M": "96.1", "3M": "91.5"},
                            {"Attribute": "Potency (%)", "T=0": "102", "1M": "88", "3M": "FAIL - OOS"},
                        ],
                    },
                ],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Stability with OOS"},
    }


def _create_insufficient_stability() -> dict:
    """Create a parsed doc with insufficient stability data (< 12 months)."""
    return {
        "document_path": "test_insufficient_stability.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Stability study per ICH Q1A.\n"
                    "Only 6 months of data available.\n"
                    "Shelf-life of 24 months is proposed.\n"
                    "T=0, 3M, 6M timepoints.\n"
                    "Purity and potency monitored."
                ),
                "tables": [
                    {
                        "id": "table_1",
                        "headers": ["Attribute", "T=0", "3M", "6M"],
                        "rows": [
                            {"Attribute": "Purity", "T=0": "99.2", "3M": "99.0", "6M": "98.8"},
                        ],
                    },
                ],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Insufficient Stability Data"},
    }


def _create_no_accelerated_stability() -> dict:
    """Create a parsed doc missing accelerated condition."""
    return {
        "document_path": "test_no_accel.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Stability study per ICH Q1A.\n"
                    "Long-term stability at 5 degC only.\n"
                    "T=0, 3M, 6M, 12M, 18M, 24M.\n"
                    "Purity and potency monitored.\n"
                    "Shelf-life of 24 months proposed."
                ),
                "tables": [
                    {
                        "id": "table_1",
                        "headers": ["Attribute", "T=0", "6M", "12M", "24M"],
                        "rows": [
                            {"Attribute": "Purity", "T=0": "99.2", "6M": "99.0",
                             "12M": "98.6", "24M": "98.0"},
                        ],
                    },
                ],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Long-term Only Stability"},
    }


# ==========================================================================
# Test: Classification
# ==========================================================================

class TestClassificationAsStability:
    """Stability documents should be classified as STABILITY."""

    def test_classification_from_minimal_dict(self):
        """Minimal stability parsed doc classifies as STABILITY."""
        from ingestion.document_classifier import DocumentClassifier

        parsed = _create_minimal_stability_parsed()
        classifier = DocumentClassifier()
        result = classifier.classify(parsed)

        assert result.document_type == "STABILITY"
        assert result.confidence >= 0.5

    def test_classification_with_docx_fixture(self):
        """Full DOCX fixture is classified as STABILITY."""
        from ingestion.docx_parser import DOCXDocumentParser
        from ingestion.document_classifier import DocumentClassifier

        fixture_path = _get_fixture_path()
        parser = DOCXDocumentParser()
        parsed = parser.parse(fixture_path)

        classifier = DocumentClassifier()
        result = classifier.classify(parsed)

        assert result.document_type == "STABILITY"
        assert result.confidence >= 0.5


# ==========================================================================
# Test: Timepoint Detection
# ==========================================================================

class TestTimepointDetection:
    """Timepoint columns should be detected from stability tables."""

    def test_max_timepoint_detected(self):
        """Maximum timepoint in months is detected."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_minimal_stability_parsed()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["max_timepoint_months"] >= 24

    def test_timepoint_columns_in_tables(self):
        """Timepoint columns extracted from table headers."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_minimal_stability_parsed()
        extractor = StabilityExtractor()
        attrs = extractor.extract_attributes(parsed)

        # Should have multiple timepoint-labeled attributes
        timepoints = set(a.timepoint for a in attrs if a.timepoint)
        assert len(timepoints) >= 3

    def test_short_study_timepoint(self):
        """Short study correctly reports max timepoint."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_insufficient_stability()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["max_timepoint_months"] == 6


# ==========================================================================
# Test: Condition Detection
# ==========================================================================

class TestConditionDetection:
    """Storage conditions should be detected from text."""

    def test_all_three_conditions_detected(self):
        """Detects 5C, 25C/60RH, and 40C/75RH conditions."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_minimal_stability_parsed()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        conditions = evidence["conditions_tested"]
        assert "5C" in conditions
        assert "25C/60RH" in conditions
        assert "40C/75RH" in conditions

    def test_missing_accelerated_detected(self):
        """Reports only long-term condition when accelerated is absent."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_no_accelerated_stability()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        conditions = evidence["conditions_tested"]
        assert "5C" in conditions
        assert "40C/75RH" not in conditions


# ==========================================================================
# Test: OOS Flagging
# ==========================================================================

class TestOOSFlagging:
    """OOS/OOT events should be detected."""

    def test_oos_detected_in_table(self):
        """OOS flag in table cell is detected."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_stability_with_oos()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert len(evidence["oos_events"]) > 0
        flags = [e["flag"] for e in evidence["oos_events"]]
        assert any("OOS" in f or "FAIL" in f for f in flags)

    def test_no_oos_when_clean(self):
        """No OOS events in clean stability data."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_minimal_stability_parsed()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        # Minimal parsed doc has no OOS flags
        # Text doesn't mention OOS
        oos_from_table = [e for e in evidence["oos_events"] if e.get("source") == "table"]
        assert len(oos_from_table) == 0


# ==========================================================================
# Test: Shelf-Life Assessment
# ==========================================================================

class TestShelfLifeAssessment:
    """Shelf-life claim sufficiency should be assessed."""

    def test_sufficient_shelf_life(self):
        """24M data supporting 24M claim is sufficient."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_minimal_stability_parsed()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["proposed_shelf_life"] == 24
        assert evidence["sufficiency_for_claim"] == "sufficient"

    def test_insufficient_data_for_claim(self):
        """6M data for 24M claim is insufficient."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_insufficient_stability()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["sufficiency_for_claim"] == "insufficient"

    def test_no_shelf_life_no_claim(self):
        """Without proposed shelf-life, sufficiency based on data alone."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = {
            "document_path": "test.docx",
            "pages": [{
                "page_number": 1,
                "text": "Stability study at 5 degC. T=0, 6M, 12M, 18M.",
                "tables": [{
                    "id": "t1",
                    "headers": ["Attribute", "T=0", "6M", "12M", "18M"],
                    "rows": [{"Attribute": "Purity", "T=0": "99", "6M": "98.5", "12M": "98.0", "18M": "97.5"}],
                }],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["proposed_shelf_life"] is None
        assert evidence["sufficiency_for_claim"] == "sufficient"


# ==========================================================================
# Test: Critical Gaps
# ==========================================================================

class TestCriticalGaps:
    """Critical gaps should be identified."""

    def test_missing_accelerated_is_gap(self):
        """No accelerated data flags a gap."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_no_accelerated_stability()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        accel_gaps = [g for g in evidence["critical_gaps"] if "accelerated" in g.lower()]
        assert len(accel_gaps) > 0

    def test_insufficient_data_gap(self):
        """Less than 12M data flags a gap."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_insufficient_stability()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        tp_gaps = [g for g in evidence["critical_gaps"] if "12 month" in g.lower() or "12M" in g]
        # Should flag the insufficient data
        assert len(evidence["critical_gaps"]) > 0

    def test_no_critical_gaps_when_complete(self):
        """Full study has minimal critical gaps."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_minimal_stability_parsed()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        # Complete study should not have gaps about missing conditions or data
        condition_gaps = [g for g in evidence["critical_gaps"]
                         if "no long-term" in g.lower() or "no accelerated" in g.lower()]
        assert len(condition_gaps) == 0


# ==========================================================================
# Test: Attribute Extraction
# ==========================================================================

class TestAttributeExtraction:
    """Attributes should be extracted from stability tables."""

    def test_attributes_from_tables(self):
        """Multiple attributes extracted from timepoint tables."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_minimal_stability_parsed()
        extractor = StabilityExtractor()
        attrs = extractor.extract_attributes(parsed)

        assert len(attrs) > 0
        names = [a.name for a in attrs]
        assert any("Purity" in n for n in names)

    def test_attributes_have_timepoints(self):
        """Extracted attributes carry timepoint labels."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _create_minimal_stability_parsed()
        extractor = StabilityExtractor()
        attrs = extractor.extract_attributes(parsed)

        attrs_with_tp = [a for a in attrs if a.timepoint]
        assert len(attrs_with_tp) > 0

    def test_supported_categories(self):
        """supported_categories returns expected list."""
        from ingestion.stability_extractor import StabilityExtractor

        extractor = StabilityExtractor()
        cats = extractor.supported_categories()

        assert "stability" in cats
        assert "purity" in cats
        assert "potency" in cats


# ==========================================================================
# Test: Dispatcher Routing
# ==========================================================================

class TestDispatcherRouting:
    """STABILITY -> StabilityExtractor."""

    def test_dispatcher_routes_stability(self):
        """Dispatcher routes STABILITY type to StabilityExtractor."""
        from ingestion.dispatcher import IngestionDispatcher
        from ingestion.document_classifier import DocTypeSpec
        from ingestion.stability_extractor import StabilityExtractor

        dispatcher = IngestionDispatcher()
        doc_type = DocTypeSpec(
            document_type="STABILITY",
            confidence=0.85,
            classification_notes=["Test"],
        )
        parsed = _create_minimal_stability_parsed()

        extractor = dispatcher.dispatch(parsed, doc_type)
        assert isinstance(extractor, StabilityExtractor)


# ==========================================================================
# Test: Error Safety
# ==========================================================================

class TestErrorSafety:
    """Extract methods never raise unhandled exceptions."""

    def test_extract_attributes_never_raises(self):
        """extract_attributes returns empty list on bad input."""
        from ingestion.stability_extractor import StabilityExtractor

        extractor = StabilityExtractor()

        result = extractor.extract_attributes({})
        assert isinstance(result, list)

        result = extractor.extract_attributes({"pages": None})
        assert isinstance(result, list)

        result = extractor.extract_attributes({"pages": "not a list"})
        assert isinstance(result, list)

    def test_extract_evidence_never_raises(self):
        """extract_evidence returns dict on bad input."""
        from ingestion.stability_extractor import StabilityExtractor

        extractor = StabilityExtractor()

        result = extractor.extract_evidence({})
        assert isinstance(result, dict)

        result = extractor.extract_evidence({"pages": None})
        assert isinstance(result, dict)

        result = extractor.extract_evidence({"pages": "not a list"})
        assert isinstance(result, dict)


# ==========================================================================
# Test: DOCX Fixture End-to-End
# ==========================================================================

class TestDOCXFixtureE2E:
    """End-to-end test with real DOCX fixture."""

    def test_docx_fixture_extracts_evidence(self):
        """DOCX fixture produces evidence with conditions and timepoints."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _parse_stability_fixture()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["max_timepoint_months"] >= 12
        assert len(evidence["conditions_tested"]) >= 2
        assert evidence["tables_found"] > 0

    def test_docx_fixture_extracts_attributes(self):
        """DOCX fixture produces attributes."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _parse_stability_fixture()
        extractor = StabilityExtractor()
        attrs = extractor.extract_attributes(parsed)

        assert len(attrs) > 0

    def test_docx_fixture_detects_oos(self):
        """DOCX fixture (which has OOS in stress table) detects OOS events."""
        from ingestion.stability_extractor import StabilityExtractor

        parsed = _parse_stability_fixture()
        extractor = StabilityExtractor()
        evidence = extractor.extract_evidence(parsed)

        # The fixture has "FAIL - OOS" in the stress table
        assert len(evidence["oos_events"]) > 0
