"""
Gap Closure Workspace -- Evidence gap inventory with remediation planning.
"""

import streamlit as st
import pandas as pd
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ui.config import setup_page, get_backend, SEVERITY_ORDER

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

st.title("Gap Closure Workspace")
st.markdown("Review evidence gaps and plan remediation activities.")
st.caption(f"Case: {case['product_name']}")

# =========================================================================
# Cluster-driven gap view (Phase 4C - additive)
# =========================================================================
overview = backend.get_overview(st.session_state.case_id)
blocking_clusters = overview.get("blocking_clusters") if overview else None

if blocking_clusters:
    st.markdown("## Cluster-Driven Gaps")
    st.markdown(
        "These gaps are identified from risk clusters in the Judgment Core. "
        "Resolving blocking clusters has the highest impact on the verdict."
    )
    for bc in blocking_clusters:
        severity_tag = bc.get("concern_level", "unknown").upper()
        with st.expander(f"[{severity_tag}] {bc.get('category', 'Unknown')} — {bc.get('risk_semantics', '')}"):
            st.markdown(f"**Cluster ID:** `{bc.get('cluster_id', 'N/A')}`")
            st.markdown(f"**Reason:** {bc.get('reason', 'N/A')}")
            st.markdown(f"**Risk Semantics:** {bc.get('risk_semantics', 'N/A')}")
            st.markdown(f"**Concern Level:** {bc.get('concern_level', 'N/A')}")
    st.markdown("---")

# =========================================================================
# Load gaps
# =========================================================================
gaps_data = backend.get_gaps(st.session_state.case_id)

if not gaps_data:
    st.error("Failed to load gaps.")
    st.stop()

gaps = gaps_data.get("gaps", [])

# =========================================================================
# Summary metrics
# =========================================================================
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Gaps", gaps_data.get("total_gaps", 0))
with col2:
    st.metric("Critical", gaps_data.get("critical_count", 0))
with col3:
    st.metric("High", gaps_data.get("high_count", 0))
with col4:
    medium_low = len([g for g in gaps if g["severity"] in ("medium", "low")])
    st.metric("Medium/Low", medium_low)

st.markdown("---")

if not gaps:
    st.success("No evidence gaps detected. Package looks complete.")
    st.stop()

# =========================================================================
# Session-state tracking for "addressed" checkboxes (v1)
# =========================================================================
if "addressed_gaps" not in st.session_state:
    st.session_state.addressed_gaps = set()

# =========================================================================
# Filter
# =========================================================================
severity_filter = st.multiselect(
    "Filter by severity:",
    ["critical", "high", "medium", "low"],
    default=["critical", "high"],
)

filtered = [g for g in gaps if g["severity"] in severity_filter]

# =========================================================================
# Gap Table
# =========================================================================
st.markdown("## Gap Inventory")

if filtered:
    rows = []
    for g in filtered:
        addressed = g["attribute"] in st.session_state.addressed_gaps
        rows.append({
            "Attribute": g["attribute"],
            "Gap Type": g["gap_type"],
            "Severity": g["severity"],
            "Why Important": g["why_important"][:80],
            "Action": g["what_to_collect"][:80],
            "If Filled": g["counterfactual_action_if_filled"][:60],
            "Addressed": "Yes" if addressed else "",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No gaps match the selected severity filter.")

# =========================================================================
# Gap Details (expandable, with address checkbox)
# =========================================================================
st.markdown("---")
st.markdown("## Gap Details")

for idx, gap in enumerate(filtered):
    attr = gap["attribute"]
    severity_tag = gap["severity"].upper()
    is_addressed = attr in st.session_state.addressed_gaps

    label = f"{'[DONE] ' if is_addressed else ''}{attr} [{gap['gap_type']}] -- {severity_tag}"
    with st.expander(label):
        st.markdown(f"""
        **Why This Matters:**
        {gap['why_important']}

        **What to Collect:**
        {gap['what_to_collect']}

        **What Changes If Filled:**
        {gap['counterfactual_action_if_filled']}
        """)

        # Checkbox to mark as addressed (session state only, v1)
        addressed = st.checkbox(
            f"Mark as addressed",
            value=is_addressed,
            key=f"gap_addr_{idx}_{attr}_{gap['gap_type']}",
        )
        if addressed:
            st.session_state.addressed_gaps.add(attr)
        elif attr in st.session_state.addressed_gaps:
            st.session_state.addressed_gaps.discard(attr)

# Summary of addressed gaps
n_addressed = len(st.session_state.addressed_gaps)
n_total = len(gaps)
if n_addressed > 0:
    st.markdown("---")
    st.info(f"{n_addressed} of {n_total} gap(s) marked as addressed (session only, not persisted).")

# Navigation
st.markdown("---")
col1, col2 = st.columns(2)
with col1:
    if st.button("Back to Overview"):
        st.switch_page("pages/01_PackageOverview.py")
with col2:
    if st.button("Export"):
        st.switch_page("pages/04_Export.py")
