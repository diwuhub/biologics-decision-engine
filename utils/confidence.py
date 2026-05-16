"""
Standardised confidence scoring used across all BDE modules.

Replaces the duplicated score-to-qualifier mapping that appears in
evidence_closure, ctd_reviewer, data_harmonizer, and others.

Usage::

    from utils.confidence import compute_confidence

    result = compute_confidence(0.85, n_items=3)
    # {"score": 0.85, "qualifier": "high", "n_items": 3}
"""

from __future__ import annotations

_DEFAULT_THRESHOLDS = {"high": 0.80, "medium": 0.50, "low": 0.0}


def compute_confidence(
    score: float,
    n_items: int = 1,
    qualifier_thresholds: dict | None = None,
) -> dict:
    """Standardised confidence computation used across all modules.

    Parameters
    ----------
    score : float
        Raw confidence score in [0, 1].
    n_items : int
        Number of items that contributed to the score.
    qualifier_thresholds : dict or None
        Override thresholds for qualifier bucketing.  Keys must be
        ``"high"``, ``"medium"``, ``"low"`` with float values.
        Defaults to ``{"high": 0.80, "medium": 0.50, "low": 0.0}``.

    Returns
    -------
    dict
        ``{"score": float, "qualifier": str, "n_items": int}``
    """
    if qualifier_thresholds is None:
        qualifier_thresholds = _DEFAULT_THRESHOLDS

    qualifier = "low"
    for level in ["high", "medium", "low"]:
        if score >= qualifier_thresholds[level]:
            qualifier = level
            break

    return {
        "score": round(score, 4),
        "qualifier": qualifier,
        "n_items": n_items,
    }
