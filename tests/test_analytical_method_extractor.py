"""
D4: Tests for AnalyticalMethodExtractor (Phase 3 Track D).

Covers (~15 tests):
- Classification as ANALYTICAL_METHOD
- Validation study detection (ICH Q2)
- Completeness scoring
- Accuracy/precision/linearity extraction
- LOD/LOQ detection
- Gap identification
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
    """Return path to test method validation DOCX, creating if needed."""
    fixture_path = os.path.join(PROJECT_ROOT, "tests", "fixtures", "test_method_validation_report.docx")
    if not os.path.exists(fixture_path):
        from tests.create_method_validation_fixture import create_method_validation_report_docx
        os.makedirs(os.path.dirname(fixture_path), exist_ok=True)
        create_method_validation_report_docx(fixture_path)
    return fixture_path


def _parse_method_validation_fixture() -> dict:
    """Parse the method validation DOCX fixture and return parsed doc."""
    from ingestion.docx_parser import DOCXDocumentParser
    fixture_path = _get_fixture_path()
    parser = DOCXDocumentParser()
    return parser.parse(fixture_path)


def _create_minimal_method_validation_parsed() -> dict:
    """Create a minimal parsed doc that looks like a method validation report."""
    return {
        "document_path": "test_method_val.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Method Validation Report for SEC-HPLC\n"
                    "Validation of the SEC-HPLC method per ICH Q2(R2).\n"
                    "Method name: SEC-HPLC Purity\n\n"
                    "1. Specificity: Placebo interference test passed.\n"
                    "2. Linearity: R2 = 0.9998. Calibration curve validated.\n"
                    "3. Range: Working range 50-200% nominal.\n"
                    "4. Accuracy: Mean recovery: 100.2%.\n"
                    "5. Precision: Repeatability %RSD = 0.8%. Intra-day.\n"
                    "6. Intermediate Precision: Inter-day %RSD = 1.2%.\n"
                    "7. LOD: Limit of detection = 0.02 mg/mL.\n"
                    "8. LOQ: Limit of quantitation = 0.05 mg/mL.\n"
                    "9. Robustness: Deliberate variation tested.\n"
                    "10. System suitability criteria met."
                ),
                "tables": [
                    {
                        "id": "table_summary",
                        "headers": ["Parameter", "Result", "Criteria"],
                        "rows": [
                            {"Parameter": "Specificity", "Result": "Pass", "Criteria": "No interference"},
                            {"Parameter": "Linearity (R2)", "Result": "0.9998", "Criteria": ">= 0.999"},
                            {"Parameter": "Accuracy (Recovery)", "Result": "100.2%", "Criteria": "98-102%"},
                            {"Parameter": "Precision (RSD)", "Result": "0.8%", "Criteria": "<= 2.0%"},
                            {"Parameter": "LOD", "Result": "0.02 mg/mL", "Criteria": "Reported"},
                            {"Parameter": "LOQ", "Result": "0.05 mg/mL", "Criteria": "Reported"},
                            {"Parameter": "Robustness", "Result": "Pass", "Criteria": "Robust"},
                        ],
                    },
                ],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Method Validation Report for SEC-HPLC"},
    }


def _create_incomplete_validation_parsed() -> dict:
    """Create a parsed doc with incomplete validation (missing several studies)."""
    return {
        "document_path": "test_incomplete_val.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Method Validation Report\n"
                    "Partial validation of the SEC-HPLC method.\n"
                    "1. Calibration curve: R2 = 0.9990.\n"
                    "2. Repeatability: %RSD = 1.5%.\n"
                    "Additional studies are pending."
                ),
                "tables": [
                    {
                        "id": "table_1",
                        "headers": ["Parameter", "Result"],
                        "rows": [
                            {"Parameter": "Calibration R2", "Result": "0.9990"},
                            {"Parameter": "Repeatability RSD", "Result": "1.5%"},
                        ],
                    },
                ],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Incomplete Method Validation"},
    }


def _create_failing_validation_parsed() -> dict:
    """Create a parsed doc with out-of-spec validation results."""
    return {
        "document_path": "test_failing_val.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Method Validation Report\n"
                    "Method validation per ICH Q2.\n"
                    "Specificity: Selectivity confirmed.\n"
                    "Linearity: R2 = 0.9950.\n"
                    "Accuracy: Mean recovery: 95.5%.\n"
                    "Precision: %RSD = 3.5%.\n"
                    "Repeatability confirmed.\n"
                    "Intermediate precision: inter-day.\n"
                    "Range: Working range.\n"
                    "LOD reported. LOQ reported.\n"
                    "Robustness: deliberate variation.\n"
                    "This is an impurity method."
                ),
                "tables": [],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Failing Method Validation"},
    }


def _create_impurity_method_no_loq() -> dict:
    """Create a parsed doc for impurity method missing LOD/LOQ."""
    return {
        "document_path": "test_impurity_no_loq.docx",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Method Validation Report for Impurity Analysis\n"
                    "Validation per ICH Q2.\n"
                    "Specificity: confirmed.\n"
                    "Linearity: R2 = 0.9995.\n"
                    "Accuracy: recovery: 99.8%.\n"
                    "Precision: %RSD = 1.2%. Repeatability.\n"
                    "Intermediate precision: inter-day.\n"
                    "Range validated.\n"
                    "Robustness tested.\n"
                    "This method detects impurities and degradation products."
                ),
                "tables": [],
            },
        ],
        "paragraphs": [],
        "metadata": {"title": "Impurity Method Validation"},
    }


# ==========================================================================
# Test: Classification
# ==========================================================================

class TestClassificationAsAnalyticalMethod:
    """Method validation documents should be classified as ANALYTICAL_METHOD."""

    def test_classification_from_minimal_dict(self):
        """Minimal method validation parsed doc classifies as ANALYTICAL_METHOD."""
        from ingestion.document_classifier import DocumentClassifier

        parsed = _create_minimal_method_validation_parsed()
        classifier = DocumentClassifier()
        result = classifier.classify(parsed)

        assert result.document_type == "ANALYTICAL_METHOD"
        assert result.confidence >= 0.5

    def test_classification_with_docx_fixture(self):
        """Full DOCX fixture is classified as ANALYTICAL_METHOD."""
        from ingestion.docx_parser import DOCXDocumentParser
        from ingestion.document_classifier import DocumentClassifier

        fixture_path = _get_fixture_path()
        parser = DOCXDocumentParser()
        parsed = parser.parse(fixture_path)

        classifier = DocumentClassifier()
        result = classifier.classify(parsed)

        assert result.document_type == "ANALYTICAL_METHOD"
        assert result.confidence >= 0.5


# ==========================================================================
# Test: Validation Study Detection
# ==========================================================================

class TestValidationStudyDetection:
    """ICH Q2 validation studies should be detected."""

    def test_all_studies_detected_in_full_report(self):
        """Full report has all Q2 studies detected."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_minimal_method_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert len(evidence["validation_studies_found"]) >= 8
        assert len(evidence["validation_studies_missing"]) <= 1

    def test_partial_studies_detected(self):
        """Incomplete report has only some studies detected."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_incomplete_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        found = evidence["validation_studies_found"]
        missing = evidence["validation_studies_missing"]
        assert len(found) < len(found) + len(missing)
        assert len(missing) > 0

    def test_study_labels_are_strings(self):
        """Studies found/missing are lists of strings."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_minimal_method_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        for s in evidence["validation_studies_found"]:
            assert isinstance(s, str)


