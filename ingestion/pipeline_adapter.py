"""
Pipeline Adapter -- converts IngestionResult to pipeline input dict.

P7-C (v4.3.1 Ingestion Contract).

Usage::

    from ingestion.pipeline_adapter import ingestion_to_pipeline_input
    pipeline_input = ingestion_to_pipeline_input(ingestion_result)

    from pipelines.comparability import run_comparability_assessment
    report = run_comparability_assessment(pipeline_input, product_name="mAb-X")
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

from specs.cross_document_bridge import (
    ExtractedAttribute,
    IngestionResult,
    UserOverride,
)


def _apply_overrides(
    attributes: List[ExtractedAttribute],
    overrides: List[UserOverride],
) -> List[ExtractedAttribute]:
    """Return a copy of *attributes* with UserOverrides applied.

    Each override targets a specific attribute (by ``attribute_name``) and a
    specific field (by ``field_name``).  The corrected value replaces the
    original value on the matching attribute.
    """
    # Build a lookup: (attribute_name, field_name) -> corrected_value
    override_map: Dict[tuple, Any] = {}
    for ov in overrides:
        override_map[(ov.attribute_name, ov.field_name)] = ov.corrected_value

    if not override_map:
        return attributes

    patched: List[ExtractedAttribute] = []
    for attr in attributes:
        attr_copy = copy.copy(attr)
        for (attr_name, field_name), corrected in override_map.items():
            if attr_copy.name == attr_name and hasattr(attr_copy, field_name):
                setattr(attr_copy, field_name, corrected)
        patched.append(attr_copy)
    return patched


def ingestion_to_pipeline_input(result: IngestionResult) -> Dict[str, Any]:
    """Convert IngestionResult to the dict format run_comparability_assessment expects.

    Applies UserOverrides (corrected values replace originals).
    Returns pipeline-ready dict with ``'attributes'`` list.

    The returned dict matches::

        {
            "attributes": [
                {"name": str, "category": str, "pre_value": float,
                 "post_value": float, "unit": str, ...},
                ...
            ],
            "molecule_class": str,
            "modality": str,
        }
    """
    # Apply overrides first
    overrides = result.user_overrides if hasattr(result, 'user_overrides') else []
    patched_attrs = _apply_overrides(result.attributes, overrides)

    # Group by attribute name -- each ExtractedAttribute may carry pre/post
    # values directly (P7-B fields), or we may need to pair pre/post from
    # metadata phase tags.  With the P7-B extension, pre_value and post_value
    # live directly on the ExtractedAttribute.
    pipeline_attributes: List[Dict[str, Any]] = []

    for attr in patched_attrs:
        entry: Dict[str, Any] = {
            "name": attr.name,
            "category": attr.category or "physicochemical",
            "unit": attr.unit,
        }

        # Use P7-B pre_value / post_value if available
        if attr.pre_value is not None:
            entry["pre_value"] = float(attr.pre_value)
        if attr.post_value is not None:
            entry["post_value"] = float(attr.post_value)

        # Fallback: if pre/post not set, use the generic `value` field
        # (legacy ExtractedAttribute uses `value` for a single measurement)
        if "pre_value" not in entry and "post_value" not in entry:
            entry["pre_value"] = float(attr.value)
            entry["post_value"] = float(attr.value)

        # Optional enrichment fields
        if attr.n_lots is not None:
            entry["n_lots"] = attr.n_lots
        if attr.cv_pct is not None:
            entry["cv_pct"] = attr.cv_pct

        pipeline_attributes.append(entry)

    return {
        "attributes": pipeline_attributes,
        "molecule_class": result.case_context.molecule_class or "mAb",
        "modality": "IV",
    }
