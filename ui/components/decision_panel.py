"""Decision Panel component — renders verdict cards, evidence gaps,
reviewer questions, confidence metrics, and blocking clusters."""

import re
import streamlit as st

from ui.config import VERDICT_COLORS, ANALYTICAL_COLORS, POSTURE_COLORS

# Extended color maps for non-comparability verdicts
VERDICT_COLOR_MAP = {
    **ANALYTICAL_COLORS,
    "adequate": "#28a745", "gaps identified": "#ffc107", "insufficient": "#dc3545",
    "ready": "#28a745", "needs data": "#ffc107", "not ready": "#dc3545",
    "sufficient": "#28a745", "extrapolated": "#ffc107", "supports claim": "#28a745",
    "complete": "#28a745", "partial": "#ffc107", "inadequate": "#dc3545",
    "validated": "#28a745", "partially validated": "#ffc107", "not validated": "#dc3545",
    "assessed": "#17a2b8", "review required": "#6c757d",
    "assessment complete": "#17a2b8", "needs review": "#ffc107",
    "needs supplement": "#ffc107", "needs monitoring": "#ffc107",
    "data gaps": "#dc3545", "not sufficient": "#dc3545",
    "needs studies": "#ffc107", "incomplete": "#dc3545",
}
POSTURE_COLOR_MAP = {**POSTURE_COLORS, **VERDICT_COLOR_MAP}

# Verdict → muted tint backgrounds per DESIGN.md
_TINT_MAP = {
    "#28a745": "#defbe6", "#ff9800": "#fdf6dd", "#dc3545": "#fff1f1",
    "#17a2b8": "#edf5ff", "#ffc107": "#fdf6dd", "#6c757d": "#f4f4f4",
}

# Type-aware axis labels
TYPE_AXIS_LABELS = {
    "COMPARABILITY": ("ANALYTICAL CONCLUSION", "PACKAGE POSTURE"),
    "CHARACTERIZATION": ("COMPLETENESS LEVEL", "SUBMISSION READINESS"),
    "STABILITY": ("SHELF-LIFE SUPPORT", "STORAGE CLAIM ADEQUACY"),
    "ANALYTICAL_METHOD": ("ICH Q2 COVERAGE", "METHOD SUITABILITY"),
}


def render_verdict_cards(overview, doc_type):
    """Render the two-axis verdict cards."""
    judgment = overview.get("judgment_summary", {})
    ac = overview.get("analytical_conclusion", judgment.get("analytical_conclusion", "insufficient_evidence"))
    pp = overview.get("package_posture", judgment.get("package_posture", "defer"))

    axis1, axis2 = TYPE_AXIS_LABELS.get(doc_type, ("ASSESSMENT", "STATUS"))

    ac_color = VERDICT_COLOR_MAP.get(ac, VERDICT_COLOR_MAP.get(ac.lower(), "#6c757d"))
    pp_color = POSTURE_COLOR_MAP.get(pp, POSTURE_COLOR_MAP.get(pp.lower(), "#6c757d"))
    ac_label = ac.replace("_", " ").title()
    pp_label = pp.replace("_", " ").title()
    ac_bg = _TINT_MAP.get(ac_color, "#f4f4f4")
    pp_bg = _TINT_MAP.get(pp_color, "#f4f4f4")

    st.markdown(f"""
    <div style="display:flex;gap:8px;margin-bottom:8px;">
        <div style="flex:1;background:{ac_bg};padding:12px 16px;border-left:4px solid {ac_color};">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#525252;font-weight:600;">{axis1}</div>
            <div style="font-size:16px;font-weight:600;color:#161616;margin-top:4px;">{ac_label}</div>
        </div>
        <div style="flex:1;background:{pp_bg};padding:12px 16px;border-left:4px solid {pp_color};">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#525252;font-weight:600;">{axis2}</div>
            <div style="font-size:16px;font-weight:600;color:#161616;margin-top:4px;">{pp_label}</div>
        </div>
    </div>
    <div style="color:#6f6f6f;font-size:12px;font-style:italic;margin-bottom:8px;">
        For decision support only. Not regulatory advice.
    </div>
    """, unsafe_allow_html=True)

    # Posture rationale
    rationale = overview.get("posture_rationale", judgment.get("posture_rationale", ""))
    if rationale:
        st.markdown(
            f'<div style="background:#edf5ff;color:#002d9c;padding:12px 16px;'
            f'border-left:4px solid #0f62fe;margin-bottom:8px;font-size:14px;">'
            f'{rationale}</div>',
            unsafe_allow_html=True,
        )