# ==========================================================================
# Test: Completeness Scoring
# ==========================================================================

class TestCompletenessScoring:
    """Completeness score should reflect validation coverage."""

    def test_full_completeness(self):
        """Full report has high completeness."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_minimal_method_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["completeness_score"] >= 0.8

    def test_partial_completeness(self):
        """Incomplete report has lower completeness."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_incomplete_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["completeness_score"] < 0.8
        assert evidence["completeness_score"] > 0.0

    def test_empty_doc_zero_completeness(self):
        """Empty doc has completeness 0.0."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = {"pages": [], "paragraphs": [], "metadata": {}}
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["completeness_score"] == 0.0


# ==========================================================================
# Test: Numeric Value Extraction
# ==========================================================================

class TestNumericExtraction:
    """Key numeric values should be extracted."""

    def test_recovery_extracted(self):
        """Accuracy recovery percentage extracted."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_minimal_method_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["accuracy_recovery"] is not None
        assert abs(evidence["accuracy_recovery"] - 100.2) < 0.1

    def test_rsd_extracted(self):
        """Precision RSD extracted."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_minimal_method_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["precision_rsd"] is not None
        assert evidence["precision_rsd"] < 2.0

    def test_linearity_r2_extracted(self):
        """Linearity R2 extracted."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_minimal_method_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["linearity_r2"] is not None
        assert evidence["linearity_r2"] >= 0.999

    def test_lod_loq_reported(self):
        """LOD and LOQ presence detected."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_minimal_method_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert evidence["lod_reported"] is True
        assert evidence["loq_reported"] is True


# ==========================================================================
# Test: Gap Identification
# ==========================================================================

class TestGapIdentification:
    """Critical gaps should be identified."""

    def test_missing_studies_are_gaps(self):
        """Incomplete validation flags gaps."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_incomplete_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert len(evidence["critical_gaps"]) > 0

    def test_failing_accuracy_is_gap(self):
        """Accuracy outside 98-102% is flagged."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_failing_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        accuracy_gaps = [g for g in evidence["critical_gaps"] if "accuracy" in g.lower() or "recovery" in g.lower()]
        assert len(accuracy_gaps) > 0

    def test_failing_linearity_is_gap(self):
        """R2 below 0.999 is flagged."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_failing_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        lin_gaps = [g for g in evidence["critical_gaps"] if "linearity" in g.lower() or "r2" in g.lower()]
        assert len(lin_gaps) > 0

    def test_missing_loq_for_impurity_method(self):
        """Missing LOQ for impurity method is major gap."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_impurity_method_no_loq()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        loq_gaps = [g for g in evidence["critical_gaps"] if "loq" in g.lower()]
        assert len(loq_gaps) > 0

    def test_no_gaps_when_complete(self):
        """Full valid report has no critical gaps."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _create_minimal_method_validation_parsed()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert len(evidence["critical_gaps"]) == 0


