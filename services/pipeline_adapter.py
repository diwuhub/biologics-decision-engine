"""
Pipeline Adapter — Converts legacy AttributeResult objects into the dict
format expected by build_risk_clusters().

QA Fix 1: Eliminates the need for manual dict construction when passing
data from the old pipeline (pipelines/schemas.AttributeResult) into the
new Judgment Core (services/cluster_builder.build_risk_clusters).
"""

from __future__ import annotations

from typing import Any, Dict, List

from pipelines.schemas import AttributeResult


def attribute_result_to_cluster_input(ar: AttributeResult) -> Dict[str, Any]:
    """Convert a single AttributeResult to a cluster builder input dict.

    Mapping:
        ar.name        -> attribute_id
        ar.concern     -> concern_level
        ar.is_cqa      -> is_cqa
        ar.category    -> category
        ar.score       -> score
        ar.uncertainty -> uncertainty
        ar.action (dict with 'action_level') -> action_level
    """
    action_dict = ar.action if isinstance(ar.action, dict) else {}

    return {
        "attribute_id": ar.name,
        "category": ar.category,
        "concern_level": ar.concern,
        "is_cqa": ar.is_cqa,
        "score": ar.score,
        "uncertainty": ar.uncertainty,
        "action_level": action_dict.get("action_level", "PROCEED"),
    }


def convert_attribute_results(
    results: List[AttributeResult],
) -> List[Dict[str, Any]]:
    """Convert a list of AttributeResult objects to cluster builder input dicts.

    Args:
        results: List of AttributeResult from the comparability pipeline.

    Returns:
        List of dicts suitable for ``build_risk_clusters(case_context, attribute_results)``.
    """
    return [attribute_result_to_cluster_input(ar) for ar in results]
