#!/usr/bin/env python3
"""
Step 0B.1 Deliverable H: Gold Case Baseline Runner.

Loads all 12 gold case fixtures from tests/gold/, runs each through the
current pipeline (run_comparability_assessment), and records baseline
outputs to docs/GOLD_CASE_BASELINE.json.

Usage:
    python3 scripts/eval/run_gold_case_baseline.py
    python3 scripts/eval/run_gold_case_baseline.py --cases tests/gold/ --output docs/GOLD_CASE_BASELINE.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.comparability import run_comparability_assessment


def load_gold_cases(cases_dir: str) -> list:
    """Load all gold case JSON fixtures from the given directory."""
    pattern = os.path.join(cases_dir, "gc_*.json")
    files = sorted(glob.glob(pattern))
    cases = []
    for fpath in files:
        with open(fpath, "r") as f:
            cases.append(json.load(f))
    return cases


def build_pipeline_input(gold_case: dict) -> dict:
    """Convert a gold case fixture into pipeline-compatible input."""
    attributes = []
    for attr in gold_case.get("attribute_results", []):
        attributes.append({
            "name": attr["attribute_id"],
            "category": attr.get("category", "physicochemical"),
            "pre_value": attr["pre_value"],
            "post_value": attr["post_value"],
            "unit": attr.get("unit", ""),
            "n_lots": 5,
            "cv_pct": 5.0,
            "n_methods": 2 if not attr.get("gaps") else 1,
            "has_functional_correlation": attr.get("is_cqa", False),
            "prior_approvals": 3,
        })

    ctx = gold_case.get("case_context", {})
    return {
        "attributes": attributes,
        "molecule_class": ctx.get("molecule_class", "mAb"),
        "modality": "IV",
    }


def extract_baseline_output(report, gold_case: dict) -> dict:
    """Extract baseline fields from a ComparabilityReport."""
    # Current pipeline verdict mapping
    pkg = report.package_verdict or {}
    pkg_verdict_obj = pkg.get("verdict")
    if pkg_verdict_obj is not None:
        # PackageVerdict enum -> string
        current_verdict = pkg_verdict_obj.value if hasattr(pkg_verdict_obj, "value") else str(pkg_verdict_obj)
    else:
        current_verdict = report.overall_verdict

    # Confidence band approximation from evidence_strength_index
    esi = report.evidence_strength_index
    if esi > 0.8:
        current_confidence_band = "high"
    elif esi >= 0.5:
        current_confidence_band = "moderate"
    else:
        current_confidence_band = "low"

    # Blocking cluster count: count attributes with critical/major CQA concerns
    blocking_count = sum(
        1 for ar in report.attribute_results
        if ar.concern in ("critical", "major") and ar.is_cqa
    )

    # Abstain flag: current system does not implement explicit abstain
    current_abstain_flag = False

    # Reviewer concern sample: from recommended_actions
    concern_sample = report.recommended_actions[:2] if report.recommended_actions else []

    # Top ref: first provenance entry
    top_ref = None
    if report.provenance_chain:
        first_prov = report.provenance_chain[0]
        top_ref = first_prov.get("source_id", None)

    return {
        "case_id": gold_case["case_id"],
        "title": gold_case["title"],
        "current_verdict": current_verdict,
        "current_confidence_band": current_confidence_band,
        "current_blocking_cluster_count": blocking_count,
        "current_abstain_flag": current_abstain_flag,
        "current_reviewer_concern_sample": concern_sample,
        "current_top_ref": top_ref,
        "current_overall_verdict": report.overall_verdict,
        "current_evidence_strength_index": report.evidence_strength_index,
        "current_package_verdict": current_verdict,
    }


def run_baseline(cases_dir: str, output_path: str) -> dict:
    """Run all gold cases and produce baseline JSON."""
    cases = load_gold_cases(cases_dir)
    if not cases:
        print(f"ERROR: No gold case fixtures found in {cases_dir}")
        sys.exit(1)

    print(f"Loaded {len(cases)} gold cases from {cases_dir}")

    results = []
    for gc in cases:
        case_id = gc["case_id"]
        print(f"  Running {case_id}: {gc['title']}...", end=" ")
        try:
            pipeline_input = build_pipeline_input(gc)
            report = run_comparability_assessment(
                pre_change_data=pipeline_input,
                product_name=f"GoldCase-{case_id}",
                change_description=gc["case_context"]["change_description"],
            )
            baseline = extract_baseline_output(report, gc)
            baseline["status"] = "success"
            print(f"verdict={baseline['current_verdict']}")
        except Exception as e:
            baseline = {
                "case_id": case_id,
                "title": gc["title"],
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc(),
                "current_verdict": None,
                "current_confidence_band": None,
                "current_blocking_cluster_count": None,
                "current_abstain_flag": None,
                "current_reviewer_concern_sample": [],
                "current_top_ref": None,
            }
            print(f"ERROR: {e}")

        results.append(baseline)

    output = {
        "baseline_timestamp": datetime.now(timezone.utc).isoformat(),
        "n_cases": len(results),
        "n_success": sum(1 for r in results if r.get("status") == "success"),
        "n_error": sum(1 for r in results if r.get("status") == "error"),
        "cases": results,
    }

    # Write output
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nBaseline written to {output_path}")
    print(f"  Success: {output['n_success']}/{output['n_cases']}")
    print(f"  Errors:  {output['n_error']}/{output['n_cases']}")

    return output


def main():
    parser = argparse.ArgumentParser(description="Run gold case baseline evaluation")
    parser.add_argument(
        "--cases", default=os.path.join(str(PROJECT_ROOT), "tests", "gold"),
        help="Directory containing gold case JSON fixtures",
    )
    parser.add_argument(
        "--output", default=os.path.join(str(PROJECT_ROOT), "docs", "GOLD_CASE_BASELINE.json"),
        help="Output path for baseline JSON",
    )
    args = parser.parse_args()

    run_baseline(args.cases, args.output)


if __name__ == "__main__":
    main()
