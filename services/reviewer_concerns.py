"""
Gap-cluster-based reviewer concern generation (pipeline-facing facade).

Replaces:
  - ui/config.py _build_predicted_questions() (inline 3-rule version)
  - services/reviewer_templates.py match_templates() (30 templates)
  - services/regulatory_evidence.py predict_reviewer_risks() (template-based)

Design principle: questions are generated per risk-cluster, not per attribute.
Each cluster groups related attributes by (category, concern_level).
Questions cite matched references and case context.

This module provides a simpler interface than reviewer_concern_engine.py
(which requires full RiskCluster + AuthorityContextPack objects). It works
directly with pipeline-level AttributeResult and ReferenceMatchResult data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from collections import defaultdict


@dataclass
class RiskCluster:
    """Lightweight risk cluster for reviewer concern generation."""
    category: str
    concern_level: str
    attributes: List[Dict[str, Any]]
    lead_attribute: Dict[str, Any]
    matched_refs: List[Dict[str, Any]]
    has_cqa: bool = False
    priority_score: float = 0.0


@dataclass
class ReviewerConcern:
    """A single reviewer concern generated from cluster analysis."""
    id: str
    question: str
    cluster_category: str
    affected_attributes: List[str]
    probability: float
    severity: str
    supporting_precedent: Optional[str] = None
    recommended_response: str = ""
    is_primary: bool = True


def generate_reviewer_concerns(
    attribute_results: List,
    matched_references: List,
    refs_by_category: Dict[str, List],
    case_context: Dict[str, Any],
    registry=None,
    max_primary: int = 4,
) -> List[ReviewerConcern]:
    """Generate reviewer concerns from risk clusters.

    Returns list sorted by priority (primary first, then secondary).
    """
    # Phase 1: Build clusters
    clusters_raw: Dict[tuple, list] = defaultdict(list)
    for ar in attribute_results:
        if isinstance(ar, dict):
            concern = ar.get("concern", "none")
            action = (ar.get("action") or {}).get("action_level", "PROCEED")
        else:
            concern = ar.concern
            action = (ar.action or {}).get("action_level", "PROCEED")

        if concern == "none" and action == "PROCEED":
            continue

        cat = ar.category if hasattr(ar, "category") else ar.get("category", "unknown")
        clusters_raw[(cat, concern)].append(ar)

    # Phase 2: Score and rank clusters
    clusters = []
    for (cat, concern), attrs in clusters_raw.items():
        def _get_delta(a: Any) -> float:
            return abs(getattr(a, "delta_pct", 0) if hasattr(a, "delta_pct") else a.get("delta_pct", 0))

        lead = max(attrs, key=_get_delta)
        has_cqa = any(
            getattr(a, "is_cqa", False) if hasattr(a, "is_cqa") else a.get("is_cqa", False)
            for a in attrs
        )
        cat_refs = refs_by_category.get(cat, [])

        concern_weight = {"critical": 4, "major": 3, "minor": 1, "none": 0}.get(concern, 0)
        cqa_weight = 2.0 if has_cqa else 1.0
        ref_weight = min(len(cat_refs) / 3.0, 1.5)
        priority = concern_weight * cqa_weight * max(ref_weight, 0.5)

        def _to_dict(a: Any) -> dict:
            if isinstance(a, dict):
                return a
            return {k: v for k, v in vars(a).items() if not k.startswith("_")}

        clusters.append(RiskCluster(
            category=cat,
            concern_level=concern,
            attributes=[_to_dict(a) for a in attrs],
            lead_attribute=_to_dict(lead),
            matched_refs=[
                {"id": getattr(r, "entry_id", ""), "title": getattr(r, "title", ""),
                 "score": getattr(r, "relevance_score", 0.0)}
                for r in cat_refs[:3]
            ],
            has_cqa=has_cqa,
            priority_score=priority,
        ))

    clusters.sort(key=lambda c: c.priority_score, reverse=True)

    # Phase 3: Generate contextual questions per cluster
    concerns = []
    mol = case_context.get("molecule_class", "biologic") if isinstance(case_context, dict) else "biologic"
    change = case_context.get("change_type", "manufacturing change") if isinstance(case_context, dict) else "manufacturing change"

    for i, cluster in enumerate(clusters):
        attr_names = [a.get("name", "") for a in cluster.attributes]
        lead = cluster.lead_attribute
        lead_name = lead.get("name", "")
        lead_delta = lead.get("delta_pct", 0)

        if len(attr_names) > 1:
            attrs_str = ", ".join(attr_names[:-1]) + " and " + attr_names[-1]
        else:
            attrs_str = attr_names[0] if attr_names else "this attribute"

        precedent_cite = ""
        if cluster.matched_refs:
            best_ref = cluster.matched_refs[0]
            precedent_cite = f" (cf. {best_ref['title'][:50]})"

        if cluster.concern_level == "critical":
            q = (
                f"For this {mol} {change}: {attrs_str} show critical-level shifts "
                f"(lead: {lead_name} at {lead_delta:+.1f}%). "
                f"What is the root cause, and what trending data across batches supports "
                f"that this shift is controlled?{precedent_cite}"
            )
        elif cluster.has_cqa and cluster.concern_level == "major":
            q = (
                f"CQA attributes {attrs_str} show major-level changes "
                f"(lead: {lead_name} at {lead_delta:+.1f}%). "
                f"Please provide additional characterization or orthogonal method data "
                f"demonstrating these changes do not impact clinical performance.{precedent_cite}"
            )
        elif cluster.concern_level == "major":
            q = (
                f"{attrs_str} in the {cluster.category} category show notable changes. "
                f"What evidence supports that the {cluster.category} profile remains within "
                f"acceptable limits for this {mol}?{precedent_cite}"
            )
        else:
            q = (
                f"Minor shifts observed in {attrs_str} ({cluster.category}). "
                f"Can you confirm lot-to-lot variability data and provide context "
                f"for the observed {lead_delta:+.1f}% change in {lead_name}?{precedent_cite}"
            )

        is_primary = i < max_primary
        prob = min(0.95, 0.50 + cluster.priority_score * 0.08)

        concerns.append(ReviewerConcern(
            id=f"RC-{i + 1:03d}",
            question=q,
            cluster_category=cluster.category,
            affected_attributes=attr_names,
            probability=round(prob, 2),
            severity=cluster.concern_level,
            supporting_precedent=cluster.matched_refs[0]["title"] if cluster.matched_refs else None,
            recommended_response=_build_response_guidance(cluster),
            is_primary=is_primary,
        ))

    return concerns


def _build_response_guidance(cluster: RiskCluster) -> str:
    """Generate response guidance based on cluster severity and refs."""
    if cluster.concern_level == "critical":
        return "Provide root-cause investigation, trending data, and process control evidence."
    elif cluster.has_cqa:
        return "Provide orthogonal characterization and lot-to-lot variability for CQA attributes."
    elif cluster.concern_level == "major":
        return "Provide additional lots and/or orthogonal method confirmation."
    else:
        return "Provide lot-to-lot variability context and manufacturing history."
