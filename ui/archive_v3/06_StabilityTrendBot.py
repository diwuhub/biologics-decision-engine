"""
StabilityTrendBot -- Advanced Stability Prediction & Arrhenius Modeling
- Real-time stability trend analysis
- Arrhenius model fitting (real-time vs accelerated)
- Shelf-life prediction and OOS risk assessment
- Interactive Arrhenius plot and trend visualization
"""

import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import sys
import json

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ui.config import setup_page

setup_page()

st.title("Stability TrendBot")
st.markdown("Advanced stability modeling, Arrhenius fitting, and shelf-life prediction.")

# =========================================================================
# Setup
# =========================================================================

# Initialize session state
if 'rt_data' not in st.session_state:
    st.session_state.rt_data = {"months": [], "potency": []}
if 'acc_data' not in st.session_state:
    st.session_state.acc_data = {"months": [], "potency": []}

# =========================================================================
# Side Panel: Data Input
# =========================================================================

with st.sidebar:
    st.header("Stability Data Entry")

    st.subheader("Real-Time (25C/60% RH)")
    rt_months = st.number_input("Months (RT)", value=0, min_value=0, key="rt_m")
    rt_potency = st.number_input("Potency % (RT)", value=100.0, key="rt_p")
    if st.button("Add RT Point", key="add_rt"):
        st.session_state.rt_data["months"].append(rt_months)
        st.session_state.rt_data["potency"].append(rt_potency)
        st.rerun()

    st.subheader("Accelerated (40C/75% RH)")
    acc_months = st.number_input("Months (ACC)", value=0, min_value=0, key="acc_m")
    acc_potency = st.number_input("Potency % (ACC)", value=100.0, key="acc_p")
    if st.button("Add ACC Point", key="add_acc"):
        st.session_state.acc_data["months"].append(acc_months)
        st.session_state.acc_data["potency"].append(acc_potency)
        st.rerun()

    st.markdown("---")
    if st.button("Clear All Data", key="clear_all"):
        st.session_state.rt_data = {"months": [], "potency": []}
        st.session_state.acc_data = {"months": [], "potency": []}
        st.rerun()

# =========================================================================
# Main Content: Tabs
# =========================================================================

tab1, tab2, tab3, tab4 = st.tabs([
    "Data Overview",
    "Trend Analysis",
    "Arrhenius Model",
    "Shelf-Life Prediction"
])

# ===== TAB 1: Data Overview =====
with tab1:
    st.subheader("Entered Stability Data")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Real-Time Data (25C/60% RH)**")
        if st.session_state.rt_data["months"]:
            rt_df = pd.DataFrame({
                "Timepoint (months)": st.session_state.rt_data["months"],
                "Potency (%)": st.session_state.rt_data["potency"]
            })
            st.dataframe(rt_df, use_container_width=True)
        else:
            st.info("No real-time data entered yet.")

    with col2:
        st.markdown("**Accelerated Data (40C/75% RH)**")
        if st.session_state.acc_data["months"]:
            acc_df = pd.DataFrame({
                "Timepoint (months)": st.session_state.acc_data["months"],
                "Potency (%)": st.session_state.acc_data["potency"]
            })
            st.dataframe(acc_df, use_container_width=True)
        else:
            st.info("No accelerated data entered yet.")

    st.markdown("---")
    st.info("Use the sidebar to add data points. Each condition (RT/ACC) tracked separately.")

