"""
Reference Matcher Service (P0-5).

Implements ROL Spec Section 4: Evidence Filtering and Matching.

Three-stage pipeline:
  1. Filter -- narrow by applicability (change type, molecule class, lifecycle stage)
  2. Score -- rank by relevance (change match 0.25, category 0.25, concern 0.20, weight 0.15, recency 0.10, escalation 0.05)
  3. Select -- pick type-diverse top-K (normative 3-5, precedent 2-3, method 1-2, concern 1-2)

Returns ranked list of ReferenceMatchResult for frontend display.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import datetime

from evidence_registry import EvidenceRegistry, RegistryEntry
from schemas.case_context import CaseContext


def build_matcher_context(
    change_type: str,
    molecule_class: str,
    lifecycle_stage: str,
    target_geography: str = "global",
    flagged_attributes: List[str] = None,
    flagged_categories: List[str] = None,
    identified_gaps: List[str] = None,
    current_action_level: str = "PROCEED",
    change_description: str = "",
) -> CaseContext:
    """Build a canonical CaseContext for the reference matcher.

    Provides a convenience constructor that accepts the field names
    historically used by ReferenceMatcher (e.g. ``flagged_attributes``)
    and maps them to the canonical schema fields (``flagged_attribute_ids``,
    ``current_action_ceiling``).
    """
    return CaseContext(
        change_type=change_type,
        molecule_class=molecule_class,
        lifecycle_stage=lifecycle_stage,
        change_description=change_description or f"{change_type} assessment",
        target_geography=target_geography,
        flagged_attribute_ids=flagged_attributes or [],
        flagged_categories=flagged_categories or [],
        identified_gaps=identified_gaps or [],
        current_action_ceiling=current_action_level,
    )


@dataclass
class ReferenceMatchResult:
    """One matched reference entry."""
    entry_id: str
    relevance_score: float
    match_reason: str
    display_tier: str
    entry_type: str = ""
    title: str = ""
    source: str = ""
    confidence: float = 0.5
    evidence_weight: str = "normative"


class ReferenceMatcher:
    """Three-stage reference matcher: filter -> score -> select."""

    # Scoring weights (must sum to 1.0)
    SCORE_WEIGHTS = {
        'change_type_match': 0.25,
        'category_overlap': 0.25,
        'concern_match': 0.20,
        'evidence_weight': 0.15,
        'recency': 0.10,
        'escalation': 0.05,
    }

    # Type-diverse selection targets
    TYPE_SELECTION = {
        'guideline_clause': {'min': 3, 'max': 5},
        'precedent': {'min': 2, 'max': 3},
        'method_standard': {'min': 1, 'max': 2},
        'issue_taxonomy': {'min': 1, 'max': 2},
    }

    def __init__(self, registry: Optional[EvidenceRegistry] = None):
        if registry is None:
            self.registry = EvidenceRegistry()
        else:
            self.registry = registry

    def match(self, case_context: CaseContext, top_k: int = 15) -> List[ReferenceMatchResult]:
        """Execute the three-stage matching pipeline."""
        # Stage 1: Filter
        filtered = self._filter_by_applicability(case_context)

        if not filtered:
            return []

        # Stage 2: Score
        scored = [
            (entry, self._score_relevance(entry, case_context))
            for entry in filtered
        ]
        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        scored = scored[:top_k]

        # Stage 3: Select (type-diverse top-K)
        selected = self._select_type_diverse(scored)

        # Convert to ReferenceMatchResult
        return [self._to_match_result(entry, score) for entry, score in selected]

    def _filter_by_applicability(self, case_context: CaseContext) -> List[RegistryEntry]:
        """Stage 1: Filter by applicability constraints."""
        filtered = []

        for entry in self.registry._entries:
            # Check change type applicability
            if case_context.change_type not in entry.applies_to_change_types and \
               "all" not in entry.applies_to_change_types:
                continue

            # Check molecule class applicability
            if case_context.molecule_class not in entry.applies_to_molecule_classes and \
               "all" not in entry.applies_to_molecule_classes:
                continue

            # Check lifecycle stage applicability
            if case_context.lifecycle_stage not in entry.applies_to_lifecycle_stages and \
               "all" not in entry.applies_to_lifecycle_stages:
                continue

            # Check geography
            if case_context.target_geography not in entry.geography and \
               "global" not in entry.geography:
                continue

            # Check temporal status
            if entry.temporal_status in ("historical_only",) or \
               entry.temporal_status.startswith("superseded_by:"):
                continue

            filtered.append(entry)

        return filtered

    def _score_relevance(self, entry: RegistryEntry, case_context: CaseContext) -> float:
        """Stage 2: Score relevance based on 6 factors."""
        scores = {}

        # change_type_match
        change_match = 0.0
        if case_context.change_type in entry.applies_to_change_types or \
           "all" in entry.applies_to_change_types:
            change_match = 1.0
        scores['change_type_match'] = change_match

        # category_overlap
        cat_overlap = 0.0
        _flagged_cats = getattr(case_context, 'flagged_categories', []) or []
        if _flagged_cats:
            overlap = set(_flagged_cats) & set(entry.applicable_categories)
            cat_overlap = len(overlap) / len(set(_flagged_cats))
        scores['category_overlap'] = cat_overlap

        # concern_match
        concern_match = 0.0
        if entry.likely_concern_categories:
            overlap = set(entry.likely_concern_categories) & set(_flagged_cats)
            concern_match = len(overlap) / len(entry.likely_concern_categories) if entry.likely_concern_categories else 0.0
        scores['concern_match'] = concern_match

        # evidence_weight
        weight_to_score = {
            'binding': 1.0,
            'normative': 0.9,
            'advisory': 0.7,
            'informative': 0.5,
            'contextual': 0.3,
        }
        scores['evidence_weight'] = weight_to_score.get(entry.evidence_weight, 0.5)

        # recency
        current_year = datetime.now().year
        if entry.year > 0:
            age = current_year - entry.year
            recency = max(0.0, 1.0 - (age / 20.0))
        else:
            recency = 0.5  # Unknown year = neutral
        scores['recency'] = recency

        # escalation (bonus if escalation-triggering and action level high)
        escalation_score = 0.0
        _action_level = getattr(case_context, 'current_action_ceiling', None) or "PROCEED"
        if entry.triggers_escalation and \
           _action_level in ("INVESTIGATE", "DEFER"):
            escalation_score = 1.0
        scores['escalation'] = escalation_score

        # Weighted average
        weighted_sum = sum(
            score * self.SCORE_WEIGHTS[key]
            for key, score in scores.items()
        )

        return weighted_sum

    def _select_type_diverse(
        self,
        scored: List[tuple],
    ) -> List[tuple]:
        """Stage 3: Select type-diverse top-K."""
        if not scored:
            return []

        # Group by type
        by_type = {}
        for entry, score in scored:
            entry_type = entry.entry_type
            if entry_type not in by_type:
                by_type[entry_type] = []
            by_type[entry_type].append((entry, score))

        # Select from each type
        selected = []
        for entry_type, items in by_type.items():
            target = self.TYPE_SELECTION.get(entry_type, {'min': 0, 'max': 2})
            selected.extend(items[:target['max']])

        # Re-sort overall by score descending
        selected.sort(key=lambda x: x[1], reverse=True)

        return selected

    def _to_match_result(self, entry: RegistryEntry, score: float) -> ReferenceMatchResult:
        """Convert RegistryEntry + score to ReferenceMatchResult."""
        return ReferenceMatchResult(
            entry_id=entry.id,
            relevance_score=round(score, 3),
            match_reason=self._build_match_reason(entry),
            display_tier=entry.display_tier,
            entry_type=entry.entry_type,
            title=entry.title,
            source=entry.source,
            confidence=entry.confidence,
            evidence_weight=entry.evidence_weight,
        )

    @staticmethod
    def _build_match_reason(entry: RegistryEntry) -> str:
        """Build a human-readable reason for this match."""
        reasons = []
        if entry.entry_type == "guideline_clause":
            reasons.append(f"Guideline: {entry.source} {entry.id}")
        elif entry.entry_type == "precedent":
            reasons.append(f"Precedent: {entry.source}")
        else:
            reasons.append(f"{entry.entry_type}: {entry.source}")

        if entry.evidence_weight in ("binding", "normative"):
            reasons.append(f"({entry.evidence_weight})")

        return " ".join(reasons)
