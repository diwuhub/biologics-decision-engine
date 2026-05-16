"""
Data Harmonizer — shared infrastructure for biologics analytical data normalization.

Provides unit conversion, field name mapping, and template detection for
CMC analytical data tables. Used by both the comparability pipeline and
regulatory modules.

Extracted from bio-cmc-ai-suite/cmc-harmonizer (archived 2026-03-25).
"""

from modules.data_harmonizer.unit_normalizer import (
    normalize,
    normalize_value,
    NormalizedUnit,
)
from modules.data_harmonizer.field_mapper import (
    map_field,
    map_fields,
    FieldMapping,
)
from modules.data_harmonizer.template_detector import (
    detect_template,
    TemplateMatch,
)

__all__ = [
    # Unit normalization
    "normalize",
    "normalize_value",
    "NormalizedUnit",
    # Field mapping
    "map_field",
    "map_fields",
    "FieldMapping",
    # Template detection
    "detect_template",
    "TemplateMatch",
    # Batch harmonization
    "harmonize_batch_data",
]


def harmonize_batch_data(raw_data: dict) -> dict:
    """Harmonize incoming batch data (CSV or JSON) to SP v5 schema.

    - Map legacy field names to canonical names (via field_mapper)
    - Normalize units where applicable (via unit_normalizer)
    - Ensure required metadata fields exist
    - Pass through attributes list unchanged if already in SP v5 format

    Parameters
    ----------
    raw_data : dict
        Raw batch data dict. Expected to have an "attributes" list with
        per-attribute dicts containing name, pre_value, post_value, unit, etc.

    Returns
    -------
    dict
        Harmonized copy of the data with canonical field names and normalized
        units where possible.
    """
    if not isinstance(raw_data, dict):
        return raw_data

    # Work on a shallow copy to avoid mutating the original
    result = dict(raw_data)

    # Ensure required top-level fields
    if "molecule_class" not in result:
        result["molecule_class"] = "unknown"
    if "product_name" not in result:
        result["product_name"] = "Unknown Product"

    # Process attributes if present
    attributes = result.get("attributes", [])
    if isinstance(attributes, list):
        harmonized_attrs = []
        for attr in attributes:
            if not isinstance(attr, dict):
                harmonized_attrs.append(attr)
                continue

            harmonized_attr = dict(attr)

            # Map field names: check for common legacy names
            if "attribute" in harmonized_attr and "name" not in harmonized_attr:
                harmonized_attr["name"] = harmonized_attr.pop("attribute")
            if "test_name" in harmonized_attr and "name" not in harmonized_attr:
                harmonized_attr["name"] = harmonized_attr.pop("test_name")

            # Normalize units on pre_value and post_value if unit is present
            unit = harmonized_attr.get("unit", "")
            if unit:
                for val_field in ("pre_value", "post_value"):
                    val = harmonized_attr.get(val_field)
                    if val is not None and isinstance(val, (int, float)):
                        try:
                            norm = normalize_value(float(val), unit)
                            harmonized_attr[val_field] = norm.normalized_value
                            harmonized_attr["unit"] = norm.normalized_unit
                        except (ValueError, TypeError):
                            pass  # Keep original if normalization fails

            # Ensure defaults for SP v5 fields
            harmonized_attr.setdefault("category", "physicochemical")
            harmonized_attr.setdefault("n_lots", 3)
            harmonized_attr.setdefault("cv_pct", 5.0)
            harmonized_attr.setdefault("n_methods", 1)
            harmonized_attr.setdefault("functional_support_level", "none")
            harmonized_attr.setdefault("orthogonal_coverage", "none")

            harmonized_attrs.append(harmonized_attr)

        result["attributes"] = harmonized_attrs

    return result
