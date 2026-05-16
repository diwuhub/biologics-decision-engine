"""
Case List -- Browse existing cases, create new cases, select for assessment.
"""

import streamlit as st
import pandas as pd
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ui.config import setup_page, get_backend, VERDICT_COLORS

setup_page()

st.title("Case List")
st.markdown("Browse existing cases or create a new assessment.")

backend = get_backend()

# =========================================================================
# Close Case button (if a case is active)
# =========================================================================
if st.session_state.get("case_id"):
    case_info = backend.get_case(st.session_state.case_id)
    if case_info:
        col_info, col_close = st.columns([3, 1])
        with col_info:
            st.info(f"Active case: **{case_info['product_name']}** ({st.session_state.case_id}) -- Status: {case_info['status']}")
        with col_close:
            if st.button("Close Case", type="secondary", use_container_width=True):
                st.session_state.case_id = None
                st.session_state.overview_data = None
                st.rerun()
    st.markdown("---")

# =========================================================================
# Benchmark demo cases (COMP-001 through COMP-009 + NISTMAB-E2E)
# =========================================================================
DEMO_CASES = {
    "COMP-001": "mAb Cell Culture Media Change (Anti-IL6R IgG1)",
    "COMP-002": "mAb Scale-Up 2L to 2000L (Anti-PD1 IgG4)",
    "COMP-003": "Biosimilar Analytical Comparison (Adalimumab-like)",
    "COMP-004": "Process Change with Potency Impact (Anti-HER2 IgG1)",
    "COMP-005": "Manufacturing Site Transfer (Anti-VEGF IgG1)",
    "COMP-006": "Better-Than-Reference Purity / Hyper-Purity Risk (Anti-PD1 Biosimilar)",
    "COMP-007": "Trending Stability -- Within Spec But Projected OOS (Anti-TNF IgG1)",
    "COMP-008": "Orthogonal Contradiction -- Two Potency Methods Disagree (Anti-EGFR IgG1)",
    "COMP-009": "Cell Line Change with Glycosylation Shift (Anti-CD20 IgG1)",
    "NISTMAB-E2E": "NISTmAb E2E Vertical Slice -- Column Resin Change Comparability",
}

CASES_DIR = PROJECT_ROOT / "benchmarks" / "cases"


def _load_demo_case(case_id: str) -> dict:
    """Load a benchmark case JSON file."""
    fpath = CASES_DIR / f"{case_id}.json"
    if not fpath.exists():
        return None
    with open(fpath) as f:
        return json.load(f)


def _csv_to_batch_data(df: pd.DataFrame) -> dict:
    """Convert an uploaded CSV DataFrame to pipeline-compatible batch data.

    Auto-detects columns by matching common names to the expected schema.
    """
    # Column name mapping (user-friendly -> pipeline key)
    col_map = {
        "attribute name": "name",
        "attribute": "name",
        "name": "name",
        "category": "category",
        "pre-change value": "pre_value",
        "pre_value": "pre_value",
        "pre value": "pre_value",
        "pre change value": "pre_value",
        "post-change value": "post_value",
        "post_value": "post_value",
        "post value": "post_value",
        "post change value": "post_value",
        "unit": "unit",
        "number of lots": "n_lots",
        "n_lots": "n_lots",
        "n lots": "n_lots",
        "lots": "n_lots",
        "cv (%)": "cv_pct",
        "cv_pct": "cv_pct",
        "cv%": "cv_pct",
        "cv pct": "cv_pct",
        "cv": "cv_pct",
        "number of methods": "n_methods",
        "n_methods": "n_methods",
        "n methods": "n_methods",
        "methods": "n_methods",
    }

    # Normalize column names
    rename = {}
    for col in df.columns:
        normalized = col.strip().lower()
        if normalized in col_map:
            rename[col] = col_map[normalized]
    df = df.rename(columns=rename)

    # Convert numeric columns
    for num_col in ["pre_value", "post_value", "n_lots", "cv_pct", "n_methods"]:
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors="coerce")

    # Build attributes list
    records = df.to_dict(orient="records")
    return {"attributes": records}


# =========================================================================
# Create New Case
# =========================================================================
st.subheader("Create New Case")

# --- CSV Template Download (OUTSIDE form — st.download_button can't be in a form) ---
template_path = Path(__file__).parent.parent / "templates" / "comparability_template.csv"
if template_path.exists():
    with open(template_path) as f:
        template_csv = f.read()
    st.download_button(
        label="Download CSV Template",
        data=template_csv,
        file_name="comparability_template.csv",
        mime="text/csv",
    )

# --- CSV Upload (OUTSIDE form — st.file_uploader works better outside forms) ---
st.markdown("**Step 1:** Upload your comparability data (CSV) or select a demo case below.")
upload_tab, demo_tab = st.tabs(["Upload CSV", "Use Demo Case"])

