"""
Comparability Assessment CLI Runner.

Usage:
    python3 -m pipelines.run_comparability --demo
    python3 -m pipelines.run_comparability --input benchmarks/mab_process_change_case.json
    python3 -m pipelines.run_comparability --input case.json --output report.json
"""

import argparse
import json
import sys
import os

# Ensure project root is importable
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pipelines.comparability import run_comparability_assessment


DEMO_PATH = os.path.join(PROJECT_ROOT, "benchmarks", "mab_process_change_case.json")


def print_report(report):
    """Pretty-print a ComparabilityReport to the terminal."""
    sep = "=" * 72
    print(sep)
    print(f"  COMPARABILITY ASSESSMENT REPORT")
    print(f"  Product:  {report.product_name}")
    print(f"  Change:   {report.change_description[:80]}")
    print(sep)

    # Verdict banner
    verdict = report.overall_verdict
    esi = report.evidence_strength_index
    if verdict == "Comparable":
        tag = "[PASS]"
    elif verdict == "Not Comparable":
        tag = "[FAIL]"
    else:
        tag = "[????]"
    print(f"\n  {tag}  {verdict}  (Evidence Strength: {esi:.1%})")
    print(f"  Attributes: {report.n_comparable}/{report.n_attributes} comparable, "
          f"{report.n_flagged} flagged, {report.n_cqa} CQAs\n")

    # Attribute table
    print(f"  {'Attribute':<30s} {'Cat':<15s} {'Pre':>8s} {'Post':>8s} "
          f"{'Delta':>8s} {'Score':>6s} {'CQA':>5s} {'Concern':<10s}")
    print(f"  {'-'*30} {'-'*15} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*5} {'-'*10}")
    for ar in report.attribute_results:
        cqa_tag = ar.cqa_designation if ar.is_cqa else ""
        print(f"  {ar.name:<30s} {ar.category:<15s} {ar.pre_value:>8.2f} {ar.post_value:>8.2f} "
              f"{ar.delta_pct:>+7.1f}% {ar.score:>6.3f} {cqa_tag:>5s} {ar.concern:<10s}")

    # Uncertainty summary
    print(f"\n  Uncertainty: mean={report.uncertainty_summary['mean_uncertainty']:.3f}, "
          f"max={report.uncertainty_summary['max_uncertainty']:.3f}")
    if report.uncertainty_summary["high_uncertainty_attributes"]:
        print(f"  High uncertainty: {', '.join(report.uncertainty_summary['high_uncertainty_attributes'])}")

    # Evidence gaps
    if report.evidence_gaps:
        print(f"\n  Evidence Gaps ({len(report.evidence_gaps)}):")
        for gap in report.evidence_gaps[:5]:
            print(f"    - {gap}")

    # Per-attribute actions (S-4 Action Layer)
    print(f"\n  Per-Attribute Actions:")
    print(f"  {'Attribute':<30s} {'Action':<14s} {'Rationale'}")
    print(f"  {'-'*30} {'-'*14} {'-'*50}")
    for ar in report.attribute_results:
        if ar.action:
            level = ar.action.get("action_level", "?")
            rationale = ar.action.get("rationale", "")
            # Truncate rationale for display
            if len(rationale) > 70:
                rationale = rationale[:67] + "..."
            print(f"  {ar.name:<30s} {level:<14s} {rationale}")

    # Overall action summary (S-4)
    if report.action_summary:
        summary = report.action_summary
        print(f"\n  Overall Action: {summary.get('overall_action', 'N/A')}")
        print(f"  Regulatory Risk: {summary.get('regulatory_risk', 'N/A')}")
        print(f"  Timeline: {summary.get('estimated_timeline', 'N/A')}")
        print(f"  Breakdown: {summary.get('n_proceed', 0)} PROCEED, "
              f"{summary.get('n_supplement', 0)} SUPPLEMENT, "
              f"{summary.get('n_monitor', 0)} MONITOR, "
              f"{summary.get('n_investigate', 0)} INVESTIGATE, "
              f"{summary.get('n_defer', 0)} DEFER")
        if summary.get("critical_attributes"):
            print(f"  Critical: {', '.join(summary['critical_attributes'])}")
        if summary.get("next_steps"):
            print(f"\n  Next Steps:")
            for i, step in enumerate(summary["next_steps"], 1):
                print(f"    {i}. {step}")

    # Legacy recommended actions
    if report.recommended_actions:
        print(f"\n  Recommended Actions:")
        for i, action in enumerate(report.recommended_actions, 1):
            print(f"    {i}. {action}")

    print(f"\n  Timestamp: {report.timestamp}")
    print(sep)


def main():
    parser = argparse.ArgumentParser(
        description="Comparability Assessment Pipeline -- "
                    "determines if a manufacturing change is acceptable"
    )
    parser.add_argument("--input", help="Path to JSON case file")
    parser.add_argument("--demo", action="store_true",
                        help="Run with built-in mAb process change demo case")
    parser.add_argument("--output", help="Save structured report JSON to file")
    args = parser.parse_args()

    if args.demo:
        input_path = DEMO_PATH
    elif args.input:
        input_path = args.input
    else:
        parser.print_help()
        return

    try:
        with open(input_path) as f:
            case_data = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Malformed JSON: {e}", file=sys.stderr)
        sys.exit(1)

    report = run_comparability_assessment(
        pre_change_data=case_data,
        product_name=case_data.get("product_name", "Product"),
        change_description=case_data.get("change_description", ""),
    )

    print_report(report)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\nReport saved to {args.output}")


if __name__ == "__main__":
    main()