def render_evidence_gaps(overview):
    """Render three-state evidence gap items."""
    ev = overview.get("extracted_evidence", {})
    if not ev:
        return

    _fields = {
        "HMW %": ev.get("hmw", {}),
        "Main Charge Peak %": ev.get("main_charge_peak", {}),
        "Afucosylation %": ev.get("afucosylation", {}),
        "Relative Potency %": ev.get("relative_potency", {}),
    }

    present = [(n, d.get("value")) for n, d in _fields.items() if isinstance(d, dict) and d.get("state") == "present"]
    uncertain = [(n, d.get("uncertainty_reason", "")) for n, d in _fields.items() if isinstance(d, dict) and d.get("state") == "uncertain"]
    absent = [(n,) for n, d in _fields.items() if isinstance(d, dict) and d.get("state") == "confirmed_absent"]

    if present or uncertain or absent:
        with st.expander("Evidence Gaps & Extraction Status", expanded=True):
            for name, val in present:
                st.markdown(
                    f'<div style="background:#defbe6;color:#0e6027;padding:12px 16px;'
                    f'border-left:4px solid #24a148;margin-bottom:8px;font-size:14px;">'
                    f'<strong>{name}</strong>: {val:.2f}</div>',
                    unsafe_allow_html=True,
                )
            for name, reason in uncertain:
                display = reason.replace("_", " ") if reason else "Could not extract — verify manually"
                st.markdown(
                    f'<div style="background:#fdf6dd;color:#735c0f;padding:12px 16px;'
                    f'border-left:4px solid #f1c21b;margin-bottom:8px;font-size:14px;">'
                    f'<strong>{name}</strong>: {display}</div>',
                    unsafe_allow_html=True,
                )
            for (name,) in absent:
                st.markdown(
                    f'<div style="background:#fff1f1;color:#750e13;padding:12px 16px;'
                    f'border-left:4px solid #da1e28;margin-bottom:8px;font-size:14px;">'
                    f'<strong>{name}</strong>: Not found in document</div>',
                    unsafe_allow_html=True,
                )


def render_reviewer_questions(overview):
    """Render predicted reviewer questions with severity badges."""
    ev = overview.get("extracted_evidence", {})
    concerns = ev.get("reviewer_concerns", []) if ev else []
    if not concerns:
        return

    with st.expander("Predicted Reviewer Questions", expanded=True):
        for idx, rc in enumerate(concerns, 1):
            if "CRITICAL" in rc:
                badge = '<span style="background:#da1e28;color:white;padding:2px 6px;border-radius:2px;font-size:11px;margin-right:6px;">CRITICAL</span>'
                border = "#da1e28"
            elif "may" in rc.lower() or "could" in rc.lower():
                badge = '<span style="background:#f1c21b;color:#161616;padding:2px 6px;border-radius:2px;font-size:11px;margin-right:6px;">MAJOR</span>'
                border = "#f1c21b"
            else:
                badge = '<span style="background:#8d8d8d;color:white;padding:2px 6px;border-radius:2px;font-size:11px;margin-right:6px;">MINOR</span>'
                border = "#c6c6c6"

            # Source cross-linking keywords
            keywords = []
            for kw in ["potency", "HOS", "structure", "glycosylation", "aggregation",
                        "charge", "purity", "reference standard", "stability",
                        "method", "OOS", "shelf life", "afucosylation"]:
                if kw.lower() in rc.lower():
                    keywords.append(kw)

            source_hint = ""
            if keywords:
                source_hint = (
                    f'<div style="font-size:11px;color:#6f6f6f;margin-top:4px;">'
                    f'Related: {", ".join(keywords)}</div>'
                )

            st.markdown(
                f'<div style="border-left:4px solid {border};padding:12px 16px;'
                f'margin-bottom:8px;font-size:14px;">'
                f'{badge}{idx}. {rc}{source_hint}</div>',
                unsafe_allow_html=True,
            )


def render_confidence(overview):
    """Render confidence breakdown metrics."""
    cb = overview.get("confidence_breakdown", {})
    ac_conf = cb.get("analytical_confidence", 0.0)
    pr_conf = cb.get("package_readiness", 0.0)
    ec_conf = cb.get("evidence_completeness", 0.0)

    cols = st.columns(3)
    cols[0].metric("Analytical", f"{ac_conf:.0%}")
    cols[1].metric("Package Readiness", f"{pr_conf:.0%}")
    cols[2].metric("Evidence Completeness", f"{ec_conf:.0%}")

    deriv = cb.get("derivation_summary", "")
    if deriv:
        st.caption(deriv)
