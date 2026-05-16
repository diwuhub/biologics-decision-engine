"""
Tests for the Regulatory Evidence Service (A-1).
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from services.regulatory_evidence import RegulatoryEvidenceService
from services.schemas import (
    EvidenceGrade,
    FindingClassification,
    PrecedentCard,
    ReadinessReport,
    ReviewerQuestion,
)

# Determine once whether reg-intel-biopharma is importable.
_svc = RegulatoryEvidenceService()
_HAS_REG_INTEL = _svc.reg_intel_available()


# =========================================================================
# 1. Evidence grading (always available)
# =========================================================================

def test_grade_evidence_returns_valid_grade():
    svc = RegulatoryEvidenceService()
    result = svc.grade_evidence(
        "Phase 3 randomized controlled trial with 500 patients "
        "demonstrated statistically significant improvement"
    )
    assert isinstance(result, EvidenceGrade)
    assert result.grade in ("strong", "moderate", "weak", "anecdotal")
    assert isinstance(result.probabilities, dict)
    assert len(result.probabilities) == 4
    # All probabilities should sum to ~1
    total = sum(result.probabilities.values())
    assert 0.99 <= total <= 1.01


def test_grade_evidence_weak_claim():
    svc = RegulatoryEvidenceService()
    result = svc.grade_evidence(
        "Single pilot study in 10 patients with no control arm"
    )
    assert isinstance(result, EvidenceGrade)
    assert result.grade in ("strong", "moderate", "weak", "anecdotal")


def test_grade_evidence_anecdotal_claim():
    svc = RegulatoryEvidenceService()
    result = svc.grade_evidence(
        "Company press release announcing positive results without data"
    )
    assert isinstance(result, EvidenceGrade)
    assert result.grade in ("strong", "moderate", "weak", "anecdotal")


# =========================================================================
# 2. Precedent search (requires reg-intel-biopharma)
# =========================================================================

@pytest.mark.skipif(not _HAS_REG_INTEL, reason="reg-intel-biopharma not available")
def test_search_precedent():
    svc = RegulatoryEvidenceService()
    results = svc.search_precedent("process_validation", top_k=3)
    assert isinstance(results, list)
    assert len(results) >= 1
    for card in results:
        assert isinstance(card, PrecedentCard)
        assert card.id
        assert card.title
        assert card.agency in ("FDA", "EMA")
        assert 0 <= card.relevance <= 1.0


@pytest.mark.skipif(not _HAS_REG_INTEL, reason="reg-intel-biopharma not available")
def test_search_precedent_no_results():
    svc = RegulatoryEvidenceService()
    results = svc.search_precedent("nonexistent_category_xyz", top_k=3)
    assert isinstance(results, list)
    assert len(results) == 0


# =========================================================================
# 3. Reviewer question prediction (requires reg-intel-biopharma)
# =========================================================================

@pytest.mark.skipif(not _HAS_REG_INTEL, reason="reg-intel-biopharma not available")
def test_predict_questions():
    svc = RegulatoryEvidenceService()
    profile = {
        "molecule_type": "mAb",
        "is_biosimilar": False,
        "clinical_phase": "BLA",
        "identified_gaps": ["stability_data_insufficient",
                            "analytical_validation_incomplete"],
    }
    questions = svc.predict_reviewer_questions(profile, top_k=5)
    assert isinstance(questions, list)
    assert len(questions) >= 1
    for q in questions:
        assert isinstance(q, ReviewerQuestion)
        assert q.id
        assert q.question
        assert 0 <= q.probability <= 1.0
        assert q.impact in ("critical", "high", "medium", "low")


# =========================================================================
# 4. Readiness assessment (requires reg-intel-biopharma)
# =========================================================================

@pytest.mark.skipif(not _HAS_REG_INTEL, reason="reg-intel-biopharma not available")
def test_assess_readiness():
    svc = RegulatoryEvidenceService()
    data = {
        "ds_characterization": {"completeness": 0.92, "gaps": []},
        "dp_manufacturing": {"completeness": 0.85, "gaps": ["P.8 incomplete"]},
        "analytical_validation": {"completeness": 0.78, "gaps": []},
        "stability_data": {"completeness": 0.65, "gaps": ["Only 12-month data"]},
        "process_validation": {"completeness": 0.90, "gaps": []},
        "spec_justification": {"completeness": 0.82, "gaps": []},
        "comparability": {"completeness": 0.50, "gaps": []},
        "regulatory_precedent": {"completeness": 0.75, "gaps": []},
    }
    report = svc.assess_readiness(data)
    assert isinstance(report, ReadinessReport)
    assert 0 <= report.composite <= 1.0
    assert report.n_areas == 8
    assert isinstance(report.area_scores, list)
    assert len(report.area_scores) == 8


# =========================================================================
# 5. Graceful fallback when reg-intel is missing
# =========================================================================

def test_service_works_without_reg_intel():
    """Evidence grading always works; reg-intel methods raise RuntimeError."""
    svc = RegulatoryEvidenceService()

    # Evidence grading always succeeds
    grade = svc.grade_evidence("Published meta-analysis confirmed the finding")
    assert isinstance(grade, EvidenceGrade)
    assert grade.grade in ("strong", "moderate", "weak", "anecdotal")

    # If reg-intel is NOT available, these should raise RuntimeError.
    # If it IS available, they should succeed -- either way the service
    # behaves correctly.
    if not svc.reg_intel_available():
        with pytest.raises(RuntimeError, match="reg-intel-biopharma"):
            svc.search_precedent("comparability")
        with pytest.raises(RuntimeError, match="reg-intel-biopharma"):
            svc.predict_reviewer_questions({"molecule_type": "mAb"})
        with pytest.raises(RuntimeError, match="reg-intel-biopharma"):
            svc.assess_readiness({"ds_characterization": {"completeness": 0.9}})
        with pytest.raises(RuntimeError, match="reg-intel-biopharma"):
            svc.classify_finding("failure to validate analytical methods")