# ===== TAB 2: Trend Analysis =====
with tab2:
    st.subheader("Linear Trend Analysis")

    col1, col2 = st.columns(2)

    if st.session_state.rt_data["months"] and len(st.session_state.rt_data["months"]) >= 2:
        rt_months_arr = np.array(st.session_state.rt_data["months"], dtype=float)
        rt_potency_arr = np.array(st.session_state.rt_data["potency"], dtype=float)

        try:
            rt_coeffs = np.polyfit(rt_months_arr, rt_potency_arr, 1)
            rt_slope, rt_intercept = rt_coeffs[0], rt_coeffs[1]
            _corr = np.corrcoef(rt_months_arr, rt_potency_arr)
            rt_r2 = _corr[0, 1] ** 2 if not np.isnan(_corr[0, 1]) else 0.0

            with col1:
                st.markdown("**Real-Time Trend**")
                st.metric("Slope (% change/month)", f"{rt_slope:.4f}")
                st.metric("Intercept (% potency @ t=0)", f"{rt_intercept:.2f}")
                st.metric("R2 (fit quality)", f"{rt_r2:.3f}")
                st.metric("Trend Direction", "Declining" if rt_slope < 0 else "Stable/Improving")
        except (np.linalg.LinAlgError, ValueError):
            with col1:
                st.warning("Cannot fit RT trend — data points may be identical or insufficient variance.")
    else:
        with col1:
            st.warning("Need at least 2 RT timepoints for analysis.")

    if st.session_state.acc_data["months"] and len(st.session_state.acc_data["months"]) >= 2:
        acc_months_arr = np.array(st.session_state.acc_data["months"], dtype=float)
        acc_potency_arr = np.array(st.session_state.acc_data["potency"], dtype=float)

        try:
            acc_coeffs = np.polyfit(acc_months_arr, acc_potency_arr, 1)
            acc_slope, acc_intercept = acc_coeffs[0], acc_coeffs[1]
            _corr = np.corrcoef(acc_months_arr, acc_potency_arr)
            acc_r2 = _corr[0, 1] ** 2 if not np.isnan(_corr[0, 1]) else 0.0

            with col2:
                st.markdown("**Accelerated Trend**")
                st.metric("Slope (% change/month)", f"{acc_slope:.4f}")
                st.metric("Intercept (% potency @ t=0)", f"{acc_intercept:.2f}")
                st.metric("R2 (fit quality)", f"{acc_r2:.3f}")
                st.metric("Trend Direction", "Declining" if acc_slope < 0 else "Stable/Improving")
        except (np.linalg.LinAlgError, ValueError):
            with col2:
                st.warning("Cannot fit accelerated trend — data points may be identical or insufficient variance.")
    else:
        with col2:
            st.warning("Need at least 2 ACC timepoints for analysis.")

    # Visualization
    st.markdown("---")
    st.subheader("Trend Visualization")

    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))

    # RT data and fit
    if st.session_state.rt_data["months"]:
        rt_months_arr = np.array(st.session_state.rt_data["months"])
        rt_potency_arr = np.array(st.session_state.rt_data["potency"])
        ax.plot(rt_months_arr, rt_potency_arr, 'o-', linewidth=2.5, markersize=8,
                color='steelblue', label='Real-Time (25C/60RH)')

        if len(rt_months_arr) >= 2:
            try:
                rt_fit = np.polyval(np.polyfit(rt_months_arr, rt_potency_arr, 1), rt_months_arr)
                ax.plot(rt_months_arr, rt_fit, '--', linewidth=2, color='steelblue', alpha=0.7)
            except (np.linalg.LinAlgError, ValueError):
                pass

    # ACC data and fit
    if st.session_state.acc_data["months"]:
        acc_months_arr = np.array(st.session_state.acc_data["months"], dtype=float)
        acc_potency_arr = np.array(st.session_state.acc_data["potency"], dtype=float)
        ax.plot(acc_months_arr, acc_potency_arr, 's-', linewidth=2.5, markersize=8,
                color='darkorange', label='Accelerated (40C/75RH)')

        if len(acc_months_arr) >= 2:
            try:
                acc_fit = np.polyval(np.polyfit(acc_months_arr, acc_potency_arr, 1), acc_months_arr)
                ax.plot(acc_months_arr, acc_fit, '--', linewidth=2, color='darkorange', alpha=0.7)
            except (np.linalg.LinAlgError, ValueError):
                pass

    ax.set_xlabel("Time (months)", fontsize=11)
    ax.set_ylabel("Potency (%)", fontsize=11)
    ax.set_title("Stability Trends: Real-Time vs Accelerated", fontsize=13, fontweight='bold')
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    st.pyplot(fig)

