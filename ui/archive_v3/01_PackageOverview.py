"""
Package Overview -- THE CORE PAGE

Displays the 5 decision blocks:
1. Judgment Summary (action-first)
2. Critical Gaps (gap-first)
3. Attribute Scorecard
4. Reviewer Risk (predicted questions)
5. Provenance Snapshot (human-readable evidence sources)
"""

import streamlit as st
import pandas as pd
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ui.config import (
    setup_page, get_backend,
    render_verdict_banner, render_action_box, render_gap_warnings,
)

setup_page()

# =========================================================================
# Guard: must have a case selected
# =========================================================================
if not st.session_state.get("case_id"):
    st.error("No case selected. Go to Case List first.")
    st.stop()

backend = get_backend()
case = backend.get_case(st.session_state.case_id)

if not case:
    st.error("Case not found.")
    st.stop()

st.title("Package Overview")
st.caption(f"Case: {case['product_name']} | {st.session_state.case_id}")

# =========================================================================
# Run assessment if not yet done
# =========================================================================
if case["status"] != "assessed":
    st.warning("This case has not been assessed yet.")
    if st.button("Run Comparability Assessment", type="primary"):
        with st.spinner("Running comparability pipeline..."):
            report = backend.run_assessment(st.session_state.case_id)
        if report:
            st.success(f"Assessment complete: {report['overall_verdict']}")
            st.rerun()
        else:
            st.error("Assessment failed. Check batch data format.")
    st.stop()

# =========================================================================
# Load overview data
# =========================================================================
overview = backend.get_overview(st.session_state.case_id)
if not overview:
    st.error("Failed to load overview data.")
    st.stop()

gaps_data = backend.get_gaps(st.session_state.case_id)

# =========================================================================
# Block 1: Judgment Summary (ACTION-FIRST)
# =========================================================================
st.markdown("## Decision")

judgment = overview.get("judgment_summary", {})

render_verdict_banner(
    verdict=judgment.get("verdict", "Unknown"),
    action=judgment.get("overall_action", "Assess"),
    confidence=judgment.get("confidence", 0.5),
)

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    render_action_box(
        judgment.get("overall_action", "Assess"),
        judgment.get("confidence", 0.5),
    )
with col2:
    st.metric("Verdict", judgment.get("verdict", "Unknown"))
    key_finding = judgment.get("key_finding", "")
    if key_finding:
        st.caption(key_finding)
with col3:
    # Authority Confidence indicator (Phase 4B)
    jc_band = judgment.get("confidence_band")
    if jc_band:
        band_colors = {"high": "green", "moderate": "orange", "low": "red"}
        band_label = jc_band.upper()
        st.metric("Confidence Band", band_label)
    jc_abstain = judgment.get("abstain_flag")
    if jc_abstain:
        st.warning("System abstained from judgment")

# =========================================================================
# Block 1B: Cluster Breakdown (Phase 4B - additive)
# =========================================================================
blocking_clusters = overview.get("blocking_clusters")
if blocking_clusters:
    st.markdown("### Blocking Clusters")
    for bc in blocking_clusters:
        severity_class = "danger-card" if bc.get("concern_level") in ("critical", "major") else "warning-card"
        st.markdown(f"""
        <div class="{severity_class}">
        <strong>{bc.get('category', 'Unknown')}</strong> — {bc.get('risk_semantics', '')}
        <br>{bc.get('reason', '')[:200]}
        <br><em>Concern: {bc.get('concern_level', 'unknown')}</em>
        </div>
        """, unsafe_allow_html=True)

# =========================================================================
# Block 1C: What-Would-Change Cards (Phase 4B - additive)
# =========================================================================
what_would_change = overview.get("what_would_change")
if what_would_change:
    with st.expander("What Would Change the Verdict"):
        for wc in what_would_change:
            st.markdown(f"""
            <div class="info-card">
            <strong>Cluster:</strong> {wc.get('cluster_id', 'N/A')}<br>
            <strong>Current Gap:</strong> {wc.get('current_gap', 'N/A')}<br>
            <strong>If Resolved:</strong> {wc.get('if_gap_resolved', 'N/A')}<br>
            <strong>Verdict Would Become:</strong> {wc.get('verdict_would_become', 'N/A')}
            </div>
            """, unsafe_allow_html=True)

# =========================================================================
# Block 1D: Decision Rule Trace (Phase 4B - collapsible, additive)
# =========================================================================
rule_ids = judgment.get("decision_rule_ids")
if rule_ids:
    with st.expander("Decision Rule Trace"):
        st.markdown("Rules applied during judgment:")
        for rule_id in rule_ids:
            st.markdown(f"- `{rule_id}`")

# =========================================================================
# Block 2: Critical Gaps (GAP-FIRST)
# =========================================================================
st.markdown("---")
st.markdown("## Top Gaps")

