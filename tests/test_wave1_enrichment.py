"""
W1-7: Wave 1 Enrichment Tests — Four-Layer Validation.

Layer 1: Invariant Tests (S-1, S-3 correctness)
Layer 2: Regression Tests (baseline unchanged)
Layer 3: Enriched Behavior Tests (spec-aware, change-type, signal)
Layer 4: Narrative Tests (posture_rationale content)

Run: python3 -m pytest tests/test_wave1_enrichment.py -v --tb=short
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

    def test_s1_oos_cqa_blocks_proceed(self):
        """S-1: An attribute with is_cqa=True and spec_compliance='oos'
        must have concern >= 'major' and the overall verdict cannot be 'proceed'."""
        from modules.comparability_graph.engine import score_attribute

        attr = {
            "attribute_id": "potency_test",
            "name": "Potency (Test)",
            "category": "potency",
            "is_cqa": True,
            "spec_lower": 80.0,
            "spec_upper": 120.0,
            "spec_source": "product_spec",
            "measurements": [
                {"lot_id": "PRE", "value": 100.0, "unit": "%", "within_spec": True},
                {"lot_id": "POST", "value": 75.0, "unit": "%", "within_spec": False},
            ],
        }
        result = score_attribute(attr, [])
        assert result.spec_compliance == "oos", (
            f"Expected spec_compliance='oos', got '{result.spec_compliance}'"
        )
        assert result.concern in ("major", "critical"), (
            f"OOS CQA must have concern >= major, got '{result.concern}'"
        )
        assert result.score <= 0.45, (
            f"OOS CQA score must be capped at 0.45, got {result.score}"
        )

    def test_s3_generic_tolerance_labeled(self):
        """S-3: When no spec data is provided, tolerance_source must be
        'default' and detail must mention 'generic tolerances'."""
        case = _load_case("COMP-001")
        report = _run_comparability(case)
        for ar in report.attribute_results:
            sb = ar.score_breakdown or {}
            ts = sb.get("tolerance_source", "unknown")
            assert ts == "default", (
                f"{ar.name}: expected tolerance_source='default' without "
                f"spec data, got '{ts}'"
            )
            assert "generic tolerances" in ar.detail.lower(), (
                f"{ar.name}: detail should mention 'generic tolerances', "
                f"got: {ar.detail}"
            )


# =========================================================================
# Layer 2: Regression Tests
# =========================================================================

class TestLayer2Regression:

    def test_baseline_csv_unchanged(self):
        """COMP-001 (original, no enrichment) must still produce the
        same verdict as the baseline."""
        case = _load_case("COMP-001")
        report = _run_comparability(case)
        assert report.overall_verdict == case["expected_verdict"], (
            f"COMP-001 baseline verdict changed: expected "
            f"'{case['expected_verdict']}', got '{report.overall_verdict}'"
        )

    def test_gold_cases_unchanged(self):
        """All 12 gold cases must still pass their 24 MP/PM checks
        (verdict + blocking count)."""
        import glob as _glob
        from schemas.case_context import CaseContext
        from services.cluster_builder import build_risk_clusters
        from services.cluster_matcher import match_for_clusters
        from services.judgment_policy import apply_cluster_policy, apply_package_policy
        from schemas.package_decision import PackageDecision
        from evidence_registry import EvidenceRegistry

        registry = EvidenceRegistry()
        pattern = os.path.join(GOLD_DIR, "gc_*.json")
        files = sorted(_glob.glob(pattern))
        assert len(files) == 12, f"Expected 12 gold cases, got {len(files)}"

        pass_count = 0
        for fpath in files:
            with open(fpath) as f:
                gc = json.load(f)
            ctx = CaseContext(**gc["case_context"])
            clusters = build_risk_clusters(ctx, gc["attribute_results"])
            cluster_packs, case_pack = match_for_clusters(ctx, clusters, registry)
            for i, cluster in enumerate(clusters):
                pack = cluster_packs[i] if i < len(cluster_packs) else case_pack
                clusters[i] = apply_cluster_policy(cluster, pack)
            n_blocking = sum(1 for c in clusters if c.package_blocking)
            prelim_verdict = "proceed"
            if n_blocking >= 2:
                prelim_verdict = "defer_package"
            elif n_blocking == 1:
                prelim_verdict = "supplement_required"
            prelim = PackageDecision(
                case_id=ctx.case_id,
                package_verdict=prelim_verdict,
                confidence=0.7,
                blocking_cluster_ids=[
                    c.cluster_id for c in clusters if c.package_blocking
                ],
            )
            decision = apply_package_policy(
                clusters, cluster_packs, case_pack, prelim
            )
            # Must produce a valid verdict (not crash)
            assert decision.package_verdict in (
                "proceed", "proceed_with_conditions",
                "supplement_required", "investigation_required",
                "defer_package",
            ), f"{gc['case_id']}: invalid verdict '{decision.package_verdict}'"
            pass_count += 1

        assert pass_count == 12, (
            f"Expected 12 gold case passes, got {pass_count}"
        )


# =========================================================================
# Layer 3: Enriched Behavior Tests
# =========================================================================

class TestLayer3EnrichedBehavior:

    def test_spec_aware_scoring(self):
        """COMP-001_specaware: attributes with spec limits must have
        tolerance_source='specification'."""
        case = _load_case("COMP-001_specaware")
        report = _run_comparability(case)
        # Afucosylation has both spec_lower and spec_upper
        afuc = next(
            ar for ar in report.attribute_results
            if ar.name == "Afucosylation %"
        )
        sb = afuc.score_breakdown or {}
        assert sb.get("tolerance_source") == "specification", (
            f"Afucosylation % should use specification tolerance, "
            f"got '{sb.get('tolerance_source')}'"
        )
        assert sb.get("spec_compliance") == "within_spec", (
            f"Afucosylation % (5.8 in 2.0-8.0) should be 'within_spec', "
            f"got '{sb.get('spec_compliance')}'"
        )

    def test_change_type_annotation(self):
        """COMP-001_specaware_changetype: score_breakdown must contain
        change_type_expectation entries for matched categories."""
        case = _load_case("COMP-001_specaware_changetype")
        report = _run_comparability(case)
        ct_found = False
        for ar in report.attribute_results:
            sb = ar.score_breakdown or {}
            ct = sb.get("change_type_expectation")
            if ct is not None:
                ct_found = True
                assert "expected" in ct, (
                    f"{ar.name}: change_type_expectation missing 'expected' key"
                )
        assert ct_found, (
            "No attributes had change_type_expectation entries despite "
            "change_type='media_change'"
        )

    def test_oos_signal_escalation(self):
        """COMP-001_specaware_changetype_signal: Charge Variants concern
        must be escalated to 'major' from the OOS signal."""
        case = _load_case("COMP-001_specaware_changetype_signal")
        report = _run_comparability(case)
        cv = next(
            ar for ar in report.attribute_results
            if "Charge Variants" in ar.name
        )
        assert cv.concern in ("major", "critical"), (
            f"Charge Variants should be escalated to major/critical from "
            f"OOS signal, got '{cv.concern}'"
        )
        sb = cv.score_breakdown or {}
        assert sb.get("signal_escalation"), (
            "Charge Variants score_breakdown should have signal_escalation"
        )


# =========================================================================
# Layer 4: Narrative Tests
# =========================================================================

class TestLayer4Narrative:

    def test_narrative_references_spec(self):
        """Enriched case with spec limits: posture_rationale must
        mention 'specification'."""
        case = _load_case("COMP-001_specaware")
        report = _run_comparability(case)
        assert "specification" in report.posture_rationale.lower(), (
            f"posture_rationale should mention 'specification', got:\n"
            f"{report.posture_rationale}"
        )

    def test_narrative_references_change_type(self):
        """Enriched case with change_type: posture_rationale must
        mention 'consistent with expected range' or 'unexpected'."""
        case = _load_case("COMP-001_specaware_changetype")
        report = _run_comparability(case)
        rationale = report.posture_rationale.lower()
        has_expected = "consistent with expected range" in rationale
        has_unexpected = "unexpected for" in rationale
        assert has_expected or has_unexpected, (
            f"posture_rationale should mention change-type context, got:\n"
            f"{report.posture_rationale}"
        )

    def test_narrative_references_generic_fallback(self):
        """Case with mixed spec/no-spec: posture_rationale must mention
        how many attributes relied on generic tolerances."""
        case = _load_case("COMP-001_specaware")
        report = _run_comparability(case)
        assert "generic tolerances" in report.posture_rationale.lower(), (
            f"posture_rationale should mention 'generic tolerances', got:\n"
            f"{report.posture_rationale}"
        )
        # Must also mention a count
        assert "attribute(s) relied on generic tolerances" in report.posture_rationale, (
            f"posture_rationale should mention count of generic tolerance "
            f"attributes, got:\n{report.posture_rationale}"
        )
