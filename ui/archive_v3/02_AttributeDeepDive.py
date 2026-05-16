"""
Attribute Deep Dive -- Single attribute drill-down with full reasoning.
"""

import streamlit as st
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ui.config import setup_page, get_backend

setup_page()

# =========================================================================
# Guard
# =========================================================================
if not st.session_state.get("case_id"):
    st.error("No case selected. Go to Case List first.")
    st.stop()

backend = get_backend()
case = backend.get_case(st.session_state.case_id)

if not case:
    st.error("Case not found.")
    st.stop()

if case["status"] != "assessed":
    st.warning("Run the assessment first from Package Overview.")
    st.stop()

st.title("Attribute Deep Dive")
st.caption(f"Case: {case['product_name']}")

# =========================================================================
# Get attribute list from overview
# =========================================================================
overview = backend.get_overview(st.session_state.case_id)
if not overview:
    st.error("Failed to load overview.")
    st.stop()

attrs = [a["name"] for a in overview.get("critical_attributes", [])]
if not attrs:
    st.warning("No attributes to analyze.")
    st.stop()

# =========================================================================
# Select attribute
# =========================================================================
selected_attr = st.selectbox("Select an attribute:", attrs)

# =========================================================================
# Load deep dive
# =========================================================================
dive = backend.get_attribute_detail(st.session_state.case_id, selected_attr)

if not dive:
    st.error(f"Failed to load details for {selected_attr}.")
    st.stop()

# =========================================================================
# Display
# =========================================================================
st.markdown(f"## {dive['attribute_name']}")

# Top metrics: pre/post comparison with delta
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Pre-Change", f"{dive['pre_value']} {dive['unit']}")
with col2:
    delta_str = f"{dive['delta_pct']:+.1f}%"
    st.metric("Post-Change", f"{dive['post_value']} {dive['unit']}", delta=delta_str)
with col3:
    st.metric("Score", f"{dive['score']:.0f}/100")
with col4:
    st.metric("Action", dive['action'])

st.markdown("---")

# Score + uncertainty visualization
col1, col2 = st.columns(2)
with col1:
    st.markdown("### Score & Uncertainty")
    score_pct = dive["score"]
    unc_pct = dive["uncertainty"]
    st.progress(min(score_pct / 100, 1.0), text=f"Score: {score_pct:.0f}/100")
    st.progress(min(unc_pct / 100, 1.0), text=f"Uncertainty: {unc_pct:.1f}%")

    cqa_label = "Yes" if dive["is_cqa"] else "No"
    st.markdown(f"**CQA:** {cqa_label} ({dive['cqa_designation']})")
    st.markdown(f"**Category:** {dive['category']}")
    st.markdown(f"**Comparable:** {'Yes' if dive['comparable'] else 'No'}")
    st.markdown(f"**Concern Level:** {dive['concern']}")

with col2:
    st.markdown("### Action Card")
    css_class = "success-card"
    if dive["action"] in ("REJECT",):
        css_class = "danger-card"
    elif dive["action"] in ("DEFER", "INVESTIGATE"):
        css_class = "warning-card"

    reg_ref = dive.get("regulatory_reference", "")
    effort = dive.get("estimated_effort", "")
    action_card_html = f'<div class="{css_class}"><h4>{dive["action"]}</h4>'
    if reg_ref:
        action_card_html += f"<p><strong>Regulatory Ref:</strong> {reg_ref}</p>"
    if effort:
        action_card_html += f"<p><strong>Estimated Effort:</strong> {effort}</p>"
    action_card_html += "</div>"
    st.markdown(action_card_html, unsafe_allow_html=True)

    # Next Best Evidence highlighted box
    nbe = dive.get("next_best_evidence", "")
    if nbe:
        st.markdown(
            f'<div class="info-card"><strong>Next Best Evidence:</strong> {nbe}</div>',
            unsafe_allow_html=True,
        )

# Reasoning
st.markdown("---")
with st.expander("Detailed Reasoning", expanded=True):
    reasoning = dive.get("action_with_reasoning", "No reasoning available.")
    st.markdown(reasoning)

# Supporting details
col1, col2 = st.columns(2)
with col1:
    st.subheader("Variability")
    st.info(dive.get("lot_variability", "Unknown"))
with col2:
    st.subheader("Supporting Evidence")
    st.info(f"**Functional Support:** {dive.get('functional_support', 'N/A')}")

# Provenance chain for this attribute (human-readable, grouped)
if dive.get("provenance"):
    st.markdown("---")
    st.subheader("Provenance Chain")

    # Group by source type
    guidelines = [p for p in dive["provenance"] if p.get("source_type") == "guideline"]
    precedents = [p for p in dive["provenance"] if p.get("source_type") == "precedent"]
    computed = [p for p in dive["provenance"] if p.get("source_type") not in ("guideline", "precedent")]

    if guidelines:
        st.markdown("**Guidelines**")
        for p in guidelines:
            source_id = p.get("source_id", "")
            context = p.get("context", "")
            confidence = p.get("confidence", 0.5)
            display = f"{source_id} -- {context}" if context and context != source_id else source_id
            st.markdown(
                f'<div class="info-card">{display} <em>(Confidence: {confidence:.1f})</em></div>',
                unsafe_allow_html=True,
            )

    if precedents:
        st.markdown("**Precedents**")
        for p in precedents:
            source_id = p.get("source_id", "")
            context = p.get("context", "")
            confidence = p.get("confidence", 0.5)
            display = f"{source_id} -- {context}" if context and context != source_id else source_id
            st.markdown(
                f'<div class="info-card">{display} <em>(Confidence: {confidence:.1f})</em></div>',
                unsafe_allow_html=True,
            )

    if computed:
        st.markdown("**Computed**")
        for p in computed:
            module = p.get("module", "engine")
            context = p.get("context", "")
            confidence = p.get("confidence", 0.5)
            display = f"{module} -- {context}" if context else module
            st.markdown(
                f'<div class="metric-card">{display} <em>(Confidence: {confidence:.1f})</em></div>',
                unsafe_allow_html=True,
            )

# Precedent
if dive.get("precedent_relevance"):
    st.subheader("Relevant Precedents")
    for prec in dive["precedent_relevance"]:
        st.caption(prec)

# Navigation
st.markdown("---")
col1, col2 = st.columns(2)
with col1:
    if st.button("Back to Overview"):
        st.switch_page("pages/01_PackageOverview.py")
with col2:
    if st.button("Gap Closure"):
        st.switch_page("pages/03_GapClosure.py")
