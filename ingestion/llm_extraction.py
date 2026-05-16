"""
LLM-Assisted Extraction — fallback for when rule-based extraction
returns UNCERTAIN or fails to parse complex table layouts.

Uses Claude API (Anthropic) to extract structured CQA values from
page text. Hybrid approach: rules first, LLM only for failures.

Requires ANTHROPIC_API_KEY environment variable.
If unavailable, gracefully returns None (no extraction improvement).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 1024


def is_available() -> bool:
    """Check if LLM extraction is available (API key set)."""
    return bool(_API_KEY)


def extract_cqa_from_text(
    page_text: str,
    field_name: str,
    document_type: str = "characterization",
    context: str = "",
) -> Optional[Dict[str, Any]]:
    """Extract a specific CQA value from page text using Claude.

    Args:
        page_text: Raw text from the PDF page containing the data.
        field_name: CQA field to extract (e.g., "hmw_pct", "potency_relative_pct").
        document_type: Document type for context.
        context: Additional context (e.g., nearby table headers).

    Returns:
        Dict with 'value' (float), 'unit' (str), 'confidence' (float),
        'source_text' (str excerpt), or None if extraction fails.
    """
    if not _API_KEY:
        return None

    prompt = _build_extraction_prompt(page_text, field_name, document_type, context)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_API_KEY)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_response(response.content[0].text, field_name)
    except Exception as e:
        logger.debug("LLM extraction failed for %s: %s", field_name, e)
        return None


def extract_table_values(
    page_text: str,
    table_text: str,
    target_fields: List[str],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Extract multiple CQA values from a table that rule-based parsing couldn't handle.

    Args:
        page_text: Full page text for context.
        table_text: Specific table text (headers + rows).
        target_fields: List of field names to extract.

    Returns:
        Dict mapping field_name -> extraction result or None.
    """
    if not _API_KEY:
        return {f: None for f in target_fields}

    prompt = f"""You are a biopharma analytical data extraction system. Extract the following
CQA (Critical Quality Attribute) values from this regulatory document table.

TABLE TEXT:
{table_text[:3000]}

PAGE CONTEXT:
{page_text[:2000]}

FIELDS TO EXTRACT:
{json.dumps(target_fields)}

For each field, respond with a JSON object. If a value cannot be found, set it to null.
Field name mapping:
- hmw_pct: High Molecular Weight percentage (aggregation, from SEC-HPLC)
- main_charge_peak_pct: Main charge peak/purity percentage (from CEX or CZE)
- potency_relative_pct: Relative potency percentage (from bioassay)
- afucosylation_pct: Afucosylation percentage (from glycan profiling)
- acidic_variants_pct: Acidic variants percentage (from charge heterogeneity)
- basic_variants_pct: Basic variants percentage (from charge heterogeneity)
- proposed_shelf_life: Proposed shelf life in months
- max_timepoint_months: Maximum stability timepoint in months

Respond ONLY with valid JSON, no markdown:
{{"field_name": {{"value": <number or null>, "unit": "<string>", "confidence": <0-1>, "source_text": "<brief excerpt>"}}}}
"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_API_KEY)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Parse JSON response
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        results = {}
        for f in target_fields:
            if f in data and data[f] and data[f].get("value") is not None:
                results[f] = data[f]
            else:
                results[f] = None
        return results
    except Exception as e:
        logger.debug("LLM table extraction failed: %s", e)
        return {f: None for f in target_fields}


def extract_pre_post_from_comparability(
    page_text: str,
    table_text: str = "",
) -> Optional[List[Dict[str, Any]]]:
    """Extract pre/post attribute pairs from a comparability report.

    This addresses the critical gap where comparability assessment
    requires CSV input. This function attempts to extract structured
    pre/post data from the report text.

    Returns:
        List of dicts with 'name', 'pre_value', 'post_value', 'unit', 'category',
        or None if extraction fails.
    """
    if not _API_KEY:
        return None

    prompt = f"""You are a biopharma comparability data extraction system. This is a
comparability report comparing pre-change and post-change analytical data.

Extract all attribute comparisons you can find. Each attribute should have:
- name: attribute name (e.g., "HMW", "Main Peak", "Potency")
- category: analytical category (e.g., "aggregation", "charge_variants", "potency", "purity", "glycosylation")
- pre_value: numeric value before the change
- post_value: numeric value after the change
- unit: measurement unit (e.g., "%", "mg/mL", "kDa")

TABLE TEXT:
{table_text[:4000]}

PAGE CONTEXT:
{page_text[:3000]}

Respond ONLY with a JSON array, no markdown:
[{{"name": "...", "category": "...", "pre_value": <number>, "post_value": <number>, "unit": "..."}}]

If no pre/post comparisons can be found, respond with: []
"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_API_KEY)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        if isinstance(data, list) and len(data) > 0:
            # Validate structure
            valid = []
            for item in data:
                if (isinstance(item, dict)
                    and "name" in item
                    and "pre_value" in item
                    and "post_value" in item
                    and item["pre_value"] is not None
                    and item["post_value"] is not None):
                    valid.append({
                        "name": str(item["name"]),
                        "category": item.get("category", "physicochemical"),
                        "pre_value": float(item["pre_value"]),
                        "post_value": float(item["post_value"]),
                        "unit": item.get("unit", ""),
                    })
            return valid if valid else None
        return None
    except Exception as e:
        logger.debug("LLM comparability extraction failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FIELD_DESCRIPTIONS = {
    "hmw_pct": "High Molecular Weight percentage (HMW%, from SEC-HPLC size exclusion chromatography). Usually reported as % of total area.",
    "main_charge_peak_pct": "Main charge peak / charge purity percentage (from cation exchange CEX or capillary zone electrophoresis CZE).",
    "potency_relative_pct": "Relative potency percentage (from cell-based bioassay, ADCC, CDC, or reporter gene assay). Usually 80-120%.",
    "afucosylation_pct": "Afucosylation percentage (from N-glycan profiling, HILIC-MS). The percentage of non-fucosylated glycans.",
    "acidic_variants_pct": "Acidic variants percentage (from charge heterogeneity analysis).",
    "basic_variants_pct": "Basic variants percentage (from charge heterogeneity analysis).",
}


def _build_extraction_prompt(
    page_text: str,
    field_name: str,
    document_type: str,
    context: str,
) -> str:
    field_desc = _FIELD_DESCRIPTIONS.get(field_name, field_name)
    return f"""You are a biopharma analytical data extraction system. Extract a specific
CQA (Critical Quality Attribute) value from this {document_type} report page.

FIELD TO EXTRACT: {field_name}
DESCRIPTION: {field_desc}

PAGE TEXT:
{page_text[:4000]}

{f'ADDITIONAL CONTEXT: {context[:1000]}' if context else ''}

If you can find the value, respond with ONLY valid JSON (no markdown):
{{"value": <number>, "unit": "<string>", "confidence": <0.0-1.0>, "source_text": "<exact text excerpt where value appears>"}}

If you cannot find the value in this text, respond with:
{{"value": null, "unit": "", "confidence": 0.0, "source_text": ""}}
"""


def _parse_response(text: str, field_name: str) -> Optional[Dict[str, Any]]:
    """Parse Claude's JSON response."""
    try:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        if data.get("value") is not None:
            return {
                "value": float(data["value"]),
                "unit": data.get("unit", ""),
                "confidence": float(data.get("confidence", 0.7)),
                "source_text": data.get("source_text", ""),
                "extraction_method": "llm_fallback",
            }
        return None
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.debug("Failed to parse LLM response for %s: %s", field_name, e)
        return None
