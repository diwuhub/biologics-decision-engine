"""Tests for StabilityAssessor — the adequacy-based judgment engine."""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from services.stability_assessor import assess_stability, _classify_conditions


class TestClassifyConditions:
    def test_full_coverage(self):
        result = _classify_conditions(["5C", "25C/60RH", "40C/75RH"])
        assert result["has_long_term"]
        assert result["has_accelerated"]
        assert result["has_stress"]

    def test_missing_stress(self):
        result = _classify_conditions(["5C", "25C/60RH"])
        assert result["has_long_term"]
        assert not result["has_stress"]

    def test_empty(self):
        result = _classify_conditions([])
        assert not result["has_long_term"]
        assert not result["has_accelerated"]
        assert not result["has_stress"]


def _good_stability():
    return {
        "conditions_tested": ["5C", "25C/60RH", "30C/65RH", "40C/75RH"],
        "max_timepoint_months": 60,
        "proposed_shelf_life": 24,
        "sufficiency_for_claim": "sufficient",
        "oos_events": [],
        "trend_concerns": [],
        "critical_gaps": [],
        "reviewer_concerns": [],
        "tables_found": 50,
    }


def _weak_stability():
    return {
        "conditions_tested": ["25C/60RH"],
        "max_timepoint_months": 6,
        "proposed_shelf_life": 24,
        "sufficiency_for_claim": "insufficient",
        "oos_events": [{"flag": "OOS", "context": "test", "source": "text"}],
        "trend_concerns": [{"attribute": "HMW", "direction": "increasing"}],
        "critical_gaps": ["Insufficient timepoints"],
        "reviewer_concerns": ["Short-term data only"],
        "tables_found": 5,
    }


class TestStabilityAssessment:
    def test_good_stability_supports_claim(self):
        result = assess_stability(_good_stability())
        assert result["analytical_conclusion"] == "Supports Claim"
        assert result["package_posture"] == "Sufficient"
        assert result["judgment"]["confidence"] > 0.8

    def test_weak_stability_not_sufficient(self):
        result = assess_stability(_weak_stability())
        assert result["analytical_conclusion"] != "Supports Claim"
        assert result["judgment"]["confidence"] < 0.8

    def test_result_structure(self):
        result = assess_stability(_good_stability())
        required = {"analytical_conclusion", "package_posture", "confidence_breakdown",
                     "judgment", "blocking_clusters", "reviewer_risk", "extracted_evidence"}
        assert required.issubset(result.keys())

    def test_real_xbonzy(self):
        """Integration: run on actual Xbonzy extraction output."""
        from ingestion import ingest_document
        r = ingest_document("benchmarks/real_documents/Xbonzy_EPAR.pdf")
        result = assess_stability(r.extracted_evidence)
        assert result["analytical_conclusion"] == "Supports Claim"
        assert result["judgment"]["confidence"] > 0.9
