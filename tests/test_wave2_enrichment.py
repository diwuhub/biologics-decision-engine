"""
W2-6: Wave 2 Enrichment Tests — Four-Layer Validation.

Layer 1: Invariant Tests (S-2 method inadequate, S-4 cross-pair traceable)
Layer 2: Regression Tests (baseline + Wave 1 unchanged)
Layer 3: Enriched Behavior Tests (method LOQ, cross-pair, pathway weights)
Layer 4: Narrative Tests (method + pair references in posture_rationale)

Run: python3 -m pytest tests/test_wave2_enrichment.py -v --tb=short
"""

from __future__ import annotations

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

CASES_DIR = os.path.join(PROJECT_ROOT, "benchmarks", "cases")
GOLD_DIR = os.path.join(PROJECT_ROOT, "tests", "gold")


def _load_case(case_id: str) -> dict:
    fpath = os.path.join(CASES_DIR, f"{case_id}.json")
    with open(fpath) as f:
        return json.load(f)


def _run_comparability(case: dict):
    from pipelines.comparability import run_comparability_assessment
    return run_comparability_assessment(
        pre_change_data=case,
        product_name=case.get("product_name", "Test"),
        change_description=case.get("change_description", ""),
    )


# =========================================================================
# Layer 1: Invariant Tests
# =========================================================================

class TestLayer1Invariants:

    def test_s2_inadequate_method_minimum_minor(self):
        """S-2: method_adequate='inadequate' must produce concern >= 'minor'.
        Inadequate/marginal method cannot produce 'no concern'."""
        from modules.comparability_graph.engine import score_attribute, _CONCERN_ORDER

        attr = {
            "attribute_id": "intact_mass_test",
            "name": "Intact Mass (MW)",
            "category": "identity",
            "method_loq": 0.5,
            "measurements": [
                {"lot_id": "PRE", "value": 148.2, "unit": "kDa", "within_spec": True},
                {"lot_id": "POST", "value": 148.1, "unit": "kDa", "within_spec": True},
            ],
        }
        result = score_attribute(attr, [])
        # Delta = 0.1, which is < LOQ of 0.5 => method_adequate='inadequate'
        assert result.method_adequate == "inadequate", (
            f"Expected method_adequate='inadequate', got '{result.method_adequate}'"
        )
        assert _CONCERN_ORDER.get(result.concern, 0) >= _CONCERN_ORDER["minor"], (
            f"S-2: inadequate method must have concern >= minor, got '{result.concern}'"
        )

    def test_s2_marginal_method_minimum_minor(self):
        """S-2: method_adequate='marginal' must produce concern >= 'minor'."""
        from modules.comparability_graph.engine import score_attribute, _CONCERN_ORDER

        attr = {
            "attribute_id": "hmw_test",
            "name": "SEC HMW %",
            "category": "purity",
            "method_loq": 0.08,
            "measurements": [
                {"lot_id": "PRE", "value": 0.8, "unit": "%", "within_spec": True},
                {"lot_id": "POST", "value": 0.9, "unit": "%", "within_spec": True},
            ],
        }
        result = score_attribute(attr, [])
        # Delta = 0.1, LOQ = 0.08, 1.5*LOQ = 0.12 => 0.08 < 0.1 < 0.12 => marginal
        assert result.method_adequate == "marginal", (
            f"Expected method_adequate='marginal', got '{result.method_adequate}'"
        )
        assert _CONCERN_ORDER.get(result.concern, 0) >= _CONCERN_ORDER["minor"], (
            f"S-2: marginal method must have concern >= minor, got '{result.concern}'"
        )

    def test_s4_cross_attribute_traceable(self):
        """S-4: pair escalation must record pair_id in cluster evidence."""
        from schemas.case_context import CaseContext
        from services.cluster_builder import build_risk_clusters

        ctx = CaseContext(
            molecule_class="mAb",
            change_type="media_change",
            change_description="Test",
            lifecycle_stage="CMC",
            flagged_attribute_ids=[],
            flagged_categories=[],
            identified_gaps=[],
        )
        # Both glycosylation and potency shifted (concern >= minor)
        attrs = [
            {
                "attribute_id": "Afucosylation % (Glycosylation)",
                "category": "physicochemical",
                "concern_level": "major",
                "is_cqa": False,
                "score": 0.4,
            },
            {
                "attribute_id": "Potency (Cell-based)",
                "category": "potency",
                "concern_level": "major",
                "is_cqa": True,
                "score": 0.5,
            },
        ]
        clusters = build_risk_clusters(ctx, attrs)
        # At least one cluster should have a cross_pair tag
        pair_tags = []
        for c in clusters:
            for tag in c.likely_reviewer_concerns:
                if tag.startswith("[cross_pair:"):
                    pair_tags.append(tag)
        assert pair_tags, (
            "S-4: pair escalation must record pair_id in cluster "
            "likely_reviewer_concerns, but none found"
        )
        # Verify pair_id is in the tag
        assert any("GLYC_POTENCY" in t for t in pair_tags), (
            f"Expected GLYC_POTENCY pair_id in tags, got: {pair_tags}"
        )


