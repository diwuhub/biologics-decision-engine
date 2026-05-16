"""
CLI for generating ICH Q5E comparability reports (DOCX).

Usage:
    python3 -m reports.generate_report --input benchmarks/cases/COMP-001.json --output report.docx
    python3 -m reports.generate_report --demo
    python3 -m reports.generate_report --demo --output my_report.docx
"""

import argparse
import json
import os
import sys

# Ensure project root is importable
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pipelines.comparability import run_comparability_assessment
from reports.comparability_report import generate_comparability_report


DEMO_PATH = os.path.join(PROJECT_ROOT, "benchmarks", "cases", "COMP-001.json")
FALLBACK_DEMO_PATH = os.path.join(PROJECT_ROOT, "benchmarks", "mab_process_change_case.json")


def _load_case(path: str) -> dict:
    """Load a JSON case file."""
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Generate an ICH Q5E-compliant comparability report (DOCX) "
                    "from a JSON case file or built-in demo data."
    )
    parser.add_argument(
        "--input", "-i",
        help="Path to JSON case file (must contain 'attributes' list)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output path for the DOCX report (default: <case_id>_report.docx)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with the built-in COMP-001 demo case",
    )
    args = parser.parse_args()

    # Determine input
    if args.demo:
        if os.path.exists(DEMO_PATH):
            input_path = DEMO_PATH
        elif os.path.exists(FALLBACK_DEMO_PATH):
            input_path = FALLBACK_DEMO_PATH
        else:
            print("ERROR: Demo case file not found.", file=sys.stderr)
            sys.exit(1)
        print(f"Using demo case: {input_path}")
    elif args.input:
        input_path = args.input
        if not os.path.exists(input_path):
            print(f"ERROR: File not found: {input_path}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(0)

    # Load case
    try:
        case_data = _load_case(input_path)
    except json.JSONDecodeError as e:
        print(f"ERROR: Malformed JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # Run pipeline
    print("Running comparability assessment pipeline...")
    report = run_comparability_assessment(
        pre_change_data=case_data,
        product_name=case_data.get("product_name", "Product"),
        change_description=case_data.get("change_description", ""),
    )

    esi = getattr(report, "evidence_strength_index",
                  getattr(report, "confidence", 0.0))
    print(f"  Verdict: {report.overall_verdict}")
    print(f"  Evidence Strength: {esi:.1%}")
    print(f"  Attributes: {report.n_attributes} ({report.n_comparable} comparable, "
          f"{report.n_flagged} flagged)")

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        case_id = case_data.get("case_id", "comparability")
        output_path = f"{case_id}_report.docx"

    # Generate report
    print(f"Generating DOCX report...")
    result_path = generate_comparability_report(report, output_path)
    print(f"Report generated: {result_path}")

    return result_path


if __name__ == "__main__":
    main()
