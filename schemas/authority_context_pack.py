"""
AuthorityContextPack — Structured authority evidence bundle.

One pack per cluster + one case-level pack. Provides evidence FACTS only.
Must NEVER embed verdict direction.

Hard rule: No confidence modifier, support_direction, verdict_implication,
or any field that prescribes a conclusion.

Step 0A: Judgment Core Refactor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class RefEntry:
    """Single reference entry within an AuthorityContextPack.

    Each entry represents one piece of authority evidence (ICH guideline,
    FDA precedent, analytical method reference, or concern pattern).
    """

    entry_id: str
    title: str
    source: str
    authority_quality_tier: str  # 'primary', 'strong_secondary', 'contextual'
    relevance_score: float
    decision_relevance_note: str  # WHY this is a top driver for this cluster


# Valid scope levels.
VALID_SCOPE_LEVELS = frozenset({"case", "cluster"})


@dataclass
class AuthorityContextPack:
    """Structured authority evidence bundle.

    Provides FACTS only. Never embeds verdict direction.

    CRITICAL: Case-level pack must NOT be the simple union of cluster
    packs. It must independently summarize the package-wide authority
    posture: cross-cluster conflict patterns, overall authority sparsity,
    and package-level normative coverage.

    Hard rule on n_refs_by_conclusion: This field is DESCRIPTIVE ONLY.
    It must never be consumed directly to infer verdict direction,
    support a proceed/supplement decision, or substitute for
    conservative_policy evaluation. Any code path that uses
    n_refs_by_conclusion to make a judgment call is a contract violation.
    """

    # ---- Identity ----
    pack_id: str
    scope_level: str  # 'case' or 'cluster'
    scope_id: str  # case_id or cluster_id this pack serves

    # ---- Reference Lists ----
    normative_refs: List[RefEntry] = field(default_factory=list)
    precedent_refs: List[RefEntry] = field(default_factory=list)
    method_refs: List[RefEntry] = field(default_factory=list)
    concern_pattern_refs: List[RefEntry] = field(default_factory=list)

    # ---- Descriptive Counts ----
    n_refs_by_type: Dict[str, int] = field(default_factory=dict)
    n_refs_by_conclusion: Dict[str, int] = field(default_factory=dict)

    # ---- Factual Flags ----
    authority_conflict_flag: bool = False
    temporal_conflict_flag: bool = False
    geography_conflict_flag: bool = False
    authority_sparsity_flag: bool = False

    # ---- Top Decision Drivers ----
    top_decision_drivers: List[RefEntry] = field(default_factory=list)

    # ---- Fallback Flags ----
    fallback_flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.scope_level not in VALID_SCOPE_LEVELS:
            raise ValueError(
                f"Invalid scope_level '{self.scope_level}'. "
                f"Must be 'case' or 'cluster'."
            )

    @staticmethod
    def generate_pack_id() -> str:
        """Generate a unique pack identifier."""
        return f"PACK-{uuid.uuid4().hex[:12].upper()}"
