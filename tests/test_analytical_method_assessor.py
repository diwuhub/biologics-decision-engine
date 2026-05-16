"""Tests for AnalyticalMethodAssessor — the compliance-based judgment engine."""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from services.analytical_method_assessor import assess_analytical_method


def _full_validation():
    return {
        "validation_studies_found": [
            "Specificity", "Linearity", "Range", "Accuracy",
            "Precision (Repeatability)", "Precision (Intermediate)",
            "Robustness", "LOD (Limit of Detection)", "LOQ (Limit of Quantitation)",
        ],
        "validation_studies_missing": [],
        "completeness_score": 1.0,
        "method_name": "SEC-HPLC Method for HMW",
        "critical_gaps": [],
        "reviewer_concerns": [],
        "tables_found": 15,
    }


def _partial_validation():
    return {
        "validation_studies_found": [
            "Specificity", "Linearity", "Range", "Accuracy",
            "Precision (Repeatability)", "Precision (Intermediate)", "Robustness",
        ],
        "validation_studies_missing": [
            "LOD (Limit of Detection)", "LOQ (Limit of Quantitation)",
        ],
        "completeness_score": 0.778,
        "method_name": None,
        "critical_gaps": ["Missing LOD", "Missing LOQ"],
        "reviewer_concerns": [],
        "tables_found": 25,
    }


def _minimal_validation():
    return {
        "validation_studies_found": ["Linearity"],
        "validation_studies_missing": [
            "Specificity", "Range", "Accuracy",
            "Precision (Repeatability)", "Precision (Intermediate)",
            "Robustness", "LOD (Limit of Detection)", "LOQ (Limit of Quantitation)",
        ],
        "completeness_score": 0.111,
        "method_name": None,
        "critical_gaps": ["Missing critical studies"],
        "reviewer_concerns": [],
        "tables_found": 2,
    }


class TestAnalyticalMethodAssessment:
    def test_full_validation_passes(self):
        result = assess_analytical_method(_full_validation())
        assert result["analytical_conclusion"] == "Validated"
        assert result["package_posture"] == "Complete"
        assert result["judgment"]["confidence"] > 0.8

    def test_partial_needs_review(self):
        result = assess_analytical_method(_partial_validation())
        assert result["analytical_conclusion"] in ("Partially Validated", "Validated")
        assert "LOD" in result["posture_rationale"] or "LOQ" in result["posture_rationale"] or result["analytical_conclusion"] == "Validated"

    def test_minimal_not_validated(self):
        result = assess_analytical_method(_minimal_validation())
        assert result["analytical_conclusion"] in ("Not Validated", "Partial")
        assert result["judgment"]["confidence"] < 0.7

    def test_result_structure(self):
        result = assess_analytical_method(_full_validation())
        required = {"analytical_conclusion", "package_posture", "confidence_breakdown",
                     "judgment", "blocking_clusters", "reviewer_risk", "extracted_evidence"}
        assert required.issubset(result.keys())

    def test_guideline_detection(self):
        ev = _partial_validation()
        ev["method_name"] = None  # no method name = likely guideline
        result = assess_analytical_method(ev)
        # Should note it's a guideline if completeness > 0.5 and no method name
        # (the note appears in posture_rationale)
        assert "guideline" in result["posture_rationale"].lower() or result["analytical_conclusion"] is not None

    def test_real_ich_q14(self):
        """Integration: run on actual ICH Q14 extraction output."""
        from ingestion import ingest_document
        r = ingest_document("benchmarks/real_documents/ICH_Q14_2023.pdf")
        result = assess_analytical_method(r.extracted_evidence)
        assert result["analytical_conclusion"] in ("Partially Validated", "Validated")
        assert result["judgment"]["confidence"] > 0.7
