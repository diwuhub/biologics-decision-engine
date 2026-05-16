"""
Tests for the Comparability Assessment Pipeline (S-3 MVP).
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from pipelines.comparability import run_comparability_assessment
from pipelines.schemas import ComparabilityReport, AttributeResult


DEMO_PATH = os.path.join(PROJECT_ROOT, "benchmarks", "mab_process_change_case.json")


def _load_demo_case():
    with open(DEMO_PATH) as f:
        return json.load(f)


# =========================================================================
# Test 1: Demo case produces a valid report
# =========================================================================

def test_demo_case_produces_report():
    case = _load_demo_case()
    report = run_comparability_assessment(
        pre_change_data=case,
        product_name=case["product_name"],
        change_description=case["change_description"],
    )
    assert isinstance(report, ComparabilityReport)
    assert report.n_attributes == len(case["attributes"])
    assert report.overall_verdict in ("Comparable", "Comparable With Caveats", "Not Comparable", "Insufficient Evidence")
    assert 0 <= report.evidence_strength_index <= 1
    assert len(report.attribute_results) == report.n_attributes
    assert report.product_name == case["product_name"]
    assert report.timestamp  # non-empty


# =========================================================================
# Test 2: All-comparable attributes give "Comparable" verdict
# =========================================================================

def test_all_comparable_gives_comparable_verdict():
    data = {
        "attributes": [
            {"name": "SEC Monomer", "category": "purity", "pre_value": 98.5,
             "post_value": 98.4, "unit": "%", "n_lots": 5, "cv_pct": 1.0,
             "n_methods": 2, "prior_approvals": 3},
            {"name": "Potency", "category": "potency", "pre_value": 100.0,
             "post_value": 99.0, "unit": "%", "n_lots": 5, "cv_pct": 5.0,
             "n_methods": 1, "has_functional_correlation": True, "prior_approvals": 3},
            {"name": "Identity", "category": "identity", "pre_value": 100.0,
             "post_value": 100.0, "unit": "%", "n_lots": 5, "cv_pct": 0.0,
             "n_methods": 2, "prior_approvals": 3},
            {"name": "Endotoxin", "category": "safety", "pre_value": 0.05,
             "post_value": 0.05, "unit": "EU/mL", "n_lots": 5, "cv_pct": 5.0,
             "n_methods": 1, "prior_approvals": 3},
            {"name": "Tm1", "category": "stability", "pre_value": 71.0,
             "post_value": 71.0, "unit": "°C", "n_lots": 5, "cv_pct": 0.5,
             "n_methods": 1, "prior_approvals": 2},
        ]
    }
    report = run_comparability_assessment(data, product_name="TestProduct")
    assert report.overall_verdict == "Comparable"
    assert report.n_flagged == 0
    assert report.evidence_strength_index > 0.5


# =========================================================================
# Test 3: Large delta flags attribute and may cause Not Comparable
# =========================================================================

def test_large_delta_flags_attribute():
    data = {
        "attributes": [
            {"name": "SEC Monomer", "category": "purity", "pre_value": 98.5,
             "post_value": 85.0, "unit": "%", "n_lots": 5, "cv_pct": 1.0},
            {"name": "Potency", "category": "potency", "pre_value": 100.0,
             "post_value": 99.0, "unit": "%", "n_lots": 5, "cv_pct": 5.0},
        ]
    }
    report = run_comparability_assessment(data)
    # SEC Monomer has ~13.7% delta vs 5% tolerance => critical concern
    sec_result = [ar for ar in report.attribute_results if ar.name == "SEC Monomer"][0]
    assert sec_result.concern in ("major", "critical")
    assert not sec_result.comparable
    assert report.n_flagged >= 1
    assert report.overall_verdict == "Not Comparable"


# =========================================================================
# Test 4: Missing data gives Insufficient Evidence
# =========================================================================

def test_missing_data_gives_insufficient_evidence():
    data = {"attributes": []}
    report = run_comparability_assessment(data)
    assert report.overall_verdict == "Insufficient Evidence"
    assert report.n_attributes == 0
    assert report.evidence_strength_index == 0.0


# =========================================================================
# Test 5: Report schema is complete
# =========================================================================

def test_report_schema_complete():
    case = _load_demo_case()
    report = run_comparability_assessment(
        pre_change_data=case,
        product_name=case["product_name"],
        change_description=case["change_description"],
    )

    # All top-level fields present
    report_dict = report.to_dict()
    required_keys = [
        "product_name", "change_description", "overall_verdict", "evidence_strength_index",
        "n_attributes", "n_cqa", "n_comparable", "n_flagged",
        "attribute_results", "cqa_summary", "uncertainty_summary",
        "evidence_gaps", "recommended_actions", "timestamp",
    ]
    for key in required_keys:
        assert key in report_dict, f"Missing key: {key}"

    # Check attribute result fields
    if report.attribute_results:
        ar = report.attribute_results[0]
        assert hasattr(ar, "name")
        assert hasattr(ar, "category")
        assert hasattr(ar, "pre_value")
        assert hasattr(ar, "post_value")
        assert hasattr(ar, "delta_pct")
        assert hasattr(ar, "score")
        assert hasattr(ar, "comparable")
        assert hasattr(ar, "concern")
        assert hasattr(ar, "is_cqa")
        assert hasattr(ar, "cqa_designation")
        assert hasattr(ar, "uncertainty")

    # Check uncertainty summary
    assert "mean_uncertainty" in report.uncertainty_summary
    assert "max_uncertainty" in report.uncertainty_summary

    # CQA summary is a list of dicts
    assert isinstance(report.cqa_summary, list)
    if report.cqa_summary:
        assert "designation" in report.cqa_summary[0]

    # Evidence gaps and recommended actions are lists
    assert isinstance(report.evidence_gaps, list)
    assert isinstance(report.recommended_actions, list)
