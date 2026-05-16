#!/usr/bin/env python3
"""
Gold Case Evaluation Runner — Steps 3-5.

Runs all 12 gold cases through the new judgment pipeline:
  Step 1: Cluster builder (existing)
  Step 2: Cluster matcher (existing)
  Step 3: Two-stage judgment policy (new)
  Step 4: Reviewer concern engine (new)
  Step 5: Authority enrichment (new)

Then checks improvement from baseline and verifies 5 critical behaviors:
  1. GC-07 abstains
  2. GC-10 doesn't block
  3. GC-06 judges (not abstain)
  4. GC-11 detects hidden insufficiency
  5. GC-12 doesn't overreact

Usage:
    python3 scripts/eval/run_gold_case_eval.py
"""

from __future__ import annotations

import glob
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evidence_registry import EvidenceRegistry
from schemas.case_context import CaseContext
from schemas.package_decision import PackageDecision
from services.cluster_builder import build_risk_clusters
from services.cluster_matcher import match_for_clusters
from services.judgment_policy import apply_cluster_policy, apply_package_policy
from services.reviewer_concern_engine import (
    generate_reviewer_concerns,
    apply_concerns_to_decision,
)
from services.authority_enrichment import run_gold_case_enrichment


GOLD_DIR = os.path.join(str(PROJECT_ROOT), "tests", "gold")


def load_gold_cases(gold_dir: str = GOLD_DIR) -> list:
    pattern = os.path.join(gold_dir, "gc_*.json")
    files = sorted(glob.glob(pattern))
    cases = []
    for fpath in files:
        with open(fpath, "r") as f:
            cases.append(json.load(f))
    return cases


def run_pipeline(gold_case: dict, registry: EvidenceRegistry) -> dict:
    """Run a single gold case through the full judgment pipeline."""
    ctx = CaseContext(**gold_case["case_context"])
    attribute_results = gold_case["attribute_results"]

    # Step 1: Cluster builder
    clusters = build_risk_clusters(ctx, attribute_results)

    # Step 2: Cluster matcher
    cluster_packs, case_pack = match_for_clusters(ctx, clusters, registry)

    # Step 3a: Cluster-level policy
    for i, cluster in enumerate(clusters):
        if i < len(cluster_packs):
            apply_cluster_policy(cluster, cluster_packs[i])

    # Step 3b: Package-level policy
    preliminary = PackageDecision(
        case_id=ctx.case_id,
        package_verdict="proceed",
        confidence=0.5,
    )
    decision = apply_package_policy(
        clusters, cluster_packs, case_pack, preliminary
    )

    # Step 4: Reviewer concern engine
    concern_result = generate_reviewer_concerns(
        clusters, cluster_packs, case_pack, decision
    )
    decision = apply_concerns_to_decision(decision, concern_result)

    return {
        "case_id": gold_case["case_id"],
        "title": gold_case["title"],
        "verdict": decision.package_verdict,
        "confidence": round(decision.confidence, 3),
        "confidence_band": decision.confidence_band,
        "blocking_cluster_count": len(decision.blocking_cluster_ids),
        "abstain_flag": decision.abstain_flag,
        "abstain_reason": decision.abstain_reason,
        "decision_rule_ids": decision.decision_rule_ids,
        "n_concerns": len(concern_result.concerns),
        "top_concern": (
            concern_result.concerns[0].concern_text[:100]
            if concern_result.concerns else ""
        ),
        "top_concern_severity": (
            concern_result.concerns[0].severity
            if concern_result.concerns else ""
        ),
        "n_clusters": len(clusters),
        "blocking_cluster_ids": decision.blocking_cluster_ids,
        "risk_semantics": [c.risk_semantics for c in clusters],
    }


