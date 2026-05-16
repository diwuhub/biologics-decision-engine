"""
Public Case-Pack Benchmark Runner (S-5).

Runs all benchmark cases through the appropriate engine pipelines,
compares actual outputs against expected values, and produces
a structured summary with pass/fail per case and overall accuracy.

Usage:
    python3 benchmarks/run_benchmarks.py
    python3 benchmarks/run_benchmarks.py --verbose
    python3 benchmarks/run_benchmarks.py --output results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Ensure project root is importable
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CASES_DIR = os.path.join(os.path.dirname(__file__), "cases")


# =========================================================================
# Result Data Classes
# =========================================================================

@dataclass
class CaseResult:
    """Result of running a single benchmark case."""
    case_id: str
    title: str
    category: str
    status: str             # "pass", "partial", "fail", "error"
    expected_verdict: str
    actual_verdict: str
    verdict_match: bool
    action_matches: Dict[str, str] = field(default_factory=dict)
    # action_matches maps attribute_name -> "match" | "mismatch" | "not_checked"
    action_accuracy: float = 0.0
    details: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BenchmarkSummary:
    """Aggregate benchmark results."""
    n_cases: int = 0
    n_pass: int = 0
    n_partial: int = 0
    n_fail: int = 0
    n_error: int = 0
    verdict_accuracy: float = 0.0
    action_accuracy: float = 0.0
    results: List[CaseResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["results"] = [r.to_dict() for r in self.results]
        return d


# =========================================================================
# Pipeline Runners
# =========================================================================

def _run_comparability_case(case_data: dict) -> CaseResult:
    """Run a comparability benchmark case through the pipeline."""
    from pipelines.comparability import run_comparability_assessment

    case_id = case_data["case_id"]
    expected_verdict = case_data["expected_verdict"]
    expected_actions = case_data.get("expected_actions", {})

    report = run_comparability_assessment(
        pre_change_data=case_data,
        product_name=case_data.get("product_name", "Benchmark Product"),
        change_description=case_data.get("change_description", ""),
    )

    actual_verdict = report.overall_verdict
    verdict_match = actual_verdict == expected_verdict

    # Check per-attribute actions
    action_matches = {}
    actual_actions = {}
    for ar in report.attribute_results:
        if ar.action:
            actual_actions[ar.name] = ar.action.get("action_level", "UNKNOWN")

    for attr_name, expected_action in expected_actions.items():
        actual_action = actual_actions.get(attr_name, "NOT_FOUND")
        if actual_action == expected_action:
            action_matches[attr_name] = "match"
        else:
            action_matches[attr_name] = f"mismatch (expected={expected_action}, actual={actual_action})"

    n_action_checks = len(action_matches)
    n_action_match = sum(1 for v in action_matches.values() if v == "match")
    action_accuracy = n_action_match / n_action_checks if n_action_checks > 0 else 1.0

    # Determine overall status
    if verdict_match and action_accuracy == 1.0:
        status = "pass"
    elif verdict_match and action_accuracy >= 0.5:
        status = "partial"
    elif verdict_match:
        status = "partial"
    else:
        status = "fail"

    details = (
        f"Verdict: {actual_verdict} (expected {expected_verdict}). "
        f"Actions: {n_action_match}/{n_action_checks} match. "
        f"Evidence Strength: {report.evidence_strength_index:.3f}. "
        f"Comparable: {report.n_comparable}/{report.n_attributes}. "
        f"Flagged: {report.n_flagged}."
    )

    return CaseResult(
        case_id=case_id,
        title=case_data.get("title", ""),
        category="comparability",
        status=status,
        expected_verdict=expected_verdict,
        actual_verdict=actual_verdict,
        verdict_match=verdict_match,
        action_matches=action_matches,
        action_accuracy=action_accuracy,
        details=details,
    )


def _run_readiness_case(case_data: dict) -> CaseResult:
    """Run a review-readiness benchmark case through the NAM readiness scorer."""
    from schemas.label_schema import NAMReadinessRecord
    from modules.nam_readiness.readiness_scorer import score_readiness

    case_id = case_data["case_id"]
    expected_tier = case_data.get("expected_readiness_tier", "unknown")

    record = NAMReadinessRecord(
        nam_type=case_data["nam_type"],
        context_of_use=case_data["context_of_use"],
        species_replaced=case_data.get("species_replaced"),
        validation_evidence=case_data.get("validation_evidence", []),
        regulatory_precedent=case_data.get("regulatory_precedent", []),
        qualification_pathway=case_data.get("qualification_pathway"),
    )
    result = score_readiness(record)
    score = result.readiness_score
    n_gaps = len(result.readiness_gaps)

    # Classify actual tier
    if score >= 0.75:
        actual_tier = "high"
    elif score >= 0.40:
        actual_tier = "medium"
    else:
        actual_tier = "low"

    tier_match = actual_tier == expected_tier

    # Check score bounds if specified
    score_ok = True
    score_notes = []
    if "expected_readiness_score_min" in case_data:
        if score < case_data["expected_readiness_score_min"]:
            score_ok = False
            score_notes.append(
                f"score {score:.4f} < expected min {case_data['expected_readiness_score_min']}"
            )
    if "expected_readiness_score_max" in case_data:
        if score > case_data["expected_readiness_score_max"]:
            score_ok = False
            score_notes.append(
                f"score {score:.4f} > expected max {case_data['expected_readiness_score_max']}"
            )

    # Check gap count bounds
    gap_ok = True
    if "expected_gap_count_max" in case_data:
        if n_gaps > case_data["expected_gap_count_max"]:
            gap_ok = False
            score_notes.append(
                f"gaps {n_gaps} > expected max {case_data['expected_gap_count_max']}"
            )
    if "expected_gap_count_min" in case_data:
        if n_gaps < case_data["expected_gap_count_min"]:
            gap_ok = False
            score_notes.append(
                f"gaps {n_gaps} < expected min {case_data['expected_gap_count_min']}"
            )

    verdict_match = tier_match
    all_ok = tier_match and score_ok and gap_ok

    if all_ok:
        status = "pass"
    elif tier_match:
        status = "partial"
    else:
        status = "fail"

    details = (
        f"Readiness: score={score:.4f}, tier={actual_tier} "
        f"(expected {expected_tier}). "
        f"Gaps: {n_gaps}. "
        + ("; ".join(score_notes) if score_notes else "All bounds met.")
    )

    return CaseResult(
        case_id=case_id,
        title=case_data.get("title", ""),
        category="review_readiness",
        status=status,
        expected_verdict=expected_tier,
        actual_verdict=actual_tier,
        verdict_match=verdict_match,
        details=details,
    )


def _run_regulatory_case(case_data: dict) -> CaseResult:
    """Run a regulatory classification case through the admissibility engine."""
    from modules.admissibility_engine.run import run_engine

    case_id = case_data["case_id"]
    expected_action = case_data.get("expected_action", "unknown")

    # Write to temp file for the engine
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, dir=tempfile.gettempdir()
    )
    try:
        json.dump(case_data, tmp)
        tmp.close()
        result = run_engine(tmp.name)
    finally:
        os.unlink(tmp.name)

    rec = result["recommendation"]
    gap = result["gap_analysis"]
    actual_action = rec["action"]
    confidence = rec["confidence"]

    verdict_match = actual_action == expected_action

    if verdict_match:
        status = "pass"
    else:
        status = "fail"

    details = (
        f"Action: {actual_action} (expected {expected_action}). "
        f"Confidence: {confidence:.2f}. "
        f"Coverage: {gap['coverage_pct']:.0f}%. "
        f"Claims: {result['n_claims']}. "
        f"Gaps: {len(gap['uncovered'])}."
    )

    return CaseResult(
        case_id=case_id,
        title=case_data.get("title", ""),
        category="regulatory_classification",
        status=status,
        expected_verdict=expected_action,
        actual_verdict=actual_action,
        verdict_match=verdict_match,
        details=details,
    )


def _run_gap_memo_case(case_data: dict) -> CaseResult:
    """Run a gap memo benchmark case through the pipeline."""
    from pipelines.gap_memo import generate_gap_memo

    case_id = case_data["case_id"]
    expected_readiness = case_data.get("expected_readiness", "Not Ready")

    memo = generate_gap_memo(
        sections=case_data.get("sections", []),
        product_type=case_data.get("product_type", "mAb"),
        submission_type=case_data.get("submission_type", "BLA"),
        product_name=case_data.get("product_name", ""),
    )

    actual_readiness = memo.overall_readiness
    verdict_match = actual_readiness == expected_readiness

    # Check expected result bounds
    expected = case_data.get("expected_results", {})
    notes = []
    min_gaps = expected.get("min_gaps", 0)
    if memo.n_gaps_found < min_gaps:
        notes.append(f"gaps {memo.n_gaps_found} < expected min {min_gaps}")
    min_flags = expected.get("min_consistency_flags", 0)
    if len(memo.consistency_flags) < min_flags:
        notes.append(f"flags {len(memo.consistency_flags)} < expected min {min_flags}")
    min_questions = expected.get("min_predicted_questions", 0)
    if len(memo.predicted_questions) < min_questions:
        notes.append(f"questions {len(memo.predicted_questions)} < expected min {min_questions}")

    bounds_ok = len(notes) == 0

    if verdict_match and bounds_ok:
        status = "pass"
    elif verdict_match:
        status = "partial"
    else:
        status = "fail"

    details = (
        f"Readiness: {actual_readiness} (expected {expected_readiness}). "
        f"Gaps: {memo.n_gaps_found} ({memo.n_critical}C/{memo.n_major}M/{memo.n_minor}m). "
        f"Flags: {len(memo.consistency_flags)}. "
        f"Questions: {len(memo.predicted_questions)}. "
        + ("; ".join(notes) if notes else "All bounds met.")
    )

    return CaseResult(
        case_id=case_id,
        title=case_data.get("title", ""),
        category="gap_memo",
        status=status,
        expected_verdict=expected_readiness,
        actual_verdict=actual_readiness,
        verdict_match=verdict_match,
        details=details,
    )


# =========================================================================
# Main Runner
# =========================================================================

def load_all_cases() -> List[dict]:
    """Load all benchmark case JSON files from the cases directory."""
    cases = []
    if not os.path.isdir(CASES_DIR):
        return cases
    for fname in sorted(os.listdir(CASES_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(CASES_DIR, fname)
        with open(fpath) as f:
            case = json.load(f)
        cases.append(case)
    return cases


def run_single_benchmark(case_data: dict) -> CaseResult:
    """Run a single benchmark case through the appropriate pipeline."""
    category = case_data.get("category", "unknown")
    try:
        if category == "comparability":
            return _run_comparability_case(case_data)
        elif category == "review_readiness":
            return _run_readiness_case(case_data)
        elif category == "regulatory_classification":
            return _run_regulatory_case(case_data)
        elif category == "gap_memo":
            return _run_gap_memo_case(case_data)
        else:
            return CaseResult(
                case_id=case_data.get("case_id", "UNKNOWN"),
                title=case_data.get("title", ""),
                category=category,
                status="error",
                expected_verdict="N/A",
                actual_verdict="N/A",
                verdict_match=False,
                error=f"Unknown benchmark category: {category}",
            )
    except Exception as e:
        return CaseResult(
            case_id=case_data.get("case_id", "UNKNOWN"),
            title=case_data.get("title", ""),
            category=category,
            status="error",
            expected_verdict=case_data.get("expected_verdict",
                             case_data.get("expected_readiness_tier",
                             case_data.get("expected_action", "N/A"))),
            actual_verdict="ERROR",
            verdict_match=False,
            error=str(e),
        )


def run_all_benchmarks() -> BenchmarkSummary:
    """Run all benchmark cases, compare actual vs expected outputs."""
    cases = load_all_cases()
    summary = BenchmarkSummary()
    summary.n_cases = len(cases)

    for case_data in cases:
        result = run_single_benchmark(case_data)
        summary.results.append(result)

        if result.status == "pass":
            summary.n_pass += 1
        elif result.status == "partial":
            summary.n_partial += 1
        elif result.status == "fail":
            summary.n_fail += 1
        else:
            summary.n_error += 1

    # Compute aggregate accuracy
    n_verdict_checked = sum(1 for r in summary.results if r.status != "error")
    n_verdict_match = sum(1 for r in summary.results if r.verdict_match)
    summary.verdict_accuracy = (
        n_verdict_match / n_verdict_checked if n_verdict_checked > 0 else 0.0
    )

    # Action accuracy across all comparability cases
    all_action_checks = 0
    all_action_matches = 0
    for r in summary.results:
        if r.category == "comparability" and r.action_matches:
            all_action_checks += len(r.action_matches)
            all_action_matches += sum(
                1 for v in r.action_matches.values() if v == "match"
            )
    summary.action_accuracy = (
        all_action_matches / all_action_checks if all_action_checks > 0 else 0.0
    )

    return summary


# =========================================================================
# CLI
# =========================================================================

def print_summary(summary: BenchmarkSummary, verbose: bool = False) -> None:
    """Pretty-print benchmark results."""
    sep = "=" * 76
    print(sep)
    print("  BIOLOGICS DECISION ENGINE -- BENCHMARK RESULTS")
    print(sep)
    print(
        f"\n  Cases: {summary.n_cases} total | "
        f"{summary.n_pass} pass | {summary.n_partial} partial | "
        f"{summary.n_fail} fail | {summary.n_error} error"
    )
    print(f"  Verdict Accuracy: {summary.verdict_accuracy:.1%}")
    print(f"  Action Accuracy:  {summary.action_accuracy:.1%}")

    # Per-case table
    print(f"\n  {'Case':<12s} {'Status':<9s} {'Expected':<20s} {'Actual':<20s} {'Match':>5s}")
    print(f"  {'-'*12} {'-'*9} {'-'*20} {'-'*20} {'-'*5}")

    for r in summary.results:
        match_tag = "Y" if r.verdict_match else "N"
        status_display = r.status.upper()
        print(
            f"  {r.case_id:<12s} {status_display:<9s} "
            f"{r.expected_verdict:<20s} {r.actual_verdict:<20s} {match_tag:>5s}"
        )

    if verbose:
        print(f"\n  {'='*76}")
        print("  DETAILED RESULTS")
        print(f"  {'='*76}")
        for r in summary.results:
            print(f"\n  [{r.case_id}] {r.title}")
            print(f"    Status: {r.status.upper()}")
            print(f"    {r.details}")
            if r.action_matches:
                print(f"    Action checks ({r.action_accuracy:.0%} match):")
                for attr, match_val in r.action_matches.items():
                    tag = "OK" if match_val == "match" else "XX"
                    print(f"      [{tag}] {attr}: {match_val}")
            if r.error:
                print(f"    ERROR: {r.error}")

    print(f"\n{sep}")


def main():
    parser = argparse.ArgumentParser(
        description="Biologics Decision Engine -- Benchmark Runner (S-5)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed per-case results"
    )
    parser.add_argument(
        "--output", "-o",
        help="Save structured JSON results to file"
    )
    parser.add_argument(
        "--case",
        help="Run a single case by case_id (e.g., COMP-001)"
    )
    args = parser.parse_args()

    if args.case:
        # Run a single case
        cases = load_all_cases()
        target = [c for c in cases if c["case_id"] == args.case]
        if not target:
            print(f"ERROR: Case {args.case} not found in {CASES_DIR}")
            sys.exit(1)
        result = run_single_benchmark(target[0])
        print(f"[{result.case_id}] {result.title}")
        print(f"  Status: {result.status.upper()}")
        print(f"  {result.details}")
        if result.action_matches:
            for attr, match_val in result.action_matches.items():
                tag = "OK" if match_val == "match" else "XX"
                print(f"  [{tag}] {attr}: {match_val}")
        if result.error:
            print(f"  ERROR: {result.error}")
        return

    summary = run_all_benchmarks()
    print_summary(summary, verbose=args.verbose)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(summary.to_dict(), f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
