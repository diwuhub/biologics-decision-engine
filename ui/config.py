"""
Shared Streamlit configuration, styling, and backend client.

For v1, the UI calls pipelines DIRECTLY (no FastAPI server required).
The HTTP API client is kept as a fallback for when the server is running.
"""

import streamlit as st
import json
import os
import sys
from pathlib import Path
from dataclasses import asdict
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Project root on sys.path so pipeline imports work
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

API_BASE_URL = os.environ.get("DECISION_API_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Colors / status mapping
# ---------------------------------------------------------------------------
VERDICT_COLORS = {
    "Comparable": "#28a745",
    "Comparable With Caveats": "#ff9800",
    "Not Comparable": "#dc3545",
    "Insufficient Evidence": "#ff9800",
}

ACTION_COLORS = {
    "PROCEED": "#28a745",
    "SUPPLEMENT": "#17a2b8",
    "MONITOR": "#28a745",
    "INVESTIGATE": "#ff9800",
    "DEFER": "#dc3545",
    # Legacy labels
    "ACCEPT": "#28a745",
    "ACCEPT_WITH_MONITORING": "#28a745",
    "REJECT": "#dc3545",
    "Proceed to Submission": "#28a745",
    "Collect Additional Data": "#ff9800",
    "Halt Review / Redesign Change": "#dc3545",
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# P6-A-1: Two-axis verdict color maps
ANALYTICAL_COLORS = {
    "comparable": "#28a745",
    "comparable_with_caveats": "#ff9800",
    "not_comparable": "#dc3545",
    "insufficient_evidence": "#6c757d",
}
POSTURE_COLORS = {
    "proceed": "#28a745",
    "proceed_with_conditions": "#17a2b8",
    "supplement_required": "#ff9800",
    "investigation_required": "#fd7e14",
    "defer": "#dc3545",
}


# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
def setup_page():
    """Inject design system per DESIGN.md — IBM Carbon structure + Sentry status colors."""
    st.markdown("""
    <style>
    /* CMC Decision Workspace — Design System
       IBM Carbon structure (8px grid, token system, flat cards)
       + Sentry status colors (pass/caution/fail)
       + system fonts only */

    /* Cards — flat, left-bordered, on #f4f4f4 surface */
    .metric-card { background: #f4f4f4; padding: 12px 16px; border-left: 4px solid #0f62fe; margin-bottom: 8px; font-size: 14px; }
    .success-card { background: #defbe6; padding: 12px 16px; border-left: 4px solid #24a148; margin-bottom: 8px; font-size: 14px; }
    .warning-card { background: #fdf6dd; padding: 12px 16px; border-left: 4px solid #f1c21b; margin-bottom: 8px; font-size: 14px; }
    .danger-card { background: #fff1f1; padding: 12px 16px; border-left: 4px solid #da1e28; margin-bottom: 8px; font-size: 14px; }
    .info-card { background: #edf5ff; padding: 12px 16px; border-left: 4px solid #0f62fe; margin-bottom: 8px; font-size: 14px; }
    .verdict-banner { padding: 16px; color: white; font-size: 16px; font-weight: 600; text-align: center; margin-bottom: 8px; }

    /* Metrics — #f4f4f4 surface, 1px border */
    [data-testid="stMetric"] { background: #f4f4f4; padding: 12px 16px; border: 1px solid #e0e0e0; }
    [data-testid="stMetricLabel"] { font-size: 11px !important; text-transform: uppercase; letter-spacing: 0.08em; color: #525252 !important; font-weight: 600 !important; }
    [data-testid="stMetricValue"] { font-size: 20px !important; font-weight: 600 !important; color: #161616 !important; }

    /* Hide branding */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    </style>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Direct pipeline backend (v1 -- no server required)
# ---------------------------------------------------------------------------
class DirectBackend:
    """Call pipelines directly, bypassing FastAPI.

    This is the default for v1 so users can run `streamlit run ui/app.py`
    without also starting uvicorn.
    """

    def __init__(self):
        self._cases: Dict[str, Dict[str, Any]] = {}  # case_id -> case data
        self._reports: Dict[str, Any] = {}            # case_id -> ComparabilityReport

    # -- Case management --------------------------------------------------

    def create_case(
        self,
        product_name: str,
        molecule_class: str,
        change_type: str,
        change_description: str = "",
        batch_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a new case and return its ID."""
        import uuid, datetime
        case_id = f"case-{uuid.uuid4().hex[:8]}"
        self._cases[case_id] = {
            "case_id": case_id,
            "product_name": product_name,
            "molecule_class": molecule_class,
            "change_type": change_type,
            "change_description": change_description,
            "batch_data": batch_data or {},
            "status": "created",
            "overall_action": "--",
            "critical_gaps_count": 0,
            "created": datetime.datetime.now().isoformat(),
            "last_updated": datetime.datetime.now().isoformat(),
        }
        return case_id

    def list_cases(self) -> List[Dict[str, Any]]:
        return list(self._cases.values())

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        return self._cases.get(case_id)

    # -- Run assessment ---------------------------------------------------

    def run_assessment(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Run the comparability pipeline on a case's batch data."""
        case = self._cases.get(case_id)
        if not case:
            return None

        from pipelines.comparability import run_comparability_assessment

        batch_data = case["batch_data"]
        report = run_comparability_assessment(
            pre_change_data=batch_data,
            product_name=case["product_name"],
            change_description=case.get("change_description", ""),
        )
        report_dict = report.to_dict()
        self._reports[case_id] = report_dict

        # Update case metadata
        import datetime
        action_summary = report_dict.get("action_summary", {})
        overall_action = action_summary.get("overall_action", report_dict["overall_verdict"])

        # Count critical gaps
        critical_gaps = sum(
            1 for ar in report_dict.get("attribute_results", [])
            if ar.get("concern") == "critical"
        )

        case["status"] = "assessed"
        case["overall_action"] = overall_action
        case["critical_gaps_count"] = critical_gaps
        case["last_updated"] = datetime.datetime.now().isoformat()

        return report_dict

    # -- Queries ----------------------------------------------------------

    def get_overview(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Return package overview data for a case."""
        report = self._reports.get(case_id)
        if not report:
            return None

        action_summary = report.get("action_summary", {})
        attr_results = report.get("attribute_results", [])

        # Build human-readable provenance entries from the provenance chain
        provenance_entries = _build_provenance_entries(report.get("provenance_chain", []))

        # Build overview structure matching what the UI expects
        # Judgment Core fields (None-guarded for legacy reports)
        jc_verdict = report.get("judgment_core_verdict")
        jc_confidence = report.get("judgment_confidence")
        jc_band = report.get("judgment_confidence_band")
        jc_blocking = report.get("blocking_clusters")
        jc_abstain = report.get("abstain_flag")
        jc_rule_ids = report.get("decision_rule_ids")
        jc_what_would_change = report.get("what_would_change_verdict")

        # P5 two-axis fields
        analytical_conclusion = report.get("analytical_conclusion", "insufficient_evidence")
        package_posture = report.get("package_posture", "defer")
        posture_rationale = report.get("posture_rationale", "")
        posture_rationale_factors = report.get("posture_rationale_factors", {})
        confidence_breakdown = report.get("confidence_breakdown") or {}

        overview = {
            "judgment_summary": {
                "overall_action": action_summary.get("overall_action", report["overall_verdict"]),
                "confidence": jc_confidence if jc_confidence is not None else report.get("evidence_strength_index", 0.5),
                "verdict": report["overall_verdict"],
                "key_finding": action_summary.get("summary", ""),
                # Judgment Core additions (None-guarded)
                "judgment_core_verdict": jc_verdict,
                "confidence_band": jc_band,
                "abstain_flag": jc_abstain,
                "decision_rule_ids": jc_rule_ids,
                # P5 two-axis verdict
                "analytical_conclusion": analytical_conclusion,
                "package_posture": package_posture,
                "posture_rationale": posture_rationale,
            },
            # P5 fields at top level for easy access
            "analytical_conclusion": analytical_conclusion,
            "package_posture": package_posture,
            "posture_rationale": posture_rationale,
            "posture_rationale_factors": posture_rationale_factors,
            "confidence_breakdown": confidence_breakdown,
            "critical_attributes": [
                {
                    "name": ar["name"],
                    "category": ar["category"],
                    "score": ar["score"] * 100,
                    "action": (ar.get("action") or {}).get("action_level", ar["concern"]),
                    "is_cqa": ar["is_cqa"],
                    "uncertainty": ar["uncertainty"] * 100,
                    "regulatory_reference": (ar.get("action") or {}).get("regulatory_reference", ""),
                }
                for ar in attr_results
            ],
            "reviewer_risk": {
                "predicted_questions": _build_predicted_questions_v2(report),
            },
            "provenance_entries": provenance_entries,
            "provenance_snapshot": {
                "sources_count": len(report.get("provenance_chain", [])),
                "precedents_cited": sum(
                    1 for p in report.get("provenance_chain", [])
                    if p.get("source_type") in ("precedent", "guideline")
                ),
                "guidelines_referenced": sum(
                    1 for p in report.get("provenance_chain", [])
                    if p.get("module") == "evidence_registry"
                ),
            },
            # Phase 4 Judgment Core additions (None-guarded for legacy reports)
            "blocking_clusters": jc_blocking,
            "what_would_change": jc_what_would_change,
        }
        return overview

    def get_attribute_detail(self, case_id: str, attr_name: str) -> Optional[Dict[str, Any]]:
        """Return deep-dive data for a single attribute."""
        report = self._reports.get(case_id)
        if not report:
            return None

        for ar in report.get("attribute_results", []):
            if ar["name"] == attr_name:
                action_info = ar.get("action") or {}
                return {
                    "attribute_name": ar["name"],
                    "category": ar["category"],
                    "pre_value": ar["pre_value"],
                    "post_value": ar["post_value"],
                    "unit": ar["unit"],
                    "delta_pct": ar["delta_pct"],
                    "score": ar["score"] * 100,
                    "comparable": ar["comparable"],
                    "concern": ar["concern"],
                    "is_cqa": ar["is_cqa"],
                    "cqa_designation": ar["cqa_designation"],
                    "uncertainty": ar["uncertainty"] * 100,
                    "action": action_info.get("action_level", ar["concern"]),
                    "action_with_reasoning": action_info.get("rationale", ar["detail"]),
                    "lot_variability": ar["detail"],
                    "functional_support": action_info.get("next_best_evidence", "See report"),
                    "regulatory_reference": action_info.get("regulatory_reference", ""),
                    "next_best_evidence": action_info.get("next_best_evidence", ""),
                    "estimated_effort": action_info.get("estimated_effort", ""),
                    "precedent_relevance": [
                        f"{p.get('title', '')} ({p.get('agency', '')} {p.get('year', '')})"
                        for p in action_info.get("precedent_context", [])
                    ],
                    "provenance": action_info.get("provenance", []),
                }
        return None

    def get_gaps(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Return evidence gaps for a case."""
        report = self._reports.get(case_id)
        if not report:
            return None

        gaps = []
        for ar in report.get("attribute_results", []):
            action_info = ar.get("action") or {}
            action_label = action_info.get("action_level", "")
            if ar["concern"] in ("major", "critical") or action_label in ("DEFER", "INVESTIGATE"):
                severity = "critical" if ar["concern"] == "critical" else "high"
                gaps.append({
                    "attribute": ar["name"],
                    "gap_type": "data_gap" if ar["uncertainty"] > 0.5 else "exceedance",
                    "severity": severity,
                    "why_important": ar["detail"],
                    "what_to_collect": action_info.get("rationale", "Collect additional data"),
                    "counterfactual_action_if_filled": action_info.get("counterfactual", "Score may improve if additional lots/methods provided"),
                })

        # Also add string-level evidence gaps
        for gap_text in report.get("evidence_gaps", []):
            if not any(g["why_important"] == gap_text for g in gaps):
                gaps.append({
                    "attribute": "General",
                    "gap_type": "evidence_gap",
                    "severity": "medium",
                    "why_important": gap_text,
                    "what_to_collect": "See recommended actions",
                    "counterfactual_action_if_filled": "Overall evidence strength may improve",
                })

        # Sort by severity
        gaps.sort(key=lambda g: SEVERITY_ORDER.get(g["severity"], 99))

        critical_count = sum(1 for g in gaps if g["severity"] == "critical")
        high_count = sum(1 for g in gaps if g["severity"] == "high")

        return {
            "gaps": gaps,
            "total_gaps": len(gaps),
            "critical_count": critical_count,
            "high_count": high_count,
        }


def _build_provenance_entries(provenance_chain: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert raw provenance chain into deduplicated, human-readable entries.

    Each entry gets a parsed source_id (e.g. "ICH Q5E Section 2.2: Purity assessment")
    and is grouped by source_type for display.
    """
    seen_ids = set()
    entries = []
    for p in provenance_chain:
        source_id = p.get("source_id", "")
        source_type = p.get("source_type", "computed")
        context = p.get("context", "")
        confidence = p.get("confidence", 0.5)
        module = p.get("module", "")

        # Deduplicate by source_id
        dedup_key = f"{source_type}:{source_id}"
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)

        # Make source_id more human-readable
        display_source = source_id
        if not display_source and module:
            display_source = module

        # Clean up context: remove module prefix patterns like "Guideline for PROCEED on purity"
        display_context = context
        if not display_context:
            display_context = display_source

        entries.append({
            "source_type": source_type,
            "source_id": display_source,
            "context": display_context,
            "confidence": confidence,
            "module": module,
        })

    return entries


def _build_predicted_questions_v2(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate predicted questions via gap-cluster system (new).

    Falls back to legacy _build_predicted_questions if pipeline data
    (matched_references, refs_by_category) is not available in the report.
    """
    try:
        from services.reviewer_concerns import generate_reviewer_concerns
        attr_results = report.get("attribute_results", [])
        matched_refs = report.get("_matched_references", [])
        refs_by_cat = report.get("_refs_by_category", {})
        case_ctx = report.get("_case_context", {})

        # If pipeline data not available, fall back to legacy
        if not attr_results:
            return _build_predicted_questions(report)

        concerns = generate_reviewer_concerns(
            attribute_results=attr_results,
            matched_references=matched_refs,
            refs_by_category=refs_by_cat,
            case_context=case_ctx,
        )

        if not concerns:
            return _build_predicted_questions(report)

        return [
            {
                "question": rc.question,
                "probability": rc.probability,
                "impact": rc.severity,
                "is_primary": rc.is_primary,
                "affected_attributes": rc.affected_attributes,
                "precedent": rc.supporting_precedent,
            }
            for rc in concerns
        ]
    except Exception:
        return _build_predicted_questions(report)


def _build_predicted_questions(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Synthesize predicted reviewer questions from report data."""
    questions = []

    for ar in report.get("attribute_results", []):
        if ar["concern"] == "critical":
            questions.append({
                "question": f"What is the root cause of the {ar['delta_pct']:+.1f}% change in {ar['name']}? Provide trending data across batches.",
                "probability": 0.95,
                "impact": "high",
            })
        elif ar["concern"] == "major" and ar["is_cqa"]:
            questions.append({
                "question": f"Please provide additional characterization data for CQA {ar['name']} (delta={ar['delta_pct']:+.1f}%).",
                "probability": 0.85,
                "impact": "high",
            })
        elif ar["uncertainty"] > 0.5:
            questions.append({
                "question": f"The uncertainty for {ar['name']} appears high ({ar['uncertainty']:.0%}). Can you provide additional lots to improve confidence?",
                "probability": 0.70,
                "impact": "medium",
            })

    if not report.get("attribute_results"):
        questions.append({
            "question": "No attribute data provided. Please submit complete comparability dataset.",
            "probability": 1.0,
            "impact": "high",
        })

    return questions


# ---------------------------------------------------------------------------
# Singleton backend (stored in session state)
# ---------------------------------------------------------------------------
def get_backend() -> DirectBackend:
    """Return the shared DirectBackend instance from session state."""
    if "backend" not in st.session_state:
        st.session_state.backend = DirectBackend()
    return st.session_state.backend


# ---------------------------------------------------------------------------
# UI Helper Components
# ---------------------------------------------------------------------------
def render_verdict_banner(verdict: str, action: str, confidence: float):
    """Render the verdict banner at the top of the overview page."""
    color = VERDICT_COLORS.get(verdict, "#6c757d")
    action_color = ACTION_COLORS.get(action, "#6c757d")

    st.markdown(f"""
    <div class="verdict-banner" style="background-color: {color};">
        <strong>{verdict}</strong> &mdash; {action} (confidence: {confidence:.0%})
    </div>
    """, unsafe_allow_html=True)


def render_action_box(action: str, confidence: float):
    """Render action recommendation box (action-first design)."""
    css_class = "success-card"
    if action in ("REJECT", "Halt Review / Redesign Change"):
        css_class = "danger-card"
    elif action in ("DEFER", "INVESTIGATE", "Collect Additional Data"):
        css_class = "warning-card"

    st.markdown(f"""
    <div class="{css_class}">
    <h3>Recommended Action</h3>
    <p><strong>{action}</strong></p>
    <p>Confidence: {confidence:.0%}</p>
    </div>
    """, unsafe_allow_html=True)


def render_gap_warnings(gaps: list):
    """Render critical gaps with warning styling (gap-first design)."""
    critical_gaps = [g for g in gaps if g.get("severity") == "critical"]
    if critical_gaps:
        st.markdown("#### Critical Gaps")
        for gap in critical_gaps:
            st.markdown(f"""
            <div class="danger-card">
            <strong>{gap['attribute']}</strong>: {gap['why_important']}
            <br><em>Action:</em> {gap['what_to_collect']}
            </div>
            """, unsafe_allow_html=True)

    high_gaps = [g for g in gaps if g.get("severity") == "high"]
    if high_gaps:
        st.markdown("#### High Priority Gaps")
        for gap in high_gaps:
            st.markdown(f"""
            <div class="warning-card">
            <strong>{gap['attribute']}</strong>: {gap['why_important']}
            <br><em>Action:</em> {gap['what_to_collect']}
            </div>
            """, unsafe_allow_html=True)