# =========================================================================
# Layer 2: Regression Tests
# =========================================================================

class TestLayer2Regression:

    def test_baseline_unchanged(self):
        """All 20 original benchmarks must be unchanged after Wave 2."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "benchmarks/run_benchmarks.py"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
            timeout=120,
        )
        assert "Verdict Accuracy: 100.0%" in result.stdout, (
            f"Benchmark accuracy changed:\n{result.stdout[-500:]}"
        )

    def test_wave1_still_passes(self):
        """Wave 1 enriched cases must still produce correct results."""
        # COMP-001_specaware: spec-aware scoring
        case = _load_case("COMP-001_specaware")
        report = _run_comparability(case)
        assert report.overall_verdict == case["expected_verdict"], (
            f"COMP-001_specaware verdict changed: expected "
            f"'{case['expected_verdict']}', got '{report.overall_verdict}'"
        )
        # Check spec tolerance_source still works
        afuc = next(
            ar for ar in report.attribute_results
            if ar.name == "Afucosylation %"
        )
        sb = afuc.score_breakdown or {}
        assert sb.get("tolerance_source") == "specification", (
            f"W1 spec-aware scoring broken: Afucosylation % tolerance_source="
            f"'{sb.get('tolerance_source')}'"
        )

        # COMP-001_specaware_changetype: change-type annotation
        case_ct = _load_case("COMP-001_specaware_changetype")
        report_ct = _run_comparability(case_ct)
        ct_found = False
        for ar in report_ct.attribute_results:
            sb = ar.score_breakdown or {}
            if sb.get("change_type_expectation") is not None:
                ct_found = True
                break
        assert ct_found, "W1 change-type annotation broken"


# =========================================================================
# Layer 3: Enriched Behavior Tests
# =========================================================================

class TestLayer3EnrichedBehavior:

    def test_method_loq_gate(self):
        """COMP-001_method: attribute with delta < LOQ gets
        method_adequate='inadequate' in score_breakdown."""
        case = _load_case("COMP-001_method")
        report = _run_comparability(case)

        # Intact Mass (MW): pre=148.2, post=148.1, delta=0.1, LOQ=0.5
        intact = next(
            ar for ar in report.attribute_results
            if ar.name == "Intact Mass (MW)"
        )
        sb = intact.score_breakdown or {}
        assert sb.get("method_adequate") == "inadequate", (
            f"Intact Mass (MW) should be method_adequate='inadequate' "
            f"(delta=0.1 < LOQ=0.5), got '{sb.get('method_adequate')}'"
        )

    def test_method_adequate_for_large_delta(self):
        """Endotoxin in COMP-001_method: delta=0.01, LOQ=0.02 =>
        delta < LOQ => inadequate."""
        case = _load_case("COMP-001_method")
        report = _run_comparability(case)
        endo = next(
            ar for ar in report.attribute_results
            if ar.name == "Endotoxin"
        )
        sb = endo.score_breakdown or {}
        assert sb.get("method_adequate") == "inadequate", (
            f"Endotoxin should be method_adequate='inadequate' "
            f"(delta=0.01 < LOQ=0.02), got '{sb.get('method_adequate')}'"
        )

    def test_cross_pair_escalation(self):
        """COMP-001_full: both glycosylation and potency shifted =>
        linked escalation should be triggered."""
        case = _load_case("COMP-001_full")
        report = _run_comparability(case)

        # The full case has Afucosylation % (Glycosylation) shifted significantly
        # and Potency (Cell-based) shifted. Both should trigger GLYC_POTENCY pair.
        rationale = report.posture_rationale.lower()
        # Check that pair-based reasoning appears in narrative OR in clusters
        has_pair_narrative = "structure-function coupling" in rationale or \
                            "glyc_potency" in rationale or \
                            "cross-attribute pair" in rationale
        has_pair_in_cluster = False
        if report.blocking_clusters:
            for bc in report.blocking_clusters:
                if "cross_pair" in bc.get("reason", "").lower():
                    has_pair_in_cluster = True
        # At minimum, the pair escalation should appear somewhere
        assert has_pair_narrative or has_pair_in_cluster, (
            "COMP-001_full: expected linked pair escalation for glycosylation+potency"
        )

    def test_pathway_weights(self):
        """Biosimilar pathway should use different category weights."""
        from modules.comparability_graph.engine import (
            get_pathway_category_weights,
            CATEGORY_WEIGHTS,
        )
        pw = get_pathway_category_weights("biosimilar")
        assert pw is not None, "biosimilar pathway weights not found"
        assert pw != CATEGORY_WEIGHTS, (
            "biosimilar pathway weights should differ from default"
        )
        # Verify specific expected values
        assert pw.get("physicochemical", 0) > CATEGORY_WEIGHTS.get("physicochemical", 0), (
            "biosimilar pathway should emphasize physicochemical more"
        )

    def test_pathway_weights_applied_in_pipeline(self):
        """Case with lifecycle_stage='biosimilar' should use pathway weights."""
        case = _load_case("COMP-001_full")
        # Override lifecycle_stage to biosimilar for this test
        case["lifecycle_stage"] = "biosimilar"
        report = _run_comparability(case)
        rationale = report.posture_rationale.lower()
        assert "regulatory pathway" in rationale, (
            f"biosimilar pathway should mention pathway in rationale, got:\n"
            f"{report.posture_rationale}"
        )

    def test_method_unknown_when_no_loq(self):
        """Attributes without method_loq should have method_adequate='unknown'."""
        from modules.comparability_graph.engine import score_attribute

        attr = {
            "attribute_id": "potency_test",
            "name": "Potency",
            "category": "potency",
            "measurements": [
                {"lot_id": "PRE", "value": 100.0, "unit": "%", "within_spec": True},
                {"lot_id": "POST", "value": 95.0, "unit": "%", "within_spec": True},
            ],
        }
        result = score_attribute(attr, [])
        assert result.method_adequate == "unknown", (
            f"Expected method_adequate='unknown' without LOQ, got '{result.method_adequate}'"
        )


# =========================================================================
# Layer 4: Narrative Tests
# =========================================================================

class TestLayer4Narrative:

    def test_narrative_method_reference(self):
        """COMP-001_method: narrative must mention method adequacy when relevant."""
        case = _load_case("COMP-001_method")
        report = _run_comparability(case)
        rationale = report.posture_rationale.lower()
        assert "method" in rationale and ("inadequate" in rationale or "loq" in rationale), (
            f"posture_rationale should mention method inadequacy, got:\n"
            f"{report.posture_rationale}"
        )

    def test_narrative_pair_reference(self):
        """COMP-001_full: narrative must mention structure-function coupling
        when glycosylation and potency are both shifted."""
        case = _load_case("COMP-001_full")
        report = _run_comparability(case)
        rationale = report.posture_rationale.lower()
        has_pair_ref = (
            "structure-function coupling" in rationale or
            "glyc_potency" in rationale or
            "linked" in rationale or
            "cross-attribute pair" in rationale or
            "concurrent" in rationale
        )
        assert has_pair_ref, (
            f"posture_rationale should reference structure-function coupling "
            f"for glycosylation+potency, got:\n{report.posture_rationale}"
        )

    def test_narrative_still_references_spec(self):
        """COMP-001_specaware: narrative must still mention specification
        (regression from W1)."""
        case = _load_case("COMP-001_specaware")
        report = _run_comparability(case)
        assert "specification" in report.posture_rationale.lower(), (
            f"posture_rationale should mention 'specification', got:\n"
            f"{report.posture_rationale}"
        )

    def test_narrative_still_references_generic_fallback(self):
        """COMP-001_specaware: narrative must still mention generic tolerances
        (regression from W1)."""
        case = _load_case("COMP-001_specaware")
        report = _run_comparability(case)
        assert "generic tolerances" in report.posture_rationale.lower(), (
            f"posture_rationale should mention 'generic tolerances', got:\n"
            f"{report.posture_rationale}"
        )
        assert "attribute(s) relied on generic tolerances" in report.posture_rationale, (
            f"posture_rationale should mention count of generic tolerance "
            f"attributes, got:\n{report.posture_rationale}"
        )
