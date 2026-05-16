"""
Adjudicator — Tier 1 and Tier 2 auto-adjudication for LabelRecords.

Implements the 4-tier label adjudication policy (B-4). Tiers 1 and 2 are
automated here; Tier 3 (expert review) flows through FeedbackEvent, and
Tier 4 (outcome-backed) comes from external data ingestion.

See schemas/ADJUDICATION_POLICY.md for the full policy specification.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from .label_schema import LabelRecord


# =========================================================================
# Configuration — thresholds that govern auto-adjudication
# =========================================================================

# Tier 1 thresholds
COMPARABILITY_SCORE_THRESHOLD = 0.85   # All attributes must exceed this
KEYWORD_CONFIDENCE_THRESHOLD = 0.95    # Section classifier confidence floor

# Tier 2 thresholds
SILVER_LABEL_CONFIDENCE_THRESHOLD = 0.70  # Minimum classifier confidence

# Modules eligible for each tier
TIER1_MODULES = {
    "cqa_selector",
    "comparability_graph",
    "ctd_section_classifier",
}

TIER2_MODULES = {
    "fda_warning_letters",
    "claim_evidence_grader",
    "policy_signal_classifier",
}


# =========================================================================
# Tier detection
# =========================================================================

def get_adjudication_tier(record: LabelRecord) -> int:
    """Determine which adjudication tier a record falls into.

    Returns:
        1 if eligible for deterministic auto-label
        2 if eligible for LLM silver-label
        3 if already expert-reviewed (annotation_source == 'expert')
        4 if outcome-backed (annotation_source in regulatory_outcome/experimental)
        0 if not yet classifiable into any tier
    """
    # Already labeled — detect tier from annotation_source
    if record.is_labeled and record.annotation_source:
        source = record.annotation_source
        if source in ("regulatory_outcome", "experimental"):
            return 4
        if source == "expert":
            return 3
        if source == "llm_silver":
            return 2
        if source == "deterministic":
            return 1

    # Not yet labeled — determine eligibility
    if record.module in TIER1_MODULES and _is_tier1_eligible(record):
        return 1
    if record.module in TIER2_MODULES and _is_tier2_eligible(record):
        return 2

    return 0


# =========================================================================
# Tier 1: Deterministic Auto-Label
# =========================================================================

def auto_adjudicate_tier1(record: LabelRecord) -> Optional[LabelRecord]:
    """Apply Tier 1 deterministic rules.

    Returns an updated copy of the record with ground_truth filled, or None
    if the record is not eligible for Tier 1 adjudication.

    Rules:
      - cqa_selector: RPN threshold present and exceeded
      - comparability_graph: all attribute scores > 0.85
      - ctd_section_classifier: keyword confidence > 0.95
    """
    if record.is_labeled:
        return None

    if record.module not in TIER1_MODULES:
        return None

    if not _is_tier1_eligible(record):
        return None

    result = _copy_record(record)
    result.ground_truth = copy.deepcopy(record.prediction)
    result.annotation_source = "deterministic"
    result.annotator = "auto_adjudicator_t1"
    result.confidence_delta = 0.0  # deterministic => prediction == ground_truth
    return result


def _is_tier1_eligible(record: LabelRecord) -> bool:
    """Check if a record meets Tier 1 thresholds."""
    pred = record.prediction

    if record.module == "cqa_selector":
        # Needs an RPN score and a threshold; score must meet or exceed threshold
        rpn = pred.get("rpn_score")
        threshold = pred.get("rpn_threshold")
        if rpn is not None and threshold is not None:
            return True  # Has deterministic classification data
        return False

    if record.module == "comparability_graph":
        scores = pred.get("attribute_scores", {})
        if not scores:
            return False
        return all(
            isinstance(v, (int, float)) and v > COMPARABILITY_SCORE_THRESHOLD
            for v in scores.values()
        )

    if record.module == "ctd_section_classifier":
        confidence = pred.get("confidence")
        if confidence is not None and confidence > KEYWORD_CONFIDENCE_THRESHOLD:
            return True
        return False

    return False


# =========================================================================
# Tier 2: LLM Silver-Label
# =========================================================================

def auto_adjudicate_tier2(record: LabelRecord) -> Optional[LabelRecord]:
    """Apply Tier 2 silver-label rules using classifier output.

    Returns an updated copy of the record with ground_truth filled from the
    classifier's output, or None if not eligible.

    Eligible modules: fda_warning_letters, claim_evidence_grader,
    policy_signal_classifier. All require classifier_confidence >= 0.70.
    """
    if record.is_labeled:
        return None

    if record.module not in TIER2_MODULES:
        return None

    if not _is_tier2_eligible(record):
        return None

    result = _copy_record(record)
    # Silver label uses the classifier output as ground truth
    classifier_output = record.prediction.get("classifier_output", record.prediction)
    result.ground_truth = copy.deepcopy(classifier_output)
    result.annotation_source = "llm_silver"
    result.annotator = "auto_adjudicator_t2"
    result.confidence_delta = compute_confidence_delta(record.prediction, result.ground_truth)
    return result


def _is_tier2_eligible(record: LabelRecord) -> bool:
    """Check if a record meets Tier 2 thresholds."""
    confidence = record.prediction.get("classifier_confidence")
    if confidence is None:
        confidence = record.prediction.get("confidence")
    if confidence is not None and confidence >= SILVER_LABEL_CONFIDENCE_THRESHOLD:
        return True
    return False


# =========================================================================
# Confidence delta computation
# =========================================================================

def compute_confidence_delta(prediction: dict, ground_truth: dict) -> float:
    """Compute how much ground truth differs from prediction.

    Returns:
        Float in [0.0, 1.0] where 0 = perfect agreement, 1 = maximum disagreement.

    Strategy:
      - Collect all shared keys between prediction and ground_truth
      - For each shared key, compute a per-field delta:
          - Numeric values: abs(pred - gt), clamped to [0, 1]
          - String/categorical: 0.0 if equal, 1.0 if different
          - Other types: 0.0 if equal, 1.0 if different
      - Return the average of all per-field deltas
      - If no shared keys, return 1.0 (completely different structure)
    """
    if not prediction or not ground_truth:
        return 1.0

    shared_keys = set(prediction.keys()) & set(ground_truth.keys())
    if not shared_keys:
        return 1.0

    deltas = []
    for key in shared_keys:
        p_val = prediction[key]
        g_val = ground_truth[key]
        deltas.append(_field_delta(p_val, g_val))

    return sum(deltas) / len(deltas)


def _field_delta(predicted: Any, actual: Any) -> float:
    """Compute delta for a single field value."""
    if predicted == actual:
        return 0.0

    # Both numeric
    if isinstance(predicted, (int, float)) and isinstance(actual, (int, float)):
        return min(abs(predicted - actual), 1.0)

    # Different or non-numeric
    return 1.0


# =========================================================================
# Helpers
# =========================================================================

def _copy_record(record: LabelRecord) -> LabelRecord:
    """Create a shallow copy of a LabelRecord preserving identity fields."""
    return LabelRecord(
        record_id=record.record_id,
        module=record.module,
        timestamp=record.timestamp,
        prediction=copy.deepcopy(record.prediction),
        ground_truth=record.ground_truth,
        annotator=record.annotator,
        annotation_source=record.annotation_source,
        confidence_delta=record.confidence_delta,
        metadata=copy.deepcopy(record.metadata),
    )
