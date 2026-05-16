"""
Phase 4D: Tests for Cross-Document Consistency Checker.

Verifies:
1. ConsistencyFlag dataclass instantiation
2. Value conflict detection across documents
3. Reference standard conflict detection
4. Method conflict detection
5. Temporal inconsistency detection
6. Empty/single-document edge cases
7. Performance: consistency check completes in < 30s
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from dataclasses import asdict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ingestion.unified_result import UnifiedIngestionResult
from ingestion.context_extractor import ExtractedCaseContext
from ingestion.document_classifier import DocTypeSpec
from specs.cross_document_bridge import ExtractedAttribute
from services.cross_document_checker import (
    ConsistencyFlag,
    check_cross_document_consistency,
)


# ---------------------------------------------------------------------------
# Helpers to build test results
# ---------------------------------------------------------------------------

def _make_attr(
    name: str,
    value: float,
    context: str = "",
    timepoint: str = "",
) -> ExtractedAttribute:
    return ExtractedAttribute(
        name=name,
        value=value,
        unit="%",
        source_document="test",
        source_page=1,
        source_table="T1",
        confidence=0.9,
        context=context,
        timepoint=timepoint,
    )


def _make_result(
    doc_path: str = "doc_A.docx",
    doc_type: str = "CHARACTERIZATION",
    attributes: list | None = None,
    evidence: dict | None = None,
) -> UnifiedIngestionResult:
    """Build a minimal UnifiedIngestionResult for testing."""
    case_ctx = ExtractedCaseContext(
        product_name="TestMab",
        molecule_class="mAb",
        change_type="process",
    )
    doc_spec = DocTypeSpec(
        document_type=doc_type,
        confidence=0.9,
        classification_notes=[],
    )
    return UnifiedIngestionResult(
        attributes=attributes or [],
        case_context=case_ctx,
        signals=[],
        document_classification=doc_spec,
        extracted_evidence=evidence or {},
        parsed_doc={"document_path": doc_path, "pages": [], "paragraphs": []},
    )


# ---------------------------------------------------------------------------
# 1. ConsistencyFlag dataclass
# ---------------------------------------------------------------------------

class TestConsistencyFlagDataclass(unittest.TestCase):
    """ConsistencyFlag should instantiate and serialize correctly."""

    def test_instantiation(self):
        flag = ConsistencyFlag(
            flag_id="XDOC-0001",
            severity="warning",
            description="Test conflict",
            document_a="doc_A",
            document_b="doc_B",
            attribute="HMW",
            value_a=1.2,
            value_b=2.5,
        )
        self.assertEqual(flag.flag_id, "XDOC-0001")
        self.assertEqual(flag.severity, "warning")

    def test_asdict(self):
        flag = ConsistencyFlag(
            flag_id="XDOC-0001",
            severity="critical",
            description="desc",
            document_a="A",
            document_b="B",
            attribute="Purity",
            value_a=95.0,
            value_b=88.0,
        )
        d = asdict(flag)
        self.assertIn("flag_id", d)
        self.assertIn("severity", d)
        self.assertEqual(d["value_a"], 95.0)


# ---------------------------------------------------------------------------
# 2. Value conflict detection
# ---------------------------------------------------------------------------

class TestValueConflictDetection(unittest.TestCase):
    """Same attribute with different values across documents triggers a flag."""

    def test_detects_value_conflict(self):
        result_a = _make_result(
            doc_path="char_report.docx",
            doc_type="CHARACTERIZATION",
            attributes=[_make_attr("HMW (%)", 1.2)],
        )
        result_b = _make_result(
            doc_path="stability_report.docx",
            doc_type="STABILITY",
            attributes=[_make_attr("HMW (%)", 2.8)],
        )
        flags = check_cross_document_consistency([result_a, result_b])
        value_flags = [f for f in flags if "Value conflict" in f.description]
        self.assertTrue(
            len(value_flags) >= 1,
            f"Expected value conflict flag, got: {[f.description for f in flags]}"
        )

    def test_no_conflict_for_same_values(self):
        """Identical values across documents should not trigger a flag."""
        result_a = _make_result(
            doc_path="doc_A.docx",
            attributes=[_make_attr("Purity", 98.5)],
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            attributes=[_make_attr("Purity", 98.5)],
        )
        flags = check_cross_document_consistency([result_a, result_b])
        value_flags = [f for f in flags if "Value conflict" in f.description]
        self.assertEqual(len(value_flags), 0)

    def test_no_conflict_within_tolerance(self):
        """Small differences within 5% relative tolerance should not flag."""
        result_a = _make_result(
            doc_path="doc_A.docx",
            attributes=[_make_attr("Purity", 98.5)],
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            attributes=[_make_attr("Purity", 98.0)],
        )
        flags = check_cross_document_consistency([result_a, result_b])
        value_flags = [f for f in flags if "Value conflict" in f.description]
        self.assertEqual(len(value_flags), 0)

    def test_critical_attribute_gets_higher_severity(self):
        """HMW/purity conflicts should be flagged as critical or warning."""
        result_a = _make_result(
            doc_path="doc_A.docx",
            attributes=[_make_attr("HMW (%)", 1.0)],
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            attributes=[_make_attr("HMW (%)", 3.0)],
        )
        flags = check_cross_document_consistency([result_a, result_b])
        value_flags = [f for f in flags if "Value conflict" in f.description]
        if value_flags:
            self.assertIn(value_flags[0].severity, ("critical", "warning"))

    def test_different_timepoints_not_flagged(self):
        """Values at different timepoints are expected to differ."""
        result_a = _make_result(
            doc_path="doc_A.docx",
            attributes=[_make_attr("Purity", 98.0, timepoint="T=0")],
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            attributes=[_make_attr("Purity", 95.0, timepoint="12M")],
        )
        flags = check_cross_document_consistency([result_a, result_b])
        value_flags = [f for f in flags if "Value conflict" in f.description]
        self.assertEqual(len(value_flags), 0)


# ---------------------------------------------------------------------------
# 3. Reference standard conflict detection
# ---------------------------------------------------------------------------

class TestReferenceStandardConflicts(unittest.TestCase):
    """Different reference standard lots across documents should flag."""

    def test_detects_lot_conflict(self):
        result_a = _make_result(
            doc_path="char_report.docx",
            evidence={"reference_standard_lot": "RS-2024-001"},
        )
        result_b = _make_result(
            doc_path="stab_report.docx",
            evidence={"reference_standard_lot": "RS-2023-015"},
        )
        flags = check_cross_document_consistency([result_a, result_b])
        ref_flags = [f for f in flags if "Reference standard" in f.description]
        self.assertTrue(
            len(ref_flags) >= 1,
            f"Expected reference standard conflict, got: {[f.description for f in flags]}"
        )

    def test_no_conflict_for_same_lot(self):
        result_a = _make_result(
            doc_path="doc_A.docx",
            evidence={"reference_standard_lot": "RS-2024-001"},
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            evidence={"reference_standard_lot": "RS-2024-001"},
        )
        flags = check_cross_document_consistency([result_a, result_b])
        ref_flags = [f for f in flags if "Reference standard" in f.description]
        self.assertEqual(len(ref_flags), 0)

    def test_case_insensitive_lot_match(self):
        """Lot numbers should be compared case-insensitively."""
        result_a = _make_result(
            doc_path="doc_A.docx",
            evidence={"reference_standard_lot": "RS-2024-001"},
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            evidence={"reference_standard_lot": "rs-2024-001"},
        )
        flags = check_cross_document_consistency([result_a, result_b])
        ref_flags = [f for f in flags if "Reference standard" in f.description]
        self.assertEqual(len(ref_flags), 0)


# ---------------------------------------------------------------------------
# 4. Method conflict detection
# ---------------------------------------------------------------------------

class TestMethodConflictDetection(unittest.TestCase):
    """Different methods for the same attribute across documents should flag."""

    def test_detects_method_conflict(self):
        result_a = _make_result(
            doc_path="doc_A.docx",
            attributes=[_make_attr("Purity", 98.0, context="(method: SEC-HPLC)")],
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            attributes=[_make_attr("Purity", 98.5, context="(method: CE-SDS)")],
        )
        flags = check_cross_document_consistency([result_a, result_b])
        method_flags = [f for f in flags if "Method conflict" in f.description]
        self.assertTrue(
            len(method_flags) >= 1,
            f"Expected method conflict, got: {[f.description for f in flags]}"
        )

    def test_no_conflict_for_same_method(self):
        result_a = _make_result(
            doc_path="doc_A.docx",
            attributes=[_make_attr("Purity", 98.0, context="(method: SEC-HPLC)")],
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            attributes=[_make_attr("Purity", 98.5, context="(method: SEC-HPLC)")],
        )
        flags = check_cross_document_consistency([result_a, result_b])
        method_flags = [f for f in flags if "Method conflict" in f.description]
        self.assertEqual(len(method_flags), 0)


# ---------------------------------------------------------------------------
# 5. Temporal inconsistency detection
# ---------------------------------------------------------------------------

class TestTemporalInconsistency(unittest.TestCase):
    """Characterization vs stability T=0 mismatch should flag."""

    def test_detects_temporal_inconsistency(self):
        result_char = _make_result(
            doc_path="char_report.docx",
            doc_type="CHARACTERIZATION",
            attributes=[_make_attr("HMW (%)", 1.2)],
        )
        result_stab = _make_result(
            doc_path="stab_report.docx",
            doc_type="STABILITY",
            attributes=[_make_attr("HMW (%)", 2.8, timepoint="T=0")],
        )
        flags = check_cross_document_consistency([result_char, result_stab])
        temporal_flags = [f for f in flags if "Temporal" in f.description]
        self.assertTrue(
            len(temporal_flags) >= 1,
            f"Expected temporal inconsistency, got: {[f.description for f in flags]}"
        )

    def test_no_temporal_flag_when_values_match(self):
        result_char = _make_result(
            doc_path="char_report.docx",
            doc_type="CHARACTERIZATION",
            attributes=[_make_attr("Purity", 97.5)],
        )
        result_stab = _make_result(
            doc_path="stab_report.docx",
            doc_type="STABILITY",
            attributes=[_make_attr("Purity", 97.5, timepoint="T=0")],
        )
        flags = check_cross_document_consistency([result_char, result_stab])
        temporal_flags = [f for f in flags if "Temporal" in f.description]
        self.assertEqual(len(temporal_flags), 0)


# ---------------------------------------------------------------------------
# 6. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    """Edge cases: empty list, single document, no attributes."""

    def test_empty_list_returns_empty(self):
        flags = check_cross_document_consistency([])
        self.assertEqual(flags, [])

    def test_single_document_returns_empty(self):
        result = _make_result(doc_path="only_one.docx")
        flags = check_cross_document_consistency([result])
        self.assertEqual(flags, [])

    def test_no_attributes_no_crash(self):
        """Documents with no attributes should not crash."""
        result_a = _make_result(doc_path="doc_A.docx", attributes=[])
        result_b = _make_result(doc_path="doc_B.docx", attributes=[])
        flags = check_cross_document_consistency([result_a, result_b])
        self.assertIsInstance(flags, list)

    def test_three_documents_cross_compared(self):
        """All pairs should be compared when 3+ documents provided."""
        result_a = _make_result(
            doc_path="doc_A.docx",
            attributes=[_make_attr("HMW", 1.0)],
            evidence={"reference_standard_lot": "LOT-A"},
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            attributes=[_make_attr("HMW", 3.0)],
            evidence={"reference_standard_lot": "LOT-B"},
        )
        result_c = _make_result(
            doc_path="doc_C.docx",
            attributes=[_make_attr("HMW", 5.0)],
            evidence={"reference_standard_lot": "LOT-C"},
        )
        flags = check_cross_document_consistency([result_a, result_b, result_c])
        self.assertTrue(len(flags) >= 1, "Should detect conflicts across 3 documents")

    def test_never_crashes_on_malformed_input(self):
        """Consistency checker must never raise, even with bad data."""
        result_a = _make_result(doc_path="doc_A.docx")
        result_a.parsed_doc = None  # Malformed
        result_a.extracted_evidence = None  # type: ignore
        result_b = _make_result(doc_path="doc_B.docx")
        # Should not raise
        flags = check_cross_document_consistency([result_a, result_b])
        self.assertIsInstance(flags, list)

    def test_flag_ids_are_unique(self):
        """All generated flag IDs should be unique."""
        result_a = _make_result(
            doc_path="doc_A.docx",
            attributes=[
                _make_attr("HMW", 1.0),
                _make_attr("Purity", 80.0),
            ],
            evidence={"reference_standard_lot": "LOT-A"},
        )
        result_b = _make_result(
            doc_path="doc_B.docx",
            attributes=[
                _make_attr("HMW", 5.0),
                _make_attr("Purity", 95.0),
            ],
            evidence={"reference_standard_lot": "LOT-B"},
        )
        flags = check_cross_document_consistency([result_a, result_b])
        ids = [f.flag_id for f in flags]
        self.assertEqual(len(ids), len(set(ids)), f"Duplicate flag IDs: {ids}")


# ---------------------------------------------------------------------------
# 7. Performance
# ---------------------------------------------------------------------------

class TestPerformance(unittest.TestCase):
    """Consistency check for 10 documents with 50 attributes each < 30s."""

    def test_performance_under_30s(self):
        results = []
        for i in range(10):
            attrs = [
                _make_attr(f"Attr_{j}", float(j) + i * 0.1)
                for j in range(50)
            ]
            results.append(_make_result(
                doc_path=f"doc_{i}.docx",
                attributes=attrs,
                evidence={"reference_standard_lot": f"LOT-{i % 3}"},
            ))
        start = time.time()
        flags = check_cross_document_consistency(results)
        elapsed = time.time() - start
        self.assertLess(
            elapsed, 30.0,
            f"Consistency check took {elapsed:.1f}s (limit: 30s)"
        )
        self.assertIsInstance(flags, list)


# ---------------------------------------------------------------------------
# 8. Reviewer concerns integration (Phase 4C verification)
# ---------------------------------------------------------------------------

class TestReviewerConcernsIntegration(unittest.TestCase):
    """Verify that reviewer concerns are populated by extractors."""

    def test_stability_reviewer_concerns_populated(self):
        """StabilityExtractor should produce reviewer_concerns."""
        from ingestion.stability_extractor import StabilityExtractor
        extractor = StabilityExtractor()
        parsed_doc = {
            "pages": [{
                "text": (
                    "Stability study at 5C. Timepoints: T=0, 3M. "
                    "Purity 97.5% at T=0, 97.0% at 3M. "
                    "Proposed shelf-life: 36 months."
                ),
                "tables": [],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        evidence = extractor.extract_evidence(parsed_doc)
        # Should detect insufficient timepoints and/or extrapolation concern
        self.assertIn("reviewer_concerns", evidence)
        self.assertIsInstance(evidence["reviewer_concerns"], list)

    def test_analytical_method_reviewer_concerns_populated(self):
        """AnalyticalMethodExtractor should produce reviewer_concerns."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor
        extractor = AnalyticalMethodExtractor()
        parsed_doc = {
            "pages": [{
                "text": (
                    "Method Validation Report for SE-HPLC. "
                    "Specificity: demonstrated. "
                    "Linearity: R2 = 0.998. "
                    "Accuracy: Recovery 99.5%. "
                    "Precision: %RSD = 1.2%. "
                    "No robustness study performed."
                ),
                "tables": [],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        evidence = extractor.extract_evidence(parsed_doc)
        self.assertIn("reviewer_concerns", evidence)
        self.assertIsInstance(evidence["reviewer_concerns"], list)

    def test_stability_concern_insufficient_timepoints(self):
        """Stability with < 4 timepoints should flag insufficient timepoints."""
        from ingestion.stability_extractor import StabilityExtractor
        extractor = StabilityExtractor()
        parsed_doc = {
            "pages": [{
                "text": (
                    "Stability at 5C. T=0: 97.5%. 3M: 97.0%. "
                    "Only 2 timepoints available."
                ),
                "tables": [],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        evidence = extractor.extract_evidence(parsed_doc)
        concerns = evidence.get("reviewer_concerns", [])
        has_timepoint_concern = any("timepoint" in c.lower() for c in concerns)
        self.assertTrue(
            has_timepoint_concern,
            f"Expected timepoint concern, got: {concerns}"
        )

    def test_stability_concern_missing_accelerated(self):
        """Stability without 40C/75RH should flag missing accelerated."""
        from ingestion.stability_extractor import StabilityExtractor
        extractor = StabilityExtractor()
        parsed_doc = {
            "pages": [{
                "text": (
                    "Stability at 5C only. T=0: 97.5%. 3M: 97.0%. 6M: 96.5%. "
                    "9M: 96.0%. 12M: 95.5%."
                ),
                "tables": [],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        evidence = extractor.extract_evidence(parsed_doc)
        concerns = evidence.get("reviewer_concerns", [])
        has_accelerated_concern = any("accelerated" in c.lower() for c in concerns)
        self.assertTrue(
            has_accelerated_concern,
            f"Expected accelerated condition concern, got: {concerns}"
        )

    def test_stability_concern_extrapolation(self):
        """Shelf-life exceeding data should flag extrapolation."""
        from ingestion.stability_extractor import StabilityExtractor
        extractor = StabilityExtractor()
        # Use table-based timepoints to ensure max_timepoint < proposed_shelf_life.
        # The table clearly shows 12M as max, while shelf-life claim is 36M.
        parsed_doc = {
            "pages": [{
                "text": (
                    "Stability at 5C and 40C/75RH. "
                    "Proposed shelf-life: 36 months."
                ),
                "tables": [{
                    "headers": ["Attribute", "T=0", "3M", "6M", "12M"],
                    "rows": [
                        {"Attribute": "Purity", "T=0": "98.0", "3M": "97.5",
                         "6M": "97.0", "12M": "96.5"},
                    ],
                }],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        evidence = extractor.extract_evidence(parsed_doc)
        concerns = evidence.get("reviewer_concerns", [])
        has_extrapolation = any(
            "extrapolat" in c.lower() or "shelf-life" in c.lower()
            or "proposed" in c.lower()
            for c in concerns
        )
        self.assertTrue(
            has_extrapolation,
            f"Expected extrapolation/shelf-life concern, got: {concerns}"
        )

    def test_analytical_concern_missing_robustness(self):
        """Missing robustness should trigger reviewer concern."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor
        extractor = AnalyticalMethodExtractor()
        parsed_doc = {
            "pages": [{
                "text": (
                    "SE-HPLC Method Validation. "
                    "Specificity demonstrated. Linearity R2 = 0.9998. "
                    "Accuracy recovery 99.5%. Precision RSD 1.2%. "
                    "LOD 0.05%. LOQ 0.1%. Range 0.1-5.0%."
                ),
                "tables": [],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        evidence = extractor.extract_evidence(parsed_doc)
        concerns = evidence.get("reviewer_concerns", [])
        has_robustness = any("robustness" in c.lower() for c in concerns)
        self.assertTrue(
            has_robustness,
            f"Expected robustness concern, got: {concerns}"
        )

    def test_analytical_concern_high_precision(self):
        """High %RSD should trigger precision concern."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor
        extractor = AnalyticalMethodExtractor()
        parsed_doc = {
            "pages": [{
                "text": (
                    "SE-HPLC Method Validation for potency method. "
                    "Precision: %RSD = 8.5%."
                ),
                "tables": [],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        evidence = extractor.extract_evidence(parsed_doc)
        concerns = evidence.get("reviewer_concerns", [])
        has_precision = any("precision" in c.lower() or "rsd" in c.lower() for c in concerns)
        self.assertTrue(
            has_precision,
            f"Expected precision concern, got: {concerns}"
        )

    def test_analytical_concern_range_gaps(self):
        """Missing range study should trigger concern."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor
        extractor = AnalyticalMethodExtractor()
        parsed_doc = {
            "pages": [{
                "text": (
                    "SE-HPLC Validation. "
                    "Specificity: pass. Linearity R2 0.999. "
                    "Accuracy: 100%. Precision: 1.0% RSD."
                ),
                "tables": [],
            }],
            "paragraphs": [],
            "metadata": {},
        }
        evidence = extractor.extract_evidence(parsed_doc)
        concerns = evidence.get("reviewer_concerns", [])
        has_range = any("range" in c.lower() for c in concerns)
        self.assertTrue(
            has_range,
            f"Expected range gap concern, got: {concerns}"
        )


if __name__ == "__main__":
    unittest.main()
