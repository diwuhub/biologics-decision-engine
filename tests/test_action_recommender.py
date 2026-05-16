"""
Tests for the Action Recommender (S-4 Action Layer).

Validates the 5-level action taxonomy and its integration into the
comparability pipeline.
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from modules.action_recommender.engine import (
    recommend_attribute_action,
    recommend_overall_actions,
    ActionRecommendation,
    OverallActionSummary,
    ACTION_LEVELS,
    _classify_action,
)
from pipelines.comparability import run_comparability_assessment
from pipelines.schemas import ComparabilityReport

DEMO_PATH = os.path.join(PROJECT_ROOT, "benchmarks", "mab_process_change_case.json")


def _load_demo_case():
    with open(DEMO_PATH) as f:
        return json.load(f)


# =========================================================================
# Test 1: PROCEED for good attribute
# =========================================================================

def test_proceed_for_good_attribute():
    """High score, low uncertainty, no concern -> PROCEED."""
    action = recommend_attribute_action(
        score=0.95,
        uncertainty=0.10,
        concern="none",
        attribute_name="SEC Monomer %",
        category="purity",
    )
    assert action.action_level == "PROCEED"
    assert "SEC Monomer %" in action.rationale
    assert action.regulatory_reference  # non-empty
    assert action.estimated_effort  # non-empty


# =========================================================================
# Test 2: DEFER for critical concern
# =========================================================================

def test_defer_for_critical_concern():
    """Critical concern -> DEFER regardless of score."""
    action = recommend_attribute_action(
        score=0.80,
        uncertainty=0.20,
        concern="critical",
        attribute_name="SEC HMW %",
        category="purity",
    )
    assert action.action_level == "DEFER"
    assert "critical" in action.rationale.lower()


def test_defer_for_very_low_score():
    """Score < 0.50 -> DEFER."""
    action = recommend_attribute_action(
        score=0.35,
        uncertainty=0.30,
        concern="major",
        attribute_name="Deamidation %",
        category="stability",
    )
    assert action.action_level == "DEFER"


def test_defer_for_high_uncertainty():
    """Uncertainty > 0.7 -> DEFER."""
    action = recommend_attribute_action(
        score=0.80,
        uncertainty=0.75,
        concern="none",
        attribute_name="HCP",
        category="safety",
    )
    assert action.action_level == "DEFER"


# =========================================================================
# Test 3: INVESTIGATE for major concern
# =========================================================================

def test_investigate_for_major_concern():
    """Major concern -> INVESTIGATE."""
    action = recommend_attribute_action(
        score=0.75,
        uncertainty=0.30,
        concern="major",
        attribute_name="Charge Variants",
        category="physicochemical",
    )
    assert action.action_level == "INVESTIGATE"
    assert "major" in action.rationale.lower()


def test_investigate_for_low_score():
    """Score < 0.70 -> INVESTIGATE (when not < 0.50)."""
    action = recommend_attribute_action(
        score=0.60,
        uncertainty=0.25,
        concern="none",
        attribute_name="Host Cell Protein",
        category="safety",
    )
    assert action.action_level == "INVESTIGATE"


def test_investigate_for_elevated_uncertainty():
    """Uncertainty > 0.5 but <= 0.7 -> INVESTIGATE."""
    action = recommend_attribute_action(
        score=0.80,
        uncertainty=0.55,
        concern="none",
        attribute_name="Potency",
        category="potency",
    )
    assert action.action_level == "INVESTIGATE"


# =========================================================================
# Test 4: MONITOR for trending concern
# =========================================================================

def test_monitor_for_trending_uncertainty():
    """Score >= 0.85, uncertainty > 0.4 but <= 0.5 -> MONITOR."""
    action = recommend_attribute_action(
        score=0.90,
        uncertainty=0.45,
        concern="none",
        attribute_name="Tm1 (Thermal Stability)",
        category="stability",
    )
    assert action.action_level == "MONITOR"
    assert "trending" in action.rationale.lower() or "monitoring" in action.rationale.lower()


# =========================================================================
# Test 5: SUPPLEMENT for minor gap
# =========================================================================

def test_supplement_for_minor_gap():
    """Score 0.70-0.85 with moderate uncertainty -> SUPPLEMENT."""
    action = recommend_attribute_action(
        score=0.80,
        uncertainty=0.25,
        concern="none",
        attribute_name="CE-SDS Purity",
        category="purity",
    )
    assert action.action_level == "SUPPLEMENT"


def test_supplement_for_minor_concern():
    """Minor concern with good score -> SUPPLEMENT."""
    action = recommend_attribute_action(
        score=0.88,
        uncertainty=0.10,
        concern="minor",
        attribute_name="Charge Variants",
        category="physicochemical",
    )
    assert action.action_level == "SUPPLEMENT"


# =========================================================================
# Test 6: Overall action summary
# =========================================================================

def test_overall_action_summary():
    """Overall summary reflects SP v5 5-rule aggregation."""
    actions = [
        recommend_attribute_action(0.95, 0.10, "none", "Attr A", "identity"),
        recommend_attribute_action(0.80, 0.25, "none", "Attr B", "purity"),
        recommend_attribute_action(0.60, 0.30, "major", "Attr C", "potency"),
    ]
    summary = recommend_overall_actions(actions)

    # SP v5 Rule 2: Single non-CQA INVESTIGATE → overall SUPPLEMENT
    assert summary.overall_action == "SUPPLEMENT"
    assert summary.n_proceed == 1
    assert summary.n_supplement == 1
    assert summary.n_investigate == 1
    assert "Attr C" in summary.critical_attributes
    assert len(summary.next_steps) > 0
    assert summary.regulatory_risk in ("low", "medium", "high")
    assert summary.estimated_timeline  # non-empty


def test_overall_action_summary_all_proceed():
    """When all attributes pass, overall should be PROCEED."""
    actions = [
        recommend_attribute_action(0.95, 0.10, "none", "Attr A", "identity"),
        recommend_attribute_action(0.92, 0.15, "none", "Attr B", "purity"),
    ]
    summary = recommend_overall_actions(actions)
    assert summary.overall_action == "PROCEED"
    assert summary.n_proceed == 2
    assert summary.regulatory_risk == "low"
    assert len(summary.critical_attributes) == 0


def test_overall_action_summary_with_defer():
    """DEFER in any attribute forces overall DEFER."""
    actions = [
        recommend_attribute_action(0.95, 0.10, "none", "Good Attr", "identity"),
        recommend_attribute_action(0.30, 0.20, "critical", "Bad Attr", "purity"),
    ]
    summary = recommend_overall_actions(actions)
    assert summary.overall_action == "DEFER"
    assert summary.n_defer == 1
    assert "Bad Attr" in summary.critical_attributes


# =========================================================================
# Test 7: Action includes next-best-evidence
# =========================================================================

def test_action_includes_next_evidence():
    """Every action must have a non-empty next_best_evidence field."""
    for level_trigger in [
        (0.95, 0.10, "none"),      # PROCEED
        (0.80, 0.35, "none"),      # SUPPLEMENT
        (0.90, 0.45, "none"),      # MONITOR
        (0.55, 0.30, "major"),     # INVESTIGATE
        (0.30, 0.80, "critical"),  # DEFER
    ]:
        score, unc, concern = level_trigger
        action = recommend_attribute_action(
            score=score, uncertainty=unc, concern=concern,
            attribute_name="TestAttr", category="purity",
        )
        assert action.next_best_evidence, (
            f"next_best_evidence missing for {action.action_level}"
        )
        assert len(action.next_best_evidence) > 10, (
            f"next_best_evidence too short for {action.action_level}"
        )


def test_action_includes_regulatory_reference():
    """Every action should reference regulatory guidance."""
    for category in ["identity", "purity", "potency", "safety", "stability", "physicochemical"]:
        action = recommend_attribute_action(
            score=0.95, uncertainty=0.10, concern="none",
            attribute_name="Test", category=category,
        )
        assert "ICH" in action.regulatory_reference or "FDA" in action.regulatory_reference


def test_action_includes_effort_estimate():
    """Every action should have an effort estimate."""
    action = recommend_attribute_action(
        score=0.55, uncertainty=0.30, concern="major",
        attribute_name="Attr", category="purity",
    )
    assert action.estimated_effort
    assert "week" in action.estimated_effort.lower() or "month" in action.estimated_effort.lower()


# =========================================================================
# Test 8: CQA escalation
# =========================================================================

def test_cqa_escalation():
    """CQA with minor concern and SUPPLEMENT should escalate to INVESTIGATE."""
    action = recommend_attribute_action(
        score=0.88,
        uncertainty=0.10,
        concern="minor",
        attribute_name="Potency CQA",
        category="potency",
        is_cqa=True,
    )
    assert action.action_level == "INVESTIGATE"


# =========================================================================
# Test 9: Integration with comparability pipeline
# =========================================================================

def test_demo_case_has_actions():
    """Demo case should have per-attribute actions and overall summary."""
    case = _load_demo_case()
    report = run_comparability_assessment(
        pre_change_data=case,
        product_name=case["product_name"],
        change_description=case["change_description"],
    )

    # Every attribute should have an action
    for ar in report.attribute_results:
        assert ar.action is not None, f"{ar.name} missing action"
        assert ar.action["action_level"] in ACTION_LEVELS
        assert ar.action["rationale"]
        assert ar.action["next_best_evidence"]
        assert ar.action["estimated_effort"]
        assert ar.action["regulatory_reference"]

    # Overall action summary should be present
    assert report.action_summary is not None
    assert report.action_summary["overall_action"] in ACTION_LEVELS
    assert len(report.action_summary["next_steps"]) > 0


def test_demo_case_critical_attributes_get_defer_or_investigate():
    """Afucosylation (critical concern) should get DEFER or INVESTIGATE.

    SEC HMW (0.8->0.9) has only 0.1pp absolute change — with hybrid scoring
    for low-value measurements, this correctly evaluates as a minor shift
    (SUPPLEMENT) rather than catastrophic (DEFER).
    """
    case = _load_demo_case()
    report = run_comparability_assessment(
        pre_change_data=case,
        product_name=case["product_name"],
        change_description=case["change_description"],
    )

    sec_hmw = [ar for ar in report.attribute_results if "SEC HMW" in ar.name][0]
    assert sec_hmw.action["action_level"] in ("SUPPLEMENT", "MONITOR", "PROCEED"), (
        f"SEC HMW (0.8->0.9, 0.1pp delta) got {sec_hmw.action['action_level']}, "
        f"expected SUPPLEMENT/MONITOR/PROCEED for low-value measurement with tiny absolute change"
    )

    afuc = [ar for ar in report.attribute_results if "Afucosylation" in ar.name][0]
    assert afuc.action["action_level"] in ("DEFER", "INVESTIGATE"), (
        f"Afucosylation got {afuc.action['action_level']}, expected DEFER or INVESTIGATE"
    )


def test_demo_case_deamidation_gets_investigate_or_defer():
    """Deamidation (major concern) should get INVESTIGATE or DEFER."""
    case = _load_demo_case()
    report = run_comparability_assessment(
        pre_change_data=case,
        product_name=case["product_name"],
        change_description=case["change_description"],
    )

    deam = [ar for ar in report.attribute_results if "Deamidation" in ar.name][0]
    assert deam.action["action_level"] in ("INVESTIGATE", "DEFER"), (
        f"Deamidation got {deam.action['action_level']}, expected INVESTIGATE or DEFER"
    )


def test_action_to_dict():
    """ActionRecommendation.to_dict() returns serializable dict."""
    action = recommend_attribute_action(
        score=0.90, uncertainty=0.10, concern="none",
        attribute_name="Test", category="purity",
    )
    d = action.to_dict()
    assert isinstance(d, dict)
    assert "action_level" in d
    assert "rationale" in d
    assert "next_best_evidence" in d
    # Should be JSON-serializable
    json.dumps(d)


def test_overall_summary_to_dict():
    """OverallActionSummary.to_dict() returns serializable dict."""
    actions = [
        recommend_attribute_action(0.95, 0.10, "none", "A", "purity"),
    ]
    summary = recommend_overall_actions(actions)
    d = summary.to_dict()
    assert isinstance(d, dict)
    json.dumps(d)


# =========================================================================
# Boundary tests for action recommender thresholds
# =========================================================================

def test_boundary_supplement_vs_monitor():
    """At score=0.71: uncertainty=0.39 -> SUPPLEMENT; uncertainty=0.41 -> MONITOR."""
    action_supplement = recommend_attribute_action(
        score=0.71, uncertainty=0.39, concern="none",
        attribute_name="BoundaryAttr", category="purity",
    )
    assert action_supplement.action_level == "SUPPLEMENT", (
        f"Expected SUPPLEMENT at uncertainty=0.39, got {action_supplement.action_level}"
    )

    action_monitor = recommend_attribute_action(
        score=0.71, uncertainty=0.41, concern="none",
        attribute_name="BoundaryAttr", category="purity",
    )
    assert action_monitor.action_level == "MONITOR", (
        f"Expected MONITOR at uncertainty=0.41, got {action_monitor.action_level}"
    )


def test_boundary_proceed_vs_supplement():
    """score=0.84 -> SUPPLEMENT; score=0.86 -> PROCEED (both low uncertainty, no concern)."""
    action_supplement = recommend_attribute_action(
        score=0.84, uncertainty=0.10, concern="none",
        attribute_name="BoundaryAttr", category="purity",
    )
    assert action_supplement.action_level == "SUPPLEMENT", (
        f"Expected SUPPLEMENT at score=0.84, got {action_supplement.action_level}"
    )

    action_proceed = recommend_attribute_action(
        score=0.86, uncertainty=0.10, concern="none",
        attribute_name="BoundaryAttr", category="purity",
    )
    assert action_proceed.action_level == "PROCEED", (
        f"Expected PROCEED at score=0.86, got {action_proceed.action_level}"
    )


def test_boundary_investigate_vs_defer():
    """score=0.49 -> DEFER; score=0.51 -> INVESTIGATE (no critical concern, moderate uncertainty)."""
    action_defer = recommend_attribute_action(
        score=0.49, uncertainty=0.30, concern="none",
        attribute_name="BoundaryAttr", category="purity",
    )
    assert action_defer.action_level == "DEFER", (
        f"Expected DEFER at score=0.49, got {action_defer.action_level}"
    )

    action_investigate = recommend_attribute_action(
        score=0.51, uncertainty=0.30, concern="none",
        attribute_name="BoundaryAttr", category="purity",
    )
    assert action_investigate.action_level == "INVESTIGATE", (
        f"Expected INVESTIGATE at score=0.51, got {action_investigate.action_level}"
    )


def test_all_levels_reachable():
    """Verify each of the 5 action levels can be triggered with specific inputs."""
    test_cases = {
        "PROCEED":     (0.95, 0.10, "none"),
        "SUPPLEMENT":  (0.80, 0.25, "none"),
        "MONITOR":     (0.90, 0.45, "none"),
        "INVESTIGATE": (0.60, 0.25, "none"),
        "DEFER":       (0.30, 0.20, "critical"),
    }
    for expected_level, (score, unc, concern) in test_cases.items():
        action = recommend_attribute_action(
            score=score, uncertainty=unc, concern=concern,
            attribute_name="ReachabilityAttr", category="purity",
        )
        assert action.action_level == expected_level, (
            f"Expected {expected_level} for (score={score}, unc={unc}, concern={concern}), "
            f"got {action.action_level}"
        )


def test_critical_concern_always_defer():
    """Any concern='critical' should produce DEFER regardless of score."""
    for score in [0.10, 0.30, 0.50, 0.70, 0.85, 0.95, 1.00]:
        action = recommend_attribute_action(
            score=score, uncertainty=0.10, concern="critical",
            attribute_name="CriticalAttr", category="purity",
        )
        assert action.action_level == "DEFER", (
            f"Expected DEFER for critical concern at score={score}, "
            f"got {action.action_level}"
        )