# ==========================================================================
# Test: Dispatcher Routing
# ==========================================================================

class TestDispatcherRouting:
    """ANALYTICAL_METHOD -> AnalyticalMethodExtractor."""

    def test_dispatcher_routes_analytical_method(self):
        """Dispatcher routes ANALYTICAL_METHOD to AnalyticalMethodExtractor."""
        from ingestion.dispatcher import IngestionDispatcher
        from ingestion.document_classifier import DocTypeSpec
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        dispatcher = IngestionDispatcher()
        doc_type = DocTypeSpec(
            document_type="ANALYTICAL_METHOD",
            confidence=0.85,
            classification_notes=["Test"],
        )
        parsed = _create_minimal_method_validation_parsed()

        extractor = dispatcher.dispatch(parsed, doc_type)
        assert isinstance(extractor, AnalyticalMethodExtractor)


# ==========================================================================
# Test: Error Safety
# ==========================================================================

class TestErrorSafety:
    """Extract methods never raise unhandled exceptions."""

    def test_extract_attributes_never_raises(self):
        """extract_attributes returns empty list on bad input."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        extractor = AnalyticalMethodExtractor()

        result = extractor.extract_attributes({})
        assert isinstance(result, list)

        result = extractor.extract_attributes({"pages": None})
        assert isinstance(result, list)

        result = extractor.extract_attributes({"pages": "not a list"})
        assert isinstance(result, list)

    def test_extract_evidence_never_raises(self):
        """extract_evidence returns dict on bad input."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        extractor = AnalyticalMethodExtractor()

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
        """DOCX fixture produces evidence with validation studies."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _parse_method_validation_fixture()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        assert len(evidence["validation_studies_found"]) >= 5
        assert evidence["completeness_score"] > 0.5
        assert evidence["tables_found"] > 0

    def test_docx_fixture_extracts_attributes(self):
        """DOCX fixture produces attributes."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _parse_method_validation_fixture()
        extractor = AnalyticalMethodExtractor()
        attrs = extractor.extract_attributes(parsed)

        assert len(attrs) > 0

    def test_docx_fixture_method_name(self):
        """DOCX fixture detects method name."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        parsed = _parse_method_validation_fixture()
        extractor = AnalyticalMethodExtractor()
        evidence = extractor.extract_evidence(parsed)

        # Method name should be detected
        assert evidence["method_name"] is not None