with upload_tab:
    st.caption(
        "Expected columns: Attribute Name, Category, Pre-Change Value, "
        "Post-Change Value, Unit, Number of Lots, CV (%), Number of Methods"
    )
    uploaded = st.file_uploader("Upload batch data (CSV)", type=["csv"])
    if uploaded is not None:
        _uploaded_df = pd.read_csv(uploaded)
        st.session_state["_uploaded_batch"] = _csv_to_batch_data(_uploaded_df)
        st.success(f"Loaded {len(st.session_state['_uploaded_batch']['attributes'])} attributes from CSV.")

with demo_tab:
    st.markdown("Select one of the 10 benchmark cases to explore the engine.")
    demo_choice = st.selectbox(
        "Demo Case",
        list(DEMO_CASES.keys()),
        format_func=lambda cid: f"{cid}: {DEMO_CASES[cid]}",
    )
    if demo_choice:
        demo_data = _load_demo_case(demo_choice)
        if demo_data:
            n_attrs = len(demo_data.get("attributes", []))
            st.caption(
                f"Product: {demo_data.get('product_name', 'N/A')} | "
                f"Attributes: {n_attrs} | "
                f"Expected verdict: {demo_data.get('expected_verdict', 'N/A')}"
            )
            st.caption(f"Change: {demo_data.get('change_description', '')[:120]}")
        else:
            st.warning(f"Demo case file not found: {demo_choice}.json")

# --- Case Metadata Form ---
st.markdown("**Step 2:** Fill in case details and create.")
with st.form("new_case_form"):
    col1, col2 = st.columns(2)
    with col1:
        product_name = st.text_input("Product Name", value="mAb-X")
        molecule_class = st.selectbox(
            "Molecule Class",
            ["mAb", "bispecific", "ADC", "fusion protein", "enzyme", "peptide", "other"],
        )
    with col2:
        change_type = st.selectbox(
            "Change Type",
            [
                "Cell culture media change",
                "Scale-up",
                "Site transfer",
                "Process optimization",
                "Equipment change",
                "Other",
            ],
        )
        change_description = st.text_area(
            "Change Description", value="", height=68
        )

    submitted = st.form_submit_button("Create Case", type="primary")

    if submitted:
        batch_data = None

        # Check for uploaded CSV first
        if "_uploaded_batch" in st.session_state and st.session_state["_uploaded_batch"]:
            batch_data = st.session_state.pop("_uploaded_batch")
        else:
            # Load selected demo case
            demo_data = _load_demo_case(demo_choice)
            if demo_data:
                batch_data = demo_data
                if product_name == "mAb-X" and demo_data.get("product_name"):
                    product_name = demo_data["product_name"]
                if not change_description and demo_data.get("change_description"):
                    change_description = demo_data["change_description"]

        if batch_data is None:
            st.error("Please upload a CSV file or select a demo case.")
        else:
            case_id = backend.create_case(
                product_name=product_name,
                molecule_class=molecule_class,
                change_type=change_type,
                change_description=change_description,
                batch_data=batch_data,
            )
            st.session_state.case_id = case_id
            st.success(f"Case created: {case_id}")
            st.rerun()

# =========================================================================
# List Existing Cases
# =========================================================================
st.markdown("---")
cases = backend.list_cases()

if cases:
    st.subheader(f"Existing Cases ({len(cases)})")

    # Build display table
    rows = []
    for c in cases:
        rows.append({
            "Case ID": c["case_id"],
            "Product": c["product_name"],
            "Molecule": c["molecule_class"],
            "Change": c["change_type"],
            "Status": c["status"],
            "Action": c["overall_action"],
            "Critical Gaps": c["critical_gaps_count"],
            "Updated": c["last_updated"][:16],
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Selection
    case_options = {c["case_id"]: f"{c['product_name']} ({c['case_id']}) [{c['status']}]" for c in cases}
    selected = st.selectbox(
        "Select a case to open:",
        list(case_options.keys()),
        format_func=lambda cid: case_options[cid],
    )

    if selected:
        case = backend.get_case(selected)
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Status", case["status"])
        with col2:
            st.metric("Action", case["overall_action"])
        with col3:
            st.metric("Critical Gaps", case["critical_gaps_count"])
        with col4:
            n_attrs = len(case.get("batch_data", {}).get("attributes", []))
            st.metric("Attributes", n_attrs)

        bcol1, bcol2 = st.columns(2)
        with bcol1:
            if st.button("Select This Case", type="primary"):
                st.session_state.case_id = selected
                st.rerun()
        with bcol2:
            if case["status"] == "created":
                if st.button("Run Assessment"):
                    with st.spinner("Running comparability assessment..."):
                        report = backend.run_assessment(selected)
                    if report:
                        st.session_state.case_id = selected
                        st.success(f"Assessment complete: {report['overall_verdict']}")
                        st.rerun()
                    else:
                        st.error("Assessment failed.")
            elif case["status"] == "assessed":
                if st.button("Open Package Overview"):
                    st.session_state.case_id = selected
                    st.switch_page("pages/01_PackageOverview.py")
else:
    st.info("No cases yet. Create one above.")
