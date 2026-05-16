"""
Unified Regulatory Evidence Service (A-1).

Merges FDA/EMA/ICH regulatory intelligence from reg-intel-biopharma
and local modules (claim_evidence_grader, admissibility_engine) into a
single evidence layer that the comparability pipeline and future
reg-intel frontend can consume.

Design principle: works standalone (evidence grading always available)
and gets richer when reg-intel-biopharma is installed nearby.

SP v5 P2 Enhancement: Three self-contained product methods that work
WITHOUT reg-intel-biopharma dependency:
  - assess_submission_readiness()  -- Submission readiness checker
  - predict_reviewer_risks()       -- Reviewer question risk engine
  - find_supporting_precedent()    -- Precedent-aware gap review

Usage:
    from services.regulatory_evidence import RegulatoryEvidenceService
    svc = RegulatoryEvidenceService()
    grade = svc.grade_evidence("Phase 3 trial demonstrated significant improvement")
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from services.schemas import (
    AreaScore,
    EvidenceGrade,
    FindingClassification,
    PrecedentCard,
    PredictedQuestion,
    ReadinessReport,
    ReviewerQuestion,
)

# ---------------------------------------------------------------------------
# reg-intel-biopharma import (graceful fallback)
# Uses standard imports — install with: pip install -e ../reg-intel-biopharma
# ---------------------------------------------------------------------------

_REG_INTEL_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "reg-intel-biopharma")
)

_reg_intel_available = False

try:
    from modules.reg_precedent.search import search_precedents as _search_precedents
    from modules.reviewer_predictor.predict import predict_questions as _predict_questions
    from modules.submission_readiness.score import score_readiness as _score_readiness
    from modules.fda_warning_letters.classify import classify_warning_letter as _classify_wl

    _reg_intel_available = True
except ImportError:
    _search_precedents = None
    _predict_questions = None
    _score_readiness = None
    _classify_wl = None

# ---------------------------------------------------------------------------
# Local module imports (always available via editable install)
# ---------------------------------------------------------------------------

from models.claim_evidence_grader import predict as _predict_evidence, train_model as _train_model
from evidence_registry import EvidenceRegistry
from evidence_registry.registry import RegistryEntry
from services.reviewer_templates import match_templates, get_templates_by_category
from pipelines.submission_readiness import assess_readiness as _pipeline_assess_readiness


class RegulatoryEvidenceService:
    """Unified facade over regulatory-intelligence and evidence modules.

    All methods return typed dataclasses (never raw dicts).
    Methods that depend on reg-intel-biopharma raise ``RuntimeError``
    with a clear message when the external repo is not available.

    Self-contained methods (always available, no external dependency):
      - grade_evidence()
      - assess_submission_readiness()
      - predict_reviewer_risks()
      - find_supporting_precedent()
    """

    def __init__(self) -> None:
        # Lazily initialised evidence-grading model (sklearn).
        self._evidence_model = None
        self._evidence_vectorizer = None
        # Lazily initialised evidence registry.
        self._registry: Optional[EvidenceRegistry] = None

    # -- availability helpers ------------------------------------------------

    @staticmethod
    def reg_intel_available() -> bool:
        """Return True if the reg-intel-biopharma modules are importable."""
        return _reg_intel_available

    def _require_reg_intel(self, method_name: str) -> None:
        if not _reg_intel_available:
            raise RuntimeError(
                f"{method_name}() requires reg-intel-biopharma. "
                f"Expected at: {_REG_INTEL_ROOT}"
            )

    def _get_registry(self) -> EvidenceRegistry:
        """Get or create the evidence registry (lazy init)."""
        if self._registry is None:
            self._registry = EvidenceRegistry()
        return self._registry

    # =====================================================================
    # SELF-CONTAINED PRODUCT METHODS (SP v5 P2)
    # These work using ONLY local evidence_registry and existing modules.
    # =====================================================================

    # -- P2-1. Submission readiness assessment (self-contained) ------------

    def assess_submission_readiness(self, package_data: Dict[str, Any]) -> ReadinessReport:
        """Score submission readiness across 8 CMC evidence areas using local logic.

        Uses the evidence_registry to check which ICH requirements are covered,
        and evidence_closure to identify gaps. No external dependency required.

        Parameters
        ----------
        package_data : dict
            Keyed by evidence area id (e.g. ``"ds_characterization"``),
            each containing ``completeness`` (0-1) and optional ``gaps``.

        Returns
        -------
        ReadinessReport
        """
        # Convert dict-of-dicts to list-of-dicts format for the pipeline
        package_sections = []
        for area_id, info in package_data.items():
            section = {"area_id": area_id}
            if isinstance(info, dict):
                section["completeness"] = info.get("completeness", 0.0)
                section["gaps"] = info.get("gaps", [])
            else:
                section["completeness"] = float(info)
            package_sections.append(section)

        return _pipeline_assess_readiness(
            package_sections=package_sections,
            registry=self._get_registry(),
        )

    # -- P2-2. Reviewer question risk prediction (self-contained) ----------

    def predict_reviewer_risks(self, comparability_report) -> List[PredictedQuestion]:
        """Given a comparability report, predict likely reviewer questions.

        For each DEFER/INVESTIGATE action, generates likely reviewer questions
        using the local reviewer_templates. Uses evidence_registry precedents
        to identify historical reviewer concerns.

        Works with both ComparabilityReport dataclasses and plain dicts.

        Parameters
        ----------
        comparability_report : ComparabilityReport or dict
            A comparability assessment report containing attribute_results.

        Returns
        -------
        list[PredictedQuestion]
            Predicted questions with probability and suggested response.
        """
        registry = self._get_registry()
        predictions: List[PredictedQuestion] = []
        seen_template_ids: set = set()

        # Extract attribute results from report
        if hasattr(comparability_report, "attribute_results"):
            attributes = comparability_report.attribute_results
        elif isinstance(comparability_report, dict):
            attributes = comparability_report.get("attribute_results", [])
        else:
            return []

        question_counter = 0

        for attr in attributes:
            # Extract fields from dataclass or dict
            if hasattr(attr, "name"):
                attr_name = attr.name
                category = attr.category
                delta_pct = attr.delta_pct
                concern = attr.concern
                action_label = (attr.action or {}).get("action", "") if isinstance(getattr(attr, "action", None), dict) else ""
                pre_value = attr.pre_value
                post_value = attr.post_value
            elif isinstance(attr, dict):
                attr_name = attr.get("name", "")
                category = attr.get("category", "")
                delta_pct = attr.get("delta_pct", 0.0)
                concern = attr.get("concern", "none")
                action_info = attr.get("action", {})
                action_label = action_info.get("action_level", "") if isinstance(action_info, dict) else ""
                pre_value = attr.get("pre_value", 0.0)
                post_value = attr.get("post_value", 0.0)
            else:
                continue

            # Only generate questions for non-PROCEED attributes
            if action_label == "PROCEED" and concern == "none":
                continue

            # Match reviewer templates
            matched = match_templates(
                category=category,
                delta_pct=abs(delta_pct),
                concern=concern,
                action=action_label,
            )

            for template in matched:
                if template.id in seen_template_ids:
                    continue
                seen_template_ids.add(template.id)
                question_counter += 1

                # Format the question with available data
                try:
                    question_text = template.question_template.format(
                        attribute_name=attr_name,
                        delta_pct=abs(delta_pct),
                        pre_value=pre_value,
                        post_value=post_value,
                    )
                except (KeyError, IndexError):
                    question_text = template.question_template

                # Calculate probability based on action severity and delta
                probability = _estimate_question_probability(
                    action=action_label,
                    concern=concern,
                    delta_pct=abs(delta_pct),
                    threshold=template.delta_threshold_pct,
                )

                # Look for supporting precedent in registry
                precedent_entries = registry.query(
                    category=category,
                    entry_type="precedent",
                )
                precedent_note = ""
                if precedent_entries:
                    best = precedent_entries[0]
                    precedent_note = f" Precedent: {best.source} ({best.year})"

                predictions.append(PredictedQuestion(
                    id=f"PQ-{question_counter:03d}",
                    question=question_text,
                    category=category,
                    attribute_name=attr_name,
                    probability=probability,
                    severity=template.severity,
                    suggested_response=template.suggested_response_approach + precedent_note,
                    ich_reference=template.ich_reference,
                    trigger=f"{action_label or concern}: {template.trigger_condition}",
                    template_id=template.id,
                ))

        # Sort by probability descending
        predictions.sort(key=lambda p: p.probability, reverse=True)
        return predictions

    # -- P2-3. Precedent search (self-contained) ---------------------------

    def find_supporting_precedent(
        self,
        attribute_name: str,
        category: str,
        delta_pct: float,
    ) -> List[PrecedentCard]:
        """Find regulatory precedents supporting or challenging a specific delta.

        Queries the local evidence_registry for matching precedents and
        returns ranked PrecedentCards with relevance scores.

        Parameters
        ----------
        attribute_name : str
            Name of the quality attribute (e.g. "SEC Monomer %").
        category : str
            CMC category (e.g. "purity", "potency").
        delta_pct : float
            Observed delta percentage.

        Returns
        -------
        list[PrecedentCard]
            Ranked precedent cards with relevance scores.
        """
        registry = self._get_registry()
        results: List[PrecedentCard] = []

        # Query by category and precedent type
        entries = registry.query(category=category, entry_type="precedent")

        # Also search by keyword from attribute name
        attr_keywords = attribute_name.lower().replace("%", "").split()
        keyword_entries = []
        for kw in attr_keywords:
            if len(kw) >= 3:
                keyword_entries.extend(registry.query(keyword=kw, entry_type="precedent"))

        # Merge and deduplicate
        seen_ids: set = set()
        all_entries: List[RegistryEntry] = []
        for e in entries + keyword_entries:
            if e.id not in seen_ids:
                seen_ids.add(e.id)
                all_entries.append(e)

        # Also include guideline entries for context
        guideline_entries = registry.query(category=category, entry_type="guideline_clause")

        for entry in all_entries:
            relevance = _compute_precedent_relevance(
                entry=entry,
                category=category,
                delta_pct=delta_pct,
                attribute_name=attribute_name,
            )

            # Determine outcome interpretation
            content_lower = entry.content.lower()
            if "accepted" in content_lower or "approval" in content_lower or "approved" in content_lower:
                outcome = "accepted"
            elif "warning" in content_lower or "failure" in content_lower or "cited" in content_lower:
                outcome = "rejected_or_cited"
            elif "insufficient" in content_lower:
                outcome = "insufficient_data"
            else:
                outcome = "informational"

            # Determine agency
            source_upper = entry.source.upper()
            if "FDA" in source_upper:
                agency = "FDA"
            elif "EMA" in source_upper or "EPAR" in source_upper:
                agency = "EMA"
            elif "ICH" in source_upper:
                agency = "ICH"
            else:
                agency = "FDA/EMA"

            # Build relevance tags
            relevance_tags = list(set(entry.tags) & set(
                _category_relevant_tags(category)
            ))

            results.append(PrecedentCard(
                id=entry.id,
                title=entry.title,
                agency=agency,
                year=entry.year,
                issue_category=category,
                outcome=outcome,
                source=entry.source,
                relevance=relevance,
                relevance_tags=relevance_tags,
                molecule_type=_extract_molecule_type(entry),
            ))

        # Add guideline-based cards with lower relevance
        for entry in guideline_entries:
            if entry.id not in seen_ids:
                seen_ids.add(entry.id)
                results.append(PrecedentCard(
                    id=entry.id,
                    title=entry.title,
                    agency="ICH",
                    year=entry.year,
                    issue_category=category,
                    outcome="guideline_requirement",
                    source=entry.source,
                    relevance=round(entry.confidence * 0.5, 3),
                    relevance_tags=[t for t in entry.tags if t in _category_relevant_tags(category)],
                    molecule_type="",
                ))

        # Sort by relevance descending
        results.sort(key=lambda c: c.relevance, reverse=True)
        return results

    # =====================================================================
    # ORIGINAL METHODS (reg-intel-biopharma dependent)
    # =====================================================================

    # -- 1. Precedent search -------------------------------------------------

    def search_precedent(self, query: str, top_k: int = 5) -> List[PrecedentCard]:
        """Search the regulatory precedent database.

        Parameters
        ----------
        query : str
            Issue category or free-text query (e.g. ``"process_validation"``).
        top_k : int
            Maximum number of precedents to return.

        Returns
        -------
        list[PrecedentCard]
        """
        self._require_reg_intel("search_precedent")
        raw = _search_precedents(query, top_k=top_k)
        return [
            PrecedentCard(
                id=r["id"],
                title=r["title"],
                agency=r["agency"],
                year=r["year"],
                issue_category=r["issue_category"],
                outcome=r["outcome"],
                source=r["source"],
                relevance=r.get("relevance", 0.0),
                relevance_tags=r.get("relevance_tags", []),
                molecule_type=r.get("molecule_type", ""),
            )
            for r in raw
        ]

    # -- 2. Reviewer question prediction -------------------------------------

    def predict_reviewer_questions(
        self, context: Dict[str, Any], top_k: int = 5
    ) -> List[ReviewerQuestion]:
        """Predict likely reviewer questions for a submission.

        Parameters
        ----------
        context : dict
            Submission profile with keys such as ``molecule_type``,
            ``is_biosimilar``, ``clinical_phase``, ``identified_gaps``.
        top_k : int
            Maximum number of questions to return.

        Returns
        -------
        list[ReviewerQuestion]
        """
        self._require_reg_intel("predict_reviewer_questions")
        raw = _predict_questions(context, top_k=top_k)
        return [
            ReviewerQuestion(
                id=q.id,
                question=q.question,
                category=q.category,
                probability=q.probability,
                impact=q.impact,
                ich_reference=q.ich_reference,
                trigger=q.trigger,
            )
            for q in raw
        ]

    # -- 3. Submission readiness assessment -----------------------------------

    def assess_readiness(self, package_data: Dict[str, Any]) -> ReadinessReport:
        """Score CMC submission readiness across 8 evidence areas.

        Parameters
        ----------
        package_data : dict
            Keyed by evidence area id (e.g. ``"ds_characterization"``),
            each containing ``completeness`` (0-1) and optional ``gaps``.

        Returns
        -------
        ReadinessReport
        """
        self._require_reg_intel("assess_readiness")
        raw = _score_readiness(package_data)
        area_scores = [
            AreaScore(
                area_id=area_id,
                name=info["name"],
                score=info["score"],
                weight=info["weight"],
                status=info["status"],
                gaps=info.get("gaps", []),
            )
            for area_id, info in raw["area_scores"].items()
        ]
        return ReadinessReport(
            composite=raw["composite"],
            verdict=raw["verdict"],
            n_areas=raw["n_areas"],
            n_ready=raw["n_ready"],
            n_blockers=raw["n_blockers"],
            blockers=raw.get("blockers", []),
            area_scores=area_scores,
        )

    # -- 4. Finding classification --------------------------------------------

    def classify_finding(self, finding_text: str, top_k: int = 3) -> FindingClassification:
        """Classify a regulatory finding into FDA warning-letter categories.

        Parameters
        ----------
        finding_text : str
            Free-text description of a regulatory finding.
        top_k : int
            Number of top category matches to include.

        Returns
        -------
        FindingClassification
        """
        self._require_reg_intel("classify_finding")
        raw = _classify_wl(finding_text, top_k=top_k)
        return FindingClassification(
            primary_category=raw["primary_category"],
            primary_severity=raw["primary_severity"],
            n_categories_matched=raw["n_categories_matched"],
            top_categories=raw["top_categories"],
        )

    # -- 5. Evidence grading (always available) --------------------------------

    def grade_evidence(self, claim_text: str) -> EvidenceGrade:
        """Grade evidence strength of a scientific/regulatory claim.

        Always available (uses local claim_evidence_grader model).

        Parameters
        ----------
        claim_text : str
            The claim or evidence statement to grade.

        Returns
        -------
        EvidenceGrade
            Contains ``grade`` ("strong"/"moderate"/"weak"/"anecdotal")
            and per-class ``probabilities``.
        """
        if self._evidence_model is None:
            self._evidence_model, self._evidence_vectorizer = _train_model()

        result = _predict_evidence(
            claim_text,
            model=self._evidence_model,
            vectorizer=self._evidence_vectorizer,
        )
        return EvidenceGrade(
            grade=result["prediction"],
            probabilities=result["probabilities"],
        )


# =========================================================================
# Helper functions for self-contained methods
# =========================================================================

def _estimate_question_probability(
    action: str,
    concern: str,
    delta_pct: float,
    threshold: Optional[float],
) -> float:
    """Estimate the probability a reviewer will ask a specific question.

    Higher probability for more severe actions and larger deltas.
    """
    base = 0.30

    # Action-based boost
    action_boost = {
        "DEFER": 0.40,
        "INVESTIGATE": 0.30,
        "MONITOR": 0.15,
        "SUPPLEMENT": 0.10,
        "PROCEED": 0.0,
    }
    base += action_boost.get(action, 0.10)

    # Concern-based boost
    concern_boost = {
        "critical": 0.20,
        "major": 0.15,
        "minor": 0.05,
        "none": 0.0,
    }
    base += concern_boost.get(concern, 0.0)

    # Delta magnitude boost (relative to threshold)
    if threshold and threshold > 0:
        delta_ratio = abs(delta_pct) / threshold
        if delta_ratio >= 2.0:
            base += 0.10
        elif delta_ratio >= 1.5:
            base += 0.05

    return round(min(base, 0.95), 2)


def _compute_precedent_relevance(
    entry: RegistryEntry,
    category: str,
    delta_pct: float,
    attribute_name: str,
) -> float:
    """Compute relevance score for a precedent entry relative to a query."""
    score = entry.confidence * 0.5  # base from entry confidence

    # Category match
    if category in entry.applicable_categories:
        score += 0.20

    # Keyword matching from attribute name
    attr_lower = attribute_name.lower()
    content_lower = entry.content.lower()
    title_lower = entry.title.lower()
    keywords = [w for w in attr_lower.replace("%", "").split() if len(w) >= 3]
    keyword_hits = sum(1 for kw in keywords if kw in content_lower or kw in title_lower)
    if keywords:
        score += 0.15 * (keyword_hits / len(keywords))

    # Recency bonus
    if entry.year >= 2020:
        score += 0.10
    elif entry.year >= 2015:
        score += 0.05

    # Delta-based relevance: entries mentioning acceptance ranges
    if any(term in content_lower for term in ["acceptance", "accepted", "within", "range"]):
        score += 0.05

    return round(min(score, 1.0), 3)


def _category_relevant_tags(category: str) -> List[str]:
    """Return tags commonly relevant to a given CMC category."""
    tag_map = {
        "purity": ["purity", "SEC", "HMW", "aggregates", "charge_variants", "impurity", "HCP"],
        "potency": ["potency", "bioassay", "biological_activity", "functional_bridging", "binding"],
        "stability": ["stability", "accelerated", "shelf_life", "degradation"],
        "safety": ["HCP", "HCD", "impurity", "endotoxin"],
        "physicochemical": ["glycosylation", "characterization", "physicochemical", "structure"],
        "identity": ["characterization", "structure", "peptide_mapping", "comparability"],
    }
    return tag_map.get(category, [])


def _extract_molecule_type(entry: RegistryEntry) -> str:
    """Extract molecule type from entry tags or content."""
    tags = set(entry.tags)
    if "mAb" in tags or "mab" in entry.content.lower():
        return "mAb"
    if "biosimilar" in tags:
        return "biosimilar"
    if "fusion_protein" in tags:
        return "fusion_protein"
    if "etanercept" in entry.content.lower():
        return "fusion_protein"
    if "adalimumab" in entry.content.lower():
        return "mAb"
    return ""
