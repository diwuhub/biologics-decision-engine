"""Package Panel component — renders multi-document package results."""

import os
import streamlit as st
import pandas as pd


def render_package_results(_pkg_ov, _audit):
    """Render the package assessment results. Calls st.stop() at the end."""

    # Package verdict banner
    _pv_color = _pkg_ov["package_verdict_color"]
    _pv_label = _pkg_ov["package_verdict_display"]
    _pv_conf = _pkg_ov["package_confidence"]
    _tint = {"#10b981": "#defbe6", "#f59e0b": "#fdf6dd", "#f43f5e": "#fff1f1", "#64748b": "#f4f4f4"}.get(_pv_color, "#f4f4f4")

    st.markdown(f"""
    <div style="background:{_tint};padding:16px;border-left:4px solid {_pv_color};margin-bottom:16px;">
        <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#525252;font-weight:600;">PACKAGE VERDICT</div>
        <div style="font-size:20px;font-weight:600;color:#161616;margin-top:4px;">{_pv_label}</div>
        <div style="font-size:14px;color:#525252;margin-top:4px;">Confidence: {_pv_conf:.0%} — {_pkg_ov['package_rationale']}</div>
    </div>
    """, unsafe_allow_html=True)

    # Document inventory table
    st.markdown("##### Document Inventory")
    _inv_rows = []
    for ds in _pkg_ov["document_summaries"]:
        _inv_rows.append({
            "File": ds["filename"],
            "Type": ds["doc_type"],
            "Verdict": ds["analytical_conclusion"],
            "Posture": ds["package_posture"],
            "Confidence": f"{ds['confidence']:.0%}",
            "Status": "Error" if ds["error"] else "OK",
        })
    st.dataframe(pd.DataFrame(_inv_rows), use_container_width=True, hide_index=True)

    # Coverage matrix
    _cov = _pkg_ov["document_coverage"]
    _cov_cols = st.columns(len(_cov))
    for i, (dtype, present) in enumerate(_cov.items()):
        with _cov_cols[i]:
            _icon = "Present" if present else "Missing"
            _bg = "#defbe6" if present else "#fff1f1"
            _fg = "#0e6027" if present else "#750e13"
            st.markdown(
                f'<div style="background:{_bg};color:{_fg};padding:8px;text-align:center;font-size:13px;">'
                f'<strong>{_icon}</strong><br>{dtype.replace("_"," ").title()}</div>',
                unsafe_allow_html=True,
            )

    # Cross-document flags
    _xflags = _pkg_ov["cross_document_flags"]
    if _xflags:
        st.markdown("##### Cross-Document Findings")
        for xf in _xflags:
            _sev_color = {"critical": "#da1e28", "warning": "#f1c21b", "info": "#0f62fe"}.get(xf["severity"], "#6f6f6f")
            st.markdown(
                f'<div style="border-left:4px solid {_sev_color};padding:12px 16px;margin-bottom:8px;font-size:14px;">'
                f'<strong>[{xf["severity"].upper()}]</strong> {xf["description"]}</div>',
                unsafe_allow_html=True,
            )

    # Reviewer questions
    _rqs = _pkg_ov["reviewer_questions"]
    if _rqs:
        with st.expander(f"Predicted Reviewer Questions ({len(_rqs)})", expanded=True):
            for _rq in _rqs:
                _src = _rq.get("source_doc_type", "")
                _badge_color = {"CHARACTERIZATION": "#0f62fe", "STABILITY": "#24a148",
                                "ANALYTICAL_METHOD": "#f1c21b", "PKG": "#525252"}.get(_src, "#6f6f6f")
                st.markdown(
                    f'<div style="border-left:4px solid {_badge_color};padding:12px 16px;margin-bottom:8px;font-size:14px;">'
                    f'<span style="background:{_badge_color};color:white;padding:2px 6px;border-radius:2px;font-size:11px;margin-right:6px;">{_src}</span>'
                    f'{_rq["question"]}</div>',
                    unsafe_allow_html=True,
                )

    # Export
    st.markdown("---")
    if st.button("Export Package Report (DOCX)", type="primary", use_container_width=True, key="export_pkg_docx"):
        try:
            from reports.package_report import generate_package_report
            _pkg_path = f"/tmp/CMC_Package_Report_{_pkg_ov['package_id']}.docx"
            generate_package_report(_pkg_ov, _pkg_path)
            with open(_pkg_path, "rb") as _pf:
                st.download_button(
                    "Download Report", data=_pf.read(),
                    file_name=os.path.basename(_pkg_path),
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="pkg_docx_download",
                )
            st.success("Package report generated.")
            _audit.log("EXPORT", "Package report exported", metadata={"format": "DOCX"})
        except Exception as _e:
            st.error(f"Report generation failed: {_e}")

    # Footer
    st.markdown("""
    <div style="margin-top:16px;color:#6f6f6f;font-size:12px;font-style:italic;">
        For decision support only. Not regulatory advice.
        Verify all findings with source documents and a qualified regulatory professional.
    </div>
    """, unsafe_allow_html=True)
    st.stop()
