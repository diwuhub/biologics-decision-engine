"""
Verdict Translation Layer -- Maps Judgment Core verdicts to legacy display
strings and action levels for backward compatibility.

Phase 2C: Pipeline Convergence v3.0.

The Judgment Core uses a 5-level internal verdict taxonomy:
    proceed, proceed_with_conditions, supplement_required,
    investigation_required, defer_package

The OLD pipeline uses a 4-level display taxonomy:
    Comparable, Comparable With Caveats, Not Comparable, Insufficient Evidence

This module provides deterministic mappings between the two.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Verdict Display Map: NEW internal verdict -> OLD display string
# ---------------------------------------------------------------------------

VERDICT_DISPLAY_MAP: Dict[str, str] = {
    "proceed": "Comparable",
    "proceed_with_conditions": "Comparable With Caveats",
    "supplement_required": "Not Comparable",
    "investigation_required": "Not Comparable",
    "defer_package": "Not Comparable",
}

# Special case: defer_package with abstain_flag=True and no blocking clusters
# maps to "Insufficient Evidence" (system truly cannot decide).
# With blocking clusters it maps to "Not Comparable" (evidence is present
# but shows non-comparability).
DEFER_ABSTAIN_ONLY_DISPLAY = "Insufficient Evidence"

# Reverse map: OLD display string -> NEW internal verdict (best match)
DISPLAY_TO_VERDICT_MAP: Dict[str, str] = {
    "Comparable": "proceed",
    "Comparable With Caveats": "proceed_with_conditions",
    "Not Comparable": "investigation_required",
    "Insufficient Evidence": "defer_package",
}


# ---------------------------------------------------------------------------
# Action Level Map: NEW internal verdict -> OLD action level
# ---------------------------------------------------------------------------

ACTION_MAP: Dict[str, str] = {
    "proceed": "PROCEED",
    "proceed_with_conditions": "MONITOR",
    "supplement_required": "SUPPLEMENT",
    "investigation_required": "INVESTIGATE",
    "defer_package": "DEFER",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def to_display_verdict(
    internal_verdict: str,
    abstain_flag: bool = False,
    has_blocking_clusters: bool = False,
) -> str:
    """Convert a Judgment Core verdict to the legacy display string.

    Args:
        internal_verdict: One of the five JC verdict strings.
        abstain_flag: If True and verdict is defer_package with no blocking
            clusters, maps to 'Insufficient Evidence' instead of 'Not Comparable'.
        has_blocking_clusters: Whether blocking clusters drove the verdict.

    Returns:
        Legacy display string (e.g., 'Comparable', 'Not Comparable').

    Raises:
        ValueError: If the internal verdict is not recognized.
    """
    result = VERDICT_DISPLAY_MAP.get(internal_verdict)
    if result is None:
        raise ValueError(
            f"Unknown internal verdict '{internal_verdict}'. "
            f"Must be one of {sorted(VERDICT_DISPLAY_MAP.keys())}"
        )
    # Special case: defer_package with abstain and no evidence of
    # non-comparability -> Insufficient Evidence
    if (
        internal_verdict == "defer_package"
        and abstain_flag
        and not has_blocking_clusters
    ):
        return DEFER_ABSTAIN_ONLY_DISPLAY
    return result


def to_action_level(internal_verdict: str) -> str:
    """Convert a Judgment Core verdict to the legacy action level.

    Args:
        internal_verdict: One of the five JC verdict strings.

    Returns:
        Action level string (PROCEED, MONITOR, SUPPLEMENT, INVESTIGATE, DEFER).

    Raises:
        ValueError: If the internal verdict is not recognized.
    """
    result = ACTION_MAP.get(internal_verdict)
    if result is None:
        raise ValueError(
            f"Unknown internal verdict '{internal_verdict}'. "
            f"Must be one of {sorted(ACTION_MAP.keys())}"
        )
    return result


def to_legacy_report_fields(
    internal_verdict: str,
    confidence: float = 0.0,
    confidence_band: str = "",
    blocking_clusters: Optional[list] = None,
    abstain_flag: bool = False,
    decision_rule_ids: Optional[list] = None,
    what_would_change: Optional[list] = None,
) -> Dict[str, Any]:
    """Convert Judgment Core decision fields to legacy report-compatible dict.

    This produces a dict that can be merged into ComparabilityReport fields
    for backward compatibility. Legacy consumers that only read
    ``overall_verdict`` and ``evidence_strength_index`` will continue to work.

    Args:
        internal_verdict: Judgment Core verdict string.
        confidence: Numeric confidence 0-1.
        confidence_band: 'high', 'moderate', or 'low'.
        blocking_clusters: List of blocking cluster IDs.
        abstain_flag: Whether the system abstained.
        decision_rule_ids: Applied decision rule IDs.
        what_would_change: Counterfactual entries.

    Returns:
        Dict with legacy field names and values.
    """
    has_blocking = bool(blocking_clusters)
    display_verdict = to_display_verdict(
        internal_verdict,
        abstain_flag=abstain_flag,
        has_blocking_clusters=has_blocking,
    )
    action_level = to_action_level(internal_verdict)

    return {
        # Legacy fields
        "overall_verdict": display_verdict,
        "evidence_strength_index": confidence,
        # New fields (None-guarded for legacy consumers)
        "judgment_core_verdict": internal_verdict,
        "judgment_confidence": confidence,
        "judgment_confidence_band": confidence_band or _derive_band(confidence),
        "blocking_clusters": blocking_clusters or [],
        "abstain_flag": abstain_flag,
        "decision_rule_ids": decision_rule_ids or [],
        "what_would_change_verdict": what_would_change or [],
        # Convenience
        "_action_level": action_level,
    }


def _derive_band(confidence: float) -> str:
    """Derive confidence band from numeric value."""
    if confidence > 0.8:
        return "high"
    elif confidence >= 0.5:
        return "moderate"
    else:
        return "low"
