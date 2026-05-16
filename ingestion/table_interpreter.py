"""
Table Interpreter — Normalizes diverse table layouts into a standard form.

Real-world regulatory PDFs use three distinct table layouts:
1. Attribute-column: headers contain 'Attribute', 'Value', 'Unit' etc.
2. Pivoted: attribute names in first column, lot/sample IDs in column headers.
3. Replicate-value: cells contain multiple measurements separated by newlines.

This module detects the layout and returns normalized (name, value, unit, lot_id)
tuples regardless of the original format.

Used by CharacterizationExtractor and other type-specific extractors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import mean, stdev
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Normalized output
# ---------------------------------------------------------------------------

@dataclass
class NormalizedAttribute:
    """A single attribute-value pair normalized from any table layout."""
    name: str
    value: float
    unit: str = ""
    lot_id: Optional[str] = None
    lot_preference: str = "unlabeled"  # 'RM' | 'PS' | 'unlabeled'
    n_replicates: int = 1
    value_cv_pct: Optional[float] = None  # CV% when aggregated from replicates
    all_replicates: List[float] = field(default_factory=list)
    source_table_id: str = ""
    source_page: int = 0


# ---------------------------------------------------------------------------
# Inverse attribute derivation
# ---------------------------------------------------------------------------

INVERSE_ATTRIBUTES: Dict[str, Tuple[str, Any]] = {
    # HMW% derivation from monomer purity
    "monomeric purity": ("hmw_pct", lambda mp: 100.0 - mp),
    "monomer %": ("hmw_pct", lambda mp: 100.0 - mp),
    "monomer purity": ("hmw_pct", lambda mp: 100.0 - mp),
    "% monomer": ("hmw_pct", lambda mp: 100.0 - mp),
    # Main charge peak derivation
    "main peak": ("charge_variant_main_pct", lambda v: v),
    "main component": ("charge_variant_main_pct", lambda v: v),
    "main peak %": ("charge_variant_main_pct", lambda v: v),
    "main charge group": ("charge_variant_main_pct", lambda v: v),
    "charge purity": ("charge_variant_main_pct", lambda v: v),
    "main species": ("charge_variant_main_pct", lambda v: v),
    "m1": ("charge_variant_main_pct", lambda v: v),
    # Afucosylation derivation from fucose %
    "fucose": ("afucosylation_pct", lambda f: 100.0 - f),
    "fucosylated": ("afucosylation_pct", lambda f: 100.0 - f),
    "fucosylation": ("afucosylation_pct", lambda f: 100.0 - f),
    "% fucosylation": ("afucosylation_pct", lambda f: 100.0 - f),
    "% fucose": ("afucosylation_pct", lambda f: 100.0 - f),
    # Direct afucosylation mapping
    "afucosylation": ("afucosylation_pct", lambda v: v),
    "afucosylated": ("afucosylation_pct", lambda v: v),
    "afucose": ("afucosylation_pct", lambda v: v),
    "non-fucosylated": ("afucosylation_pct", lambda v: v),
}


# ---------------------------------------------------------------------------
# CQA keyword gate — filter out non-CQA table rows
# ---------------------------------------------------------------------------

_CQA_KEYWORDS = frozenset([
    "purity", "potency", "mass", "weight", "content", "charge", "variant",
    "concentration", "ph", "polysorbate", "aggregate", "hmw", "fragment",
    "glycan", "fucose", "sialylation", "galactose", "deamidation",
    "oxidation", "misfold", "disulfide", "free thiol", "titre", "titer",
    "monomer", "dimer", "trimer", "acidic", "basic", "main peak",
    "main charge", "main component", "endotoxin", "bioburden",
    "sub-visible", "subvisible", "visible particles", "osmolality",
    "viscosity", "turbidity", "color", "appearance", "identity",
    "sterility", "moisture", "water content", "host cell", "hcp",
    "residual dna", "protein a", "leachable", "extractable",
    "binding", "affinity", "kd", "kon", "koff", "ec50", "ic50",
    "adcc", "cdc", "relative potency", "biological activity",
    "isoelectric", "molecular weight", "intact mass",
    "peptide map", "sequence coverage", "glycosylation",
    "n-glycan", "o-glycan", "g0f", "g1f", "g2f", "man5",
    "afucosylation", "afucosylated", "fucosylated",
    "cd spectrum", "dsc", "ftir", "secondary structure",
    "thermal stability", "melting temperature", "tm",
    "sec-hplc", "ce-sds", "rce-sds", "rp-hplc", "hic",
    "cex", "icief", "auc", "dls", "mals",
    "recovery", "yield", "throughput",
    "shelf life", "stability", "degradation",
])


def _looks_like_attribute(name: str) -> bool:
    """Check if a cell value looks like a CQA attribute name."""
    if not name or len(name.strip()) < 3:
        return False
    name_clean = name.strip()
    # Reject pure numeric
    if re.match(r"^[\d.\s,\-+]+$", name_clean):
        return False
    # Reject known non-attribute labels
    if name_clean.lower().strip() in {
        "rack", "row", "sample", "replicate", "run", "n/a", "na", "nd",
        "date", "analyst", "method", "instrument", "column", "batch",
        "lot", "vial", "position", "injection", "sequence",
    }:
        return False
    # Must contain at least one CQA-related word
    name_lower = name_clean.lower()
    return any(kw in name_lower for kw in _CQA_KEYWORDS)


# ---------------------------------------------------------------------------
# Lot column classification
# ---------------------------------------------------------------------------

def classify_lot_header(header: str) -> str:
    """Classify a lot column header as 'RM', 'PS', or 'unlabeled'.

    PS (Primary Sample): bare 3-5 digit numbers (e.g., '8670')
    RM (Reference Material): dash-separated lot IDs (e.g., '14HB-D-001')
    """
    h = header.strip()
    if not h or h.startswith("col_"):
        return "unlabeled"
    # Explicit prefixes
    if re.match(r"^(?:PS|primary\s+sample)\b", h, re.IGNORECASE):
        return "PS"
    if re.match(r"^(?:RM|reference\s+material)\b", h, re.IGNORECASE):
        return "RM"
    # Bare 3-5 digit number → PS
    if re.match(r"^\d{3,5}$", h):
        return "PS"
    # Lot ID with dashes containing both letters and digits → RM
    if re.match(r"^[A-Za-z0-9]+-[A-Za-z0-9]", h) and re.search(r"[A-Za-z]", h) and re.search(r"\d", h):
        return "RM"
    # "Lot" header (first column label)
    if h.lower() == "lot":
        return "unlabeled"
    return "unlabeled"


# ---------------------------------------------------------------------------
# Layout detection
# ---------------------------------------------------------------------------

def detect_layout(headers: List[str], rows: List[Any]) -> str:
    """Detect the table layout: attribute_column, pivoted, or unknown.

    Returns one of: 'attribute_column', 'pivoted', 'unknown'
    """
    if not headers:
        return "unknown"

    headers_lower = [h.lower().strip() for h in headers]

    # Check for attribute-column layout: headers contain keywords
    attr_kws = {"attribute", "parameter", "test", "assay", "analyte",
                "quality attribute", "name", "method", "analysis",
                "property", "characteristic", "measure", "specification"}
    value_kws = {"value", "result", "measured", "mean", "average",
                 "observed", "actual", "data"}

    has_attr_header = any(any(kw in h for kw in attr_kws) for h in headers_lower)
    has_value_header = any(any(kw in h for kw in value_kws) for h in headers_lower)

    if has_attr_header and has_value_header:
        return "attribute_column"
    if has_attr_header:
        return "attribute_column"

    # Check for pivoted layout: first column of data rows contains attribute names
    if rows and len(headers) >= 2:
        first_col_attr_count = 0
        check_rows = rows[:min(10, len(rows))]
        for row in check_rows:
            first_val = _get_first_cell(row, headers)
            if first_val and _looks_like_attribute(first_val):
                first_col_attr_count += 1
        if first_col_attr_count >= 1:
            return "pivoted"

    return "unknown"


def _get_first_cell(row: Any, headers: List[str]) -> Optional[str]:
    """Get the first cell value from a row."""
    if isinstance(row, dict):
        if headers:
            return str(row.get(headers[0], ""))
        vals = list(row.values())
        return str(vals[0]) if vals else None
    elif isinstance(row, list) and row:
        return str(row[0])
    return None


# ---------------------------------------------------------------------------
# Replicate value parsing
# ---------------------------------------------------------------------------

def parse_replicate_values(cell_text: str) -> List[float]:
    """Parse replicate values from a cell: '98.748\\n98.744\\n98.740' -> [98.748, 98.744, 98.740]."""
    if not cell_text:
        return []
    candidates = re.split(r"[\n;,]+", str(cell_text))
    values = []
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        # Remove common prefixes/suffixes
        cleaned = re.sub(r"[%<>≤≥~±]", "", c).strip()
        if cleaned.upper() in ("N/A", "NA", "ND", "NT", "-", ""):
            continue
        try:
            values.append(float(cleaned))
        except ValueError:
            continue
    return values


def aggregate_replicates(values: List[float]) -> Tuple[float, Optional[float], int]:
    """Aggregate replicate values into (mean, cv_pct, n).

    Returns (mean_value, cv_percent_or_None, count).
    """
    if not values:
        return (0.0, None, 0)
    if len(values) == 1:
        return (values[0], None, 1)
    m = mean(values)
    cv = (stdev(values) / m * 100) if m != 0 else None
    return (m, cv, len(values))


# ---------------------------------------------------------------------------
# Unit extraction from attribute names
# ---------------------------------------------------------------------------

def extract_unit_from_name(name: str) -> str:
    """Extract unit from an attribute name like 'Monomeric Purity (%)' -> '%'."""
    m = re.search(r"\(([^)]+)\)\s*$", name)
    if m:
        return m.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Main interpretation functions
# ---------------------------------------------------------------------------

def interpret_table(
    table: Dict[str, Any],
    page_number: int = 0,
) -> List[NormalizedAttribute]:
    """Interpret a raw table dict into normalized attributes.

    Parameters
    ----------
    table : dict
        Raw table from pdfplumber with 'headers', 'rows', and optional 'id'.
    page_number : int
        Source page number for traceability.

    Returns
    -------
    list[NormalizedAttribute]
    """
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    table_id = table.get("id", "unknown_table")

    if not headers or not rows:
        return []

    layout = detect_layout(headers, rows)

    if layout == "attribute_column":
        return _interpret_attribute_column(headers, rows, table_id, page_number)
    elif layout == "pivoted":
        return _interpret_pivoted(headers, rows, table_id, page_number)
    else:
        return []


def _interpret_attribute_column(
    headers: List[str],
    rows: List[Any],
    table_id: str,
    page_number: int,
) -> List[NormalizedAttribute]:
    """Interpret standard attribute-column layout."""
    results = []
    headers_lower = [h.lower().strip() for h in headers]

    # Find column indices
    name_idx = _find_column(headers_lower, ["attribute", "parameter", "test", "assay",
                                             "analyte", "quality attribute", "name",
                                             "method", "analysis", "property"])
    value_idx = _find_column(headers_lower, ["value", "result", "measured", "mean",
                                              "average", "observed", "actual", "data"])
    unit_idx = _find_column(headers_lower, ["unit", "uom", "units"])

    if name_idx is None:
        return []

    for row in rows:
        name = _get_cell(row, headers, name_idx)
        if not name or not name.strip():
            continue

        raw_value = _get_cell(row, headers, value_idx) if value_idx is not None else None
        unit = _get_cell(row, headers, unit_idx) if unit_idx is not None else ""
        unit = unit or extract_unit_from_name(name)

        replicates = parse_replicate_values(raw_value) if raw_value else []
        if not replicates:
            continue

        avg, cv, n = aggregate_replicates(replicates)

        results.append(NormalizedAttribute(
            name=name.strip(),
            value=avg,
            unit=unit.strip() if unit else "",
            n_replicates=n,
            value_cv_pct=cv,
            all_replicates=replicates,
            source_table_id=table_id,
            source_page=page_number,
        ))

    return results


def _interpret_pivoted(
    headers: List[str],
    rows: List[Any],
    table_id: str,
    page_number: int,
) -> List[NormalizedAttribute]:
    """Interpret pivoted layout: attribute names in first column, lot IDs in headers."""
    results = []

    # Pre-classify lot columns
    lot_classes = [classify_lot_header(h) for h in headers]

    for row in rows:
        # First cell is attribute name
        if isinstance(row, dict):
            cells = list(row.values())
        elif isinstance(row, list):
            cells = row
        else:
            continue

        if not cells:
            continue

        attr_name = str(cells[0]).strip()
        if not _looks_like_attribute(attr_name):
            continue

        unit = extract_unit_from_name(attr_name)
        # Clean name (remove unit suffix)
        clean_name = re.sub(r"\s*\([^)]+\)\s*$", "", attr_name).strip()

        # Iterate over value columns (skip first which is the name)
        for col_idx, cell in enumerate(cells[1:], start=1):
            lot_id = headers[col_idx] if col_idx < len(headers) else None
            lot_pref = lot_classes[col_idx] if col_idx < len(lot_classes) else "unlabeled"

            replicates = parse_replicate_values(str(cell))
            if not replicates:
                continue

            avg, cv, n = aggregate_replicates(replicates)

            results.append(NormalizedAttribute(
                name=clean_name,
                value=avg,
                unit=unit,
                lot_id=lot_id,
                lot_preference=lot_pref,
                n_replicates=n,
                value_cv_pct=cv,
                all_replicates=replicates,
                source_table_id=table_id,
                source_page=page_number,
            ))

    return results


def derive_inverse_attributes(
    attributes: List[NormalizedAttribute],
) -> Dict[str, float]:
    """Derive inverse attributes (e.g., monomeric purity -> HMW%).

    Returns a dict of derived field names to values.
    """
    derived = {}
    for attr in attributes:
        name_lower = attr.name.lower().strip()
        for pattern, (derived_name, transform) in INVERSE_ATTRIBUTES.items():
            if pattern in name_lower:
                try:
                    derived_val = transform(attr.value)
                    if derived_name not in derived:
                        derived[derived_name] = derived_val
                except (ValueError, TypeError):
                    pass
    return derived


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_column(headers_lower: List[str], keywords: List[str]) -> Optional[int]:
    """Find a column index by keyword match."""
    for i, h in enumerate(headers_lower):
        for kw in keywords:
            if kw in h:
                return i
    return None


def _get_cell(row: Any, headers: List[str], idx: int) -> Optional[str]:
    """Get cell value from a row dict or list by column index."""
    if isinstance(row, dict):
        if idx < len(headers):
            return str(row.get(headers[idx], ""))
        return None
    elif isinstance(row, list):
        if idx < len(row):
            return str(row[idx])
        return None
    return None
