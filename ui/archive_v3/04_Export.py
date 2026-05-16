"""
Export -- Download case data and reports.
"""

import streamlit as st
import json
import csv
import io
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

st.title("Export Case")
st.caption(f"Case: {case['product_name']} | {st.session_state.case_id}")

if case["status"] != "assessed":
    st.warning("Run the assessment first before exporting.")
    st.stop()

# =========================================================================
# Get report data
# =========================================================================
report = backend._reports.get(st.session_state.case_id)
if not report:
    st.error("No assessment report found for this case.")
    st.stop()

# =========================================================================
# Export options
# =========================================================================
st.subheader("Export Format")

format_choice = st.radio(
    "Choose export format:",
    [
        "JSON (Full Report)",
        "JSON (Summary Only)",
        "CSV (Attribute Scores)",
        "ICH Q5E DOCX Report",
    ],
)

# =========================================================================
# Generate exports
# =========================================================================

if format_choice == "JSON (Full Report)":
    json_str = json.dumps(report, indent=2, default=str)
    st.download_button(
        label="Download Full Report JSON",
        data=json_str,
        file_name=f"{case['product_name'].replace(' ', '_')}_full_report.json",
        mime="application/json",
    )
    with st.expander("Preview"):
        st.json(report)

elif format_choice == "JSON (Summary Only)":
    summary = {
        "product_name": report.get("product_name"),
        "overall_verdict": report.get("overall_verdict"),
        "evidence_strength_index": report.get("evidence_strength_index"),
        "n_attributes": report.get("n_attributes"),
        "n_cqa": report.get("n_cqa"),
        "n_comparable": report.get("n_comparable"),
        "n_flagged": report.get("n_flagged"),
        "action_summary": report.get("action_summary"),
        "evidence_gaps": report.get("evidence_gaps"),
        "recommended_actions": report.get("recommended_actions"),
        "timestamp": report.get("timestamp"),
    }
    json_str = json.dumps(summary, indent=2, default=str)
    st.download_button(
        label="Download Summary JSON",
        data=json_str,
        file_name=f"{case['product_name'].replace(' ', '_')}_summary.json",
        mime="application/json",
    )
    with st.expander("Preview"):
        st.json(summary)

elif format_choice == "CSV (Attribute Scores)":
    attrs = report.get("attribute_results", [])
    if attrs:
        output = io.StringIO()
        fieldnames = [
            "name", "category", "pre_value", "post_value", "unit",
            "delta_pct", "score", "comparable", "concern",
            "is_cqa", "cqa_designation", "uncertainty",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for ar in attrs:
            writer.writerow({k: ar.get(k, "") for k in fieldnames})

        csv_str = output.getvalue()
        st.download_button(
            label="Download Attribute CSV",
            data=csv_str,
            file_name=f"{case['product_name'].replace(' ', '_')}_attributes.csv",
            mime="text/csv",
        )
        with st.expander("Preview"):
            import pandas as pd
            df = pd.DataFrame([{k: ar.get(k, "") for k in fieldnames} for ar in attrs])
            st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.warning("No attributes in report.")

elif format_choice == "ICH Q5E DOCX Report":
    st.markdown("---")
    st.markdown("### ICH Q5E Comparability Report")

    # Try to use the existing report generator
    try:
        from reports.comparability_report import generate_comparability_report
        from pipelines.schemas import ComparabilityReport as CR, AttributeResult as AR

        if st.button("Generate DOCX Report", type="primary"):
            with st.spinner("Generating ICH Q5E report..."):
                # Reconstruct the dataclass from dict
                attr_results = []
                for ar_dict in report.get("attribute_results", []):
                    attr_results.append(AR(
                        name=ar_dict["name"],
                        category=ar_dict["category"],
                        pre_value=ar_dict["pre_value"],
                        post_value=ar_dict["post_value"],
                        unit=ar_dict["unit"],
                        delta_pct=ar_dict["delta_pct"],
                        score=ar_dict["score"],
                        comparable=ar_dict["comparable"],
                        concern=ar_dict["concern"],
                        is_cqa=ar_dict["is_cqa"],
                        cqa_designation=ar_dict["cqa_designation"],
                        uncertainty=ar_dict["uncertainty"],
                        detail=ar_dict["detail"],
                        action=ar_dict.get("action"),
                    ))

                report_obj = CR(
                    product_name=report["product_name"],
                    change_description=report.get("change_description", ""),
                    overall_verdict=report["overall_verdict"],
                    evidence_strength_index=report["evidence_strength_index"],
                    n_attributes=report["n_attributes"],
                    n_cqa=report["n_cqa"],
                    n_comparable=report["n_comparable"],
                    n_flagged=report["n_flagged"],
                    attribute_results=attr_results,
                    cqa_summary=report.get("cqa_summary", []),
                    uncertainty_summary=report.get("uncertainty_summary", {}),
                    evidence_gaps=report.get("evidence_gaps", []),
                    recommended_actions=report.get("recommended_actions", []),
                    action_summary=report.get("action_summary"),
                    provenance_chain=report.get("provenance_chain", []),
                    timestamp=report.get("timestamp", ""),
                )

                out_path = f"/tmp/{case['product_name'].replace(' ', '_')}_comparability_report.docx"
                generate_comparability_report(report_obj, out_path)

                with open(out_path, "rb") as f:
                    docx_bytes = f.read()

                st.download_button(
                    label="Download DOCX",
                    data=docx_bytes,
                    file_name=Path(out_path).name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                st.success("Report generated.")

    except ImportError:
        st.info("""
        DOCX report generation requires the reports module.
        For now, use CLI: `python -m pipelines.run_comparability --export-docx`
        """)

# =========================================================================
# Navigation
# =========================================================================
st.markdown("---")
col1, col2 = st.columns(2)
with col1:
    if st.button("Back to Overview"):
        st.switch_page("pages/01_PackageOverview.py")
with col2:
    if st.button("Gap Closure"):
        st.switch_page("pages/03_GapClosure.py")
