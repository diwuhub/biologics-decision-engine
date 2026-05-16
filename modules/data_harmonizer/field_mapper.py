"""
Field Mapper — column name synonym resolution with confidence scoring.

Maps raw/messy column headers from analytical data tables to canonical
field names using a three-tier strategy: direct match, synonym match,
and fuzzy token-overlap match.

Extracted from bio-cmc-ai-suite/cmc-harmonizer (archived 2026-03-25).
Stripped of Streamlit/SDK dependencies for use as shared infrastructure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Canonical field vocabulary: direct match forms (Tier 1)
# ---------------------------------------------------------------------------
_DIRECT_MAPS: dict[str, list[str]] = {
    "sample_id": [
        "sample id", "sample", "sample no", "sample number",
    ],
    "batch_id": [
        "batch id", "batch", "batch no", "batch number",
        "lot", "lot no", "lot number", "lot id",
    ],
    "test_name": [
        "test name", "test", "parameter", "quality attribute", "attribute",
    ],
    "method_reference": [
        "method", "method reference", "method ref", "analytical method",
        "test method",
    ],
    "acceptance_criteria": [
        "acceptance criteria", "acceptance criterion", "specification",
        "spec", "limit", "acceptance limit",
    ],
    "result_value": [
        "result", "result value", "value", "observed", "observed value",
        "measured value", "test result",
    ],
    "result_unit": [
        "unit", "units", "unit of measure", "uom",
    ],
    "comments": [
        "comments", "notes", "remarks", "observations", "comment",
    ],
    "date": [
        "date", "test date", "analysis date", "report date",
    ],
    "analyst": [
        "analyst", "tested by", "analyst name",
    ],
    "instrument": [
        "instrument", "instrument id", "equipment",
    ],
    "storage_condition": [
        "condition", "storage condition", "storage",
    ],
    "time_point": [
        "time point", "time", "timepoint", "month", "months",
    ],
    "conforms": [
        "conforms", "pass/fail", "result status", "complies",
    ],
    # Biologics-specific analytical fields
    "hic_rt": [
        "hic retention time", "hic rt", "hic retention",
    ],
    "sec_main_peak": [
        "sec main peak", "se-hplc main peak", "sec purity", "monomer",
    ],
    "cex_main_peak": [
        "cex main peak", "cex main", "charge variant main",
    ],
    "ce_sds_main": [
        "ce-sds main band", "ce sds main", "ce-sds purity",
    ],
    "potency": [
        "potency", "relative potency", "bioassay",
    ],
}

# ---------------------------------------------------------------------------
# Synonym table (Tier 2): canonical -> [(synonym, confidence)]
# ---------------------------------------------------------------------------
_SYNONYMS: dict[str, list[tuple[str, float]]] = {
    "test_name": [
        ("assay", 0.92),
        ("analytical test", 0.90),
        ("characteristic", 0.70),
        ("quality parameter", 0.70),
    ],
    "method_reference": [
        ("sop", 0.90),
        ("sop number", 0.90),
        ("procedure", 0.70),
        ("compendial method", 0.90),
        ("analytical procedure", 0.75),
    ],
    "acceptance_criteria": [
        ("release specification", 0.90),
        ("release limit", 0.90),
        ("target", 0.65),
        ("range", 0.65),
    ],
    "result_value": [
        ("data", 0.45),
        ("finding", 0.45),
        ("measurement", 0.70),
    ],
    "batch_id": [
        ("material lot", 0.70),
        ("batch/lot", 0.90),
        ("production batch", 0.70),
    ],
    "sample_id": [
        ("specimen", 0.70),
        ("aliquot", 0.70),
        ("sample identification", 0.90),
    ],
    "storage_condition": [
        ("temperature/humidity", 0.90),
        ("storage temperature", 0.90),
    ],
    "time_point": [
        ("study interval", 0.70),
        ("pull point", 0.70),
        ("stability time", 0.70),
    ],
    "comments": [
        ("footnote", 0.70),
        ("additional information", 0.70),
    ],
    "hic_rt": [
        ("hic retention time", 0.95),
        ("hydrophobic interaction rt", 0.85),
    ],
    "sec_main_peak": [
        ("size exclusion main", 0.85),
        ("monomer peak", 0.80),
    ],
    "potency": [
        ("biological activity", 0.80),
        ("cell-based potency", 0.85),
    ],
}

# Fields to ignore (structural artifacts)
_IGNORE_PATTERNS = [
    r"^#$", r"^row\s*number$", r"^index$", r"^$", r"^no\.?$",
    r"^s\.?\s*no\.?$", r"^sr\.?\s*no\.?$",
]


@dataclass
class FieldMapping:
    """Result of mapping a raw column name to a canonical name."""
    raw_name: str
    canonical_name: Optional[str]
    confidence: float
    qualifier: str        # "high", "medium", "low", "unknown"
    basis: str            # "direct", "synonym", "fuzzy", "unmapped"
    embedded_unit: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize_header(header: str) -> str:
    """Normalize a header: lowercase, strip, remove parenthetical units."""
    h = header.strip().lower()
    h = re.sub(r"[.:;]+$", "", h)
    # Remove parenthetical unit annotations (and optional trailing footnote letter)
    h = re.sub(r"\s*\([^)]*\)\s*[a-z]?$", "", h)
    # Remove standalone trailing footnote markers (single letter after space)
    h = re.sub(r"\s+[a-z]$", "", h)
    # Collapse whitespace
    h = re.sub(r"\s+", " ", h).strip()
    return h


def _extract_embedded_unit(header: str) -> str | None:
    """Extract a unit embedded in parentheses, e.g. 'Conc (mg/mL)' -> 'mg/mL'."""
    m = re.search(r"\(([^)]+)\)", header)
    if m:
        candidate = m.group(1).strip()
        if any(c in candidate for c in ("/", "%")) or candidate.lower() in (
            "mg", "ml", "g", "l", "eu", "cfu", "iu", "da", "kda",
            "min", "hr", "s",
        ):
            return candidate
    return None


def _is_structural(header: str) -> bool:
    """Check if a header is a structural artifact to be ignored."""
    h = header.strip().lower()
    return any(re.match(p, h) for p in _IGNORE_PATTERNS)


def _try_direct(normalized: str) -> tuple[str | None, float]:
    """Tier 1: Direct match."""
    for canonical, forms in _DIRECT_MAPS.items():
        if normalized in forms:
            return canonical, 0.95
    return None, 0.0


def _try_synonym(normalized: str) -> tuple[str | None, float]:
    """Tier 2: Synonym match."""
    best_canonical = None
    best_score = 0.0
    for canonical, synonyms in _SYNONYMS.items():
        for syn, score in synonyms:
            if normalized == syn.lower():
                if score > best_score:
                    best_canonical = canonical
                    best_score = score
    return best_canonical, best_score


def _try_fuzzy(normalized: str) -> tuple[str | None, float]:
    """Tier 3: Token-overlap fuzzy match (word overlap as proxy)."""
    norm_tokens = set(normalized.split())
    if not norm_tokens:
        return None, 0.0

    best_canonical = None
    best_score = 0.0

    for canonical, forms in _DIRECT_MAPS.items():
        for form in forms:
            form_tokens = set(form.split())
            if not form_tokens:
                continue
            overlap = norm_tokens & form_tokens
            score = len(overlap) / max(len(norm_tokens), len(form_tokens))
            if score > best_score and score >= 0.40:
                best_score = score
                best_canonical = canonical

    for canonical, synonyms in _SYNONYMS.items():
        for syn, _conf in synonyms:
            form_tokens = set(syn.lower().split())
            if not form_tokens:
                continue
            overlap = norm_tokens & form_tokens
            score = len(overlap) / max(len(norm_tokens), len(form_tokens))
            if score > best_score and score >= 0.40:
                best_score = score
                best_canonical = canonical

    return best_canonical, round(best_score * 0.80, 2)  # Discount fuzzy


def map_field(raw_name: str) -> FieldMapping:
    """Map a single raw column/field name to its canonical name.

    Examples:
        >>> map_field("HIC Retention Time (Min)a")
        FieldMapping(raw_name='HIC Retention Time (Min)a',
                     canonical_name='hic_rt', confidence=0.95, ...)

        >>> map_field("Batch Number")
        FieldMapping(raw_name='Batch Number', canonical_name='batch_id',
                     confidence=0.95, ...)

        >>> map_field("xyzzy_gobbledygook")
        FieldMapping(raw_name='xyzzy_gobbledygook', canonical_name=None,
                     confidence=0.0, ...)
    """
    if _is_structural(raw_name):
        return FieldMapping(
            raw_name=raw_name, canonical_name=None, confidence=0.0,
            qualifier="unknown", basis="structural",
        )

    normalized = _normalize_header(raw_name)
    embedded_unit = _extract_embedded_unit(raw_name)

    # Tier 1: Direct
    canonical, score = _try_direct(normalized)
    if canonical:
        return FieldMapping(
            raw_name=raw_name, canonical_name=canonical, confidence=score,
            qualifier="high", basis="direct", embedded_unit=embedded_unit,
        )

    # Tier 2: Synonym
    canonical, score = _try_synonym(normalized)
    if canonical and score >= 0.40:
        qualifier = "high" if score >= 0.85 else "medium" if score >= 0.65 else "low"
        return FieldMapping(
            raw_name=raw_name, canonical_name=canonical, confidence=score,
            qualifier=qualifier, basis="synonym", embedded_unit=embedded_unit,
        )

    # Tier 3: Fuzzy
    canonical, score = _try_fuzzy(normalized)
    if canonical and score >= 0.40:
        qualifier = "medium" if score >= 0.65 else "low"
        return FieldMapping(
            raw_name=raw_name, canonical_name=canonical, confidence=round(score, 2),
            qualifier=qualifier, basis="fuzzy", embedded_unit=embedded_unit,
        )

    # Unmapped
    return FieldMapping(
        raw_name=raw_name, canonical_name=None, confidence=0.0,
        qualifier="unknown", basis="unmapped", embedded_unit=embedded_unit,
    )


def map_fields(raw_names: list[str]) -> list[FieldMapping]:
    """Map a list of raw column names. Convenience batch wrapper."""
    return [map_field(name) for name in raw_names]
