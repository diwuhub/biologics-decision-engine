"""
Template Detector — recognizes analytical data table templates.

Matches tabular data (CSV/TSV) against known biologics CMC template
patterns (characterization summary, release testing, stability summary)
using header pattern matching.

Extracted from bio-cmc-ai-suite/cmc-harmonizer (archived 2026-03-25).
Stripped of Streamlit/SDK dependencies for use as shared infrastructure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional


_TEMPLATES: list[dict] = [
    {
        "template_id": "char-summary-v1",
        "template_name": "Characterization Summary Table",
        "header_variants": {
            "test_name": [
                "test", "assay", "test name", "analytical test", "parameter",
            ],
            "method": [
                "method", "analytical method", "method ref", "test method", "sop",
            ],
            "acceptance_criteria": [
                "acceptance criteria", "spec", "specification", "limit",
                "acceptance limit",
            ],
            "result": [
                "result", "value", "observed", "measured value", "test result",
            ],
            "unit": ["unit", "units", "unit of measure"],
            "batch": [
                "batch", "lot", "batch no", "lot no", "batch id",
                "sample id", "sample",
            ],
            "comments": ["comments", "notes", "remarks", "observations"],
            "conforms": ["conforms", "pass/fail", "compliance", "status"],
        },
        "required_columns": ["test_name", "result"],
        "distinguishing": ["conforms", "pass/fail", "compliance"],
        "min_headers": 3,
    },
    {
        "template_id": "release-testing-v1",
        "template_name": "Release Testing Results Table",
        "header_variants": {
            "test_name": [
                "test", "parameter", "quality attribute", "test name",
            ],
            "method": [
                "method", "compendial method", "test method",
                "analytical procedure",
            ],
            "acceptance_criteria": [
                "acceptance criteria", "specification", "limit",
                "release specification",
            ],
            "batch_result": ["batch", "lot", "result"],
            "unit": ["unit", "units"],
        },
        "required_columns": ["test_name"],
        "distinguishing": ["release", "batch.*result", "lot.*result"],
        "min_headers": 2,
    },
    {
        "template_id": "stability-summary-v1",
        "template_name": "Stability Summary Table",
        "header_variants": {
            "test_name": [
                "test", "parameter", "attribute", "quality attribute",
            ],
            "method": ["method", "analytical method", "test method"],
            "acceptance_criteria": [
                "acceptance criteria", "specification", "limit",
            ],
            "time_point": [
                "initial", "0m", "t0", "1m", "3m", "6m", "9m", "12m",
                "18m", "24m", "36m", "month",
            ],
            "storage_condition": [
                "condition", "storage condition", "temperature",
            ],
            "batch": ["batch", "lot", "batch no"],
            "unit": ["unit", "units"],
        },
        "required_columns": ["test_name"],
        "distinguishing": [r"\d+\s*m(?:onth)?", "initial", "t0", "stability"],
        "min_headers": 2,
    },
]


@dataclass
class TemplateMatch:
    """Result of template detection."""
    template_id: Optional[str]
    template_name: str
    confidence_score: float
    confidence_qualifier: str      # "high", "medium", "low", "unknown"
    detected_headers: list[str] = field(default_factory=list)
    matched_columns: dict[str, str] = field(default_factory=dict)
    match_details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_headers(raw_content: str) -> list[str]:
    """Extract likely header fields from the first few rows of CSV/tabular data."""
    lines = raw_content.strip().split("\n")
    if not lines:
        return []

    best_headers: list[str] = []
    best_score = 0

    for line in lines[:5]:
        if "\t" in line:
            cells = [c.strip().strip('"').strip() for c in line.split("\t")]
        else:
            cells = [c.strip().strip('"').strip() for c in line.split(",")]

        text_cells = [
            c for c in cells
            if c and not re.match(r"^[\d.<>≤≥±]+$", c)
        ]
        score = len(text_cells)
        if score > best_score:
            best_score = score
            best_headers = cells

    return [h for h in best_headers if h]


def _match_headers(
    headers: list[str], template: dict
) -> tuple[int, int, dict[str, str]]:
    header_variants = template["header_variants"]
    matched_map: dict[str, str] = {}
    headers_lower = [h.lower() for h in headers]

    for canonical, variants in header_variants.items():
        for header_lower, header_orig in zip(headers_lower, headers):
            for variant in variants:
                vl = variant.lower()
                if vl == header_lower or vl in header_lower:
                    if canonical not in matched_map:
                        matched_map[canonical] = header_orig
                    break

    return len(matched_map), len(header_variants), matched_map


def _check_distinguishing(
    headers: list[str], raw_content: str, patterns: list[str]
) -> int:
    combined = " ".join(headers).lower() + " " + raw_content[:500].lower()
    count = 0
    for pattern in patterns:
        if ".*" in pattern or "(" in pattern:
            if re.search(pattern, combined, re.IGNORECASE):
                count += 1
        elif pattern.lower() in combined:
            count += 1
    return count


def _check_time_points(headers: list[str]) -> int:
    count = 0
    for h in headers:
        h_lower = h.lower().strip()
        if re.match(r"^\d+\s*m(?:onth)?s?$", h_lower):
            count += 1
        elif h_lower in ("initial", "t0", "t=0", "baseline"):
            count += 1
    return count


def detect_template(
    raw_content: str, template_override: str = "auto-detect"
) -> TemplateMatch:
    """Detect which supported template the tabular data matches.

    Args:
        raw_content: Raw CSV or TSV string.
        template_override: Force a specific template ID instead of auto-detecting.

    Returns:
        TemplateMatch with template_id, confidence, and matched columns.
    """
    if not raw_content or not raw_content.strip():
        return TemplateMatch(
            template_id=None, template_name="No data",
            confidence_score=0.0, confidence_qualifier="unknown",
            match_details="No content to analyze.",
        )

    if template_override != "auto-detect":
        return TemplateMatch(
            template_id=template_override,
            template_name=template_override.replace("-", " ").title(),
            confidence_score=1.0, confidence_qualifier="high",
            detected_headers=["[Manual override]"],
            match_details=f"Template manually set to {template_override}.",
        )

    headers = _extract_headers(raw_content)
    if not headers:
        return TemplateMatch(
            template_id=None, template_name="Unknown Template",
            confidence_score=0.0, confidence_qualifier="unknown",
            match_details="Could not extract headers from the input.",
        )

    scores: list[tuple[dict, float, dict[str, str], str]] = []

    for template in _TEMPLATES:
        matched_count, total_canonical, matched_map = _match_headers(
            headers, template
        )
        dist_count = _check_distinguishing(
            headers, raw_content, template["distinguishing"]
        )
        time_points = _check_time_points(headers)

        required = template.get("required_columns", [])
        required_met = all(r in matched_map for r in required)

        base_score = matched_count / total_canonical if total_canonical else 0.0
        dist_boost = min(dist_count * 0.15, 0.25)

        if template["template_id"] == "stability-summary-v1" and time_points >= 2:
            dist_boost += 0.20
        elif template["template_id"] == "stability-summary-v1" and time_points == 0:
            dist_boost -= 0.15

        score = min(base_score + dist_boost, 1.0)
        if not required_met:
            score *= 0.5
        if matched_count < template.get("min_headers", 2):
            score *= 0.5

        col_bonus = matched_count * 0.01
        score = min(score + col_bonus, 1.0)

        detail = (
            f"Matched {matched_count}/{total_canonical} canonical columns "
            f"({', '.join(matched_map.keys())}). "
            f"Distinguishing features: {dist_count}."
        )
        if time_points > 0:
            detail += f" Time-point columns: {time_points}."

        scores.append((template, round(score, 2), matched_map, detail))

    scores.sort(key=lambda x: x[1], reverse=True)
    best_template, best_score, best_map, best_detail = scores[0]

    if best_score >= 0.70:
        qualifier = "high"
    elif best_score >= 0.45:
        qualifier = "medium"
    elif best_score > 0.0:
        qualifier = "low"
    else:
        qualifier = "unknown"

    if best_score < 0.25:
        return TemplateMatch(
            template_id="unknown-template",
            template_name="Unknown Template",
            confidence_score=best_score,
            confidence_qualifier=qualifier,
            detected_headers=headers,
            match_details=(
                f"No supported template matched with sufficient confidence. "
                f"Best candidate: {best_template['template_id']} "
                f"(score: {best_score}). {best_detail}"
            ),
        )

    return TemplateMatch(
        template_id=best_template["template_id"],
        template_name=best_template["template_name"],
        confidence_score=best_score,
        confidence_qualifier=qualifier,
        detected_headers=headers,
        matched_columns=best_map,
        match_details=best_detail,
    )