def check_critical_behaviors(results: dict) -> dict:
    """Check the 5 critical behaviors."""
    by_id = {r["case_id"]: r for r in results}
    checks = {}

    # 1. GC-07 abstains
    gc07 = by_id.get("GC-07", {})
    checks["gc07_abstains"] = {
        "pass": gc07.get("abstain_flag") is True,
        "expected": "abstain_flag=True",
        "actual": f"abstain_flag={gc07.get('abstain_flag')}",
        "verdict": gc07.get("verdict"),
    }

    # 2. GC-10 doesn't block
    gc10 = by_id.get("GC-10", {})
    checks["gc10_no_blocking"] = {
        "pass": gc10.get("blocking_cluster_count", -1) == 0,
        "expected": "blocking_cluster_count=0",
        "actual": f"blocking_cluster_count={gc10.get('blocking_cluster_count')}",
        "verdict": gc10.get("verdict"),
    }

    # 3. GC-06 judges (not abstain)
    gc06 = by_id.get("GC-06", {})
    checks["gc06_judges_not_abstain"] = {
        "pass": gc06.get("abstain_flag") is False,
        "expected": "abstain_flag=False",
        "actual": f"abstain_flag={gc06.get('abstain_flag')}",
        "verdict": gc06.get("verdict"),
    }

    # 4. GC-11 detects hidden insufficiency
    gc11 = by_id.get("GC-11", {})
    checks["gc11_detects_insufficiency"] = {
        "pass": gc11.get("blocking_cluster_count", 0) >= 1,
        "expected": "blocking_cluster_count>=1",
        "actual": f"blocking_cluster_count={gc11.get('blocking_cluster_count')}",
        "verdict": gc11.get("verdict"),
    }

    # 5. GC-12 doesn't overreact
    gc12 = by_id.get("GC-12", {})
    gc12_verdict = gc12.get("verdict", "")
    checks["gc12_no_overreact"] = {
        "pass": gc12_verdict in ("proceed", "proceed_with_conditions"),
        "expected": "verdict in [proceed, proceed_with_conditions]",
        "actual": f"verdict={gc12_verdict}",
        "verdict": gc12_verdict,
    }

    return checks


def compare_to_expected(results: list, gold_cases: list) -> list:
    """Compare each result to expected decision."""
    comparisons = []
    expected_map = {gc["case_id"]: gc["expected_decision"] for gc in gold_cases}

    for r in results:
        expected = expected_map.get(r["case_id"], {})
        comparison = {
            "case_id": r["case_id"],
            "title": r["title"],
            "verdict_match": r["verdict"] == expected.get("verdict"),
            "confidence_band_match": r["confidence_band"] == expected.get("confidence_band"),
            "blocking_match": r["blocking_cluster_count"] == expected.get("blocking_cluster_count"),
            "abstain_match": r["abstain_flag"] == expected.get("abstain_flag"),
            "actual_verdict": r["verdict"],
            "expected_verdict": expected.get("verdict"),
            "actual_confidence_band": r["confidence_band"],
            "expected_confidence_band": expected.get("confidence_band"),
            "actual_blocking": r["blocking_cluster_count"],
            "expected_blocking": expected.get("blocking_cluster_count"),
        }
        comparisons.append(comparison)

    return comparisons


