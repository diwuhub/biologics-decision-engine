"""
Value Correctness Probe — compares extractor output against gold standard.

Loads benchmarks/real_documents/gold_standard.yaml, runs ingest_document
for each document, and checks extracted values against expected values
within tolerance.

Usage:
    python3 qa/value_correctness_probe.py
    python3 qa/value_correctness_probe.py --doc NISTMAB-CHAR-001
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is importable
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml")
    sys.exit(1)

logger = logging.getLogger(__name__)

GOLD_STANDARD_PATH = (
    Path(PROJECT_ROOT) / "benchmarks" / "real_documents" / "gold_standard.yaml"
)
DOCS_DIR = Path(PROJECT_ROOT) / "benchmarks" / "real_documents"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FieldCheck:
    """Result of checking one field against its gold-standard value."""
    field_name: str
    expected: Optional[float]
    tolerance: Optional[float]
    actual: Optional[float]
    passed: bool
    reason: str


@dataclass
class DocumentResult:
    """Probe result for one document."""
    doc_id: str
    filename: str
    document_type: str
    elapsed_seconds: float = 0.0

    # Classification
    classification_pass: bool = False
    classification_detail: str = ""

    # Completeness
    completeness_pass: bool = True
    completeness_detail: str = ""

    # Value correctness
    value_checks: List[FieldCheck] = field(default_factory=list)
    value_correctness_passes: int = 0
    value_correctness_fails: int = 0

    # False critical gaps
    false_critical_gaps: List[str] = field(default_factory=list)

    # Required uncertain fields
    uncertain_field_checks: List[FieldCheck] = field(default_factory=list)

    # Required populated fields
    populated_field_checks: List[FieldCheck] = field(default_factory=list)

    # Section coverage
    section_checks: List[str] = field(default_factory=list)  # missing required sections

    # Reference standard
    ref_standard_pass: bool = True
    ref_standard_detail: str = ""

    # Type-specific
    type_specific_checks: List[FieldCheck] = field(default_factory=list)

    # Overall
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: str = ""

    @property
    def all_passed(self) -> bool:
        if self.skipped:
            return False
        if self.error:
            return False
        if not self.classification_pass:
            return False
        if not self.completeness_pass:
            return False
        if self.value_correctness_fails > 0:
            return False
        if self.false_critical_gaps:
            return False
        if any(not c.passed for c in self.uncertain_field_checks):
            return False
        if any(not c.passed for c in self.populated_field_checks):
            return False
        if self.section_checks:
            return False
        if not self.ref_standard_pass:
            return False
        if any(not c.passed for c in self.type_specific_checks):
            return False
        return True


@dataclass
class ProbeReport:
    """Aggregate report from the value correctness probe."""
    documents: List[DocumentResult] = field(default_factory=list)
    total_value_passes: int = 0
    total_value_fails: int = 0
    total_false_critical_gaps: int = 0
    total_docs_passed: int = 0
    total_docs_failed: int = 0
    total_docs_error: int = 0
    total_docs_skipped: int = 0


# ---------------------------------------------------------------------------
# Core probe logic
# ---------------------------------------------------------------------------

def load_gold_standard() -> Dict[str, Any]:
    """Load gold_standard.yaml."""
    with open(GOLD_STANDARD_PATH) as f:
        return yaml.safe_load(f)


def run_probe(target_doc_id: Optional[str] = None) -> ProbeReport:
    """Run the value correctness probe against gold standard documents."""
    gold = load_gold_standard()
    documents = gold.get("documents", [])
    global_conf = gold.get("global", {})

    report = ProbeReport()

    for doc_spec in documents:
        doc_id = doc_spec.get("doc_id", "UNKNOWN")
        if target_doc_id and doc_id != target_doc_id:
            continue

        result = _probe_one_document(doc_spec, global_conf)
        report.documents.append(result)

        report.total_value_passes += result.value_correctness_passes
        report.total_value_fails += result.value_correctness_fails
        report.total_false_critical_gaps += len(result.false_critical_gaps)

        if result.skipped:
            report.total_docs_skipped += 1
        elif result.error:
            report.total_docs_error += 1
        elif result.all_passed:
            report.total_docs_passed += 1
        else:
            report.total_docs_failed += 1

    return report


def _probe_one_document(
    doc_spec: Dict[str, Any],
    global_conf: Dict[str, Any],
) -> DocumentResult:
    """Probe one document against its gold standard."""
    doc_id = doc_spec.get("doc_id", "UNKNOWN")
    filename = doc_spec.get("filename", "")
    doc_type = doc_spec.get("document_type", "UNKNOWN")

    result = DocumentResult(doc_id=doc_id, filename=filename, document_type=doc_type)
    filepath = DOCS_DIR / filename

    if not filepath.exists():
        result.skipped = True
        result.skip_reason = (
            f"Optional benchmark document not present: {filepath}. "
            "Run python benchmarks/real_documents/download.py to enable this probe."
        )
        return result

    start = time.time()
    try:
        from ingestion import ingest_document
        ingestion_result = ingest_document(str(filepath))
    except Exception as e:
        result.error = f"ingest_document failed: {e}"
        result.elapsed_seconds = time.time() - start
        return result

    result.elapsed_seconds = time.time() - start
    ev = ingestion_result.extracted_evidence
    classification = ingestion_result.document_classification

    # --- Classification check ---
    expected_type = doc_spec.get("expected_classification")
    if expected_type:
        actual_type = getattr(classification, "document_type", None)
        actual_conf = getattr(classification, "confidence", 0.0)
        min_conf = doc_spec.get("expected_classification_confidence_min", 0.0)

        type_ok = actual_type == expected_type
        conf_ok = actual_conf >= min_conf
        result.classification_pass = type_ok and conf_ok
        result.classification_detail = (
            f"expected={expected_type} actual={actual_type} "
            f"(conf={actual_conf:.2f}, min={min_conf:.2f})"
        )

    # --- Completeness check ---
    completeness = ev.get("completeness_score")
    if completeness is not None:
        min_c = doc_spec.get("expected_completeness_score_min")
        max_c = doc_spec.get("expected_completeness_score_max")
        if min_c is not None and completeness < min_c:
            result.completeness_pass = False
            result.completeness_detail = f"completeness {completeness:.3f} < min {min_c}"
        elif max_c is not None and completeness > max_c:
            result.completeness_pass = False
            result.completeness_detail = f"completeness {completeness:.3f} > max {max_c}"
        else:
            result.completeness_detail = f"completeness {completeness:.3f} OK"

    # --- Value correctness checks ---
    # Map gold-standard value field names to evidence keys when they differ
    _VALUE_FIELD_MAP = {
        "proposed_shelf_life_months": "proposed_shelf_life",
    }
    expected_values = doc_spec.get("expected_values", {})
    for field_name, spec in expected_values.items():
        expected_val = spec.get("value")
        tolerance = spec.get("tolerance")
        if expected_val is None:
            continue

        ev_key = _VALUE_FIELD_MAP.get(field_name, field_name)
        actual_val = ev.get(ev_key)
        # For three-state fields, also check the dict form
        if actual_val is None:
            base = ev_key.replace("_pct", "")
            if isinstance(ev.get(base), dict):
                actual_val = ev.get(base, {}).get("value")

        check = _check_value(field_name, expected_val, tolerance, actual_val, global_conf)
        result.value_checks.append(check)
        if check.passed:
            result.value_correctness_passes += 1
        else:
            result.value_correctness_fails += 1

    # --- Forbidden critical gaps ---
    critical_gaps = ev.get("critical_gaps", [])
    forbidden_substrings = doc_spec.get("forbidden_critical_gaps_substrings", [])
    for gap in critical_gaps:
        gap_lower = gap.lower()
        for forbidden in forbidden_substrings:
            if forbidden.lower() in gap_lower:
                result.false_critical_gaps.append(f"{gap} (matches '{forbidden}')")
                break

    # --- Required uncertain fields ---
    # Map gold-standard field names to three-state evidence keys
    _UNCERTAIN_FIELD_MAP = {
        "potency_relative_pct": "relative_potency",
        "afucosylation_pct": "afucosylation",
        "hmw_pct": "hmw",
        "main_charge_peak_pct": "main_charge_peak",
    }
    required_uncertain = doc_spec.get("required_uncertain_fields", [])
    for field_name in required_uncertain:
        # Check three-state dict
        base_name = _UNCERTAIN_FIELD_MAP.get(field_name, field_name.replace("_pct", ""))
        three_state = ev.get(base_name, {})
        if isinstance(three_state, dict):
            state = three_state.get("state", "")
            passed = state == "uncertain"
            result.uncertain_field_checks.append(FieldCheck(
                field_name=field_name,
                expected=None, tolerance=None,
                actual=None,
                passed=passed,
                reason=f"state={state}" + (" (expected uncertain)" if not passed else ""),
            ))
        else:
            result.uncertain_field_checks.append(FieldCheck(
                field_name=field_name,
                expected=None, tolerance=None, actual=None,
                passed=False,
                reason=f"No three-state data for {base_name}",
            ))

    # --- Required populated fields ---
    required_populated = doc_spec.get("required_populated_fields", [])
    for field_name in required_populated:
        actual_val = ev.get(field_name)
        passed = actual_val is not None
        result.populated_field_checks.append(FieldCheck(
            field_name=field_name,
            expected=None, tolerance=None,
            actual=actual_val,
            passed=passed,
            reason="" if passed else f"{field_name} is None",
        ))

    # --- Required sections ---
    required_sections = doc_spec.get("required_sections_found", [])
    sections_found = ev.get("sections_found", [])
    for section in required_sections:
        if section not in sections_found:
            result.section_checks.append(section)

    # --- Reference standard ---
    expected_ref = doc_spec.get("expected_reference_standard_identified")
    if expected_ref is not None:
        actual_ref = ev.get("reference_standard_identified", False)
        result.ref_standard_pass = actual_ref == expected_ref
        result.ref_standard_detail = f"expected={expected_ref} actual={actual_ref}"

    # --- Type-specific checks ---
    _check_stability_fields(doc_spec, ev, result)
    _check_analytical_fields(doc_spec, ev, result)

    return result


def _check_value(
    field_name: str,
    expected: float,
    tolerance: Optional[float],
    actual: Optional[float],
    global_conf: Dict[str, Any],
) -> FieldCheck:
    """Check one numeric value against expected with tolerance."""
    if actual is None:
        treat_none = global_conf.get("treat_none_as_failure", True)
        return FieldCheck(
            field_name=field_name,
            expected=expected,
            tolerance=tolerance,
            actual=None,
            passed=not treat_none,
            reason="actual is None (not extracted)",
        )

    if tolerance is None:
        default_pct = global_conf.get("default_numeric_tolerance_pct", 10)
        tolerance = abs(expected) * default_pct / 100.0

    diff = abs(actual - expected)
    passed = diff <= tolerance
    return FieldCheck(
        field_name=field_name,
        expected=expected,
        tolerance=tolerance,
        actual=actual,
        passed=passed,
        reason=f"diff={diff:.4f} {'<=' if passed else '>'} tolerance={tolerance}"
    )


def _check_stability_fields(
    doc_spec: Dict[str, Any],
    ev: Dict[str, Any],
    result: DocumentResult,
) -> None:
    """Check stability-specific gold standard fields."""
    # Required conditions tested
    required_conditions = doc_spec.get("required_conditions_tested", [])
    conditions_found = ev.get("conditions_tested", [])
    for cond in required_conditions:
        found = any(cond.lower() in c.lower() for c in conditions_found)
        result.type_specific_checks.append(FieldCheck(
            field_name=f"condition:{cond}",
            expected=None, tolerance=None, actual=None,
            passed=found,
            reason="" if found else f"Condition '{cond}' not found in {conditions_found}",
        ))

    # OOS events bounds
    oos_max = doc_spec.get("expected_oos_events_max")
    oos_min = doc_spec.get("expected_oos_events_min")
    oos_events = ev.get("oos_events", [])
    oos_count = len(oos_events)
    if oos_max is not None:
        passed = oos_count <= oos_max
        result.type_specific_checks.append(FieldCheck(
            field_name="oos_events_max",
            expected=float(oos_max), tolerance=0,
            actual=float(oos_count),
            passed=passed,
            reason=f"oos_count={oos_count} {'<=' if passed else '>'} max={oos_max}",
        ))
    if oos_min is not None:
        passed = oos_count >= oos_min
        result.type_specific_checks.append(FieldCheck(
            field_name="oos_events_min",
            expected=float(oos_min), tolerance=0,
            actual=float(oos_count),
            passed=passed,
            reason=f"oos_count={oos_count} {'>=' if passed else '<'} min={oos_min}",
        ))


def _check_analytical_fields(
    doc_spec: Dict[str, Any],
    ev: Dict[str, Any],
    result: DocumentResult,
) -> None:
    """Check analytical method-specific gold standard fields."""
    required_min = doc_spec.get("required_validation_studies_min")
    if required_min is not None:
        studies = ev.get("validation_studies_found", [])
        actual_count = len(studies)
        passed = actual_count >= required_min
        result.type_specific_checks.append(FieldCheck(
            field_name="validation_studies_min",
            expected=float(required_min), tolerance=0,
            actual=float(actual_count),
            passed=passed,
            reason=f"found={actual_count} {'>=' if passed else '<'} min={required_min}",
        ))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def print_report(report: ProbeReport) -> None:
    """Pretty-print the probe report."""
    sep = "=" * 76
    print(sep)
    print("  VALUE CORRECTNESS PROBE REPORT")
    print(sep)
    print(f"\n  Documents: {len(report.documents)} "
          f"({report.total_docs_passed} pass, {report.total_docs_failed} fail, "
          f"{report.total_docs_error} error, {report.total_docs_skipped} skipped)")
    print(f"  Value checks: {report.total_value_passes} pass, "
          f"{report.total_value_fails} fail")
    print(f"  False critical gaps: {report.total_false_critical_gaps}")

    for doc in report.documents:
        print(f"\n  {'-'*72}")
        if doc.skipped:
            status = "SKIP"
        elif doc.all_passed:
            status = "PASS"
        else:
            status = "ERROR" if doc.error else "FAIL"
        print(f"  [{status}] {doc.doc_id} ({doc.filename})")
        print(f"  Type: {doc.document_type} | Elapsed: {doc.elapsed_seconds:.1f}s")

        if doc.skipped:
            print(f"  SKIP: {doc.skip_reason}")
            continue

        if doc.error:
            print(f"  ERROR: {doc.error}")
            continue

        # Classification
        tag = "OK" if doc.classification_pass else "FAIL"
        print(f"  Classification: [{tag}] {doc.classification_detail}")

        # Completeness
        if doc.completeness_detail:
            tag = "OK" if doc.completeness_pass else "FAIL"
            print(f"  Completeness: [{tag}] {doc.completeness_detail}")

        # Value checks
        for vc in doc.value_checks:
            tag = "OK" if vc.passed else "FAIL"
            print(f"  Value {vc.field_name}: [{tag}] "
                  f"expected={vc.expected} actual={vc.actual} ({vc.reason})")

        # False critical gaps
        if doc.false_critical_gaps:
            for gap in doc.false_critical_gaps:
                print(f"  FALSE GAP: {gap}")

        # Uncertain field checks
        for uc in doc.uncertain_field_checks:
            tag = "OK" if uc.passed else "FAIL"
            print(f"  Uncertain {uc.field_name}: [{tag}] {uc.reason}")

        # Populated field checks
        for pc in doc.populated_field_checks:
            tag = "OK" if pc.passed else "FAIL"
            print(f"  Populated {pc.field_name}: [{tag}] actual={pc.actual}")

        # Missing sections
        if doc.section_checks:
            print(f"  Missing required sections: {doc.section_checks}")

        # Reference standard
        if doc.ref_standard_detail:
            tag = "OK" if doc.ref_standard_pass else "FAIL"
            print(f"  Ref standard: [{tag}] {doc.ref_standard_detail}")

        # Type-specific
        for tc in doc.type_specific_checks:
            tag = "OK" if tc.passed else "FAIL"
            print(f"  {tc.field_name}: [{tag}] {tc.reason}")

    print(f"\n{sep}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Value Correctness Probe")
    parser.add_argument("--doc", help="Probe a single document by doc_id")
    args = parser.parse_args()

    report = run_probe(target_doc_id=args.doc)
    print_report(report)

    # Exit code: non-zero if any document failed
    sys.exit(1 if report.total_docs_failed > 0 or report.total_docs_error > 0 else 0)


if __name__ == "__main__":
    main()
