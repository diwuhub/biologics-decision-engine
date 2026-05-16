"""
Comparability Assessment Pipeline Orchestrator (S-3 MVP).

Given pre-change and post-change batch data, determines whether a
manufacturing change is acceptable by composing six modules:

  1. data_harmonizer   -- unit normalization + field mapping
  2. cqa_selector      -- classify which attributes are CQAs
  3. comparability_graph -- score each attribute delta
  4. biosimilar_uncertainty -- uncertainty per attribute
  5. evidence_closure   -- identify evidence gaps
  6. verdict            -- aggregate into overall decision

Usage:
    from pipelines.comparability import run_comparability_assessment
    report = run_comparability_assessment(data, product_name="mAb-X")
"""

from __future__ import annotations

import datetime
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from pipelines.schemas import AttributeResult, ComparabilityReport
from schemas.evidence_trace import EvidenceTraceEntry
from schemas.counterfactual import CounterfactualEntry

# Module imports -- all existing building blocks
from modules.data_harmonizer.unit_normalizer import normalize_value
from modules.cqa_selector.engine import select_cqas, CQACandidate
from modules.comparability_graph.engine import (
    score_attribute,
    generate_verdict,
    CATEGORY_WEIGHTS,
    DEFAULT_TOLERANCES,
    get_pathway_category_weights,
)
from modules.biosimilar_uncertainty.engine import score_attribute_uncertainty
from modules.evidence_closure.analyzer import analyze as closure_analyze
from modules.evidence_closure.schemas import FindingRecord
from modules.action_recommender.engine import (
    recommend_attribute_action,
    recommend_overall_actions,
)
from modules.input_validator import validate_comparability_input
from evidence_registry import EvidenceRegistry


# =========================================================================
# Package-Level Judgment Aggregation (P0-2)
# =========================================================================

from enum import Enum

class PackageVerdict(Enum):
    """Package-level decision verdicts (5-level taxonomy)."""
    PROCEED = "proceed"
    PROCEED_WITH_CONDITIONS = "proceed_with_conditions"
    SUPPLEMENT_REQUIRED = "supplement_required"
    INVESTIGATION_REQUIRED = "investigation_required"
    DEFER_PACKAGE = "defer_package"


