"""Tests for CharacterizationAssessor — the completeness-based judgment engine."""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from services.characterization_assessor import (
    assess_characterization,
    _map_evidence_to_attribute_results,
    _score_cqa_value,
)


# ---------------------------------------------------------------------------
# Helpers — build evidence dicts for testing
# ---------------------------------------------------------------------------

def _full_evidence():
    """Complete characterization evidence — all sections, all CQAs present."""
    return {
        "sections_found": [
            "Primary Structure", "Higher-Order Structure",
            "Aggregation / Size Variants", "Charge Heterogeneity",
            "Glycosylation / PTMs", "Biological Activity / Potency",
            "Immunochemical Properties", "Purity / Impurities",
        ],
        "sections_missing": [],
        "completeness_score": 1.0,
        "reference_standard_identified": True,
        "hmw": {"state": "present", "value": 1.5},
        "main_charge_peak": {"state": "present", "value": 72.0},
        "afucosylation": {"state": "present", "value": 8.5},
        "relative_potency": {"state": "present", "value": 102.0},
        "hmw_pct": 1.5,
        "main_charge_peak_pct": 72.0,
        "afucosylation_pct": 8.5,
        "potency_relative_pct": 102.0,
        "acidic_variants_pct": 16.0,
        "basic_variants_pct": 10.0,
        "critical_gaps": [],
        "extraction_uncertainties": [],
        "reviewer_concerns": [],
    }


def _partial_evidence():
    """Partial — some CQAs uncertain, potency missing."""
    ev = _full_evidence()
    ev["sections_found"] = [
        "Primary Structure", "Higher-Order Structure",
        "Aggregation / Size Variants", "Charge Heterogeneity",
        "Glycosylation / PTMs", "Purity / Impurities",
    ]
    ev["sections_missing"] = ["Biological Activity / Potency", "Immunochemical Properties"]
    ev["completeness_score"] = 0.75
    ev["relative_potency"] = {"state": "uncertain", "value": None}
    ev["potency_relative_pct"] = None
    ev["afucosylation"] = {"state": "uncertain", "value": None}
    ev["afucosylation_pct"] = None
    ev["extraction_uncertainties"] = ["Potency: uncertain", "Afucosylation: uncertain"]
    return ev


def _minimal_evidence():
    """Minimal — few sections, most CQAs absent."""
    return {
        "sections_found": ["Primary Structure", "Purity / Impurities"],
        "sections_missing": [
            "Higher-Order Structure", "Aggregation / Size Variants",
            "Charge Heterogeneity", "Glycosylation / PTMs",
            "Biological Activity / Potency", "Immunochemical Properties",
        ],
        "completeness_score": 0.25,
        "reference_standard_identified": False,
        "hmw": {"state": "confirmed_absent", "value": None},
        "main_charge_peak": {"state": "confirmed_absent", "value": None},
        "afucosylation": {"state": "confirmed_absent", "value": None},
        "relative_potency": {"state": "confirmed_absent", "value": None},
        "hmw_pct": None,
        "main_charge_peak_pct": None,
        "afucosylation_pct": None,
        "potency_relative_pct": None,
        "acidic_variants_pct": None,
        "basic_variants_pct": None,
        "critical_gaps": ["No reference standard"],
        "extraction_uncertainties": [],
        "reviewer_concerns": [],
    }


# ---------------------------------------------------------------------------
# Tests — _score_cqa_value
# ---------------------------------------------------------------------------

class TestScoreCQAValue:
    def test_present_within_spec(self):
        score, concern, gaps = _score_cqa_value("hmw_pct", "present", 1.5, True)
        assert score >= 0.9
        assert concern == "none"
        assert len(gaps) == 0

    def test_present_marginal(self):
        score, concern, gaps = _score_cqa_value("hmw_pct", "present", 7.0, True)
        assert 0.4 < score < 0.8
        assert concern in ("minor", "major")

    def test_present_out_of_spec(self):
        score, concern, gaps = _score_cqa_value("hmw_pct", "present", 15.0, True)
        assert score < 0.5
        assert concern in ("major", "critical")
        assert any("out_of_spec" in g for g in gaps)

    def test_uncertain_cqa(self):
        score, concern, gaps = _score_cqa_value("potency_relative_pct", "uncertain", None, True)
        assert 0.3 < score < 0.7
        assert concern == "minor"

    def test_confirmed_absent_cqa(self):
        score, concern, gaps = _score_cqa_value("potency_relative_pct", "confirmed_absent", None, True)
        assert score < 0.2
        assert concern == "critical"

    def test_confirmed_absent_non_cqa(self):
        score, concern, gaps = _score_cqa_value("basic_variants_pct", "confirmed_absent", None, False)
        assert concern == "major"  # less severe than CQA

    def test_no_spec_present(self):
        """Field with no defined spec — present is good."""
        score, concern, gaps = _score_cqa_value("afucosylation_pct", "present", 8.0, False)
        assert score >= 0.8
        assert concern == "none"


