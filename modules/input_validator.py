"""
Input Validator — SP v5 Schema Gate.

Validates comparability assessment input against the SP v5 tightened schema.
Rejects or warns on missing/invalid fields before the pipeline runs.

SP v5 Section 2.2 defines:
  Top-level: product_name, molecule_class, modality, reference_product,
             change_description, attributes[]
  Per-attribute: name, category, pre_value, post_value, unit,
                 n_lots, cv_pct, n_methods,
                 functional_support_level (none/weak/indirect/direct),
                 orthogonal_coverage (none/partial/strong)

Removed from user input (SP v5 decision): prior_approvals
  → This is now derived context from Layer 2 Evidence Services.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# =========================================================================
# Valid Values (SP v5 Section 2.2)
# =========================================================================

VALID_MOLECULE_CLASSES = {"mAb", "bispecific", "ADC", "fusion_protein", "enzyme", "peptide", "other"}
VALID_MODALITIES = {"IV", "SC", "IM", "topical", "intravitreal", "other"}
VALID_CATEGORIES = {"identity", "purity", "potency", "safety", "stability", "physicochemical"}
VALID_FUNCTIONAL_SUPPORT = {"none", "weak", "indirect", "direct"}
VALID_ORTHOGONAL_COVERAGE = {"none", "partial", "strong"}


@dataclass
class ValidationResult:
    """Result of input validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)     # hard failures — pipeline should not run
    warnings: List[str] = field(default_factory=list)    # soft issues — pipeline runs but flags
    normalized_input: Optional[Dict[str, Any]] = None    # cleaned input if valid


def validate_comparability_input(data: Dict[str, Any]) -> ValidationResult:
    """Validate input against SP v5 schema.

    Parameters
    ----------
    data : dict
        Raw input dict. Expected keys: product_name, molecule_class, modality,
        reference_product, change_description, attributes.

    Returns
    -------
    ValidationResult
        .valid is True only if zero errors. Warnings are informational.
    """
    errors = []
    warnings = []

    # --- Top-level required fields ---
    if not isinstance(data, dict):
        return ValidationResult(valid=False, errors=["Input must be a dict"])

    product_name = data.get("product_name", "")
    if not product_name or not isinstance(product_name, str):
        warnings.append("Missing or empty 'product_name'. SP v5 requires this field.")
        product_name = product_name if isinstance(product_name, str) else "Product"

    molecule_class = data.get("molecule_class", "")
    if not molecule_class:
        warnings.append("Missing 'molecule_class' — defaulting to 'mAb'. SP v5 requires this field.")
        molecule_class = "mAb"
    elif molecule_class not in VALID_MOLECULE_CLASSES:
        warnings.append(f"molecule_class '{molecule_class}' not in standard set {VALID_MOLECULE_CLASSES}. Proceeding but flagging.")

    modality = data.get("modality", "")
    if not modality:
        warnings.append("Missing 'modality' — defaulting to 'IV'. SP v5 requires this field.")
        modality = "IV"
    elif modality not in VALID_MODALITIES:
        warnings.append(f"modality '{modality}' not in standard set {VALID_MODALITIES}. Proceeding but flagging.")

    reference_product = data.get("reference_product", "")
    if not reference_product:
        warnings.append("Missing 'reference_product'. For biosimilar assessments this is required.")

    # --- prior_approvals check (SP v5: removed from user input) ---
    if "prior_approvals" in data:
        warnings.append("'prior_approvals' found in top-level input. SP v5 removes this from user input — it is derived from Layer 2. Ignoring.")

    # --- Attributes ---
    attributes = data.get("attributes", [])
    if not isinstance(attributes, list) or len(attributes) == 0:
        errors.append("'attributes' must be a non-empty list")
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    validated_attrs = []
    for i, attr in enumerate(attributes):
        attr_errors, attr_warnings, clean_attr = _validate_attribute(attr, i)
        errors.extend(attr_errors)
        warnings.extend(attr_warnings)
        if clean_attr:
            validated_attrs.append(clean_attr)

        # Check for prior_approvals at attribute level too
        if isinstance(attr, dict) and "prior_approvals" in attr:
            warnings.append(f"Attribute [{i}] '{attr.get('name', '?')}': 'prior_approvals' found. SP v5 removes this — ignoring.")

    if errors:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    normalized = {
        "product_name": product_name,
        "molecule_class": molecule_class,
        "modality": modality,
        "reference_product": reference_product,
        "change_description": data.get("change_description", ""),
        "attributes": validated_attrs,
    }

    # Preserve additional top-level fields used by downstream pipeline stages
    for top_key in ("change_type", "_ingestion_signals", "_narrative_signals",
                     "lifecycle_stage"):
        if top_key in data:
            normalized[top_key] = data[top_key]

    return ValidationResult(valid=True, errors=[], warnings=warnings, normalized_input=normalized)


