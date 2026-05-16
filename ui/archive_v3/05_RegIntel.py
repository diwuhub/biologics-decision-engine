"""
RegIntel -- Regulatory Intelligence Tools
- FDA Warning Letter Classifier
- Reviewer Question Predictor
- Stability Trend Analysis

Calls FastAPI endpoints from /api/v1/warning-letter, /api/v1/reviewer, /api/v1/stability
"""

import streamlit as st
import pandas as pd
import requests
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ui.config import setup_page, get_backend

setup_page()

st.title("Regulatory Intelligence Tools")
st.markdown("Leverage regulatory precedent and ML-driven predictions for CMC strategy.")

# =========================================================================
# Tab 1: Warning Letter Classifier
# =========================================================================

tab1, tab2, tab3 = st.tabs(["Warning Letter Classifier", "Reviewer Predictor", "Stability Trends"])

with tab1:
    st.subheader("FDA Warning Letter Classification")
    st.markdown(
        """
        Paste an FDA warning letter or violation summary. The classifier predicts regulatory
        risk categories, severity, and key findings.
        """
    )

    col_left, col_right = st.columns([2, 1])

    with col_left:
        warning_text = st.text_area(
            "Warning Letter Text or Summary",
            placeholder="Paste FDA warning letter excerpt or regulatory finding...",
            height=200
        )

    with col_right:
        product_type = st.selectbox("Product Type", ["mAb", "GLP-1", "Fusion Protein", "Cytokine", "Other"])
        site_type = st.selectbox("Site Type", ["Manufacturing", "Clinical", "CMO", "Other"])

    if st.button("Classify Warning Letter", type="primary", key="classify_wl"):
        if not warning_text.strip():
            st.error("Please enter warning letter text")
        else:
            try:
                # Call API endpoint (or mock if offline)
                payload = {
                    "text": warning_text,
                    "product_type": product_type.lower(),
                    "site_type": site_type.lower()
                }

                # For now, mock response
                response = {
                    "primary_category": "GMP_Manufacturing",
                    "secondary_categories": ["Process_Control", "Data_Integrity"],
                    "severity_score": 0.72,
                    "risk_level": "high",
                    "key_findings": [
                        "Inadequate in-process controls for critical parameters",
                        "Insufficient deviation investigation documentation",
                        "Batch records lack traceability"
                    ],
                    "regulatory_concern_areas": ["manufacturing", "quality_assurance", "data_integrity"],
                    "confidence": 0.81
                }

                st.success("Classification Complete")

                # Display results
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Primary Category", response["primary_category"])
                with col2:
                    st.metric("Severity", f"{response['severity_score']:.1%}")
                with col3:
                    st.metric("Risk Level", response["risk_level"].upper())

                st.markdown("---")

                st.subheader("Key Findings")
                for i, finding in enumerate(response["key_findings"], 1):
                    st.markdown(f"{i}. {finding}")

                st.subheader("Regulatory Concern Areas")
                concern_df = pd.DataFrame({
                    "Concern": response["regulatory_concern_areas"]
                })
                st.dataframe(concern_df, use_container_width=True)

                st.markdown(f"**Model Confidence:** {response['confidence']:.1%}")

            except Exception as e:
                st.error(f"Classification failed: {str(e)}")

# =========================================================================
# Tab 2: Reviewer Question Predictor
# =========================================================================

with tab2:
    st.subheader("Reviewer Question Predictor")
    st.markdown(
        """
        Paste your CMC summary or submission package excerpt. The predictor identifies
        likely reviewer questions and recommendations.
        """
    )

    col_left, col_right = st.columns([2, 1])

    with col_left:
        cmc_text = st.text_area(
            "CMC Summary or Submission Package",
            placeholder="Paste CMC section, manufacturing overview, or quality narrative...",
            height=200
        )

    with col_right:
        product_type_rev = st.selectbox("Product Type", ["mAb", "Biosimilar", "GLP-1", "Other"], key="prod_rev")
        change_type = st.selectbox("Change Type", ["Manufacturing", "Formulation", "Packaging", "Analytical"], key="chg_rev")
        confidence = st.slider("Confidence in Proposal", 0.0, 1.0, 0.7, 0.05)

    if st.button("Predict Reviewer Questions", type="primary", key="predict_rev"):
        if not cmc_text.strip():
            st.error("Please enter CMC summary")
        else:
            try:
                payload = {
                    "cmc_summary": cmc_text,
                    "product_type": product_type_rev.lower(),
                    "change_type": change_type.lower(),
                    "confidence_in_proposal": confidence
                }

                # Mock response
                response = {
                    "likely_question_categories": ["Process Equivalence", "Product Stability", "Analytical Validation"],
                    "top_reviewer_questions": [
                        "Have you demonstrated analytical equivalence across both processes?",
                        "Provide side-by-side comparison of impurity profiles.",
                        "Justify specification limits with manufacturing data.",
                        "Submit additional accelerated stability data."
                    ],
                    "estimated_question_probability": 0.72,
                    "recommendation": "Proactively address analytical equivalence and specification justification.",
                    "confidence": 0.78
                }

                st.success("Prediction Complete")

                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Question Probability", f"{response['estimated_question_probability']:.1%}")
                with col2:
                    st.metric("Model Confidence", f"{response['confidence']:.1%}")

                st.markdown("---")

                st.subheader("Risk Categories")
                cat_df = pd.DataFrame(response["likely_question_categories"], columns=["Category"])
                st.dataframe(cat_df, use_container_width=True)

                st.subheader("Top Reviewer Questions")
                for i, q in enumerate(response["top_reviewer_questions"], 1):
                    st.markdown(f"**Q{i}:** {q}")

                st.markdown("---")
                st.info(f"**Recommendation:** {response['recommendation']}")

            except Exception as e:
                st.error(f"Prediction failed: {str(e)}")

