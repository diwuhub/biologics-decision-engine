"""Rule-based action recommendation from gap analysis + 6-question scores.

Adapter layer: maps the 3-level admissibility taxonomy to the shared
5-level action taxonomy defined in modules.action_recommender.engine.

Mapping:
    push_forward       -> PROCEED
    supplement_evidence -> SUPPLEMENT
    observe            -> MONITOR
"""

from typing import Dict, List

from modules.action_recommender.engine import (
    ACTION_LEVELS,
    recommend_attribute_action,
)

# 3-level -> 5-level mapping
_ACTION_MAP = {
    "push_forward": "PROCEED",
    "supplement_evidence": "SUPPLEMENT",
    "observe": "MONITOR",
}

# Reverse map for backward compatibility (5-level -> 3-level)
_REVERSE_MAP = {v: k for k, v in _ACTION_MAP.items()}


def _map_to_5_level(action_3: str) -> str:
    """Map a 3-level action label to the shared 5-level taxonomy."""
    return _ACTION_MAP.get(action_3, "MONITOR")


def recommend_action(gap_analysis: Dict, six_q_scores: List[Dict[str, float]]) -> Dict:
    """Given gap analysis and 6-question scores, recommend an action.

    Uses the shared 5-level action taxonomy from
    modules.action_recommender.engine, with backward-compatible 3-level
    labels preserved in the output.

    Returns dict with action (3-level), action_5level (5-level),
    confidence, rationale, priorities.
    """
    coverage = gap_analysis.get("coverage_pct", 0)
    n_gaps = len(gap_analysis.get("uncovered", []))

    # Average 6-question scores across all claims
    if six_q_scores:
        avg_scores = {}
        for key in six_q_scores[0]:
            vals = [s.get(key, 0) for s in six_q_scores]
            avg_scores[key] = sum(vals) / len(vals) if vals else 0
    else:
        avg_scores = {}

    avg_regulatory = avg_scores.get("regulatory_acceptable", 0)
    avg_biology = avg_scores.get("biology_credible", 0)

    # Decision logic (unchanged)
    if coverage >= 80 and avg_regulatory >= 0.6 and n_gaps <= 2:
        action = "push_forward"
        confidence = min(1.0, coverage / 100 * 0.6 + avg_regulatory * 0.4)
        rationale = f"Coverage {coverage:.0f}%, regulatory acceptability {avg_regulatory:.2f}. Remaining {n_gaps} gaps are minor."
    elif coverage >= 50 and avg_biology >= 0.5:
        action = "supplement_evidence"
        confidence = min(1.0, coverage / 100 * 0.5 + avg_biology * 0.3)
        rationale = f"Coverage {coverage:.0f}% with {n_gaps} gaps. Biology credible ({avg_biology:.2f}) but regulatory evidence incomplete."
    else:
        action = "observe"
        confidence = 0.3
        rationale = f"Coverage only {coverage:.0f}% with {n_gaps} uncovered requirements. More evidence needed."

    # Map to 5-level taxonomy
    action_5level = _map_to_5_level(action)

    # Priority actions for supplement_evidence
    priorities = []
    for gap in gap_analysis.get("gap_details", [])[:3]:
        priorities.append(gap["action_needed"])

    return {
        "action": action,
        "action_5level": action_5level,
        "action_5level_label": ACTION_LEVELS.get(action_5level, ""),
        "confidence": round(confidence, 3),
        "rationale": rationale,
        "n_gaps": n_gaps,
        "coverage_pct": coverage,
        "avg_six_question_scores": {k: round(v, 3) for k, v in avg_scores.items()},
        "priority_actions": priorities,
    }