if gaps_data:
    gaps = gaps_data.get("gaps", [])
    if gaps:
        render_gap_warnings(gaps)

        # Summary metrics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Gaps", gaps_data.get("total_gaps", 0))
        with col2:
            st.metric("Critical", gaps_data.get("critical_count", 0))
        with col3:
            st.metric("High", gaps_data.get("high_count", 0))
    else:
        st.success("No evidence gaps detected.")
else:
    st.info("Gap analysis not available.")

# =========================================================================
# Block 3: Attribute Scorecard
# =========================================================================
st.markdown("---")
st.markdown("## Attribute Scores")

critical_attrs = overview.get("critical_attributes", [])

if critical_attrs:
    # Top 5 as metric cards
    top_attrs = sorted(critical_attrs, key=lambda a: a.get("score", 100))[:5]
    cols = st.columns(min(len(top_attrs), 5))
    for idx, attr in enumerate(top_attrs):
        with cols[idx]:
            cqa_tag = " [CQA]" if attr.get("is_cqa") else ""
            st.metric(
                f"{attr['name'][:18]}{cqa_tag}",
                f"{attr.get('score', 0):.0f}/100",
                delta=attr.get("action", ""),
            )

    # Full table
    with st.expander(f"View All Attributes ({len(critical_attrs)})"):
        rows = []
        for attr in critical_attrs:
            rows.append({
                "Attribute": attr["name"],
                "Category": attr["category"],
                "Score": f"{attr['score']:.0f}",
                "Action": attr.get("action", ""),
                "CQA": "Yes" if attr.get("is_cqa") else "No",
                "Uncertainty": f"{attr.get('uncertainty', 0):.1f}%",
                "Regulatory Ref": attr.get("regulatory_reference", ""),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No attributes to display.")

# =========================================================================
# Block 4: Reviewer Risk
# =========================================================================
st.markdown("---")
st.markdown("## Predicted Reviewer Questions")

reviewer_risk = overview.get("reviewer_risk", {})
questions = reviewer_risk.get("predicted_questions", [])

if questions:
    for q in questions:
        q_text = q["question"]
        display = q_text[:80] + "..." if len(q_text) > 80 else q_text
        with st.expander(f"Q: {display}"):
            st.write(f"**Probability:** {q['probability']:.0%}")
            st.write(f"**Impact:** {q['impact']}")
            st.write(f"**Full question:** {q['question']}")
else:
    st.info("No predicted questions.")

# =========================================================================
# Block 5: Evidence Sources (human-readable)
# =========================================================================
st.markdown("---")
st.markdown("## Evidence Sources")

provenance_entries = overview.get("provenance_entries", [])

if provenance_entries:
    # Group by source type
    guidelines = [e for e in provenance_entries if e["source_type"] == "guideline"]
    precedents = [e for e in provenance_entries if e["source_type"] == "precedent"]
    computed = [e for e in provenance_entries if e["source_type"] == "computed"]

    if guidelines:
        st.markdown("#### Guidelines")
        for entry in guidelines:
            conf = entry.get("confidence", 0.0)
            st.markdown(
                f'<div class="info-card">'
                f'<strong>{entry["source_id"]}</strong> '
                f'&mdash; {entry["context"]} '
                f'<em>(Confidence: {conf:.1f})</em>'
                f'</div>',
                unsafe_allow_html=True,
            )

    if precedents:
        st.markdown("#### Precedents")
        for entry in precedents:
            conf = entry.get("confidence", 0.0)
            st.markdown(
                f'<div class="info-card">'
                f'<strong>{entry["source_id"]}</strong> '
                f'&mdash; {entry["context"]} '
                f'<em>(Confidence: {conf:.1f})</em>'
                f'</div>',
                unsafe_allow_html=True,
            )

    if computed:
        st.markdown("#### Computed")
        for entry in computed:
            conf = entry.get("confidence", 0.0)
            st.markdown(
                f'<div class="metric-card">'
                f'<strong>{entry["module"]}</strong> '
                f'&mdash; {entry["context"]} '
                f'<em>(Confidence: {conf:.1f})</em>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Summary counts
    st.markdown("")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Guidelines", len(guidelines))
    with col2:
        st.metric("Precedents", len(precedents))
    with col3:
        st.metric("Computed", len(computed))
else:
    # Fallback to counts if no detailed entries available
    prov = overview.get("provenance_snapshot", {})
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Data Sources", prov.get("sources_count", 0))
    with col2:
        st.metric("Precedents Cited", prov.get("precedents_cited", 0))
    with col3:
        st.metric("Guidelines Referenced", prov.get("guidelines_referenced", 0))

# =========================================================================
# Navigation
# =========================================================================
st.markdown("---")
col1, col2, col3 = st.columns(3)
with col1:
    if st.button("Attribute Deep Dive"):
        st.switch_page("pages/02_AttributeDeepDive.py")
with col2:
    if st.button("Gap Closure Workspace"):
        st.switch_page("pages/03_GapClosure.py")
with col3:
    if st.button("Export"):
        st.switch_page("pages/04_Export.py")
