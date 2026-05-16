"""
Phase 4A: Cross-Document Consistency Checker.

When multiple documents are ingested for the same case (e.g., characterization +
stability + comparability), this service checks for consistency across documents.

Detects:
1. Value conflicts: same attribute reported differently
2. Reference standard conflicts: different lot numbers
3. Method conflicts: different analytical methods for same attribute
4. Temporal inconsistency: stability data contradicts characterization data

Never raises unhandled exceptions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ingestion.unified_result import UnifiedIngestionResult

logger = logging.getLogger(__name__)


@dataclass
class ConsistencyFlag:
    """A consistency issue detected across two documents."""
    flag_id: str
    severity: str  # 'critical' | 'warning' | 'info'
    description: str
    document_a: str
    document_b: str
    attribute: str
    value_a: Any
    value_b: Any


def check_cross_document_consistency(
    results: List[UnifiedIngestionResult],
) -> List[ConsistencyFlag]:
    """Check for consistency issues across multiple ingested documents.

    Parameters
    ----------
    results : list of UnifiedIngestionResult
        Ingestion results from multiple documents for the same case.

    Returns
    -------
    list of ConsistencyFlag
        All consistency issues found. Empty list if no conflicts.
    """
    try:
        return _check_consistency_impl(results)
    except Exception as e:
        logger.error("Cross-document consistency check failed: %s", e)
        return []


def _check_consistency_impl(
    results: List[UnifiedIngestionResult],
) -> List[ConsistencyFlag]:
    """Internal implementation of cross-document consistency checking."""
    if len(results) < 2:
        return []

    flags: List[ConsistencyFlag] = []
    flag_counter = 0

    def _next_flag_id() -> str:
        nonlocal flag_counter
        flag_counter += 1
        return f"XDOC-{flag_counter:04d}"

    # 1. Value conflicts: same attribute name, different numeric values
    flags.extend(_check_value_conflicts(results, _next_flag_id))

    # 2. Reference standard conflicts: different lot numbers across documents
    flags.extend(_check_reference_standard_conflicts(results, _next_flag_id))

    # 3. Method conflicts: different analytical methods for the same attribute
    flags.extend(_check_method_conflicts(results, _next_flag_id))

    # 4. Temporal inconsistency: stability vs characterization mismatch
    flags.extend(_check_temporal_inconsistency(results, _next_flag_id))

    return flags


# ---------------------------------------------------------------------------
# 1. Value conflict detection
# ---------------------------------------------------------------------------

def _check_value_conflicts(
    results: List[UnifiedIngestionResult],
    next_id,
) -> List[ConsistencyFlag]:
    """Detect cases where the same attribute has different values across documents."""
    flags: List[ConsistencyFlag] = []

    # Build attribute index: normalized_name -> [(doc_source, value, attr)]
    attr_index: Dict[str, List[tuple]] = {}
    for result in results:
        doc_source = _get_doc_source(result)
        for attr in result.attributes:
            norm_name = _normalize_attr_name(attr.name)
            if attr.value is not None and attr.value != 0.0:
                attr_index.setdefault(norm_name, []).append(
                    (doc_source, attr.value, attr)
                )

    # Check for conflicting values across different documents
    for norm_name, entries in attr_index.items():
        # Group by document
        by_doc: Dict[str, List[tuple]] = {}
        for doc_source, value, attr in entries:
            by_doc.setdefault(doc_source, []).append((value, attr))

        if len(by_doc) < 2:
            continue

        # Compare values across documents
        doc_list = list(by_doc.items())
        for i in range(len(doc_list)):
            for j in range(i + 1, len(doc_list)):
                doc_a, vals_a = doc_list[i]
                doc_b, vals_b = doc_list[j]

                for val_a, attr_a in vals_a:
                    for val_b, attr_b in vals_b:
                        # Skip timepoint-specific values (they're expected to differ)
                        if attr_a.timepoint and attr_b.timepoint:
                            if attr_a.timepoint != attr_b.timepoint:
                                continue

                        # Check for meaningful difference
                        if _values_conflict(val_a, val_b):
                            severity = _value_conflict_severity(
                                norm_name, val_a, val_b
                            )
                            flags.append(ConsistencyFlag(
                                flag_id=next_id(),
                                severity=severity,
                                description=(
                                    f"Value conflict for '{attr_a.name}': "
                                    f"{val_a} in {doc_a} vs {val_b} in {doc_b}"
                                ),
                                document_a=doc_a,
                                document_b=doc_b,
                                attribute=attr_a.name,
                                value_a=val_a,
                                value_b=val_b,
                            ))

    return flags


def _values_conflict(val_a: float, val_b: float) -> bool:
    """Determine if two numeric values represent a meaningful conflict.

    Uses a relative tolerance of 5% for values > 1.0 and absolute
    tolerance of 0.1 for values <= 1.0.
    """
    if val_a == val_b:
        return False
    avg = (abs(val_a) + abs(val_b)) / 2.0
    if avg <= 0:
        return val_a != val_b
    if avg <= 1.0:
        return abs(val_a - val_b) > 0.1
    relative_diff = abs(val_a - val_b) / avg
    return relative_diff > 0.05


def _value_conflict_severity(
    norm_name: str, val_a: float, val_b: float
) -> str:
    """Determine severity of a value conflict based on attribute and magnitude."""
    avg = (abs(val_a) + abs(val_b)) / 2.0
    if avg <= 0:
        return "info"
    relative_diff = abs(val_a - val_b) / avg

    # Critical quality attributes get higher severity
    critical_attrs = [
        "hmw", "aggregat", "purity", "potency", "monomer",
    ]
    is_critical = any(kw in norm_name for kw in critical_attrs)

    if relative_diff > 0.20:
        return "critical"
    if relative_diff > 0.10 or is_critical:
        return "warning" if not is_critical else "critical"
    return "info"


# ---------------------------------------------------------------------------
# 2. Reference standard conflict detection
# ---------------------------------------------------------------------------

def _check_reference_standard_conflicts(
    results: List[UnifiedIngestionResult],
    next_id,
) -> List[ConsistencyFlag]:
    """Detect conflicting reference standard lot numbers across documents."""
    flags: List[ConsistencyFlag] = []

    # Extract reference standard lots from evidence
    ref_lots: List[tuple] = []  # (doc_source, lot_number)
    for result in results:
        doc_source = _get_doc_source(result)
        evidence = result.extracted_evidence or {}

        # Check characterization evidence
        lot = evidence.get("reference_standard_lot", "")
        if lot and isinstance(lot, str) and lot.strip():
            ref_lots.append((doc_source, lot.strip()))

        # Also check text for reference standard lot patterns
        if result.parsed_doc:
            text_lot = _extract_ref_lot_from_text(result.parsed_doc)
            if text_lot and text_lot.strip():
                # Avoid duplicate if same lot already found
                if not any(text_lot == existing_lot for _, existing_lot in ref_lots
                          if _ == doc_source):
                    ref_lots.append((doc_source, text_lot.strip()))

    # Compare lots across documents
    if len(ref_lots) < 2:
        return flags

    seen_pairs = set()
    for i in range(len(ref_lots)):
        for j in range(i + 1, len(ref_lots)):
            doc_a, lot_a = ref_lots[i]
            doc_b, lot_b = ref_lots[j]
            if doc_a == doc_b:
                continue
            pair_key = tuple(sorted([doc_a, doc_b]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            if lot_a.upper() != lot_b.upper():
                flags.append(ConsistencyFlag(
                    flag_id=next_id(),
                    severity="warning",
                    description=(
                        f"Reference standard lot conflict: "
                        f"'{lot_a}' in {doc_a} vs '{lot_b}' in {doc_b}"
                    ),
                    document_a=doc_a,
                    document_b=doc_b,
                    attribute="reference_standard_lot",
                    value_a=lot_a,
                    value_b=lot_b,
                ))

    return flags


_REF_LOT_PATTERNS = [
    r"\breference\s+standard\s+(?:lot\s*(?:#|number)?|batch)\s*[:=]?\s*([A-Z0-9][\w\-]+)",
    r"\blot\s*(?:#|number)?\s*[:=]?\s*([A-Z0-9][\w\-]+)\s*\(?\s*reference\s+standard",
    r"\breference\s+standard\b.*?\blot\s*[:=]?\s*([A-Z0-9][\w\-]+)",
]


def _extract_ref_lot_from_text(parsed_doc: Dict[str, Any]) -> Optional[str]:
    """Extract reference standard lot from parsed document text."""
    text = _gather_all_text(parsed_doc)
    for pattern in _REF_LOT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# 3. Method conflict detection
# ---------------------------------------------------------------------------

def _check_method_conflicts(
    results: List[UnifiedIngestionResult],
    next_id,
) -> List[ConsistencyFlag]:
    """Detect cases where different methods are reported for the same attribute."""
    flags: List[ConsistencyFlag] = []

    # Build method index: normalized_name -> [(doc_source, method)]
    method_index: Dict[str, List[tuple]] = {}
    for result in results:
        doc_source = _get_doc_source(result)
        for attr in result.attributes:
            norm_name = _normalize_attr_name(attr.name)
            method = _extract_method_from_context(attr.context)
            if method:
                method_index.setdefault(norm_name, []).append(
                    (doc_source, method, attr.name)
                )

    # Check for conflicting methods across documents
    for norm_name, entries in method_index.items():
        by_doc: Dict[str, List[tuple]] = {}
        for doc_source, method, orig_name in entries:
            by_doc.setdefault(doc_source, []).append((method, orig_name))

        if len(by_doc) < 2:
            continue

        doc_list = list(by_doc.items())
        for i in range(len(doc_list)):
            for j in range(i + 1, len(doc_list)):
                doc_a, methods_a = doc_list[i]
                doc_b, methods_b = doc_list[j]

                methods_set_a = {m.lower() for m, _ in methods_a}
                methods_set_b = {m.lower() for m, _ in methods_b}

                if methods_set_a and methods_set_b and not methods_set_a.intersection(methods_set_b):
                    orig_name = methods_a[0][1]
                    flags.append(ConsistencyFlag(
                        flag_id=next_id(),
                        severity="warning",
                        description=(
                            f"Method conflict for '{orig_name}': "
                            f"{methods_set_a} in {doc_a} vs {methods_set_b} in {doc_b}"
                        ),
                        document_a=doc_a,
                        document_b=doc_b,
                        attribute=orig_name,
                        value_a=list(methods_set_a),
                        value_b=list(methods_set_b),
                    ))

    return flags


# ---------------------------------------------------------------------------
# 4. Temporal inconsistency detection
# ---------------------------------------------------------------------------

def _check_temporal_inconsistency(
    results: List[UnifiedIngestionResult],
    next_id,
) -> List[ConsistencyFlag]:
    """Detect temporal inconsistencies between stability and characterization data.

    For example, if a characterization report reports HMW% = 1.2 but the stability
    T=0 value is 2.5%, that's a temporal inconsistency.
    """
    flags: List[ConsistencyFlag] = []

    # Separate characterization vs stability results
    char_results = []
    stab_results = []
    for result in results:
        doc_type = "UNKNOWN"
        if result.document_classification:
            doc_type = result.document_classification.document_type
        if doc_type == "CHARACTERIZATION":
            char_results.append(result)
        elif doc_type == "STABILITY":
            stab_results.append(result)

    if not char_results or not stab_results:
        return flags

    # Build characterization value index
    char_values: Dict[str, List[tuple]] = {}  # norm_name -> [(doc, value)]
    for result in char_results:
        doc_source = _get_doc_source(result)
        for attr in result.attributes:
            if attr.value is not None and attr.value != 0.0:
                norm_name = _normalize_attr_name(attr.name)
                char_values.setdefault(norm_name, []).append(
                    (doc_source, attr.value, attr.name)
                )

        # Also use evidence-level values
        evidence = result.extracted_evidence or {}
        for key, norm in [
            ("hmw_pct", "hmw"),
            ("potency_relative_pct", "potency"),
            ("main_charge_peak_pct", "main charge peak"),
        ]:
            val = evidence.get(key)
            if val is not None:
                char_values.setdefault(norm, []).append(
                    (doc_source, val, key)
                )

    # Build stability T=0 value index
    stab_t0_values: Dict[str, List[tuple]] = {}  # norm_name -> [(doc, value)]
    for result in stab_results:
        doc_source = _get_doc_source(result)
        for attr in result.attributes:
            if attr.value is not None and attr.value != 0.0:
                # Only consider T=0 / initial values
                tp = (attr.timepoint or "").lower().strip()
                if tp in ("t=0", "t0", "initial", "0m", "0 m", "0"):
                    norm_name = _normalize_attr_name(attr.name)
                    stab_t0_values.setdefault(norm_name, []).append(
                        (doc_source, attr.value, attr.name)
                    )

    # Compare characterization values vs stability T=0 values
    for norm_name in set(char_values.keys()) & set(stab_t0_values.keys()):
        for doc_a, val_a, name_a in char_values[norm_name]:
            for doc_b, val_b, name_b in stab_t0_values[norm_name]:
                if _values_conflict(val_a, val_b):
                    flags.append(ConsistencyFlag(
                        flag_id=next_id(),
                        severity="critical",
                        description=(
                            f"Temporal inconsistency: characterization reports "
                            f"'{name_a}' = {val_a} but stability T=0 shows "
                            f"'{name_b}' = {val_b}"
                        ),
                        document_a=doc_a,
                        document_b=doc_b,
                        attribute=name_a,
                        value_a=val_a,
                        value_b=val_b,
                    ))

    return flags


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_doc_source(result: UnifiedIngestionResult) -> str:
    """Get a document source identifier from a result."""
    if result.parsed_doc:
        path = result.parsed_doc.get("document_path", "")
        if path:
            return path
    if result.document_classification:
        return f"doc_{result.document_classification.document_type}"
    return "unknown_doc"


def _normalize_attr_name(name: str) -> str:
    """Normalize an attribute name for comparison."""
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9\s]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name


def _extract_method_from_context(context: str) -> Optional[str]:
    """Extract a method name from attribute context string."""
    if not context:
        return None
    m = re.search(r'\(method:\s*([^)]+)\)', context, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _gather_all_text(parsed_doc: Dict[str, Any]) -> str:
    """Combine all text from a parsed document."""
    parts = []
    for page in parsed_doc.get("pages", []):
        text = page.get("text", "")
        if text:
            parts.append(text)
    for para in parsed_doc.get("paragraphs", []):
        text = para.get("text", "")
        if text:
            parts.append(text)
    return "\n".join(parts)