# ---------------------------------------------------------------------------
# Tests — attribute mapping
# ---------------------------------------------------------------------------

class TestAttributeMapping:
    def test_full_evidence_produces_attributes(self):
        attrs = _map_evidence_to_attribute_results(_full_evidence(), "mAb")
        assert len(attrs) > 0
        ids = [a["attribute_id"] for a in attrs]
        assert "char_hmw_pct" in ids
        assert "char_reference_standard" in ids

    def test_all_attributes_have_required_keys(self):
        attrs = _map_evidence_to_attribute_results(_full_evidence(), "mAb")
        required = {"attribute_id", "category", "score", "concern_level", "is_cqa", "gaps"}
        for attr in attrs:
            assert required.issubset(attr.keys()), f"Missing keys in {attr['attribute_id']}"

    def test_hmw_present_scores_high(self):
        attrs = _map_evidence_to_attribute_results(_full_evidence(), "mAb")
        hmw = [a for a in attrs if a["attribute_id"] == "char_hmw_pct"][0]
        assert hmw["score"] >= 0.9
        assert hmw["concern_level"] == "none"
        assert hmw["is_cqa"] is True

    def test_missing_ref_standard(self):
        ev = _full_evidence()
        ev["reference_standard_identified"] = False
        attrs = _map_evidence_to_attribute_results(ev, "mAb")
        ref = [a for a in attrs if a["attribute_id"] == "char_reference_standard"][0]
        assert ref["score"] == 0.0
        assert ref["concern_level"] == "major"


# ---------------------------------------------------------------------------
# Tests — full assessment
# ---------------------------------------------------------------------------

class TestFullAssessment:
    def test_full_evidence_adequate(self):
        result = assess_characterization(_full_evidence())
        assert result["analytical_conclusion"] == "Adequate"
        assert result["package_posture"] in ("Ready", "Needs Data")
        assert result["judgment"]["confidence"] > 0.7

    def test_partial_evidence_needs_data(self):
        result = assess_characterization(_partial_evidence())
        assert result["analytical_conclusion"] in ("Adequate", "Gaps Identified")
        assert result["judgment"]["confidence"] > 0.5

    def test_minimal_evidence_not_ready(self):
        result = assess_characterization(_minimal_evidence())
        assert result["analytical_conclusion"] in ("Gaps Identified", "Insufficient")
        assert result["package_posture"] in ("Not Ready", "Needs Data")

    def test_result_has_required_keys(self):
        result = assess_characterization(_full_evidence())
        required = {
            "analytical_conclusion", "package_posture", "posture_rationale",
            "confidence_breakdown", "judgment", "blocking_clusters",
            "reviewer_risk", "critical_attributes", "extracted_evidence",
        }
        assert required.issubset(result.keys())

    def test_confidence_breakdown_structure(self):
        result = assess_characterization(_full_evidence())
        cb = result["confidence_breakdown"]
        assert "analytical_confidence" in cb
        assert "package_readiness" in cb
        assert "evidence_completeness" in cb
        assert all(0.0 <= v <= 1.0 for v in cb.values())

    def test_reviewer_questions_populated(self):
        ev = _partial_evidence()
        ev["reviewer_concerns"] = ["Potency data not extracted"]
        result = assess_characterization(ev)
        questions = result["reviewer_risk"]["predicted_questions"]
        assert len(questions) > 0
        assert "question" in questions[0]

    def test_real_nistmab(self):
        """Integration: run on actual NISTmAb extraction output."""
        from ingestion import ingest_document
        r = ingest_document("benchmarks/real_documents/NISTmAb_SP260-237.pdf")
        result = assess_characterization(r.extracted_evidence)
        assert result["analytical_conclusion"] == "Adequate"
        assert result["judgment"]["confidence"] > 0.7
        assert "extracted_evidence" in result