def _validate_attribute(attr: Dict[str, Any], index: int) -> Tuple[List[str], List[str], Optional[Dict]]:
    """Validate a single attribute dict. Returns (errors, warnings, cleaned_attr)."""
    errors = []
    warnings = []
    prefix = f"Attribute [{index}]"

    if not isinstance(attr, dict):
        return ([f"{prefix}: must be a dict"], [], None)

    name = attr.get("name", "")
    if not name:
        errors.append(f"{prefix}: missing 'name'")
        return (errors, warnings, None)
    prefix = f"Attribute '{name}'"

    # Required numeric fields
    for fld in ("pre_value", "post_value"):
        v = attr.get(fld)
        if v is None:
            errors.append(f"{prefix}: missing '{fld}'")
        elif not isinstance(v, (int, float)):
            warnings.append(f"{prefix}: '{fld}' should be numeric, got {type(v).__name__}. Passing through for downstream handling.")

    if errors:
        return (errors, warnings, None)

    # Category
    category = attr.get("category", "physicochemical")
    if category not in VALID_CATEGORIES:
        warnings.append(f"{prefix}: category '{category}' not standard. Using as-is.")

    # functional_support_level (SP v5: 4-level, replaces has_functional_correlation boolean)
    fsl = attr.get("functional_support_level", "")
    has_func_bool = attr.get("has_functional_correlation")
    if fsl:
        if fsl not in VALID_FUNCTIONAL_SUPPORT:
            warnings.append(f"{prefix}: functional_support_level '{fsl}' not in {VALID_FUNCTIONAL_SUPPORT}. Defaulting to 'none'.")
            fsl = "none"
    elif has_func_bool is not None:
        # Backward compatibility: convert boolean to 4-level
        fsl = "direct" if has_func_bool else "none"
        warnings.append(f"{prefix}: converted has_functional_correlation={has_func_bool} → functional_support_level='{fsl}'. SP v5 prefers 4-level field.")
    else:
        fsl = "none"

    # orthogonal_coverage (SP v5 new field)
    oc = attr.get("orthogonal_coverage", "")
    if oc:
        if oc not in VALID_ORTHOGONAL_COVERAGE:
            warnings.append(f"{prefix}: orthogonal_coverage '{oc}' not in {VALID_ORTHOGONAL_COVERAGE}. Defaulting to 'none'.")
            oc = "none"
    else:
        n_methods = attr.get("n_methods", 1)
        if n_methods >= 3:
            oc = "strong"
        elif n_methods >= 2:
            oc = "partial"
        else:
            oc = "none"

    clean = {
        "name": name,
        "category": category,
        "pre_value": attr["pre_value"],
        "post_value": attr["post_value"],
        "unit": attr.get("unit", ""),
        "n_lots": attr.get("n_lots", 3),
        "cv_pct": attr.get("cv_pct", 5.0),
        "n_methods": attr.get("n_methods", 1),
        "functional_support_level": fsl,
        "orthogonal_coverage": oc,
    }

    # Preserve extra fields that downstream modules need (e.g., assay, impact, etc.)
    for extra_key in ("assay", "impact", "detectability", "controllability",
                      "within_spec", "n_replicates",
                      "has_functional_correlation", "prior_approvals",
                      "spec_lower", "spec_upper", "spec_source",
                      "method_loq", "method_lod", "method_precision_cv"):
        if extra_key in attr:
            clean[extra_key] = attr[extra_key]

    return (errors, warnings, clean)