def main():
    print("=" * 70)
    print("Gold Case Evaluation — Judgment Core Refactor Steps 3-5")
    print("=" * 70)

    # Step 5: Run enrichment first
    print("\n--- Step 5: Authority Enrichment ---")
    registry = EvidenceRegistry()
    enrichment_report = run_gold_case_enrichment(registry=registry)
    print(f"  Enriched {enrichment_report.n_entries_enriched} entries")
    print(f"  Completeness: {enrichment_report.completeness_before:.1%} -> {enrichment_report.completeness_after:.1%}")

    # Load gold cases
    gold_cases = load_gold_cases()
    print(f"\nLoaded {len(gold_cases)} gold cases")

    # Run pipeline for each
    print("\n--- Pipeline Evaluation ---")
    results = []
    for gc in gold_cases:
        case_id = gc["case_id"]
        try:
            result = run_pipeline(gc, registry)
            result["status"] = "success"
            print(f"  {case_id}: verdict={result['verdict']}, "
                  f"confidence={result['confidence']:.2f}, "
                  f"blocking={result['blocking_cluster_count']}, "
                  f"abstain={result['abstain_flag']}")
        except Exception as e:
            result = {
                "case_id": case_id,
                "title": gc["title"],
                "status": "error",
                "error": str(e),
                "verdict": None,
                "confidence": None,
                "confidence_band": None,
                "blocking_cluster_count": None,
                "abstain_flag": None,
            }
            print(f"  {case_id}: ERROR - {e}")
            traceback.print_exc()
        results.append(result)

    # Compare to expected
    print("\n--- Comparison to Expected ---")
    comparisons = compare_to_expected(
        [r for r in results if r.get("status") == "success"],
        gold_cases,
    )

    matches = {"verdict": 0, "confidence_band": 0, "blocking": 0, "abstain": 0}
    total = len(comparisons)
    for comp in comparisons:
        if comp["verdict_match"]:
            matches["verdict"] += 1
        if comp["confidence_band_match"]:
            matches["confidence_band"] += 1
        if comp["blocking_match"]:
            matches["blocking"] += 1
        if comp["abstain_match"]:
            matches["abstain"] += 1

        status = "MATCH" if all([
            comp["verdict_match"],
            comp["confidence_band_match"],
            comp["blocking_match"],
            comp["abstain_match"],
        ]) else "DIFF"

        if status == "DIFF":
            diffs = []
            if not comp["verdict_match"]:
                diffs.append(f"verdict: {comp['actual_verdict']} vs {comp['expected_verdict']}")
            if not comp["confidence_band_match"]:
                diffs.append(f"band: {comp['actual_confidence_band']} vs {comp['expected_confidence_band']}")
            if not comp["blocking_match"]:
                diffs.append(f"blocking: {comp['actual_blocking']} vs {comp['expected_blocking']}")
            print(f"  {comp['case_id']}: {status} ({'; '.join(diffs)})")
        else:
            print(f"  {comp['case_id']}: {status}")

    print(f"\n  Verdict match: {matches['verdict']}/{total}")
    print(f"  Confidence band match: {matches['confidence_band']}/{total}")
    print(f"  Blocking match: {matches['blocking']}/{total}")
    print(f"  Abstain match: {matches['abstain']}/{total}")

    # Check 5 critical behaviors
    print("\n--- 5 Critical Behaviors ---")
    success_results = [r for r in results if r.get("status") == "success"]
    checks = check_critical_behaviors(success_results)

    all_pass = True
    for name, check in checks.items():
        status = "PASS" if check["pass"] else "FAIL"
        if not check["pass"]:
            all_pass = False
        print(f"  [{status}] {name}: {check['actual']} (expected: {check['expected']})")

    # Summary
    n_success = sum(1 for r in results if r.get("status") == "success")
    n_errors = sum(1 for r in results if r.get("status") == "error")

    print(f"\n--- Summary ---")
    print(f"  Pipeline success: {n_success}/{len(results)}")
    print(f"  Pipeline errors: {n_errors}/{len(results)}")
    print(f"  Critical behaviors: {'ALL PASS' if all_pass else 'SOME FAIL'}")
    print(f"  Overall score: {matches['verdict'] + matches['abstain']}/{total * 2} (verdict + abstain)")

    # Write results
    output_path = os.path.join(str(PROJECT_ROOT), "docs", "GOLD_CASE_EVAL_STEP3_5.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_cases": len(results),
        "n_success": n_success,
        "matches": matches,
        "critical_behaviors": checks,
        "all_critical_pass": all_pass,
        "enrichment": {
            "entries_enriched": enrichment_report.n_entries_enriched,
            "completeness_after": enrichment_report.completeness_after,
        },
        "cases": results,
        "comparisons": comparisons,
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results written to {output_path}")


if __name__ == "__main__":
    main()