# ===== TAB 3: Arrhenius Model =====
with tab3:
    st.subheader("Arrhenius Model Fitting")
    st.markdown(
        """
        The Arrhenius equation relates degradation rate to temperature:

        **k = A x e^(-Ea/RT)**

        Where:
        - k = degradation rate constant
        - Ea = activation energy (kJ/mol)
        - R = gas constant (8.314 J/mol K)
        - T = absolute temperature (K)
        """
    )

    if (st.session_state.rt_data["months"] and st.session_state.acc_data["months"] and
        len(st.session_state.rt_data["months"]) >= 2 and len(st.session_state.acc_data["months"]) >= 2):

        # Extract slopes from both conditions
        rt_months_arr = np.array(st.session_state.rt_data["months"], dtype=float)
        rt_potency_arr = np.array(st.session_state.rt_data["potency"], dtype=float)
        acc_months_arr = np.array(st.session_state.acc_data["months"], dtype=float)
        acc_potency_arr = np.array(st.session_state.acc_data["potency"], dtype=float)

        try:
            rt_slope = np.polyfit(rt_months_arr, rt_potency_arr, 1)[0]
            acc_slope = np.polyfit(acc_months_arr, acc_potency_arr, 1)[0]
        except (np.linalg.LinAlgError, ValueError):
            st.warning("Cannot compute Arrhenius model — insufficient variance in data points.")
            st.stop()

        # Simplified Arrhenius model: Q10 (acceleration factor for 10C rise)
        temp_delta = 15  # 40C - 25C
        q10 = 2.0  # Typical assumption for biological systems
        acceleration_factor = q10 ** (temp_delta / 10)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("RT Degradation Rate", f"{abs(rt_slope):.4f} %/month")
        with col2:
            st.metric("ACC Degradation Rate", f"{abs(acc_slope):.4f} %/month")
        with col3:
            st.metric("Acceleration Factor (Q10)", f"{acceleration_factor:.2f}x")

        st.markdown("---")

        # Prediction at 40C
        st.subheader("Arrhenius Prediction Chart")
        fig, ax = plt.subplots(figsize=(10, 5))

        # Plot both datasets
        ax.plot(rt_months_arr, rt_potency_arr, 'o-', linewidth=2.5, markersize=8, color='steelblue', label='RT (25C/60RH)')
        ax.plot(acc_months_arr, acc_potency_arr, 's-', linewidth=2.5, markersize=8, color='darkorange', label='ACC (40C/75RH)')

        # Extend predictions
        max_months = max(np.max(rt_months_arr), np.max(acc_months_arr)) + 6
        rt_extended = np.linspace(0, max_months, 100)
        try:
            rt_pred = np.polyval(np.polyfit(rt_months_arr, rt_potency_arr, 1), rt_extended)
        except (np.linalg.LinAlgError, ValueError):
            rt_pred = np.full_like(rt_extended, np.mean(rt_potency_arr))

        ax.plot(rt_extended, rt_pred, '--', linewidth=2, color='steelblue', alpha=0.6, label='RT projection')

        ax.axhline(95, color='red', linestyle=':', linewidth=2, label='Spec Limit (95%)')
        ax.set_xlabel("Time (months)", fontsize=11)
        ax.set_ylabel("Potency (%)", fontsize=11)
        ax.set_title("Arrhenius Model: Degradation Projection", fontsize=13, fontweight='bold')
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_ylim([85, 105])
        st.pyplot(fig)

    else:
        st.warning("Arrhenius model requires 2 or more data points from both RT and ACC conditions.")