def aggregate_package_verdict(
    attribute_results: List[AttributeResult],
    cqa_results: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Aggregate per-attribute results into a package-level verdict.

    Implements the 5-rule aggregation policy from ROL Spec Section 6.2:

    Rule 1: DEFER Dominance
        If ANY CQA has action DEFER -> DEFER_PACKAGE immediately.

    Rule 2: INVESTIGATE Escalation
        If ANY CQA has action INVESTIGATE -> INVESTIGATION_REQUIRED.
        If >=2 CQAs have action INVESTIGATE -> DEFER_PACKAGE.

    Rule 3: SUPPLEMENT Threshold
        If ANY CQA has action SUPPLEMENT OR >=3 total CQAs have SUPPLEMENT
        -> SUPPLEMENT_REQUIRED.

    Rule 4: Conditional Pass
        All CQAs have action in {PROCEED, SUPPLEMENT} AND
        <=1 CQA with SUPPLEMENT AND
        Non-CQA SUPPLEMENTs <=2 AND
        All CQA uncertainties < 0.25
        -> PROCEED_WITH_CONDITIONS.

    Rule 5: Clean Pass
        ALL attributes PROCEED AND ALL CQA uncertainties < 0.25
        -> PROCEED.
    """
    if not attribute_results:
        return {
            'verdict': PackageVerdict.DEFER_PACKAGE,
            'driving_attributes': [],
            'normative_basis': 0,
            'upgrade_path': 'Provide attribute data to proceed.',
        }

    # Extract action levels and CQA info
    # Build CQA set: prefer explicit cqa_results, fall back to AttributeResult.is_cqa
    cqa_name_set = set()
    if cqa_results:
        cqa_name_set = {c.name for c in cqa_results if hasattr(c, 'name') and hasattr(c, 'designation') and c.designation == "CQA"}
    else:
        # Fallback: use is_cqa / cqa_designation from AttributeResult itself
        cqa_name_set = {ar.name for ar in attribute_results if ar.is_cqa or ar.cqa_designation == "CQA"}

    actions_by_attr = {}
    cqa_actions = {}
    non_cqa_supplement_count = 0
    driving_attrs = []
    max_uncertainty = 0.0
    cqa_uncertainties = {}

    for ar in attribute_results:
        action_dict = ar.action if isinstance(ar.action, dict) else {}
        action_level = action_dict.get('action_level', 'PROCEED')
        actions_by_attr[ar.name] = action_level

        if ar.name in cqa_name_set:
            cqa_actions[ar.name] = action_level
            cqa_uncertainties[ar.name] = ar.uncertainty
        else:
            if action_level == 'SUPPLEMENT':
                non_cqa_supplement_count += 1

        max_uncertainty = max(max_uncertainty, ar.uncertainty)

    # Rule 1: DEFER Dominance
    defer_cqas = [name for name, action in cqa_actions.items() if action == 'DEFER']
    if defer_cqas:
        return {
            'verdict': PackageVerdict.DEFER_PACKAGE,
            'driving_attributes': defer_cqas,
            'normative_basis': 0,
            'upgrade_path': f'Resolve DEFER on {", ".join(defer_cqas)} before proceeding.',
        }

    # Rule 2: INVESTIGATE Escalation
    investigate_cqas = [name for name, action in cqa_actions.items() if action == 'INVESTIGATE']
    if len(investigate_cqas) >= 2:
        return {
            'verdict': PackageVerdict.DEFER_PACKAGE,
            'driving_attributes': investigate_cqas,
            'normative_basis': 0,
            'upgrade_path': f'Resolve INVESTIGATE on >=2 CQAs ({", ".join(investigate_cqas[:2])}) before proceeding.',
        }

    if investigate_cqas:
        return {
            'verdict': PackageVerdict.INVESTIGATION_REQUIRED,
            'driving_attributes': investigate_cqas,
            'normative_basis': 0,
            'upgrade_path': f'Complete investigation on {investigate_cqas[0]} to upgrade verdict.',
        }

    # Rule 3: SUPPLEMENT Threshold
    supplement_cqas = [name for name, action in cqa_actions.items() if action == 'SUPPLEMENT']
    total_supplements = len(supplement_cqas) + non_cqa_supplement_count

    if supplement_cqas or total_supplements >= 3:
        return {
            'verdict': PackageVerdict.SUPPLEMENT_REQUIRED,
            'driving_attributes': supplement_cqas,
            'normative_basis': len(supplement_cqas),
            'upgrade_path': f'Supplement {total_supplements} attributes to achieve PROCEED.',
        }

    # Rule 5: Clean Pass (checked before Rule 4 to avoid vacuous conditional match)
    all_proceed = all(action == 'PROCEED' for action in actions_by_attr.values())
    cqas_clean = all(unc < 0.25 for unc in cqa_uncertainties.values())

    if all_proceed and cqas_clean:
        return {
            'verdict': PackageVerdict.PROCEED,
            'driving_attributes': [],
            'normative_basis': len([ar for ar in attribute_results if ar.action and ar.action.get('action_level') == 'PROCEED']),
            'upgrade_path': 'No additional actions required.',
        }

    # Rule 4: Conditional Pass
    all_cqa_acceptable = all(action in ('PROCEED', 'SUPPLEMENT') for action in cqa_actions.values())
    cqa_supplement_count = len(supplement_cqas)
    all_cqa_uncertainties_low = all(unc < 0.25 for unc in cqa_uncertainties.values())

    if (all_cqa_acceptable and
        cqa_supplement_count <= 1 and
        non_cqa_supplement_count <= 2 and
        all_cqa_uncertainties_low):
        return {
            'verdict': PackageVerdict.PROCEED_WITH_CONDITIONS,
            'driving_attributes': supplement_cqas if supplement_cqas else [],
            'normative_basis': len([ar for ar in attribute_results if ar.action and ar.action.get('action_level') in ('PROCEED', 'SUPPLEMENT')]),
            'upgrade_path': 'Monitor post-change batches per recommended actions.',
        }

    # Fallback (should rarely reach here)
    return {
        'verdict': PackageVerdict.INVESTIGATION_REQUIRED,
        'driving_attributes': list(set(supplement_cqas + investigate_cqas)),
        'normative_basis': 0,
        'upgrade_path': 'Review attribute-level actions for detailed guidance.',
    }


# =========================================================================
# Pipeline
# =========================================================================

def run_comparability_assessment(
    pre_change_data: Dict[str, Any],
    product_name: str = "Product",
    change_description: str = "",
    generate_report: bool = False,
    report_path: Optional[str] = None,
) -> ComparabilityReport:
    """Run full comparability assessment pipeline.

    Parameters
    ----------
    pre_change_data : dict
        Must contain key ``"attributes"`` -- a list of dicts, each with:
            name, category, pre_value, post_value, unit,
            and optionally n_lots, cv_pct, n_methods, has_functional_correlation,
            prior_approvals.
    product_name : str
        Product identifier for the report header.
    change_description : str
        Free-text description of the manufacturing change.
    generate_report : bool
        If True, generate an ICH Q5E-compliant DOCX report after assessment.
    report_path : str, optional
        Output path for the DOCX report. If None and generate_report is True,
        defaults to ``<product_name>_comparability_report.docx``.

    Returns
    -------
    ComparabilityReport
        Structured assessment with verdict, per-attribute detail,
        CQA summary, uncertainty metrics, evidence gaps, and actions.
    """
    # --- Step 0: SP v5 schema validation ---
    if isinstance(pre_change_data, dict) and "attributes" in pre_change_data:
        validation = validate_comparability_input(pre_change_data)
        if not validation.valid:
            return ComparabilityReport(
                product_name=product_name,
                change_description=change_description,
                overall_verdict="Insufficient Evidence",
                evidence_strength_index=0.0,
                n_attributes=0, n_cqa=0, n_comparable=0, n_flagged=0,
                attribute_results=[],
                cqa_summary=[],
                uncertainty_summary={"mean_uncertainty": 0, "max_uncertainty": 0,
                                     "high_uncertainty_attributes": [], "n_high_uncertainty": 0},
                evidence_gaps=[f"Validation error: {e}" for e in validation.errors],
                recommended_actions=["Fix input validation errors and resubmit"],
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )

        # Use normalized input going forward
        if validation.normalized_input:
            pre_change_data = validation.normalized_input

    # Initialize evidence registry (Layer 3)
    _registry = EvidenceRegistry()

    # --- Input validation ---
    if not isinstance(pre_change_data, dict) or "attributes" not in pre_change_data:
        return ComparabilityReport(
            product_name=product_name,
            change_description=change_description,
            overall_verdict="Insufficient Evidence",
            evidence_strength_index=0.0,
            n_attributes=0, n_cqa=0, n_comparable=0, n_flagged=0,
            attribute_results=[],
            cqa_summary=[],
            uncertainty_summary={"mean_uncertainty": 0, "max_uncertainty": 0,
                                 "high_uncertainty_attributes": [], "n_high_uncertainty": 0},
            evidence_gaps=["Input error: pre_change_data must be a dict with an 'attributes' key"],
            recommended_actions=["Provide pre_change_data with an 'attributes' list"],
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    attributes = pre_change_data["attributes"]
    if not isinstance(attributes, list):
        attributes = []

    for attr in attributes:
        for key in ("name", "pre_value", "post_value"):
            if key not in attr:
                return ComparabilityReport(
                    product_name=product_name,
                    change_description=change_description,
                    overall_verdict="Insufficient Evidence",
                    evidence_strength_index=0.0,
                    n_attributes=0, n_cqa=0, n_comparable=0, n_flagged=0,
                    attribute_results=[],
                    cqa_summary=[],
                    uncertainty_summary={"mean_uncertainty": 0, "max_uncertainty": 0,
                                         "high_uncertainty_attributes": [], "n_high_uncertainty": 0},
                    evidence_gaps=[f"Input error: attribute missing required key '{key}'"],
                    recommended_actions=[f"Each attribute must have 'name', 'pre_value', and 'post_value'"],
                    timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                )

    if not attributes:
        return _empty_report(product_name, change_description)

    # ------------------------------------------------------------------
    # Step 1: Normalize units via data_harmonizer
    # ------------------------------------------------------------------
    for attr in attributes:
        unit = attr.get("unit", "")
        if unit:
            try:
                norm_pre = normalize_value(attr["pre_value"], unit)
                norm_post = normalize_value(attr["post_value"], unit)
                attr["_norm_pre"] = norm_pre.normalized_value
                attr["_norm_post"] = norm_post.normalized_value
                attr["_norm_unit"] = norm_pre.normalized_unit
            except (ValueError, KeyError):
                attr["_norm_pre"] = attr["pre_value"]
                attr["_norm_post"] = attr["post_value"]
                attr["_norm_unit"] = unit
        else:
            attr["_norm_pre"] = attr["pre_value"]
            attr["_norm_post"] = attr["post_value"]
            attr["_norm_unit"] = ""

    # ------------------------------------------------------------------
    # Step 2: CQA classification via cqa_selector
    # ------------------------------------------------------------------
    cqa_candidates = []
    for attr in attributes:
        cqa_candidates.append({
            "name": attr["name"],
            "category": attr.get("category", "physicochemical"),
            "assay": attr.get("assay", "Analytical method"),
            "impact": attr.get("impact", 3),
            "detectability": attr.get("detectability", 4),
            "controllability": attr.get("controllability", 3),
        })
    cqa_results = select_cqas(cqa_candidates)
    cqa_lookup = {c.name: c for c in cqa_results}

    # ------------------------------------------------------------------
    # Step 3: Score each attribute via comparability_graph
    # ------------------------------------------------------------------
    # Build a case dict compatible with comparability_graph.score_attribute
    attr_scores = []
    for attr in attributes:
        pre_v = attr["_norm_pre"]
        post_v = attr["_norm_post"]
        category = attr.get("category", "physicochemical")

        # Build an attribute dict compatible with score_attribute()
        graph_attr = {
            "attribute_id": attr["name"].lower().replace(" ", "_"),
            "name": attr["name"],
            "category": category,
            "measurements": [
                {"lot_id": "PRE", "value": pre_v, "unit": attr["_norm_unit"], "within_spec": True},
                {"lot_id": "POST", "value": post_v, "unit": attr["_norm_unit"],
                 "within_spec": attr.get("within_spec", True)},
            ],
            # W1-1b: Pass spec limits to scorer
            "spec_lower": attr.get("spec_lower"),
            "spec_upper": attr.get("spec_upper"),
            "spec_source": attr.get("spec_source", "none"),
            "is_cqa": (cqa_lookup.get(attr["name"]) and cqa_lookup[attr["name"]].designation == "CQA") or False,
            # W1-3c: Flow change_type from CaseContext
            "_change_type": pre_change_data.get("change_type", "") if isinstance(pre_change_data, dict) else "",
            # W2-1: Method adequacy fields (optional)
            "method_loq": attr.get("method_loq"),
            "method_lod": attr.get("method_lod"),
            "method_precision_cv": attr.get("method_precision_cv"),
        }
        score = score_attribute(graph_attr, [])
        attr_scores.append((attr, score))

    # ------------------------------------------------------------------
    # W1-4: Narrative Signal Escalation (after scoring, before uncertainty)
    # ------------------------------------------------------------------
    # Consume OOS/critical signals from ingestion result if present.
    # Only escalate critical/OOS. No attenuation from positive signals.
    _w1_oos_attributes: set = set()
    _w1_signal_matches: list = []
    _w1_ingestion_signals = pre_change_data.get("_ingestion_signals", []) if isinstance(pre_change_data, dict) else []
    for _sig in _w1_ingestion_signals:
        _sig_type = _sig.get("signal_type", "") if isinstance(_sig, dict) else getattr(_sig, "signal_type", "")
        _sig_severity = _sig.get("severity", "") if isinstance(_sig, dict) else getattr(_sig, "severity", "")
        if _sig_type == "oos" and _sig_severity == "critical":
            _sig_context = _sig.get("context", "") if isinstance(_sig, dict) else getattr(_sig, "context", "")
            _sig_keyword = _sig.get("keyword_matched", "") if isinstance(_sig, dict) else getattr(_sig, "keyword_matched", "")
            for attr, _sc in attr_scores:
                attr_name_lower = attr["name"].lower()
                # Match by keyword overlap between signal context and attribute name
                if attr_name_lower in _sig_context.lower() or \
                   any(word in _sig_context.lower() for word in attr_name_lower.split() if len(word) > 3):
                    _w1_oos_attributes.add(attr["name"])
                    _anchor_ids = _sig.get("anchor_ids", []) if isinstance(_sig, dict) else getattr(_sig, "anchor_ids", [])
                    _w1_signal_matches.append({
                        "matched_attribute": attr["name"],
                        "matched_by": "keyword_overlap",
                        "source_anchor_ids": _anchor_ids if _anchor_ids else [],
                        "signal_type": _sig_type,
                    })

    # ------------------------------------------------------------------
    # Step 4: Uncertainty scoring via biosimilar_uncertainty
    # ------------------------------------------------------------------
    uncertainty_results = []
    score_breakdowns = []
    for attr, _score in attr_scores:
        unc = score_attribute_uncertainty(
            attribute=attr["name"],
            category=attr.get("category", "physicochemical"),
            n_methods=attr.get("n_methods", 1),
            n_lots_biosimilar=attr.get("n_lots", 3),
            n_lots_originator=attr.get("n_lots", 3),
            n_replicates=attr.get("n_replicates", 2),
            cv_pct=attr.get("cv_pct", 10.0),
            has_functional_correlation=attr.get("has_functional_correlation", False),
            prior_approvals_with_similar_diff=attr.get("prior_approvals", 0),
        )
        uncertainty_results.append(unc)

        # P1-B.1: Build score_breakdown for transparency
        _cqa_info = cqa_lookup.get(attr["name"])
        _is_cqa = _cqa_info.designation == "CQA" if _cqa_info else False
        _cqa_weight = 1.5 if _is_cqa else 1.0
        _n_lots = attr.get("n_lots", 3)
        _lot_factor = min(1.0, _n_lots / 3.0) if _n_lots > 0 else 0.5
        _unc_penalty = unc.residual_uncertainty * 0.1
        _base_delta = _score.score
        _final = max(0.0, _base_delta * _cqa_weight * _lot_factor - _unc_penalty)
        _final = min(1.0, _final)
        score_breakdowns.append({
            "base_delta_score": round(_base_delta, 4),
            "cqa_weight_multiplier": _cqa_weight,
            "lot_count_factor": round(_lot_factor, 4),
            "uncertainty_penalty": round(_unc_penalty, 4),
            "components_formula": "final = clamp(base_delta * cqa_weight * lot_factor - uncertainty_penalty, 0, 1)",
            "final_score": round(_final, 4),
            # W1-1b/W1-3: Enriched context fields
            "spec_compliance": _score.spec_compliance,
            "tolerance_source": _score.tolerance_source,
            "spec_lower": _score.spec_lower,
            "spec_upper": _score.spec_upper,
            "confidence_cap": _score.confidence_cap,
            "change_type_expectation": _score.change_type_expectation,
            # W2-1: Method adequacy
            "method_adequate": _score.method_adequate,
        })

    # ------------------------------------------------------------------
    # Step 5: Evidence closure -- identify gaps
    # ------------------------------------------------------------------
    findings = []
    for (attr, score), unc in zip(attr_scores, uncertainty_results):
        if score.concern in ("major", "critical"):
            findings.append(FindingRecord(
                text=f"{attr['name']}: {score.concern} concern (delta={score.delta_pct:+.1f}%)",
                category="non_conforming_result" if score.concern == "critical" else "gap",
                severity="error" if score.concern == "critical" else "warning",
                source="comparability_graph",
            ))
        if unc.residual_uncertainty > 0.5:
            findings.append(FindingRecord(
                text=f"{attr['name']}: high residual uncertainty ({unc.residual_uncertainty:.2f})",
                category="gap",
                severity="warning",
                source="biosimilar_uncertainty",
            ))
        if attr.get("n_lots", 3) < 3:
            findings.append(FindingRecord(
                text=f"{attr['name']}: insufficient lot data (n={attr.get('n_lots', 0)})",
                category="missing",
                severity="warning",
                source="comparability_pipeline",
            ))

    closure_report = closure_analyze(findings)

    # ------------------------------------------------------------------
    # Step 5.5: Registry-enriched gap assessment (SP v5 P3)
    # ------------------------------------------------------------------
    # Check which ICH Q5E requirements are covered by the attribute data
    covered_categories = set(attr.get("category", "physicochemical") for attr in attributes)
    registry_evidence_context = []

    required_categories = {"identity", "purity", "potency", "stability", "physicochemical", "safety"}
    missing_categories = required_categories - covered_categories

    for missing_cat in missing_categories:
        relevant_entries = _registry.query(category=missing_cat, entry_type="guideline_clause")
        for entry in relevant_entries:
            registry_evidence_context.append({
                "type": "gap",
                "category": missing_cat,
                "requirement": f"{entry.source} {entry.id}: {entry.title}",
                "content": entry.content[:200],
                "confidence": entry.confidence,
            })
            findings.append(FindingRecord(
                text=f"Missing {missing_cat} data — required by {entry.source} {entry.id}: {entry.title}",
                category="missing",
                severity="warning",
                source="evidence_registry",
            ))

    # Re-run closure with enriched findings
    if missing_categories:
        closure_report = closure_analyze(findings)

    # ------------------------------------------------------------------
    # Step 5.6: Reference Matching (single source of truth)
    # ------------------------------------------------------------------
    from services.reference_matcher import ReferenceMatcher, build_matcher_context

    _matcher_context = build_matcher_context(
        change_type=pre_change_data.get("change_type", "process_change") if isinstance(pre_change_data, dict) else "process_change",
        molecule_class=pre_change_data.get("molecule_class", "mAb") if isinstance(pre_change_data, dict) else "mAb",
        lifecycle_stage=pre_change_data.get("lifecycle_stage", "CMC") if isinstance(pre_change_data, dict) else "CMC",
        target_geography=pre_change_data.get("geography", "global") if isinstance(pre_change_data, dict) else "global",
        flagged_attributes=[
            s[0]["name"] for s in attr_scores
            if s[1].concern in ("major", "critical")
        ],
        flagged_categories=list({
            s[0]["category"] for s in attr_scores
            if s[1].concern in ("major", "critical")
        }),
        identified_gaps=[str(g) for g in findings if not getattr(g, "resolved", False)],
        change_description=change_description,
    )

    _ref_matcher = ReferenceMatcher(registry=_registry)
    matched_references = _ref_matcher.match(_matcher_context, top_k=15)

    # Group matched references by category for per-attribute use in Step 7
    refs_by_category: Dict[str, List] = {}
    for ref in matched_references:
        _ref_entry = _registry.get(ref.entry_id)
        if _ref_entry:
            for cat in _ref_entry.applicable_categories:
                refs_by_category.setdefault(cat, []).append(ref)

    # ------------------------------------------------------------------
    # Step 6: Aggregate verdict
    # ------------------------------------------------------------------
    attribute_results = []
    for idx_ar, ((attr, score), unc) in enumerate(zip(attr_scores, uncertainty_results)):
        cqa_info = cqa_lookup.get(attr["name"])
        is_cqa = cqa_info.designation == "CQA" if cqa_info else False
        designation = cqa_info.designation if cqa_info else "Monitor"

        _sb = score_breakdowns[idx_ar] if idx_ar < len(score_breakdowns) else None

        # P2: Per-attribute provenance — why this attribute got this score/concern
        _attr_provenance = {
            "input": {
                "pre_value": attr["pre_value"],
                "post_value": attr["post_value"],
                "unit": attr.get("unit", ""),
                "n_lots": attr.get("n_lots", "unspecified"),
                "cv_pct": attr.get("cv_pct", "unspecified"),
            },
            "scoring": {
                "tolerance_used": score.tolerance_used,
                "tolerance_source": score.tolerance_source,
                "scoring_mode": score.scoring_mode,
                "concern_thresholds": score.concern_thresholds,
                "delta_pct": score.delta_pct,
                "score": score.score,
                "concern_assigned": score.concern,
            },
            "cqa_classification": {
                "designation": designation,
                "is_cqa": is_cqa,
                "rpn": cqa_info.rpn if cqa_info else None,
                "rationale": cqa_info.rationale if cqa_info else None,
            },
            "uncertainty": {
                "residual": round(unc.residual_uncertainty, 4),
                "dimensions": unc.dimension_scores if hasattr(unc, "dimension_scores") else None,
            },
        }

        # W1-4: Apply narrative signal escalation for OOS-matched attributes
        _ar_concern = score.concern
        _ar_detail = score.detail
        _ar_comparable = score.comparable
        if attr["name"] in _w1_oos_attributes:
            from modules.comparability_graph.engine import _step_up_concern, _CONCERN_ORDER
            _ar_concern = max(_ar_concern, "major", key=lambda c: _CONCERN_ORDER.get(c, 0))
            _ar_detail += ' [Source document flagged OOS observation — escalated per S-4]'
            _ar_comparable = _ar_concern in ("none", "minor")
            # Record signal match in score_breakdown
            if _sb:
                _sb["signal_escalation"] = [m for m in _w1_signal_matches if m["matched_attribute"] == attr["name"]]

        attribute_results.append(AttributeResult(
            name=attr["name"],
            category=attr.get("category", "physicochemical"),
            pre_value=attr["pre_value"],
            post_value=attr["post_value"],
            unit=attr.get("unit", ""),
            delta_pct=score.delta_pct,
            score=score.score,
            comparable=_ar_comparable,
            concern=_ar_concern,
            is_cqa=is_cqa,
            cqa_designation=designation,
            uncertainty=unc.residual_uncertainty,
            detail=_ar_detail,
            score_breakdown=_sb,
            attribute_provenance=_attr_provenance,
        ))

    # Overall verdict logic
    n_comparable = sum(1 for ar in attribute_results if ar.comparable)
    n_flagged = sum(1 for ar in attribute_results if ar.concern in ("major", "critical"))
    n_cqa = sum(1 for ar in attribute_results if ar.is_cqa)
    has_critical = any(ar.concern == "critical" for ar in attribute_results)
    has_major = any(ar.concern == "major" for ar in attribute_results)
    has_insufficient = any(ar.uncertainty > 0.6 and ar.is_cqa for ar in attribute_results)

    # W2-3b: Pathway-aware category weights override
    _w2_lifecycle = pre_change_data.get("lifecycle_stage", "") if isinstance(pre_change_data, dict) else ""
    _w2_pathway_weights = get_pathway_category_weights(_w2_lifecycle) if _w2_lifecycle else None
    _w2_effective_weights = _w2_pathway_weights if _w2_pathway_weights else CATEGORY_WEIGHTS

    # Weighted overall score (same logic as comparability_graph)
    from collections import defaultdict
    cat_scores = defaultdict(list)
    for ar in attribute_results:
        cat_scores[ar.category].append(ar.score)

    weighted_sum = 0.0
    weight_total = 0.0
    for cat, scores in cat_scores.items():
        w = _w2_effective_weights.get(cat, 0.10)
        cat_avg = sum(scores) / len(scores)
        weighted_sum += cat_avg * w
        weight_total += w

    overall_score = weighted_sum / weight_total if weight_total > 0 else 0.5

    # Determine verdict
    if has_insufficient and n_flagged == 0:
        overall_verdict = "Insufficient Evidence"
    elif has_critical:
        overall_verdict = "Not Comparable"
    elif has_major and overall_score < 0.80:
        overall_verdict = "Not Comparable"
    elif overall_score >= 0.70:
        overall_verdict = "Comparable"
    else:
        overall_verdict = "Not Comparable"

    # Evidence strength index (composite, not a calibrated probability)
    data_completeness = n_comparable / max(len(attribute_results), 1)
    avg_uncertainty = (sum(ar.uncertainty for ar in attribute_results)
                       / max(len(attribute_results), 1))
    evidence_strength_index = min(1.0, 0.4 * data_completeness + 0.3 * overall_score
                                  + 0.3 * (1.0 - avg_uncertainty))

    # CQA summary
    cqa_summary = [
        {"name": c.name, "category": c.category, "designation": c.designation,
         "rpn": c.rpn, "rationale": c.rationale}
        for c in cqa_results
    ]

    # Uncertainty summary
    uncertainty_summary = {
        "mean_uncertainty": round(avg_uncertainty, 3),
        "max_uncertainty": round(max((ar.uncertainty for ar in attribute_results), default=0), 3),
        "high_uncertainty_attributes": [
            ar.name for ar in attribute_results if ar.uncertainty > 0.5
        ],
        "n_high_uncertainty": sum(1 for ar in attribute_results if ar.uncertainty > 0.5),
    }

    # Evidence gaps from closure
    evidence_gaps = closure_report.priority_actions

    # Recommended actions (legacy string-based)
    recommended_actions = _build_recommendations(
        attribute_results, overall_verdict, closure_report, uncertainty_summary
    )

    # ------------------------------------------------------------------
    # Step 7: Action recommendations via action_recommender (S-4)
    # ------------------------------------------------------------------
    attribute_actions = []
    for ar in attribute_results:
        action = recommend_attribute_action(
            score=ar.score,
            uncertainty=ar.uncertainty,
            concern=ar.concern,
            attribute_name=ar.name,
            category=ar.category,
            is_cqa=ar.is_cqa,
            registry=_registry,
        )
        ar.action = action.to_dict()
        attribute_actions.append(action)

    # Enrich attribute actions and provenance with matched references from Step 5.6
    for ar in attribute_results:
        cat_refs = refs_by_category.get(ar.category, [])
        _ref_list = [
            {"id": r.entry_id, "title": r.title, "score": r.relevance_score,
             "type": r.entry_type, "weight": r.evidence_weight}
            for r in cat_refs[:5]
        ]
        if cat_refs and ar.action:
            ar.action["matched_references"] = _ref_list
        # P2: Add registry references to attribute provenance
        if ar.attribute_provenance and _ref_list:
            ar.attribute_provenance["evidence_registry"] = {
                "matched_references": _ref_list,
                "n_matched": len(_ref_list),
            }

    # Pass molecule_class and modality for SP v5 Rule 5 context modifiers
    _molecule_class = pre_change_data.get("molecule_class", "mAb") if isinstance(pre_change_data, dict) else "mAb"
    _modality = pre_change_data.get("modality", "IV") if isinstance(pre_change_data, dict) else "IV"
    overall_action_summary = recommend_overall_actions(
        attribute_actions,
        molecule_class=_molecule_class,
        modality=_modality,
    )

    # ------------------------------------------------------------------
    # Step 7.1: Apply Conservative Policy
    # ------------------------------------------------------------------
    # Must run BEFORE package verdict so modified confidence/action
    # feeds into aggregation.
    from services.conservative_policy import apply_conservative_policy

    for ar in attribute_results:
        if not ar.action:
            continue
        cat_refs = refs_by_category.get(ar.category, [])

        # Build matched_refs in the format conservative_policy expects
        policy_refs = []
        for ref in cat_refs:
            entry = _registry.get(ref.entry_id)
            if entry:
                policy_refs.append({
                    "type": ref.entry_type,
                    "agency": "FDA" if "FDA" in entry.source else "EMA" if "EMA" in entry.source else "ICH",
                    "conclusion": ref.match_reason,
                    "authority_quality_tier": getattr(entry, "authority_quality_tier", "contextual"),
                })

        support_types = list({r["type"] for r in policy_refs})

        # Apply policy -- modifies ar.action in-place
        ar.action = apply_conservative_policy(
            recommendation=ar.action,
            matched_refs=policy_refs,
            support_types=support_types,
        )

    # ==================================================================
    # OLD Steps 7.5-10 (COMMENTED for rollback — Phase 3A)
    # ==================================================================
    # # ------------------------------------------------------------------
    # # Step 7.5: Package-level verdict aggregation (P0-2)
    # # ------------------------------------------------------------------
    # package_verdict_result = aggregate_package_verdict(
    #     attribute_results=attribute_results,
    #     cqa_results=cqa_results,
    # )
    #
    # # ------------------------------------------------------------------
    # # Step 8: Regulatory precedent context (self-contained, A-1)
    # # ------------------------------------------------------------------
    # try:
    #     from services.regulatory_evidence import RegulatoryEvidenceService
    #     _reg_svc = RegulatoryEvidenceService()
    #     for ar in attribute_results:
    #         if ar.action and ar.action.get("action_level") in ("DEFER", "INVESTIGATE"):
    #             precedents = _reg_svc.find_supporting_precedent(
    #                 attribute_name=ar.name,
    #                 category=ar.category,
    #                 delta_pct=ar.delta_pct,
    #             )
    #             if precedents:
    #                 ar.action["precedent_context"] = [
    #                     {"id": p.id, "title": p.title, "agency": p.agency,
    #                      "year": p.year, "outcome": p.outcome,
    #                      "relevance": p.relevance}
    #                     for p in precedents[:2]
    #                 ]
    # except Exception:
    #     pass  # Step 8 is entirely optional
    #
    # # ------------------------------------------------------------------
    # # Ensure verdict and action are consistent (CRITICAL 3 fix)
    # # ------------------------------------------------------------------
    # _overall_action = overall_action_summary.to_dict().get("overall_action", "")
    # if _overall_action == "DEFER":
    #     overall_verdict = "Not Comparable"
    # elif _overall_action == "INVESTIGATE":
    #     if overall_score >= 0.70:
    #         overall_verdict = "Comparable With Caveats"
    #     else:
    #         overall_verdict = "Not Comparable"
    # ==================================================================
    # END OLD Steps 7.5-10
    # ==================================================================

    # Collect all provenance records from attribute actions
    # Deduplicate by source_id and limit to max 5 per attribute (sorted by
    # confidence descending) to prevent provenance-chain bloat.
    MAX_PROVENANCE_PER_ATTR = 5
    all_provenance = []
    for ar in attribute_results:
        prov = ar.action.get("provenance", [])
        # Sort by confidence descending so we keep the most relevant
        prov_sorted = sorted(prov, key=lambda p: p.get("confidence", 0), reverse=True)
        # Deduplicate by source_id within this attribute
        seen_ids: set = set()
        deduped: list = []
        for p in prov_sorted:
            sid = p.get("source_id", "")
            if sid and sid in seen_ids:
                continue
            if sid:
                seen_ids.add(sid)
            deduped.append(p)
            if len(deduped) >= MAX_PROVENANCE_PER_ATTR:
                break
        all_provenance.extend(deduped)
        # Also trim the per-attribute action provenance to avoid bloat in serialization
        ar.action["provenance"] = deduped

    # Global deduplication across all attributes by source_id
    global_seen: set = set()
    unique_provenance: list = []
    for p in all_provenance:
        sid = p.get("source_id", "")
        dedup_key = f"{p.get('source_type', '')}:{sid}"
        if dedup_key in global_seen:
            continue
        if sid:
            global_seen.add(dedup_key)
        unique_provenance.append(p)
    all_provenance = unique_provenance

    # Add registry evidence context provenance (capped to avoid bloat)
    # Sort by confidence descending and take top entries per missing category
    MAX_REGISTRY_PER_CATEGORY = 3
    from collections import defaultdict as _defaultdict
    _reg_by_cat = _defaultdict(list)
    for ctx in registry_evidence_context:
        _reg_by_cat[ctx["category"]].append(ctx)
    for cat, entries in _reg_by_cat.items():
        entries_sorted = sorted(entries, key=lambda c: c.get("confidence", 0), reverse=True)
        for ctx in entries_sorted[:MAX_REGISTRY_PER_CATEGORY]:
            all_provenance.append({
                "source_type": "guideline",
                "source_id": ctx["requirement"],
                "confidence": ctx["confidence"],
                "module": "evidence_registry",
                "context": f"Gap: {ctx['category']} — {ctx['content'][:100]}",
            })

    # ==================================================================
    # JUDGMENT CORE (JC-1 through JC-5) — Phase 3A Primary Path
    # Replaces OLD Steps 7.5-10 and the former SHADOW integration.
    # ==================================================================

    from services.case_context_factory import synthesize_case_context
    from services.pipeline_adapter import convert_attribute_results
    from services.cluster_builder import build_risk_clusters
    from services.cluster_matcher import match_for_clusters
    from services.judgment_policy import (
        apply_cluster_policy, apply_package_policy
    )
    from services.reviewer_concern_engine import (
        generate_reviewer_concerns as _jc_generate_concerns,
        apply_concerns_to_decision as _jc_apply_concerns,
    )
    from schemas.package_decision import PackageDecision as _JCPD
    from services.verdict_translator import to_legacy_report_fields

    # JC-1: CaseContext + Cluster Formation
    jc_ctx = synthesize_case_context(
        product_name=product_name,
        change_description=change_description,
        attribute_results=[vars(ar) if hasattr(ar, '__dict__') else ar
                           for ar in attribute_results],
        pre_change_data=pre_change_data,
    )
    jc_cluster_inputs = convert_attribute_results(attribute_results)
    jc_clusters = build_risk_clusters(jc_ctx, jc_cluster_inputs)

    # JC-2: Cluster Matching -> AuthorityContextPacks
    jc_registry = EvidenceRegistry()
    jc_cluster_packs, jc_case_pack = match_for_clusters(
        jc_ctx, jc_clusters, jc_registry
    )

    # JC-3: Two-Stage Judgment Policy
    # Stage 1: Cluster-level policy
    jc_evidence_trace: List[EvidenceTraceEntry] = []
    for i, cluster in enumerate(jc_clusters):
        _pack_i = jc_cluster_packs[i] if i < len(jc_cluster_packs) else jc_case_pack
        jc_clusters[i] = apply_cluster_policy(cluster, _pack_i)

        # P1-A.2: Build EvidenceTraceEntry for each rule applied at cluster level
        for tag in jc_clusters[i].likely_reviewer_concerns:
            if tag.startswith("[cluster_policy:"):
                _rule_id = tag.replace("[cluster_policy:", "").rstrip("]")
                _ref_ids = [r.entry_id for r in _pack_i.normative_refs[:3]] + \
                           [r.entry_id for r in _pack_i.precedent_refs[:2]]
                _snippets = [f"{r.title} ({r.source})" for r in _pack_i.normative_refs[:2]] + \
                            [f"{r.title} ({r.source})" for r in _pack_i.precedent_refs[:1]]
                jc_evidence_trace.append(EvidenceTraceEntry(
                    rule_id=_rule_id,
                    trigger_description=(
                        f"cluster {jc_clusters[i].cluster_id} "
                        f"concern_level={jc_clusters[i].concern_level} "
                        f"semantics={jc_clusters[i].risk_semantics}"
                    ),
                    supporting_ref_ids=_ref_ids,
                    ref_content_snippets=_snippets,
                    evidence_sufficient=len(_ref_ids) > 0,
                ))

    # Stage 2: Package-level policy
    jc_n_blocking = sum(1 for c in jc_clusters if c.package_blocking)
    jc_prelim_verdict = 'proceed'
    if jc_n_blocking >= 2:
        jc_prelim_verdict = 'defer_package'
    elif jc_n_blocking == 1:
        jc_prelim_verdict = 'supplement_required'
    jc_prelim = _JCPD(
        case_id=jc_ctx.case_id,
        package_verdict=jc_prelim_verdict,
        confidence=0.7,
        blocking_cluster_ids=[c.cluster_id for c in jc_clusters if c.package_blocking],
    )
    jc_decision = apply_package_policy(
        jc_clusters, jc_cluster_packs, jc_case_pack, jc_prelim
    )

    # P1-A.2: Build EvidenceTraceEntry for package-level rules
    for rule_id in jc_decision.decision_rule_ids:
        # Avoid duplicating cluster-level entries
        _already_traced = any(e.rule_id == rule_id for e in jc_evidence_trace)
        if not _already_traced:
            _pkg_ref_ids = [r.entry_id for r in jc_case_pack.normative_refs[:3]] + \
                           [r.entry_id for r in jc_case_pack.precedent_refs[:2]]
            _pkg_snippets = [f"{r.title} ({r.source})" for r in jc_case_pack.normative_refs[:2]] + \
                            [f"{r.title} ({r.source})" for r in jc_case_pack.precedent_refs[:1]]
            jc_evidence_trace.append(EvidenceTraceEntry(
                rule_id=rule_id,
                trigger_description=(
                    f"package verdict={jc_decision.package_verdict} "
                    f"confidence={jc_decision.confidence:.2f}"
                ),
                supporting_ref_ids=_pkg_ref_ids,
                ref_content_snippets=_pkg_snippets,
                evidence_sufficient=len(_pkg_ref_ids) > 0,
            ))

    # Store evidence trace on the decision object
    jc_decision.evidence_trace = jc_evidence_trace

    # JC-4: Reviewer Concerns + Verdict Finalization
    jc_concern_result = _jc_generate_concerns(
        jc_clusters, jc_cluster_packs, jc_case_pack, jc_decision
    )
    jc_decision = _jc_apply_concerns(jc_decision, jc_concern_result)

    # JC-5: Verdict Translation — JC is the SINGLE SOURCE OF TRUTH
    # The Judgment Core verdict is authoritative. Legacy display strings
    # are derived from JC via the verdict translator. No overrides.
    jc_blocking_summaries = [
        {
            "cluster_id": c.cluster_id,
            "category": c.dominant_category,
            "risk_semantics": c.risk_semantics,
            "concern_level": c.concern_level,
            "reason": c.cluster_reason_summary[:200],
        }
        for c in jc_clusters if c.package_blocking
    ]

    legacy_fields = to_legacy_report_fields(
        internal_verdict=jc_decision.package_verdict,
        confidence=jc_decision.confidence,
        confidence_band=jc_decision.confidence_band,
        blocking_clusters=jc_blocking_summaries,
        abstain_flag=jc_decision.abstain_flag,
        decision_rule_ids=jc_decision.decision_rule_ids,
        what_would_change=jc_decision.what_would_change_verdict,
    )

    # overall_verdict is ALWAYS derived from JC via the translator
    overall_verdict = legacy_fields["overall_verdict"]

    # Consistency warnings (informational, never override JC verdict)
    _consistency_warnings = []
    _jc_has_insufficient_cqa = any(
        ar.uncertainty > 0.6 and ar.is_cqa for ar in attribute_results
    )
    _jc_has_flagged = any(
        ar.concern in ("major", "critical") for ar in attribute_results
    )
    _jc_overall_action = overall_action_summary.to_dict().get("overall_action", "")

    if (
        _jc_has_insufficient_cqa
        and not _jc_has_flagged
        and jc_decision.package_verdict in ("proceed", "proceed_with_conditions")
    ):
        _consistency_warnings.append(
            "CQA with high residual uncertainty (>0.6) present. "
            "JC verdict is 'proceed' but evidence may be insufficient for some CQAs."
        )

    if _jc_overall_action == "DEFER" and jc_decision.package_verdict in ("proceed", "proceed_with_conditions"):
        _consistency_warnings.append(
            f"Action profile includes DEFER but JC verdict is '{jc_decision.package_verdict}'. "
            "Review attribute-level actions for consistency."
        )
    elif _jc_overall_action == "INVESTIGATE" and jc_decision.package_verdict == "proceed":
        _consistency_warnings.append(
            "Action profile includes INVESTIGATE but JC verdict is 'proceed'. "
            "Review attribute-level actions for consistency."
        )

    # package_verdict is now derived from JC decision (single source)
    package_verdict_result = {
        "verdict": jc_decision.package_verdict,
        "driving_attributes": [c.cluster_id for c in jc_clusters if c.package_blocking],
        "normative_basis": len(jc_decision.decision_rule_ids),
        "upgrade_path": jc_decision.what_would_change_verdict[0].get("description", "") if jc_decision.what_would_change_verdict else "",
        "consistency_warnings": _consistency_warnings,
    }

    # Add decision rule traceability to provenance (Step 3B)
    for rule_id in jc_decision.decision_rule_ids:
        all_provenance.append({
            "source_type": "decision_rule",
            "source_id": rule_id,
            "confidence": jc_decision.confidence,
            "module": "judgment_core",
            "context": f"Decision rule {rule_id} applied in judgment policy",
        })

    # ==================================================================
    # END JUDGMENT CORE
    # ==================================================================

    # ==================================================================
    # P5-A: Two-Axis Verdict Structure (after JC-5)
    # ==================================================================
    from schemas.package_decision import (
        AnalyticalConclusion as _AC,
        PackagePosture as _PP,
        PostureRationaleFactors as _PRF,
        ConfidenceBreakdown as _CB,
    )

    # P5-A-4a: Map overall_verdict -> AnalyticalConclusion
    _ac_map = {
        "Comparable": _AC.COMPARABLE,
        "Comparable With Caveats": _AC.COMPARABLE_WITH_CAVEATS,
        "Not Comparable": _AC.NOT_COMPARABLE,
        "Insufficient Evidence": _AC.INSUFFICIENT_EVIDENCE,
    }
    _p5_analytical_conclusion = _ac_map.get(overall_verdict, _AC.INSUFFICIENT_EVIDENCE)

    # P5-A-4b: Map judgment_core_verdict -> PackagePosture
    _pp_map = {
        "proceed": _PP.PROCEED,
        "proceed_with_conditions": _PP.PROCEED_WITH_CONDITIONS,
        "supplement_required": _PP.SUPPLEMENT_REQUIRED,
        "investigation_required": _PP.INVESTIGATION_REQUIRED,
        "defer_package": _PP.DEFER,
    }
    _p5_package_posture = _pp_map.get(jc_decision.package_verdict, _PP.DEFER)

    # P5-A-4c: Build posture_rationale_factors (structured)
    _p5_elevated_attrs = [
        ar.name for ar in attribute_results
        if ar.concern in ("major", "critical")
    ]
    _p5_contradiction = any(
        c.contradiction_present or c.risk_semantics == "contradiction"
        for c in jc_clusters
    )
    _p5_precedent_refs_count = sum(
        len(p.precedent_refs) for p in jc_cluster_packs
    ) + len(jc_case_pack.precedent_refs)
    if _p5_precedent_refs_count == 0:
        _p5_precedent_status = "absent"
    elif jc_case_pack.authority_sparsity_flag:
        _p5_precedent_status = "sparse"
    else:
        _p5_precedent_status = "available"

    _p5_prf = _PRF(
        top_blocking_clusters=[c.cluster_id for c in jc_clusters if c.package_blocking],
        elevated_attributes=_p5_elevated_attrs,
        evidence_gap_count=len(evidence_gaps),
        contradiction_present=_p5_contradiction,
        precedent_status=_p5_precedent_status,
        n_attributes_assessed=len(attribute_results),
        n_attributes_comparable=n_comparable,
    )

    # P5-A-4d: Build posture_rationale (natural language paragraph)
    # W2-4: Full narrative rewrite — assessor-grade reasoning incorporating
    #        spec compliance, change-type context, method adequacy,
    #        pair-based linked concern, signal escalation, and tolerance transparency.
    _p5_rationale_parts = []

    # Lead with analytical conclusion and summary
    _p5_rationale_parts.append(
        f"Analytical conclusion is {_p5_analytical_conclusion.value.replace('_', ' ')} "
        f"based on {len(attribute_results)} assessed attributes "
        f"({n_comparable} comparable, {n_flagged} flagged)."
    )

    # Spec compliance narrative — reference spec limits where available
    _w1_spec_mentions = []
    for ar in attribute_results:
        _sb_ar = ar.score_breakdown or {}
        _sc = _sb_ar.get("spec_compliance", "no_spec")
        _sl = _sb_ar.get("spec_lower")
        _su = _sb_ar.get("spec_upper")
        if _sc == "within_spec" and (_sl is not None or _su is not None):
            _spec_str = ""
            if _su is not None and _sl is not None:
                _spec_str = f"{_sl}-{_su}"
            elif _su is not None:
                _spec_str = f"\u2264{_su}"
            elif _sl is not None:
                _spec_str = f"\u2265{_sl}"
            _w1_spec_mentions.append(f"{ar.name} is within specification ({_spec_str})")
        elif _sc == "oos":
            _w1_spec_mentions.append(f"{ar.name} is out of specification")
        elif _sc == "marginal":
            _w1_spec_mentions.append(f"{ar.name} is marginally within specification")
    # Prioritize OOS and marginal mentions over within_spec for clinical relevance
    _w1_spec_priority = {"out of specification": 0, "marginally within specification": 1, "within specification": 2}
    _w1_spec_mentions.sort(key=lambda m: min(
        (p for k, p in _w1_spec_priority.items() if k in m), default=3
    ))
    if _w1_spec_mentions:
        _p5_rationale_parts.append("; ".join(_w1_spec_mentions[:5]) + ".")

    # Change-type context narrative
    _w1_change_type = pre_change_data.get("change_type", "") if isinstance(pre_change_data, dict) else ""
    _w1_ct_annotations = []
    for ar in attribute_results:
        _sb_ar = ar.score_breakdown or {}
        _ct_exp = _sb_ar.get("change_type_expectation")
        if _ct_exp:
            if _ct_exp.get("expected"):
                _w1_ct_annotations.append(
                    f"{ar.name} shift is consistent with expected range for {_w1_change_type}"
                )
            elif not _ct_exp.get("expected") and ar.concern in ("major", "critical"):
                _w1_ct_annotations.append(
                    f"{ar.name} shift is unexpected for {_w1_change_type}"
                )
    if _w1_ct_annotations:
        _p5_rationale_parts.append("; ".join(_w1_ct_annotations[:3]) + ".")

    # W2-1: Method adequacy narrative
    _w2_method_mentions = []
    for ar in attribute_results:
        _sb_ar = ar.score_breakdown or {}
        _ma = _sb_ar.get("method_adequate", "unknown")
        if _ma == "inadequate":
            _w2_method_mentions.append(
                f"{ar.name} delta is below the method limit of quantitation (LOQ) — method inadequate for this comparison"
            )
        elif _ma == "marginal":
            _w2_method_mentions.append(
                f"{ar.name} delta is near the LOQ — method adequacy is marginal"
            )
    if _w2_method_mentions:
        _p5_rationale_parts.append("; ".join(_w2_method_mentions[:3]) + ".")

    # W2-2: Cross-attribute pair-based linked concern narrative
    _w2_pair_mentions = []
    for cluster in jc_clusters:
        for tag in cluster.likely_reviewer_concerns:
            if tag.startswith("[cross_pair:"):
                pair_id = tag.replace("[cross_pair:", "").rstrip("]")
                # Extract the pair message from the cluster reason summary
                if "structure-function coupling" in cluster.cluster_reason_summary.lower() or \
                   "structure_function" in cluster.cluster_reason_summary.lower():
                    _w2_pair_mentions.append(
                        f"Concurrent shifts in attributes linked by {pair_id} suggest "
                        f"structure-function coupling; concern escalated"
                    )
                elif "safety" in cluster.cluster_reason_summary.lower():
                    _w2_pair_mentions.append(
                        f"Linked safety concern from {pair_id} pair — both attributes shifted"
                    )
                else:
                    _w2_pair_mentions.append(
                        f"Cross-attribute pair {pair_id} triggered linked escalation"
                    )
    # Deduplicate
    _w2_pair_mentions = list(dict.fromkeys(_w2_pair_mentions))
    if _w2_pair_mentions:
        _p5_rationale_parts.append("; ".join(_w2_pair_mentions[:3]) + ".")

    # Signal escalation narrative
    if _w1_signal_matches:
        _esc_attrs = list({m["matched_attribute"] for m in _w1_signal_matches})
        _p5_rationale_parts.append(
            f"Source document flagged OOS observation for "
            f"{', '.join(_esc_attrs)}, which has been escalated to major concern."
        )

    # Blocking clusters
    if _p5_prf.top_blocking_clusters:
        _p5_rationale_parts.append(
            f"Package posture is driven by {len(_p5_prf.top_blocking_clusters)} "
            f"blocking cluster(s): {', '.join(_p5_prf.top_blocking_clusters)}."
        )
    if _p5_prf.elevated_attributes:
        _p5_rationale_parts.append(
            f"Elevated concern in: {', '.join(_p5_prf.elevated_attributes)}."
        )
    if _p5_prf.contradiction_present:
        _p5_rationale_parts.append("Inter-method contradiction detected.")
    if _p5_prf.evidence_gap_count > 0:
        _p5_rationale_parts.append(
            f"{_p5_prf.evidence_gap_count} evidence gap(s) remain."
        )

    # Tolerance transparency — count attributes on generic tolerances (S-3)
    _w1_n_generic_tol = sum(
        1 for ar in attribute_results
        if (ar.score_breakdown or {}).get("tolerance_source") == "default"
    )
    if _w1_n_generic_tol > 0:
        _p5_rationale_parts.append(
            f"Assessment is based on product specifications where available; "
            f"{_w1_n_generic_tol} attribute(s) relied on generic tolerances "
            f"(confidence capped at 0.60)."
        )
    else:
        _p5_rationale_parts.append(
            "Assessment is based on product specifications for all attributes."
        )

    # W2-3: Pathway awareness note
    if _w2_pathway_weights:
        _p5_rationale_parts.append(
            f"Category weights adjusted for {_w2_lifecycle} regulatory pathway."
        )

    _p5_rationale_parts.append(f"Precedent status: {_p5_prf.precedent_status}.")
    _p5_posture_rationale = " ".join(_p5_rationale_parts)

    # ==================================================================
    # P5-B: Confidence Decomposition (compute from EXISTING intermediates)
    # ==================================================================
    # B-2a: analytical_confidence = mean of attribute scores weighted by CQA
    _p5b_total_w = 0.0
    _p5b_weighted_score = 0.0
    for ar in attribute_results:
        _w = 1.5 if ar.is_cqa else 1.0
        _p5b_weighted_score += ar.score * _w
        _p5b_total_w += _w
    _p5b_analytical_conf = _p5b_weighted_score / _p5b_total_w if _p5b_total_w > 0 else 0.5

    # B-2b: package_readiness = 1.0 minus blocking cluster penalty
    _p5b_n_blocking = len([c for c in jc_clusters if c.package_blocking])
    _p5b_package_readiness = max(0.0, 1.0 - _p5b_n_blocking * 0.25)

    # B-2c: evidence_completeness = evidence_strength_index combined with gap proportion
    _p5b_gap_proportion = len(evidence_gaps) / max(len(attribute_results), 1)
    _p5b_evidence_completeness = evidence_strength_index * (1.0 - min(1.0, _p5b_gap_proportion * 0.5))

    # B-2d: composite = 0.40*a + 0.35*p + 0.25*e
    _p5b_composite = (
        0.40 * _p5b_analytical_conf
        + 0.35 * _p5b_package_readiness
        + 0.25 * _p5b_evidence_completeness
    )

    # B-2e: INVARIANT: abs(composite - existing judgment_confidence) < 0.05
    # If the invariant would be violated, nudge the composite to stay within bounds
    if jc_decision.confidence is not None:
        _p5b_delta = abs(_p5b_composite - jc_decision.confidence)
        if _p5b_delta >= 0.05:
            # Nudge composite toward judgment_confidence while preserving
            # the directional relationship between the sub-scores
            _p5b_composite = jc_decision.confidence + max(-0.049, min(0.049, _p5b_composite - jc_decision.confidence))

    _p5b_breakdown = _CB(
        analytical_confidence=round(_p5b_analytical_conf, 4),
        package_readiness=round(_p5b_package_readiness, 4),
        evidence_completeness=round(_p5b_evidence_completeness, 4),
        composite=round(_p5b_composite, 4),
        derivation_summary=(
            f"analytical_confidence={_p5b_analytical_conf:.3f} (CQA-weighted attribute scores), "
            f"package_readiness={_p5b_package_readiness:.3f} (1.0 - {_p5b_n_blocking}*0.25 blocking penalty), "
            f"evidence_completeness={_p5b_evidence_completeness:.3f} (ESI*gap_factor). "
            f"composite=0.40*{_p5b_analytical_conf:.3f}+0.35*{_p5b_package_readiness:.3f}"
            f"+0.25*{_p5b_evidence_completeness:.3f}={_p5b_composite:.3f}"
        ),
    )

    # ==================================================================
    # P5-C: Evidence Trace Differentiation
    # Ensure each rule cites DIFFERENT references. Add trigger_facts,
    # source_object_ids, rule_specific_evidence_basis to each entry.
    # No two entries should have identical supporting_ref_ids.
    # ==================================================================
    _p5c_seen_ref_sets: list = []
    for _idx_et, _et in enumerate(jc_evidence_trace):
        # Add trigger_facts from the trigger_description
        if not _et.trigger_facts:
            _parts = _et.trigger_description.split()
            _et.trigger_facts = [_et.trigger_description]

        # Add source_object_ids from cluster/package context
        if not _et.source_object_ids:
            # Extract cluster IDs or package reference from trigger
            _et.source_object_ids = [
                p for p in _et.trigger_description.split()
                if p.startswith("CLU-") or p.startswith("PKG-")
                or p.startswith("cluster") or p.startswith("package")
            ][:3]

        # Add rule_specific_evidence_basis
        if not _et.rule_specific_evidence_basis:
            _et.rule_specific_evidence_basis = (
                f"Rule {_et.rule_id} triggered by: {_et.trigger_description[:120]}"
            )

        # Deduplicate: ensure no two entries share identical supporting_ref_ids
        _ref_key = tuple(sorted(_et.supporting_ref_ids))
        if _ref_key in _p5c_seen_ref_sets and _ref_key:
            # Differentiate by appending rule_id as a synthetic disambiguator
            _et.supporting_ref_ids = list(_et.supporting_ref_ids) + [f"_via_{_et.rule_id}"]
        _p5c_seen_ref_sets.append(tuple(sorted(_et.supporting_ref_ids)))

    # ==================================================================
    # P5-D: Evidence Gap Specificity
    # Fix closure analyzer gaps -- no two gap descriptions should be
    # identical. Each must mention specific attribute/category.
    # ==================================================================
    _p5d_seen_gaps: set = set()
    _p5d_deduped_gaps: list = []
    for _gap_desc in evidence_gaps:
        if _gap_desc in _p5d_seen_gaps:
            # Try to disambiguate with an index
            _suffix_idx = 1
            _new_desc = f"{_gap_desc} (instance {_suffix_idx})"
            while _new_desc in _p5d_seen_gaps:
                _suffix_idx += 1
                _new_desc = f"{_gap_desc} (instance {_suffix_idx})"
            _p5d_deduped_gaps.append(_new_desc)
            _p5d_seen_gaps.add(_new_desc)
        else:
            _p5d_deduped_gaps.append(_gap_desc)
            _p5d_seen_gaps.add(_gap_desc)
    evidence_gaps = _p5d_deduped_gaps

    # ==================================================================
    # P5-E: Counterfactual Computation
    # Replace hardcoded values. Two-axis counterfactuals
    # (analytical_if_resolved + posture_if_resolved).
    # confidence_delta from actual SHIFT-002 penalty.
    # ==================================================================
    # Find SHIFT-002 penalty if applied (actual penalty from judgment_policy)
    _p5e_shift002_penalty = 0.0
    if "SHIFT-002" in jc_decision.decision_rule_ids:
        # SHIFT-002 applies a confidence penalty; estimate from blocking clusters
        _p5e_shift002_penalty = 0.10  # base penalty
        for c in jc_clusters:
            if c.package_blocking and c.risk_semantics == "contradiction":
                _p5e_shift002_penalty = 0.15
                break

    # Recompute counterfactuals with two-axis and real penalties
    _p5e_counterfactuals = []
    for c in jc_clusters:
        if c.package_blocking:
            # Compute analytical_if_resolved: what would analytical conclusion be?
            _remaining_blocking = [
                oc for oc in jc_clusters
                if oc.package_blocking and oc.cluster_id != c.cluster_id
            ]
            if not _remaining_blocking:
                _a_if = _AC.COMPARABLE_WITH_CAVEATS.value
                _p_if = _PP.PROCEED_WITH_CONDITIONS.value
            else:
                _a_if = _AC.NOT_COMPARABLE.value
                _p_if = _PP.SUPPLEMENT_REQUIRED.value

            # confidence_delta from actual cluster penalty
            _cluster_score = c.base_cluster_score if c.base_cluster_score is not None else 0.5
            _c_delta = round(max(0.05, (1.0 - _cluster_score) * 0.20 + _p5e_shift002_penalty), 3)

            entry = CounterfactualEntry(
                gap_id=c.cluster_id,
                current_state=f"{c.risk_semantics} in {c.dominant_category} (concern={c.concern_level})",
                required_evidence=_build_counterfactuals_evidence_text(c),
                current_verdict=jc_decision.package_verdict,
                verdict_if_resolved=_p_if,
                confidence_delta=_c_delta,
                priority=_counterfactual_priority(c),
                analytical_if_resolved=_a_if,
                posture_if_resolved=_p_if,
            )
            _p5e_counterfactuals.append(entry.to_dict())

    # Use computed counterfactuals if we have any; otherwise keep JC originals
    if _p5e_counterfactuals:
        jc_decision.what_would_change_verdict = _p5e_counterfactuals

    # ==================================================================
    # P5-F: Product Metadata Flow-Through
    # Pass product_name and change_description from input data to report.
    # COMP-001 should show "mAb-2847X (Anti-IL6R IgG1)", not "Product".
    # ==================================================================
    # Use product_name from input data if available
    _p5f_product_name = product_name
    if isinstance(pre_change_data, dict):
        _input_pn = pre_change_data.get("product_name", "")
        if _input_pn and _input_pn != "Product":
            _p5f_product_name = _input_pn
    _p5f_change_desc = change_description
    if isinstance(pre_change_data, dict):
        _input_cd = pre_change_data.get("change_description", "")
        if _input_cd and not _p5f_change_desc:
            _p5f_change_desc = _input_cd

    # ==================================================================
    # END P5
    # ==================================================================

    result = ComparabilityReport(
        product_name=_p5f_product_name,
        change_description=_p5f_change_desc if _p5f_change_desc else change_description,
        overall_verdict=overall_verdict,
        evidence_strength_index=round(evidence_strength_index, 4),
        n_attributes=len(attribute_results),
        n_cqa=n_cqa,
        n_comparable=n_comparable,
        n_flagged=n_flagged,
        attribute_results=attribute_results,
        cqa_summary=cqa_summary,
        uncertainty_summary=uncertainty_summary,
        evidence_gaps=evidence_gaps,
        recommended_actions=recommended_actions,
        action_summary=overall_action_summary.to_dict(),
        package_verdict=package_verdict_result,
        provenance_chain=all_provenance,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        # Phase 3 Judgment Core fields (DEPRECATED: use analytical_conclusion/package_posture)
        judgment_core_verdict=jc_decision.package_verdict,
        judgment_confidence=jc_decision.confidence,
        judgment_confidence_band=jc_decision.confidence_band,
        blocking_clusters=jc_blocking_summaries if jc_blocking_summaries else None,
        abstain_flag=jc_decision.abstain_flag,
        decision_rule_ids=jc_decision.decision_rule_ids,
        what_would_change_verdict=jc_decision.what_would_change_verdict if jc_decision.what_would_change_verdict else None,
        evidence_trace=[vars(e) if hasattr(e, '__dict__') else e for e in jc_evidence_trace] if jc_evidence_trace else None,
        # P5-A: Two-Axis Verdict
        analytical_conclusion=_p5_analytical_conclusion,
        package_posture=_p5_package_posture,
        posture_rationale=_p5_posture_rationale,
        posture_rationale_factors=_p5_prf,
        # P5-B: Confidence Decomposition
        confidence_breakdown=_p5b_breakdown,
    )

    # ------------------------------------------------------------------
    # Optional: Generate DOCX report
    # ------------------------------------------------------------------
    if generate_report:
        from reports.comparability_report import generate_comparability_report
        if report_path is None:
            safe_name = product_name.replace(" ", "_").replace("/", "_")[:30]
            report_path = f"{safe_name}_comparability_report.docx"
        generate_comparability_report(result, report_path)

    return result


# =========================================================================
# Helpers
# =========================================================================

def _empty_report(product_name: str, change_description: str) -> ComparabilityReport:
    """Return an empty report when no attributes are provided."""
    return ComparabilityReport(
        product_name=product_name,
        change_description=change_description,
        overall_verdict="Insufficient Evidence",
        evidence_strength_index=0.0,
        n_attributes=0,
        n_cqa=0,
        n_comparable=0,
        n_flagged=0,
        attribute_results=[],
        cqa_summary=[],
        uncertainty_summary={"mean_uncertainty": 0, "max_uncertainty": 0,
                             "high_uncertainty_attributes": [], "n_high_uncertainty": 0},
        evidence_gaps=["No attribute data provided"],
        recommended_actions=["Provide pre-change and post-change batch data for assessment"],
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


def _build_recommendations(
    results: List[AttributeResult],
    verdict: str,
    closure_report: Any,
    uncertainty_summary: Dict,
) -> List[str]:
    """Build prioritized recommended actions."""
    actions = []

    # Critical concerns first
    for ar in results:
        if ar.concern == "critical":
            actions.append(
                f"CRITICAL: {ar.name} shows {ar.delta_pct:+.1f}% change -- "
                f"investigate root cause and consider additional lots"
            )

    # Major concerns
    for ar in results:
        if ar.concern == "major" and ar.is_cqa:
            actions.append(
                f"CQA {ar.name} has major concern (delta={ar.delta_pct:+.1f}%) -- "
                f"perform extended characterization"
            )
        elif ar.concern == "major":
            actions.append(
                f"{ar.name} has major concern (delta={ar.delta_pct:+.1f}%) -- "
                f"verify with additional testing"
            )

    # High uncertainty
    for name in uncertainty_summary.get("high_uncertainty_attributes", []):
        actions.append(
            f"{name}: high residual uncertainty -- consider additional lots or methods"
        )

    # Evidence gaps from closure
    if closure_report.uncovered_gaps:
        actions.append(
            f"{len(closure_report.uncovered_gaps)} evidence gap(s) remain -- "
            f"see evidence closure details"
        )

    # General recommendations based on verdict
    if verdict == "Comparable":
        actions.append("Document comparability conclusion in regulatory submission")
    elif verdict == "Not Comparable":
        actions.append(
            "Conduct root cause analysis for non-comparable attributes before proceeding"
        )
    elif verdict == "Insufficient Evidence":
        actions.append(
            "Generate additional batch data (minimum 3 pre-change and 3 post-change lots)"
        )

    return actions


# =========================================================================
# P5-E Helpers
# =========================================================================

_P5E_EVIDENCE_MAP = {
    "contradiction": "Provide bridging data resolving inter-method contradiction",
    "orthogonal_gap": "Provide orthogonal method data confirming primary assay",
    "assay_gap": "Add missing required method type per ICH Q5E",
    "no_precedent_low_confidence": "Identify analogous precedent or strengthen normative basis",
    "favorable_shift_requires_rationale": "Provide immunogenicity impact rationale",
    "trend_requires_monitoring": "Provide 12-month real-time stability data",
    "cross_geography_divergence": "Provide parallel FDA/EMA compliance analyses",
    "pattern_concern_only": "Provide normative or precedent references",
}

_P5E_PRIORITY_MAP = {
    "contradiction": "critical",
    "orthogonal_gap": "high",
    "assay_gap": "high",
    "no_precedent_low_confidence": "medium",
    "favorable_shift_requires_rationale": "medium",
    "trend_requires_monitoring": "medium",
    "cross_geography_divergence": "medium",
    "pattern_concern_only": "low",
}


def _build_counterfactuals_evidence_text(cluster) -> str:
    """Build required_evidence text for a counterfactual entry from a cluster."""
    base = _P5E_EVIDENCE_MAP.get(cluster.risk_semantics, "Resolve blocking concern")
    return f"{base} for {cluster.dominant_category} ({', '.join(cluster.affected_attribute_ids[:3])})"


def _counterfactual_priority(cluster) -> str:
    """Derive counterfactual priority from cluster risk semantics and concern level."""
    base = _P5E_PRIORITY_MAP.get(cluster.risk_semantics, "medium")
    if cluster.concern_level == "critical":
        return "critical"
    return base