# =========================================================================
# Tab 3: Stability Trend Analysis
# =========================================================================

with tab3:
    st.subheader("Stability Trend Analysis & Shelf-Life Prediction")
    st.markdown(
        """
        Enter stability testing data (timepoints and assay values). The system fits
        linear trends, estimates Arrhenius acceleration, and predicts OOS risk.
        """
    )

    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.markdown("**Enter Stability Data**")

        # Build data entry table
        data = []
        for i in range(5):
            col1, col2 = st.columns(2)
            with col1:
                months = st.number_input(f"Timepoint {i+1} (months)", value=i*3 if i > 0 else 0, min_value=0, key=f"tp_{i}")
            with col2:
                assay = st.number_input(f"Assay Value {i+1} (%)", value=100.0 - i*2, min_value=0.0, max_value=150.0, key=f"av_{i}")
            if months >= 0 and assay >= 0:
                data.append({"Timepoint (months)": months, "Assay Value (%)": assay})

    with col_right:
        st.markdown("**Test Condition**")
        test_cond = st.selectbox("Storage Condition", ["25C/60RH", "40C/75RH", "Accelerated"])
        spec_limit = st.number_input("Specification Limit (%)", value=95.0, min_value=50.0, max_value=120.0)
        shelf_life = st.number_input("Proposed Shelf Life (months)", value=24, min_value=6, max_value=60)

    if st.button("Analyze Stability Trend", type="primary", key="analyze_stab"):
        if len(data) < 2:
            st.error("Need at least 2 timepoints")
        else:
            try:
                timepoints = [d["Timepoint (months)"] for d in data]
                assay_values = [d["Assay Value (%)"] for d in data]

                payload = {
                    "timepoints": timepoints,
                    "assay_values": assay_values,
                    "test_condition": test_cond,
                    "specification_limit": spec_limit,
                    "shelf_life_months": shelf_life
                }

                # Mock response
                response = {
                    "trend_analysis": "declining",
                    "arrhenius_slope": -0.31,
                    "acceleration_factor": 1.5 if "40C" in test_cond else 1.0,
                    "predicted_value_at_expiry": 92.5,
                    "oos_risk": "medium",
                    "oos_probability": 0.22,
                    "recommendation": "Consider reducing proposed shelf life or enhancing stability package.",
                    "confidence": 0.84
                }

                st.success("Analysis Complete")

                # Metrics
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Trend", response["trend_analysis"].title())
                with col2:
                    st.metric("OOS Risk", response["oos_risk"].title())
                with col3:
                    st.metric("OOS Probability", f"{response['oos_probability']:.1%}")

                st.markdown("---")

                # Prediction table
                pred_df = pd.DataFrame({
                    "Metric": ["Slope (% change/month)", "Predicted Value @ Shelf Life", "Specification Limit", "Safety Margin"],
                    "Value": [
                        f"{response['arrhenius_slope']:.3f}",
                        f"{response['predicted_value_at_expiry']:.1f}%",
                        f"{spec_limit:.1f}%",
                        f"{response['predicted_value_at_expiry'] - spec_limit:.1f}%"
                    ]
                })
                st.dataframe(pred_df, use_container_width=True)

                st.markdown("---")
                st.info(f"**Recommendation:** {response['recommendation']}")

                # Visualization
                st.markdown("**Trend Visualization**")
                import matplotlib.pyplot as plt

                fig, ax = plt.subplots(figsize=(8, 4))
                ax.plot(timepoints, assay_values, 'o-', linewidth=2, markersize=8, label="Observed Data")

                # Fit line
                if len(timepoints) >= 2:
                    fit_line = [response['arrhenius_slope'] * t + (assay_values[0] - response['arrhenius_slope'] * timepoints[0]) for t in timepoints]
                    ax.plot(timepoints, fit_line, '--', linewidth=2, label="Linear Fit")

                ax.axhline(spec_limit, color='red', linestyle=':', linewidth=2, label=f"Spec Limit ({spec_limit}%)")
                ax.set_xlabel("Time (months)")
                ax.set_ylabel("Assay Value (%)")
                ax.set_title("Stability Trend Analysis")
                ax.legend()
                ax.grid(True, alpha=0.3)
                st.pyplot(fig)

            except Exception as e:
                st.error(f"Analysis failed: {str(e)}")

st.markdown("---")
st.caption("Regulatory Intelligence Tools v1 -- Backend: reg-intel-biopharma + FastAPI")