# ===== TAB 4: Shelf-Life Prediction =====
with tab4:
    st.subheader("Shelf-Life Prediction & OOS Risk")

    st.markdown("Enter shelf-life candidate and specification lower limit:")

    col1, col2 = st.columns(2)
    with col1:
        candidate_shelf_life = st.number_input("Proposed Shelf Life (months)", value=24, min_value=6, max_value=60)
    with col2:
        spec_lower = st.number_input("Specification Lower Limit (%)", value=95.0, min_value=50.0, max_value=120.0)

    if st.session_state.rt_data["months"] and len(st.session_state.rt_data["months"]) >= 2:
        rt_months_arr = np.array(st.session_state.rt_data["months"], dtype=float)
        rt_potency_arr = np.array(st.session_state.rt_data["potency"], dtype=float)
        try:
            rt_coeffs = np.polyfit(rt_months_arr, rt_potency_arr, 1)
            rt_slope, rt_intercept = rt_coeffs[0], rt_coeffs[1]
        except (np.linalg.LinAlgError, ValueError):
            st.warning("Cannot fit trend — data points may be identical or have insufficient variance.")
            st.stop()

        # Predict at shelf life
        predicted_potency = rt_intercept + rt_slope * candidate_shelf_life
        safety_margin = predicted_potency - spec_lower

        # OOS probability (simple model)
        if safety_margin > 5:
            oos_risk = "Low"
            oos_prob = 0.05
        elif safety_margin > 2:
            oos_risk = "Medium"
            oos_prob = 0.20
        else:
            oos_risk = "High"
            oos_prob = 0.50

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Predicted Potency @ Expiry", f"{max(spec_lower, predicted_potency):.1f}%")
        with col2:
            st.metric("Safety Margin", f"{safety_margin:.1f}%")
        with col3:
            st.metric("OOS Risk", oos_risk)
        with col4:
            st.metric("OOS Probability", f"{oos_prob:.1%}")

        st.markdown("---")

        # Recommendation
        if oos_risk == "Low":
            st.success(f"Recommended: {candidate_shelf_life} months shelf life is justified.")
        elif oos_risk == "Medium":
            st.warning(f"Consider: Additional real-time stability data recommended before committing to {candidate_shelf_life} months.")
        else:
            st.error(f"Not Recommended: {candidate_shelf_life} months exceeds current data. Reduce to <{int(candidate_shelf_life * 0.7)} months or collect additional data.")

        st.markdown("---")

        # Shelf-life confidence chart
        st.subheader("Shelf-Life Viability Map")

        fig, ax = plt.subplots(figsize=(10, 5))

        # Real data
        ax.plot(rt_months_arr, rt_potency_arr, 'o-', linewidth=2.5, markersize=8, color='steelblue', label='Observed RT Data')

        # Extended projection
        extended_months = np.linspace(0, 60, 200)
        extended_potency = rt_intercept + rt_slope * extended_months
        ax.plot(extended_months, extended_potency, '--', linewidth=2, color='steelblue', alpha=0.6, label='RT Projection')

        # Spec limit
        ax.axhline(spec_lower, color='red', linestyle=':', linewidth=2.5, label=f'Spec Limit ({spec_lower}%)')

        # Proposed shelf life
        ax.axvline(candidate_shelf_life, color='green', linestyle=':', linewidth=2.5, label=f'Proposed Expiry ({candidate_shelf_life}m)')

        # Color bands
        ax.fill_between(extended_months, spec_lower, 105, where=(extended_potency >= spec_lower),
                        color='green', alpha=0.1, label='Compliant Region')
        ax.fill_between(extended_months, 85, spec_lower,
                        color='red', alpha=0.1, label='Non-Compliant Region')

        ax.set_xlabel("Time (months)", fontsize=11)
        ax.set_ylabel("Potency (%)", fontsize=11)
        ax.set_title("Shelf-Life Viability Map", fontsize=13, fontweight='bold')
        ax.legend(fontsize=10, loc='upper right')
        ax.grid(True, alpha=0.3)
        ax.set_xlim([0, 60])
        ax.set_ylim([85, 105])
        st.pyplot(fig)

    else:
        st.warning("Need at least 2 real-time data points to predict shelf life.")

st.markdown("---")
st.caption("StabilityTrendBot v1 -- Backend: reg-intel-biopharma + FastAPI + Arrhenius modeling")
