"""
Biologics Decision Engine -- Single-Page Decision View

P2 rewrite: Upload + Split-View + Export on one page.
Replaces 7-page Streamlit with action-first, gap-first decision workspace.

v4.1: Direct pipeline mode (no FastAPI server required).
v4.3.1-P9: DOCX ingestion integration, extraction review, document preview.
"""

import streamlit as st
import pandas as pd
import json
import csv
import io
import re
import sys
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.config import (
    setup_page, get_backend, DirectBackend,
    VERDICT_COLORS, ACTION_COLORS, SEVERITY_ORDER,
    ANALYTICAL_COLORS, POSTURE_COLORS,
    render_verdict_banner, render_action_box, render_gap_warnings,
)

# =========================================================================
# Page Config
# =========================================================================
st.set_page_config(
    page_title="CMC Decision Workspace",
    page_icon="",
    layout="wide",
    initial_sidebar_state="collapsed",
)

setup_page()

# =========================================================================
# Session State Initialization
# =========================================================================
if "case_id" not in st.session_state:
    st.session_state.case_id = None
if "overview_data" not in st.session_state:
    st.session_state.overview_data = None
if "report_dict" not in st.session_state:
    st.session_state.report_dict = None
if "uploaded_df" not in st.session_state:
    st.session_state.uploaded_df = None
if "auto_ran" not in st.session_state:
    st.session_state.auto_ran = False
if "molecule_type_override" not in st.session_state:
    st.session_state.molecule_type_override = None
if "selected_cluster" not in st.session_state:
    st.session_state.selected_cluster = None
# P9: DOCX ingestion state
if "docx_ingestion_result" not in st.session_state:
    st.session_state.docx_ingestion_result = None
if "docx_review_confirmed" not in st.session_state:
    st.session_state.docx_review_confirmed = False
if "docx_user_overrides" not in st.session_state:
    st.session_state.docx_user_overrides = []
if "docx_temp_path" not in st.session_state:
    st.session_state.docx_temp_path = None
if "selected_anchor" not in st.session_state:
    st.session_state.selected_anchor = None

backend = get_backend()

# Audit trail — foundation for 21 CFR Part 11
from services.audit_trail import AuditTrail
if "audit_trail" not in st.session_state:
    _audit_db = os.path.join(str(PROJECT_ROOT), "data", "audit_trail.db")
    st.session_state.audit_trail = AuditTrail(db_path=_audit_db)
_audit = st.session_state.audit_trail


@st.cache_data(show_spinner=False)
def _cached_ingest(_file_bytes: bytes, _suffix: str):
    """Cached document ingestion — avoids re-parsing on Streamlit rerun."""
    _tf = tempfile.NamedTemporaryFile(suffix=_suffix, delete=False)
    _tf.write(_file_bytes)
    _tf.close()
    from ingestion import ingest_document
    return ingest_document(_tf.name), _tf.name


# =========================================================================
# Demo Cases
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
    """Convert uploaded CSV DataFrame to pipeline-compatible batch data."""
    col_map = {
        "attribute name": "name", "attribute": "name", "name": "name",
        "category": "category",
        "pre-change value": "pre_value", "pre_value": "pre_value",
        "pre value": "pre_value", "pre change value": "pre_value",
        "post-change value": "post_value", "post_value": "post_value",
        "post value": "post_value", "post change value": "post_value",
        "unit": "unit",
        "number of lots": "n_lots", "n_lots": "n_lots", "n lots": "n_lots", "lots": "n_lots",
        "cv (%)": "cv_pct", "cv_pct": "cv_pct", "cv%": "cv_pct", "cv pct": "cv_pct", "cv": "cv_pct",
        "number of methods": "n_methods", "n_methods": "n_methods",
        "n methods": "n_methods", "methods": "n_methods",
    }
    rename = {}
    for col in df.columns:
        normalized = col.strip().lower()
        if normalized in col_map:
            rename[col] = col_map[normalized]
    df = df.rename(columns=rename)
    for num_col in ["pre_value", "post_value", "n_lots", "cv_pct", "n_methods"]:
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors="coerce")
    return {"attributes": df.to_dict(orient="records")}


def _run_assessment_for_case(case_id: str):
    """Run pipeline assessment and store results in session state."""
    report = backend.run_assessment(case_id)
    if report:
        st.session_state.report_dict = report
        st.session_state.overview_data = backend.get_overview(case_id)
        st.session_state.auto_ran = True
    return report


def _verdict_color(verdict: str) -> str:
    """Map verdict to CSS color (supports both legacy and two-axis values)."""
    return VERDICT_COLORS.get(verdict, ANALYTICAL_COLORS.get(verdict, "#6c757d"))


def _concern_color(concern: str) -> str:
    """Map concern level to CSS color."""
    mapping = {
        "critical": "#dc3545", "major": "#ff9800",
        "minor": "#ffc107", "none": "#28a745",
    }
    return mapping.get(concern, "#6c757d")


def _check_spec_status(value: float, spec: str) -> str:
    """UI-2: Check if a value is within spec limits. Returns checkmark or X."""
    try:
        if value is None or not spec:
            return ""
        spec = spec.strip()
        m = re.match(r'>=?\s*([\d.]+)', spec)
        if m:
            return "\u2713" if value >= float(m.group(1)) else "\u2717"
        m = re.match(r'<=?\s*([\d.]+)', spec)
        if m:
            return "\u2713" if value <= float(m.group(1)) else "\u2717"
        m = re.match(r'([\d.]+)\s*[-\u2013]\s*([\d.]+)', spec)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            return "\u2713" if lo <= value <= hi else "\u2717"
        return ""
    except Exception:
        return ""


def _confidence_band_color(band: str) -> str:
    """Map confidence band to display color."""
    if band is None:
        return "#6c757d"
    mapping = {"high": "#28a745", "moderate": "#ff9800", "low": "#dc3545"}
    return mapping.get(band.lower(), "#6c757d")


# =========================================================================
# TITLE
# =========================================================================
st.markdown("""
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.5rem;">
    <div>
        <div style="font-size:1.6rem;font-weight:700;color:#0f172a;letter-spacing:-0.03em;line-height:1.2;">
            CMC Decision Workspace</div>
        <div style="font-size:0.78rem;color:#64748b;font-weight:500;letter-spacing:0.02em;margin-top:2px;">
            Biologics Analytical Assessment &middot; ICH Q5E &middot; Q6B &middot; Q1A/Q5C</div>
    </div>
</div>
""", unsafe_allow_html=True)

_spacer, _reset_col = st.columns([8, 2])
with _reset_col:
    if st.button("Start New Analysis", key="reset_session"):
        # Clean up temp files before clearing state
        _tmp_path = st.session_state.get("docx_temp_path")
        if _tmp_path and os.path.exists(_tmp_path) and _tmp_path.startswith(tempfile.gettempdir()):
            try:
                os.remove(_tmp_path)
            except OSError:
                pass
        for key in list(st.session_state.keys()):
            if key not in ("welcome_dismissed",):
                del st.session_state[key]
        st.rerun()

# =========================================================================
# Instruction Panel (always visible, collapsible)
# =========================================================================
with st.expander("About This Tool — How It Works", expanded="case_id" not in st.session_state):
    st.markdown("""
**CMC Decision Workspace** is a decision-support tool for biopharma CMC scientists.
It analyzes regulatory documents and structured analytical data to assess quality,
identify evidence gaps, and predict reviewer questions — aligned with ICH Q5E, Q6B,
Q1A/Q5C, and Q2(R2) guidelines.

**Two Analysis Modes:**

| Mode | Input | What It Does |
|------|-------|-------------|
| **Document Analysis** | Upload a PDF or DOCX regulatory report (characterization, stability, analytical method) | Classifies document type, extracts CQA values, assesses section coverage, identifies gaps, predicts reviewer questions |
| **Comparability Assessment** | Upload a CSV with pre/post attribute data, or use a demo case from the sidebar | Runs the full ICH Q5E comparability pipeline: CQA scoring, risk clustering, two-axis verdict, action recommendations |

**What You Provide:** A single regulatory document (PDF/DOCX) or a structured CSV with pre-change and post-change analytical values.

**What You Get:**
- Document classification with confidence score
- Key CQA extraction (HMW%, charge variants, potency, glycosylation) with spec compliance
- Section coverage assessment against ICH guidelines
- Evidence gap analysis (three-state: present / uncertain / absent)
- Predicted reviewer questions with severity ratings
- Exportable analysis report

> *For decision support only. Not regulatory advice. Verify all findings with source documents and a qualified regulatory professional.*
""")

# =========================================================================
# TOP SECTION: Upload
# =========================================================================
_upload_mode = st.radio(
    "Upload mode",
    ["Single Document", "Multi-Document Package"],
    horizontal=True,
    key="upload_mode",
    label_visibility="collapsed",
)

if _upload_mode == "Multi-Document Package":
    st.markdown("#### Upload CMC Package (Multiple Documents)")
    uploaded_files = st.file_uploader(
        'Upload multiple regulatory documents for package assessment',
        type=['docx', 'pdf'],
        accept_multiple_files=True,
        help='Upload characterization + stability + analytical method documents together',
        key="multi_file_uploader",
    )
    uploaded_file = None  # Single-file path disabled

    if uploaded_files and len(uploaded_files) > 0:
        if st.button("Run Package Assessment", type="primary", use_container_width=True, key="run_package_btn"):
            from ingestion import ingest_document as _pkg_ingest
            from services.package_assessor import assess_package, build_package_overview

            _pkg_results = []
            _pkg_names = []
            _pkg_errors = []
            _progress = st.progress(0, text="Preparing documents...")
            for _file_idx, uf in enumerate(uploaded_files):
                _pct = int((_file_idx / len(uploaded_files)) * 100)
                _progress.progress(_pct, text=f"Analyzing {uf.name} ({_file_idx+1}/{len(uploaded_files)})...")

                # Large file warning
                _fsize_mb = len(uf.getvalue()) / (1024 * 1024)
                if _fsize_mb > 10:
                    st.caption(f"{uf.name}: {_fsize_mb:.1f} MB — large file, may take longer")

                import tempfile as _tf
                _tmp = _tf.NamedTemporaryFile(suffix=Path(uf.name).suffix, delete=False)
                _tmp.write(uf.getvalue())
                _tmp.close()
                try:
                    _r = _pkg_ingest(_tmp.name)

                    # Check for scanned PDF
                    if Path(uf.name).suffix.lower() == ".pdf" and hasattr(_r, "parsed_doc") and _r.parsed_doc:
                        _total_chars = sum(len(p.get("text", "")) for p in _r.parsed_doc.get("pages", []))
                        if _total_chars < 100:
                            st.warning(f"{uf.name}: appears to be a scanned PDF (no extractable text). Skipping.")
                            _pkg_errors.append(f"{uf.name}: scanned PDF")
                            continue

                    _pkg_results.append(_r)
                    _pkg_names.append(uf.name)
                except Exception as _e:
                    _err_msg = str(_e)
                    if "password" in _err_msg.lower() or "corrupt" in _err_msg.lower():
                        st.warning(f"{uf.name}: file may be corrupted or password-protected. Skipping.")
                    else:
                        st.warning(f"{uf.name}: processing failed. Skipping.")
                    _pkg_errors.append(f"{uf.name}: {_err_msg[:80]}")

            _progress.progress(100, text="Assessment complete.")

            if _pkg_results:
                _pkg = assess_package(_pkg_results, _pkg_names)
                _pkg_overview = build_package_overview(_pkg)
                st.session_state.package_overview = _pkg_overview
                st.session_state.case_id = _pkg.package_id
                st.session_state.overview_data = None  # Use package view
                st.session_state.report_dict = None
                st.rerun()

    # Display package results if available
    _pkg_ov = st.session_state.get("package_overview")
    if _pkg_ov:
        st.markdown("---")
        # Package verdict banner
        _pv_color = _pkg_ov["package_verdict_color"]
        _pv_label = _pkg_ov["package_verdict_display"]
        _pv_conf = _pkg_ov["package_confidence"]
        st.markdown(f"""
        <div style="background:{_pv_color};color:white;padding:1rem 1.5rem;border-radius:6px;margin-bottom:1rem;">
            <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:0.08em;opacity:0.8;">PACKAGE VERDICT</div>
            <div style="font-size:1.3rem;font-weight:700;">{_pv_label}</div>
            <div style="font-size:0.85rem;opacity:0.9;">Confidence: {_pv_conf:.0%} — {_pkg_ov['package_rationale']}</div>
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
                _icon = "✓" if present else "✗"
                _bg = "#ecfdf5" if present else "#fff1f2"
                _fg = "#059669" if present else "#e11d48"
                st.markdown(
                    f'<div style="background:{_bg};color:{_fg};padding:0.5rem;border-radius:4px;text-align:center;font-size:0.85rem;">'
                    f'<strong>{_icon}</strong> {dtype.replace("_"," ").title()}</div>',
                    unsafe_allow_html=True,
                )

        # Cross-document flags
        _xflags = _pkg_ov["cross_document_flags"]
        if _xflags:
            st.markdown("##### Cross-Document Findings")
            for xf in _xflags:
                _sev_color = {"critical": "#f43f5e", "warning": "#f59e0b", "info": "#3b82f6"}.get(xf["severity"], "#64748b")
                st.markdown(
                    f'<div style="border-left:3px solid {_sev_color};padding:0.4rem 0.8rem;margin-bottom:0.3rem;font-size:0.88rem;">'
                    f'<strong>[{xf["severity"].upper()}]</strong> {xf["description"]}</div>',
                    unsafe_allow_html=True,
                )

        # Reviewer questions (aggregated)
        _rqs = _pkg_ov["reviewer_questions"]
        if _rqs:
            with st.expander(f"Predicted Reviewer Questions ({len(_rqs)})", expanded=True):
                for _rq in _rqs:
                    _src = _rq.get("source_doc_type", "")
                    _badge_color = {"CHARACTERIZATION": "#2563eb", "STABILITY": "#059669", "ANALYTICAL_METHOD": "#d97706", "PKG": "#7c3aed"}.get(_src, "#64748b")
                    st.markdown(
                        f'<div style="border-left:3px solid {_badge_color};padding:0.4rem 0.8rem;margin-bottom:0.3rem;font-size:0.88rem;">'
                        f'<span style="background:{_badge_color};color:white;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.7rem;margin-right:0.4rem;">{_src}</span>'
                        f'{_rq["question"]}</div>',
                        unsafe_allow_html=True,
                    )

        # Export package report
        st.markdown("---")
        if st.button("Export Package Report (DOCX)", type="primary", use_container_width=True, key="export_pkg_docx"):
            try:
                from reports.package_report import generate_package_report
                _pkg_path = f"/tmp/CMC_Package_Report_{_pkg_ov['package_id']}.docx"
                generate_package_report(_pkg_ov, _pkg_path)
                with open(_pkg_path, "rb") as _pf:
                    st.download_button(
                        "Download Report",
                        data=_pf.read(),
                        file_name=os.path.basename(_pkg_path),
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="pkg_docx_download",
                    )
                st.success("Package report generated.")
            except Exception as _e:
                st.error(f"Report generation failed: {_e}")

        # Footer disclaimer
        st.markdown("""
        <div style="margin-top:1rem;padding:0.5rem 1rem;background:#f0f7ff;border-left:3px solid #3b82f6;border-radius:4px;font-size:0.78rem;color:#475569;font-style:italic;">
            For decision support only. Not regulatory advice. Verify all findings with source documents and a qualified regulatory professional.
        </div>
        """, unsafe_allow_html=True)
        st.stop()

else:
    st.markdown("#### Upload Document or Data")
    uploaded_file = st.file_uploader(
        'Upload a regulatory document or data file',
        type=['docx', 'pdf', 'csv'],
        help='Supports: DOCX/PDF regulatory reports, CSV attribute tables',
        key="file_uploader",
    )

# UI-5: User-friendly sample reports dropdown (replaces Developer Tools)
SAMPLE_REPORTS = {
    "COMP-001": {
        "label": "Media Change (Anti-IL6R IgG1)",
        "description": "Compare cell culture media change for an anti-IL6R monoclonal antibody.",
    },
    "COMP-002": {
        "label": "Scale-Up 2L to 2000L (Anti-PD1 IgG4)",
        "description": "Scale-up study from lab-scale (2L) to commercial-scale (2000L) bioreactor.",
    },
    "COMP-005": {
        "label": "Site Transfer (Anti-VEGF IgG1)",
        "description": "Manufacturing site transfer with full analytical comparison package.",
    },
}

with st.sidebar:
    st.markdown("### Quick Start Samples")

    # Real document samples
    st.markdown("**Document Analysis** *(PDF/DOCX reports)*")
    _REAL_DOCS = {
        "NISTmAb_SP260-237.pdf": ("NISTmAb Characterization (PDF)", "CHARACTERIZATION"),
        "Xbonzy_EPAR.pdf": ("Xbonzy Stability EPAR (PDF)", "STABILITY"),
        "ICH_Q14_2023.pdf": ("ICH Q14 Analytical Method (PDF)", "ANALYTICAL_METHOD"),
    }
    _real_doc_choice = st.selectbox(
        "Select a regulatory document",
        ["-- Select --"] + list(_REAL_DOCS.keys()),
        format_func=lambda k: _REAL_DOCS.get(k, (k,))[0] if k in _REAL_DOCS else k,
        key="real_doc_selector",
    )
    if _real_doc_choice in _REAL_DOCS:
        st.caption(f"Type: {_REAL_DOCS[_real_doc_choice][1]}")
        if st.button("Analyze This Document", type="primary", use_container_width=True, key="analyze_real_doc"):
            _real_path = PROJECT_ROOT / "benchmarks" / "real_documents" / _real_doc_choice
            if _real_path.exists():
                st.session_state._sidebar_analyze_doc = str(_real_path)
                st.session_state._sidebar_analyze_type = _REAL_DOCS[_real_doc_choice][1]

    st.markdown("---")
    st.markdown("**Comparability Assessment** *(pre/post data)*")
    demo_choice = st.selectbox(
        "Select a comparability case",
        ["-- Select --"] + list(SAMPLE_REPORTS.keys()),
        format_func=lambda cid: (
            f"{SAMPLE_REPORTS[cid]['label']}" if cid in SAMPLE_REPORTS else cid
        ),
        key="demo_selector",
    )
    if demo_choice in SAMPLE_REPORTS:
        st.caption(SAMPLE_REPORTS[demo_choice]["description"])
        if st.button("Run Comparability Analysis", type="primary", use_container_width=True, key="analyze_demo"):
            st.session_state._sidebar_demo_choice = demo_choice

# ---------------------------------------------------------------------------
# Handle upload: auto-detect, confirm, auto-trigger (P9-A dispatch)
# ---------------------------------------------------------------------------
needs_assessment = False
active_case_id = st.session_state.case_id

if uploaded_file is not None:
    _file_ext = Path(uploaded_file.name).suffix.lower()
    _file_id = f"{uploaded_file.name}_{uploaded_file.size}"
    _prev_file_id = st.session_state.get("_last_uploaded_file_id")

    # Skip re-ingestion if this file was already confirmed
    if _file_id == _prev_file_id and st.session_state.get("docx_review_confirmed"):
        pass  # Already processed and confirmed — go straight to split-view
    elif _file_ext in (".docx", ".pdf"):
        # -----------------------------------------------------------------
        # P9-A: DOCX/PDF path -- ingest, classify, then show review before assessment
        # -----------------------------------------------------------------
        try:
            from ingestion import ingest_document

            _audit.log("UPLOAD", f"File uploaded: {uploaded_file.name} ({_file_ext})", document_name=uploaded_file.name)
            with st.spinner(f"Analyzing {uploaded_file.name} -- this may take 30-60 seconds for large PDFs..."):
                ingestion_result, _tmp_path = _cached_ingest(uploaded_file.getvalue(), _file_ext)
            st.session_state.docx_temp_path = _tmp_path
            st.session_state.docx_ingestion_result = ingestion_result
            st.session_state._last_uploaded_file_id = _file_id
            st.session_state.docx_review_confirmed = False
            st.session_state.docx_user_overrides = []

            # Fix 2: Colored classification banner
            _doc_classification = getattr(ingestion_result, "document_classification", None)
            if _doc_classification:
                _doc_type = _doc_classification.document_type
                _conf = _doc_classification.confidence
                _type_label = _doc_type.replace("_", " ").title()
                _conf_pct = int(_conf * 100)
                if _conf >= 0.80:
                    _bg, _fg = "#d4edda", "#155724"
                elif _conf >= 0.50:
                    _bg, _fg = "#fff3cd", "#856404"
                else:
                    _bg, _fg = "#f8d7da", "#721c24"
                st.markdown(
                    f'<div style="background:{_bg};color:{_fg};padding:0.8rem 1rem;'
                    f'border-radius:0.4rem;margin-bottom:0.5rem;">'
                    f'<strong>Document Type:</strong> {_type_label}  '
                    f'<span style="opacity:0.7;">(Confidence: {_conf_pct}%)</span></div>',
                    unsafe_allow_html=True,
                )
                st.session_state.detected_doc_type = _doc_type
                _audit.log("CLASSIFY", f"Classified as {_doc_type} ({_conf_pct}%)",
                           document_name=uploaded_file.name, document_type=_doc_type)
            else:
                st.caption(f"Document type: {_file_ext.upper().strip('.')} (classification unavailable)")
                st.session_state.detected_doc_type = "UNKNOWN"

            _n_attrs = len(ingestion_result.attributes) if ingestion_result.attributes else 0
            _n_tables = getattr(ingestion_result, "n_tables_found", 0)
            _n_signals = len(ingestion_result.signals) if ingestion_result.signals else 0
            st.success(
                f"{_file_ext.upper().strip('.')} ingested: {_n_attrs} attributes, "
                f"{_n_tables} tables, {_n_signals} signals detected."
            )
            # Check for scanned PDF (no text extracted)
            if _file_ext == ".pdf" and ingestion_result.parsed_doc:
                _total_text = sum(
                    len(p.get("text", ""))
                    for p in ingestion_result.parsed_doc.get("pages", [])
                )
                if _total_text < 100:
                    st.warning(
                        "This document appears to be a scanned image. "
                        "OCR is not supported in this release -- please upload a text-based PDF."
                    )

            # Low classification confidence warning
            if _doc_classification and _doc_classification.confidence < 0.3:
                st.warning(
                    f"Could not confidently identify document type. "
                    f"Classifier suggests: **{_doc_classification.document_type.replace('_', ' ').title()}** "
                    f"(confidence: {int(_doc_classification.confidence * 100)}%). "
                    f"Proceeding with generic extraction."
                )

        except ImportError:
            st.error(
                f"{_file_ext.upper().strip('.')} ingestion requires the `python-docx` package. "
                f"Install with: `pip3 install python-docx`"
            )
        except Exception as e:
            _err_msg = str(e)
            if "password" in _err_msg.lower() or "corrupt" in _err_msg.lower() or "invalid" in _err_msg.lower():
                st.error(
                    "Unable to read file. It may be corrupted or password-protected. "
                    "Try re-saving the document and uploading again."
                )
            else:
                st.error(
                    f"An error occurred while processing the document. "
                    f"Please try a different file or check the format."
                )
                with st.expander("Error details (for support)", expanded=False):
                    st.code(f"{type(e).__name__}: {e}")

    elif _file_ext == ".csv":
        # -----------------------------------------------------------------
        # CSV path -- unchanged from original
        # -----------------------------------------------------------------
        df_uploaded = pd.read_csv(uploaded_file)
        st.session_state.uploaded_df = df_uploaded
        st.session_state.docx_ingestion_result = None
        st.session_state.docx_review_confirmed = False
        batch_data = _csv_to_batch_data(df_uploaded)
        n_attrs = len(batch_data.get("attributes", []))

        # UI-1: Document classification display for CSV
        st.info(f"Identified as: **CSV Attribute Table** (Confidence: 100%)")
        st.success(f"Detected {n_attrs} attributes from uploaded CSV.")

        # Create case from upload
        case_id = backend.create_case(
            product_name="Uploaded Case",
            molecule_class="mAb",
            change_type="User Upload",
            change_description="Uploaded via CSV",
            batch_data=batch_data,
        )
        st.session_state.case_id = case_id
        active_case_id = case_id
        needs_assessment = True

    else:
        st.error(
            f"File format **{_file_ext}** is not supported. "
            f"Please upload a DOCX, PDF, or CSV file."
        )

# Handle sidebar button: "Analyze This Document" (real PDF samples)
_sidebar_doc = st.session_state.pop("_sidebar_analyze_doc", None)
_sidebar_doc_type = st.session_state.pop("_sidebar_analyze_type", None)
if _sidebar_doc and uploaded_file is None:
    try:
        from ingestion import ingest_document as _ingest_sidebar
        with st.spinner(f"Analyzing document -- this may take 30-60 seconds..."):
            _sb_result = _ingest_sidebar(_sidebar_doc)
        _sb_cls = _sb_result.document_classification
        _sb_ev = _sb_result.extracted_evidence
        _sb_type = _sb_cls.document_type if _sb_cls else "UNKNOWN"
        _sb_conf = _sb_cls.confidence if _sb_cls else 0.0
        _sb_completeness = _sb_ev.get("completeness_score", 0) if _sb_ev else 0
        _sb_gaps = _sb_ev.get("critical_gaps", []) if _sb_ev else []

        _sb_overview = {
            "document_type": _sb_type,
            "classification_confidence": _sb_conf,
            "extracted_evidence": _sb_ev,
            "judgment": {}, "judgment_summary": {},
            "blocking_clusters": [], "counterfactuals": [],
            "reviewer_risk": {"predicted_questions": [
                {"question": q, "confidence": "moderate", "source": "extraction",
                 "affected_attributes": [], "primary": i == 0}
                for i, q in enumerate(_sb_ev.get("reviewer_concerns", []))
            ] if _sb_ev else []},
            "critical_attributes": [],
            "confidence_breakdown": {
                "analytical_confidence": _sb_conf,
                "package_readiness": _sb_completeness,
                "evidence_completeness": _sb_completeness,
            },
        }
        # Type-specific verdicts
        if _sb_type == "CHARACTERIZATION":
            from services.characterization_assessor import assess_characterization
            _char_result = assess_characterization(_sb_ev)
            _sb_overview.update(_char_result)
        elif _sb_type == "STABILITY":
            from services.stability_assessor import assess_stability
            _stab_result = assess_stability(_sb_ev)
            _sb_overview.update(_stab_result)
        elif _sb_type == "ANALYTICAL_METHOD":
            from services.analytical_method_assessor import assess_analytical_method
            _anal_result = assess_analytical_method(_sb_ev)
            _sb_overview.update(_anal_result)
        else:
            _sb_overview["analytical_conclusion"] = "Assessment Complete"
            _sb_overview["package_posture"] = "Review Required"

        st.session_state.overview_data = _sb_overview
        st.session_state.report_dict = None
        st.session_state.docx_ingestion_result = _sb_result
        st.session_state.docx_review_confirmed = True
        st.session_state.docx_temp_path = _sidebar_doc
        st.session_state.detected_doc_type = _sb_type
        st.session_state.case_id = f"sample-{_sb_type.lower()}-{uuid.uuid4().hex[:8]}"
    except Exception as _e:
        st.error(f"Failed to analyze document: {_e}")

# Handle sidebar button: "Run Comparability Analysis" (demo cases)
_sidebar_demo = st.session_state.pop("_sidebar_demo_choice", None)
if _sidebar_demo and uploaded_file is None:
    with st.spinner(f"Running comparability analysis for {_sidebar_demo}..."):
        demo_data = _load_demo_case(_sidebar_demo)
        if demo_data:
            case_id = backend.create_case(
                product_name=demo_data.get("product_name", _sidebar_demo),
                molecule_class="mAb",
                change_type=demo_data.get("change_description", "Demo")[:60],
                change_description=f"{_sidebar_demo}: {demo_data.get('change_description', '')}",
                batch_data=demo_data,
            )
            st.session_state.case_id = case_id
            active_case_id = case_id
            st.session_state.auto_ran = False
            st.session_state.detected_doc_type = "COMPARABILITY"
            needs_assessment = True

            # Store the uploaded df for left panel display
            attrs = demo_data.get("attributes", [])
            if attrs:
                st.session_state.uploaded_df = pd.DataFrame(attrs)
        else:
            st.error(f"Demo case file not found: {_sidebar_demo}.json")

# =========================================================================
# P9-B: Extraction Review Step (DOCX only -- before assessment)
# P9-C: Document Preview (left panel)
# P9-D: Anchor-based cross-linking
# =========================================================================
_ingestion = st.session_state.get("docx_ingestion_result")
if _ingestion is not None and not st.session_state.docx_review_confirmed:
    st.markdown("---")
    st.markdown("### Extraction Review")
    st.caption("Review extracted data before running assessment. Edit fields as needed.")

    _review_left, _review_right = st.columns([1, 1])

    # -----------------------------------------------------------------
    # P9-C: Document Preview (left panel -- mammoth HTML or fallback)
    # -----------------------------------------------------------------
    with _review_left:
        st.markdown("#### Document Preview")
        _temp_path = st.session_state.get("docx_temp_path")
        _selected_anchor = st.session_state.get("selected_anchor")

        if _temp_path and os.path.exists(_temp_path):
            _is_pdf = _temp_path.lower().endswith(".pdf")

            if not _is_pdf:
                # DOCX: rich HTML preview via mammoth
                try:
                    import mammoth
                    with open(_temp_path, 'rb') as f:
                        _mammoth_result = mammoth.convert_to_html(f)
                    _doc_html = _mammoth_result.value

                    _anchor_counter = [0]
                    def _add_anchor(match):
                        _anchor_counter[0] += 1
                        _anchor_id = f"docx-section-{_anchor_counter[0]}"
                        _tag = match.group(1)
                        _content = match.group(2)
                        _highlight = ""
                        if _selected_anchor and _selected_anchor == _anchor_id:
                            _highlight = ' style="background-color: #fff3cd; border-left: 4px solid #ffc107; padding-left: 8px;"'
                        return f'<{_tag} id="{_anchor_id}"{_highlight}>{_content}</{_tag}>'
                    _doc_html = re.sub(r'<(h[1-6])>(.*?)</\1>', _add_anchor, _doc_html)

                    _table_counter = [0]
                    def _add_table_anchor(match):
                        _table_counter[0] += 1
                        _table_id = f"docx-table-{_table_counter[0]}"
                        _highlight = ""
                        if _selected_anchor and _selected_anchor == _table_id:
                            _highlight = ' style="border: 3px solid #ffc107; background-color: #fff3cd;"'
                        return f'<table id="{_table_id}"{_highlight}>'
                    _doc_html = re.sub(r'<table>', _add_table_anchor, _doc_html)

                    st.markdown(
                        f'<div style="max-height: 800px; overflow-y: auto; border: 1px solid #ddd; '
                        f'padding: 1rem; border-radius: 0.5rem;">{_doc_html}</div>',
                        unsafe_allow_html=True,
                    )
                except ImportError:
                    _parsed = getattr(_ingestion, "parsed_doc", None) or {}
                    _paragraphs = [_pg.get("text", "") for _pg in _parsed.get("pages", [])]
                    st.text_area("Document Content", "\n\n".join(_paragraphs) or "(No text)", height=800, key="docx_preview_fallback")
                except Exception as _preview_err:
                    st.warning(f"Document preview unavailable: {_preview_err}")
            else:
                # PDF: text-based preview from parsed pages
                _parsed = getattr(_ingestion, "parsed_doc", None) or {}
                _pages = _parsed.get("pages", [])
                if _pages:
                    _page_texts = []
                    for _pg in _pages:
                        _pn = _pg.get("page_number", "?")
                        _pt = _pg.get("text", "").strip()
                        if _pt:
                            _page_texts.append(f"--- Page {_pn} ---\n{_pt}")
                    _full_text = "\n\n".join(_page_texts) if _page_texts else "(No text extracted from PDF)"
                    st.text_area("PDF Content", _full_text, height=800, key="pdf_preview_review")
                else:
                    st.info("No text could be extracted from this PDF.")
        else:
            st.info("No document file available for preview.")

    # -----------------------------------------------------------------
    # P9-B: Extraction Review (right panel)
    # -----------------------------------------------------------------
    with _review_right:
        st.markdown("#### Extracted Case Context")

        _ctx = _ingestion.case_context
        _product_name = getattr(_ctx, "product_name", "") or ""
        _molecule_class = getattr(_ctx, "molecule_class", "unknown") or "unknown"
        _change_type = getattr(_ctx, "change_type", "unknown") or "unknown"
        _change_desc = getattr(_ctx, "change_description", "") or ""
        # molecule_class_confidence: ingestion/__init__.py uses 'confidence' on ExtractedCaseContext
        _mol_confidence = getattr(_ctx, "molecule_class_confidence", None)
        if _mol_confidence is None:
            _mol_confidence = getattr(_ctx, "confidence", 0.0)

        # Editable fields for case context
        _edit_product = st.text_input("Product Name", value=_product_name, key="review_product_name")
        _mol_options = ["mAb", "bispecific", "ADC", "fusion protein", "enzyme", "peptide", "unknown", "other"]
        _mol_idx = _mol_options.index(_molecule_class) if _molecule_class in _mol_options else 0
        _edit_mol = st.selectbox("Molecule Class", _mol_options, index=_mol_idx, key="review_molecule_class")

        # P9-B.2: Low-confidence warning
        if _mol_confidence < 0.8:
            st.warning(
                f"Molecule class confidence is low ({_mol_confidence:.0%}). "
                f"Please verify the molecule class is correct."
            )

        _edit_change = st.text_input("Change Type", value=_change_type, key="review_change_type")

        # -----------------------------------------------------------------
        # P9-B.3: Extracted attributes in editable data table
        # -----------------------------------------------------------------
        st.markdown("#### Extracted Attributes")
        _attrs = _ingestion.attributes
        if _attrs:
            _attr_rows = []
            for _a in _attrs:
                _attr_rows.append({
                    "Name": getattr(_a, "name", ""),
                    "Category": getattr(_a, "category", ""),
                    "Pre Value": getattr(_a, "pre_value", None),
                    "Post Value": getattr(_a, "post_value", None),
                    "Unit": getattr(_a, "unit", ""),
                    "Confidence": getattr(_a, "extraction_confidence", 1.0),
                })
            _attr_df = pd.DataFrame(_attr_rows)
            _edited_df = st.data_editor(
                _attr_df,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="review_attr_editor",
            )
        else:
            _edited_df = None
            st.info("No attributes extracted from document.")

        # -----------------------------------------------------------------
        # P9-B.4: ExtractionIssues with severity badges
        # -----------------------------------------------------------------
        _issues = _ingestion.issues
        if _issues:
            st.markdown("#### Extraction Issues")
            _severity_colors = {
                "critical": "#dc3545",
                "warning": "#ffc107",
                "info": "#17a2b8",
            }
            _has_critical = False
            for _issue in _issues:
                if isinstance(_issue, str):
                    # Simple string issues from ingestion/__init__.py
                    _sev = "warning"
                    _desc = _issue
                    if "critical" in _issue.lower():
                        _sev = "critical"
                    elif "info" in _issue.lower():
                        _sev = "info"
                else:
                    # ExtractionIssue dataclass from specs
                    _sev = getattr(_issue, "severity", "info")
                    _desc = getattr(_issue, "description", str(_issue))

                _badge_color = _severity_colors.get(_sev, "#6c757d")
                if _sev == "critical":
                    _has_critical = True
                st.markdown(
                    f'<span style="background-color: {_badge_color}; color: white; padding: 2px 8px; '
                    f'border-radius: 3px; font-size: 0.8rem;">{_sev.upper()}</span> {_desc}',
                    unsafe_allow_html=True,
                )

            # P9-B.5: Critical issues block assessment
            if _has_critical:
                st.error("Critical extraction issues must be resolved before running assessment.")

        # -----------------------------------------------------------------
        # P9-D: Anchor cross-linking -- buttons to jump to source
        # -----------------------------------------------------------------
        if _attrs:
            st.markdown("#### Source Anchors")
            for _idx, _a in enumerate(_attrs[:10]):
                _anchor_ids = getattr(_a, "anchor_ids", [])
                if _anchor_ids:
                    for _aid in _anchor_ids[:2]:
                        if st.button(f"Show source: {_a.name}", key=f"anchor_btn_{_idx}_{_aid}"):
                            st.session_state.selected_anchor = _aid

        # -----------------------------------------------------------------
        # P9-B.6: Confirm & Run Assessment button
        # Fix 4: Only block on critical issues for the SPECIFIC doc type
        # (missing pre/post is NOT critical for characterization/stability/analytical)
        # -----------------------------------------------------------------
        st.markdown("---")
        _detected_type = st.session_state.get("detected_doc_type", "COMPARABILITY")
        _can_confirm = True
        if _issues:
            for _issue in _issues:
                if isinstance(_issue, str):
                    _is_critical = "critical" in _issue.lower()
                    # Pre/post split failure is only critical for COMPARABILITY
                    if _is_critical and "pre" in _issue.lower() and "post" in _issue.lower():
                        if _detected_type != "COMPARABILITY":
                            continue  # Not critical for non-comparability
                    if _is_critical:
                        _can_confirm = False
                        break
                elif hasattr(_issue, "severity") and getattr(_issue, "severity", "") == "critical":
                    _desc = getattr(_issue, "description", "").lower()
                    if "pre" in _desc and "post" in _desc and _detected_type != "COMPARABILITY":
                        continue
                    _can_confirm = False
                    break

        if st.button(
            "Confirm & Run Assessment",
            type="primary",
            use_container_width=True,
            disabled=not _can_confirm,
            key="confirm_review_btn",
        ):
            # P9-B.7: Track UserOverride records for any edits
            _overrides = []
            _now = datetime.now(timezone.utc).isoformat()

            # Track context overrides
            if _edit_product != _product_name:
                _overrides.append({
                    "override_id": str(uuid.uuid4()),
                    "attribute_name": "__case_context__",
                    "field_name": "product_name",
                    "original_value": _product_name,
                    "corrected_value": _edit_product,
                    "corrected_by": "ui_user",
                    "reason": "User correction during extraction review",
                    "timestamp": _now,
                })
            if _edit_mol != _molecule_class:
                _overrides.append({
                    "override_id": str(uuid.uuid4()),
                    "attribute_name": "__case_context__",
                    "field_name": "molecule_class",
                    "original_value": _molecule_class,
                    "corrected_value": _edit_mol,
                    "corrected_by": "ui_user",
                    "reason": "User correction during extraction review",
                    "timestamp": _now,
                })
            if _edit_change != _change_type:
                _overrides.append({
                    "override_id": str(uuid.uuid4()),
                    "attribute_name": "__case_context__",
                    "field_name": "change_type",
                    "original_value": _change_type,
                    "corrected_value": _edit_change,
                    "corrected_by": "ui_user",
                    "reason": "User correction during extraction review",
                    "timestamp": _now,
                })

            # Track attribute edits from data editor
            if _edited_df is not None and _attrs:
                _orig_df = pd.DataFrame([{
                    "Name": getattr(a, "name", ""),
                    "Category": getattr(a, "category", ""),
                    "Pre Value": getattr(a, "pre_value", None),
                    "Post Value": getattr(a, "post_value", None),
                    "Unit": getattr(a, "unit", ""),
                    "Confidence": getattr(a, "extraction_confidence", 1.0),
                } for a in _attrs])
                # Compare row-by-row for edits
                for _row_idx in range(min(len(_orig_df), len(_edited_df))):
                    for _col in ["Pre Value", "Post Value", "Unit", "Category"]:
                        _orig_val = _orig_df.iloc[_row_idx].get(_col)
                        _edit_val = _edited_df.iloc[_row_idx].get(_col)
                        if str(_orig_val) != str(_edit_val):
                            _field_map = {
                                "Pre Value": "pre_value", "Post Value": "post_value",
                                "Unit": "unit", "Category": "category",
                            }
                            _overrides.append({
                                "override_id": str(uuid.uuid4()),
                                "attribute_name": _edited_df.iloc[_row_idx].get("Name", f"attr_{_row_idx}"),
                                "field_name": _field_map.get(_col, _col),
                                "original_value": _orig_val,
                                "corrected_value": _edit_val,
                                "corrected_by": "ui_user",
                                "reason": "User correction during extraction review",
                                "timestamp": _now,
                            })

            st.session_state.docx_user_overrides = _overrides
            _audit.log("CONFIRM", "User confirmed extraction review",
                       document_name=st.session_state.get("docx_temp_path", ""),
                       metadata={"overrides": len(_overrides)})
            st.session_state.docx_review_confirmed = True

            _detected_type = st.session_state.get("detected_doc_type", "COMPARABILITY")

            if _detected_type == "COMPARABILITY":
                # ---- Comparability path: create case via backend pipeline ----
                _pipeline_input = _ingestion.pipeline_input.copy() if hasattr(_ingestion, "pipeline_input") else {}
                _pipeline_input["product_name"] = _edit_product
                _pipeline_input["molecule_class"] = _edit_mol if _edit_mol != "unknown" else "mAb"

                # If no structured attributes extracted, try LLM-assisted pre/post extraction
                _has_prepost = bool(_pipeline_input.get("attributes"))
                if not _has_prepost and _edited_df is not None:
                    _has_prepost = len(_edited_df) > 0
                if not _has_prepost:
                    try:
                        from ingestion.llm_extraction import is_available, extract_pre_post_from_comparability
                        if is_available():
                            _parsed = getattr(_ingestion, "parsed_doc", None) or {}
                            _all_text = " ".join(p.get("text", "") for p in _parsed.get("pages", []))
                            _tbl_text = ""
                            for _pg in _parsed.get("pages", []):
                                for _tbl in _pg.get("tables", []):
                                    for _h in _tbl.get("headers", []):
                                        _tbl_text += _h + " "
                                    for _row in _tbl.get("rows", []):
                                        if isinstance(_row, dict):
                                            _tbl_text += " ".join(str(v) for v in _row.values()) + " "
                                        elif isinstance(_row, list):
                                            _tbl_text += " ".join(str(v) for v in _row) + " "
                            with st.spinner("Extracting pre/post comparability data with AI..."):
                                _prepost = extract_pre_post_from_comparability(_all_text, _tbl_text)
                            if _prepost:
                                _pipeline_input["attributes"] = _prepost
                                st.success(f"AI extracted {len(_prepost)} pre/post attribute pairs.")
                                _audit.log("EXTRACT", f"LLM extracted {len(_prepost)} pre/post pairs",
                                           document_name=st.session_state.get("docx_temp_path", ""),
                                           document_type="COMPARABILITY")
                            else:
                                st.warning("Could not extract pre/post data from this document. Please upload a CSV with structured comparability data.")
                    except Exception as _llm_e:
                        logger.debug("LLM comparability extraction failed: %s", _llm_e)

                if _edited_df is not None and len(_edited_df) > 0:
                    _pipeline_attrs = []
                    for _, _row in _edited_df.iterrows():
                        _pa = {"name": _row.get("Name", ""), "category": _row.get("Category", "physicochemical")}
                        if pd.notna(_row.get("Pre Value")):
                            _pa["pre_value"] = float(_row["Pre Value"])
                        if pd.notna(_row.get("Post Value")):
                            _pa["post_value"] = float(_row["Post Value"])
                        _pa["unit"] = _row.get("Unit", "")
                        if "pre_value" in _pa and "post_value" in _pa:
                            _pipeline_attrs.append(_pa)
                    if _pipeline_attrs:
                        _pipeline_input["attributes"] = _pipeline_attrs

                _batch_data = {"attributes": _pipeline_input.get("attributes", [])}
                case_id = backend.create_case(
                    product_name=_edit_product or "DOCX Upload",
                    molecule_class=_pipeline_input.get("molecule_class", "mAb"),
                    change_type=_edit_change or "DOCX Upload",
                    change_description=_pipeline_input.get("change_description", "Uploaded via DOCX"),
                    batch_data=_batch_data,
                )
                st.session_state.case_id = case_id
                active_case_id = case_id
                if _edited_df is not None:
                    st.session_state.uploaded_df = _edited_df.rename(columns={
                        "Name": "name", "Category": "category",
                        "Pre Value": "pre_value", "Post Value": "post_value",
                        "Unit": "unit", "Confidence": "confidence",
                    })
                needs_assessment = True

            else:
                # ---- Non-comparability path: build overview from ingestion evidence ----
                _ev = _ingestion.extracted_evidence
                _doc_cls = _ingestion.document_classification
                _conf = getattr(_doc_cls, "confidence", 0.0)
                _completeness = _ev.get("completeness_score", 0) if _ev else 0
                _gaps = _ev.get("critical_gaps", []) if _ev else []

                _overview = {
                    "document_type": _detected_type,
                    "classification_confidence": _conf,
                    "extracted_evidence": _ev,
                    "judgment": {},
                    "judgment_summary": {},
                    "blocking_clusters": [],
                    "counterfactuals": [],
                    "reviewer_risk": {"predicted_questions": [
                        {"question": q, "confidence": "moderate", "source": "extraction",
                         "affected_attributes": [], "primary": i == 0}
                        for i, q in enumerate(_ev.get("reviewer_concerns", []) if _ev else [])
                    ]},
                    "critical_attributes": [],
                    "confidence_breakdown": {
                        "analytical_confidence": _conf,
                        "package_readiness": _completeness,
                        "evidence_completeness": _completeness,
                    },
                }

                # Type-specific verdict fields
                if _detected_type == "CHARACTERIZATION":
                    from services.characterization_assessor import assess_characterization
                    _char_result = assess_characterization(_ev)
                    _overview.update(_char_result)
                elif _detected_type == "STABILITY":
                    from services.stability_assessor import assess_stability
                    _stab_result = assess_stability(_ev)
                    _overview.update(_stab_result)
                elif _detected_type == "ANALYTICAL_METHOD":
                    from services.analytical_method_assessor import assess_analytical_method
                    _anal_result = assess_analytical_method(_ev)
                    _overview.update(_anal_result)
                else:
                    _overview["analytical_conclusion"] = "Assessment Complete"
                    _overview["package_posture"] = "Review Required"

                st.session_state.overview_data = _overview
                st.session_state.report_dict = None
                # Use a synthetic case_id so the stop-guard passes
                st.session_state.case_id = f"ingestion-{_detected_type.lower()}-{uuid.uuid4().hex[:8]}"

            st.rerun()

    # Stop here until review is confirmed (DOCX path)
    if not st.session_state.docx_review_confirmed:
        st.stop()

# ---------------------------------------------------------------------------
# Molecule type override (auto-re-run)
# ---------------------------------------------------------------------------
if active_case_id and backend.get_case(active_case_id):
    case_data = backend.get_case(active_case_id)
    mol_options = ["mAb", "bispecific", "ADC", "fusion protein", "enzyme", "peptide", "other"]
    current_mol = case_data.get("molecule_class", "mAb")
    current_idx = mol_options.index(current_mol) if current_mol in mol_options else 0

    with st.sidebar:
        st.markdown("### Settings")
        new_mol = st.selectbox(
            "Override Molecule Type",
            mol_options,
            index=current_idx,
            key="mol_override",
        )
        if new_mol != current_mol:
            case_data["molecule_class"] = new_mol
            st.session_state.molecule_type_override = new_mol
            # Force re-run
            needs_assessment = True

        if st.session_state.case_id:
            st.markdown("---")
            st.caption(f"Case: {st.session_state.case_id}")
            st.caption(f"Status: {case_data.get('status', 'unknown')}")

# ---------------------------------------------------------------------------
# Auto-run assessment
# ---------------------------------------------------------------------------
if needs_assessment and active_case_id:
    _detected_type = st.session_state.get("detected_doc_type", "COMPARABILITY")
    case_obj = backend.get_case(active_case_id)

    if case_obj and case_obj["status"] != "assessed":
        if _detected_type == "COMPARABILITY":
            # EXISTING: comparability assessment pipeline
            with st.spinner("Running comparability assessment..."):
                report = _run_assessment_for_case(active_case_id)
            if report:
                _ac = report.get("analytical_conclusion", report["overall_verdict"])
                _pp = report.get("package_posture", "")
                st.success(f"Assessment complete: **{_ac}** | Posture: **{_pp}**")
            else:
                st.error("Assessment failed. Check data format.")
        else:
            # NEW: Non-comparability documents — build type-specific report from ingestion result
            _ingestion = st.session_state.get("docx_ingestion_result")
            if _ingestion:
                _ev = _ingestion.extracted_evidence or {}
                _report = {
                    "document_type": _detected_type,
                    "extracted_evidence": _ev,
                    "attributes_count": len(_ingestion.attributes) if _ingestion.attributes else 0,
                    "signals_count": len(_ingestion.signals) if _ingestion.signals else 0,
                    "issues": _ingestion.issues or [],
                }

                if _detected_type == "CHARACTERIZATION":
                    from services.characterization_assessor import assess_characterization
                    _char_result = assess_characterization(_ev)
                    _report.update(_char_result)
                    st.success(
                        f"Characterization assessment: **{_char_result['analytical_conclusion']}** | "
                        f"Readiness: **{_char_result['package_posture']}**"
                    )
                elif _detected_type == "STABILITY":
                    from services.stability_assessor import assess_stability
                    _stab_result = assess_stability(_ev)
                    _report.update(_stab_result)
                    st.success(f"Stability assessment: **{_stab_result['analytical_conclusion']}** | {_stab_result['package_posture']}")
                elif _detected_type == "ANALYTICAL_METHOD":
                    from services.analytical_method_assessor import assess_analytical_method
                    _anal_result = assess_analytical_method(_ev)
                    _report.update(_anal_result)
                    st.success(f"Analytical method: **{_anal_result['analytical_conclusion']}** | {_anal_result['package_posture']}")
                else:
                    _report["analytical_conclusion"] = "Assessment Complete"
                    _report["package_posture"] = "Review Required"
                    _report["overall_verdict"] = "Review Required"
                    st.success("Document assessment complete. Manual review recommended.")

                st.session_state.report_dict = _report
                st.session_state.overview_data = _report
                case_obj["status"] = "assessed"
            else:
                st.error("No ingestion result available.")

    elif case_obj and case_obj["status"] == "assessed":
        # Already assessed, just load data
        st.session_state.report_dict = backend._reports.get(active_case_id) or st.session_state.get("report_dict")
        st.session_state.overview_data = backend.get_overview(active_case_id) or st.session_state.get("overview_data")

# =========================================================================
# STOP if no case loaded
# =========================================================================
if not st.session_state.case_id or not st.session_state.overview_data:
    st.markdown("---")
    st.info("Upload a DOCX/PDF regulatory document or CSV data file to begin. Or select an example from the sidebar.")
    st.stop()

# =========================================================================
# SPLIT VIEW: Left (Data) + Right (Decision Panel)
# =========================================================================
overview = st.session_state.overview_data
report = st.session_state.report_dict
gaps_data = backend.get_gaps(st.session_state.case_id)
judgment = overview.get("judgment_summary", {})

st.markdown("---")

left_col, right_col = st.columns([1, 1])

# =========================================================================
# LEFT COLUMN: Data Table
# =========================================================================
with left_col:
    st.markdown("### Data")

    # P6-D: Show selected cluster highlight indicator
    _sel_cluster = st.session_state.get("selected_cluster")
    if _sel_cluster:
        st.info(f"Highlighting attributes related to: **{_sel_cluster}**")
        if st.button("Clear highlight", key="clear_cluster_highlight"):
            st.session_state.selected_cluster = None

    # P9-C/D: If DOCX was uploaded and confirmed, show document preview tab + data tab
    _post_review_ingestion = st.session_state.get("docx_ingestion_result")
    _post_review_temp = st.session_state.get("docx_temp_path")
    if _post_review_ingestion is not None and _post_review_temp and os.path.exists(_post_review_temp):
        _data_tab, _preview_tab = st.tabs(["Attribute Data", "Document Preview"])
        with _preview_tab:
            _sel_anchor_post = st.session_state.get("selected_anchor") or st.session_state.get("selected_cluster")
            _is_pdf_post = _post_review_temp.lower().endswith(".pdf")

            if not _is_pdf_post:
                # DOCX: rich HTML preview via mammoth
                try:
                    import mammoth
                    with open(_post_review_temp, 'rb') as f:
                        _m_result = mammoth.convert_to_html(f)
                    _preview_html = _m_result.value

                    _sec_ctr = [0]
                    def _add_post_anchor(match):
                        _sec_ctr[0] += 1
                        _sid = f"docx-section-{_sec_ctr[0]}"
                        _tag = match.group(1)
                        _content = match.group(2)
                        _hl = ""
                        if _sel_anchor_post and _sel_anchor_post.lower() in _content.lower():
                            _hl = ' style="background-color: #fff3cd; border-left: 4px solid #ffc107; padding-left: 8px;"'
                        return f'<{_tag} id="{_sid}"{_hl}>{_content}</{_tag}>'
                    _preview_html = re.sub(r'<(h[1-6])>(.*?)</\1>', _add_post_anchor, _preview_html)

                    st.markdown(
                        f'<div style="max-height: 800px; overflow-y: auto; border: 1px solid #ddd; '
                        f'padding: 1rem; border-radius: 0.5rem; font-size: 0.9rem;">{_preview_html}</div>',
                        unsafe_allow_html=True,
                    )
                except ImportError:
                    _parsed_post = getattr(_post_review_ingestion, "parsed_doc", None) or {}
                    _paras = [_pg.get("text", "") for _pg in _parsed_post.get("pages", [])]
                    st.text_area("Document Content", "\n\n".join(_paras) or "(No text)", height=800, key="post_review_preview")
                except Exception as _pe:
                    st.warning(f"Document preview unavailable: {_pe}")
            else:
                # PDF: text-based preview from parsed pages
                _parsed_post = getattr(_post_review_ingestion, "parsed_doc", None) or {}
                _pages_post = _parsed_post.get("pages", [])
                if _pages_post:
                    _ptexts = []
                    for _pg in _pages_post:
                        _pn = _pg.get("page_number", "?")
                        _pt = _pg.get("text", "").strip()
                        if _pt:
                            _ptexts.append(f"--- Page {_pn} ---\n{_pt}")
                    st.text_area("PDF Content", "\n\n".join(_ptexts) or "(No text)", height=800, key="post_pdf_preview")
                else:
                    st.info("No text could be extracted from this PDF.")

        with _data_tab:
            if st.session_state.uploaded_df is not None:
                display_df = st.session_state.uploaded_df.copy()
                keep_cols = [c for c in display_df.columns if c.lower() not in (
                    "source", "expected_verdict", "expected_actions", "case_id", "title",
                )]
                if keep_cols:
                    display_df = display_df[keep_cols]
                st.dataframe(display_df, use_container_width=True, hide_index=True, height=500)
            elif overview is not None and overview.get("extracted_evidence"):
                # Show type-aware CQA table for non-comparability documents
                _ev_tab = overview["extracted_evidence"]
                _cqa_tab_rows = []
                _ov_doc_type = overview.get("document_type", "")

                if _ov_doc_type == "STABILITY":
                    # Stability-specific fields
                    _sl = _ev_tab.get("proposed_shelf_life")
                    if _sl is not None:
                        _cqa_tab_rows.append({"Field": "Proposed Shelf Life", "Value": str(_sl), "Unit": "months", "Detail": ""})
                    _mt = _ev_tab.get("max_timepoint_months")
                    if _mt is not None:
                        _cqa_tab_rows.append({"Field": "Max Timepoint", "Value": str(_mt), "Unit": "months", "Detail": ""})
                    _conds = _ev_tab.get("conditions_tested", [])
                    if _conds:
                        _cqa_tab_rows.append({"Field": "Conditions Tested", "Value": ", ".join(_conds), "Unit": "", "Detail": f"{len(_conds)} conditions"})
                    _suff = _ev_tab.get("sufficiency_for_claim", "")
                    if _suff:
                        _cqa_tab_rows.append({"Field": "Sufficiency for Claim", "Value": str(_suff).replace("_", " ").title(), "Unit": "", "Detail": ""})
                    _oos = _ev_tab.get("oos_events", [])
                    _cqa_tab_rows.append({"Field": "OOS/OOT Events", "Value": str(len(_oos)), "Unit": "events", "Detail": "Requires investigation" if _oos else "None detected"})
                    _trends = _ev_tab.get("trend_concerns", [])
                    _cqa_tab_rows.append({"Field": "Trend Concerns", "Value": str(len(_trends)), "Unit": "", "Detail": "; ".join(_trends[:2]) if _trends else "None"})

                elif _ov_doc_type == "ANALYTICAL_METHOD":
                    # Analytical method fields
                    _vs = _ev_tab.get("validation_studies_found", [])
                    _vm = _ev_tab.get("validation_studies_missing", [])
                    _cqa_tab_rows.append({"Field": "Validation Studies Found", "Value": str(len(_vs)), "Unit": f"of {len(_vs)+len(_vm)}", "Detail": ", ".join(_vs[:5])})
                    if _vm:
                        _cqa_tab_rows.append({"Field": "Studies Missing", "Value": str(len(_vm)), "Unit": "", "Detail": ", ".join(_vm)})
                    _cs = _ev_tab.get("completeness_score")
                    if _cs is not None:
                        _cqa_tab_rows.append({"Field": "ICH Q2 Completeness", "Value": f"{_cs:.1%}", "Unit": "", "Detail": ""})

                else:
                    # Characterization or UNKNOWN — show CQA fields
                    for _lbl, _vk, _sk, _u, _sp in [
                        ("HMW %", "hmw_pct", "hmw", "%", "< 5.0"),
                        ("Main Charge Peak %", "main_charge_peak_pct", "main_charge_peak", "%", "> 50.0"),
                        ("Acidic Variants %", "acidic_variants_pct", None, "%", ""),
                        ("Basic Variants %", "basic_variants_pct", None, "%", ""),
                        ("Afucosylation %", "afucosylation_pct", "afucosylation", "%", ""),
                        ("Potency %", "potency_relative_pct", "relative_potency", "%", "80 - 120"),
                    ]:
                        _v = _ev_tab.get(_vk)
                        _st = ""
                        if _sk and isinstance(_ev_tab.get(_sk), dict):
                            _st = _ev_tab[_sk].get("state", "")
                        _isp = "--"
                        if _v is not None and _sp:
                            if _sp.startswith("<"):
                                _isp = "\u2713" if _v < float(_sp[1:].strip()) else "\u2717"
                            elif _sp.startswith(">"):
                                _isp = "\u2713" if _v > float(_sp[1:].strip()) else "\u2717"
                            elif "-" in _sp:
                                _lo, _hi = [float(x.strip()) for x in _sp.split("-")]
                                _isp = "\u2713" if _lo <= _v <= _hi else "\u2717"
                        _cqa_tab_rows.append({
                            "Field": _lbl,
                            "Value": f"{_v:.3f}" if _v is not None else "--",
                            "Unit": _u,
                            "Detail": f"Spec: {_sp} {_isp}" if _sp else (_st.replace("_", " ").title() if _st else "--"),
                        })
                    _cs = _ev_tab.get("completeness_score")
                    if _cs is not None:
                        _cqa_tab_rows.append({
                            "Field": "Section Completeness", "Value": f"{_cs:.1%}", "Unit": "",
                            "Detail": "\u2713 Adequate" if _cs >= 0.8 else "\u2717 Gaps",
                        })

                if _cqa_tab_rows:
                    st.dataframe(pd.DataFrame(_cqa_tab_rows), use_container_width=True, hide_index=True, height=400)
                else:
                    st.info("No CQA data extracted.")
            else:
                st.info("No attribute data to display.")
    elif st.session_state.uploaded_df is not None:
        display_df = st.session_state.uploaded_df.copy()
        # Trim to relevant columns for display
        keep_cols = [c for c in display_df.columns if c.lower() not in (
            "source", "expected_verdict", "expected_actions", "case_id", "title",
        )]
        if keep_cols:
            display_df = display_df[keep_cols]
        st.dataframe(display_df, use_container_width=True, hide_index=True, height=500)
    elif overview is not None and overview.get("extracted_evidence"):
        # Non-comparability documents: show CQA table with spec compliance
        _ev = overview.get("extracted_evidence", {})
        _cqa_rows = []

        # Standard CQA fields
        _cqa_fields = [
            ("HMW %", "hmw_pct", "hmw", "%", "< 5.0"),
            ("Main Charge Peak %", "main_charge_peak_pct", "main_charge_peak", "%", "> 50.0"),
            ("Acidic Variants %", "acidic_variants_pct", None, "%", ""),
            ("Basic Variants %", "basic_variants_pct", None, "%", ""),
            ("Afucosylation %", "afucosylation_pct", "afucosylation", "%", ""),
            ("Relative Potency %", "potency_relative_pct", "relative_potency", "%", "80 - 120"),
        ]

        for _label, _val_key, _state_key, _unit, _spec in _cqa_fields:
            _val = _ev.get(_val_key)
            _state = ""
            if _state_key and isinstance(_ev.get(_state_key), dict):
                _state = _ev[_state_key].get("state", "")

            _in_spec = "--"
            if _val is not None and _spec:
                if _spec.startswith("<"):
                    _limit = float(_spec.replace("<", "").strip())
                    _in_spec = "\u2713" if _val < _limit else "\u2717"
                elif _spec.startswith(">"):
                    _limit = float(_spec.replace(">", "").strip())
                    _in_spec = "\u2713" if _val > _limit else "\u2717"
                elif "-" in _spec:
                    _parts = _spec.split("-")
                    _lo, _hi = float(_parts[0].strip()), float(_parts[1].strip())
                    _in_spec = "\u2713" if _lo <= _val <= _hi else "\u2717"

            _cqa_rows.append({
                "CQA": _label,
                "Value": f"{_val:.3f}" if _val is not None else "--",
                "Unit": _unit,
                "Spec Limit": _spec if _spec else "--",
                "Within Spec": _in_spec,
                "State": _state.replace("_", " ").title() if _state else "--",
            })

        # Add stability-specific fields
        for _sk, _sl in [("proposed_shelf_life", "Proposed Shelf Life (months)"),
                          ("max_timepoint_months", "Max Timepoint (months)")]:
            _sv = _ev.get(_sk)
            if _sv is not None:
                _cqa_rows.append({
                    "CQA": _sl, "Value": str(_sv), "Unit": "months",
                    "Spec Limit": "--", "Within Spec": "--", "State": "Present",
                })

        # Add completeness score
        _cs = _ev.get("completeness_score")
        if _cs is not None:
            _cqa_rows.append({
                "CQA": "Section Completeness", "Value": f"{_cs:.1%}", "Unit": "",
                "Spec Limit": ">= 80%", "Within Spec": "\u2713" if _cs >= 0.8 else "\u2717",
                "State": "--",
            })

        if _cqa_rows:
            st.dataframe(pd.DataFrame(_cqa_rows), use_container_width=True, hide_index=True, height=500)
        else:
            st.info("No CQA data extracted.")
    elif report:
        attrs = report.get("attribute_results", [])
        if attrs:
            rows = []
            for ar in attrs:
                # UI-2: Spec compliance column
                _spec = ar.get("specification", "")
                _in_spec = ""
                if _spec:
                    _in_spec = _check_spec_status(ar["post_value"], _spec)
                rows.append({
                    "Attribute": ar["name"],
                    "Category": ar["category"],
                    "Pre": ar["pre_value"],
                    "Post": ar["post_value"],
                    "Unit": ar["unit"],
                    "Spec": _spec if _spec else "--",
                    "In Spec": _in_spec if _in_spec else "--",
                    "Delta%": f"{ar['delta_pct']:+.1f}",
                    "Score": f"{ar['score'] * 100:.0f}",
                    "Concern": ar["concern"],
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=500)
        else:
            st.info("No attribute data in report.")
    else:
        st.info("No data to display.")

# =========================================================================
# RIGHT COLUMN: Decision Panel (P6 — 6 sections, two-axis verdict)
# =========================================================================
with right_col:
    st.markdown("### Decision Panel")

    # Extract P5 two-axis fields (INVARIANT V-1: use analytical_conclusion/package_posture)
    analytical_conclusion = overview.get("analytical_conclusion", judgment.get("analytical_conclusion", "insufficient_evidence"))
    package_posture = overview.get("package_posture", judgment.get("package_posture", "defer"))
    posture_rationale = overview.get("posture_rationale", judgment.get("posture_rationale", ""))
    confidence_breakdown = overview.get("confidence_breakdown", {})

    # Fix 5: Type-aware labels for the verdict card
    _panel_doc_type = st.session_state.get("detected_doc_type", "COMPARABILITY")
    _TYPE_AXIS_LABELS = {
        "COMPARABILITY": ("ANALYTICAL CONCLUSION", "PACKAGE POSTURE"),
        "CHARACTERIZATION": ("COMPLETENESS LEVEL", "SUBMISSION READINESS"),
        "STABILITY": ("SHELF-LIFE SUPPORT", "STORAGE CLAIM ADEQUACY"),
        "ANALYTICAL_METHOD": ("ICH Q2 COVERAGE", "METHOD SUITABILITY"),
    }
    _axis1_label, _axis2_label = _TYPE_AXIS_LABELS.get(_panel_doc_type, ("ASSESSMENT", "STATUS"))

    # Extended color maps for non-comparability verdicts
    _VERDICT_COLOR_MAP = {
        **ANALYTICAL_COLORS,
        "adequate": "#28a745", "gaps identified": "#ffc107", "insufficient": "#dc3545",
        "ready": "#28a745", "needs data": "#ffc107", "not ready": "#dc3545",
        "sufficient": "#28a745", "extrapolated": "#ffc107",
        "complete": "#28a745", "partial": "#ffc107", "inadequate": "#dc3545",
        "assessed": "#17a2b8", "review required": "#6c757d",
        "assessment complete": "#17a2b8",
    }
    _POSTURE_COLOR_MAP = {**POSTURE_COLORS, **_VERDICT_COLOR_MAP}

    # -----------------------------------------------------------------
    # Section A: Final Decision (verdict card + confidence + rationale)
    # -----------------------------------------------------------------
    with st.expander("Final Decision", expanded=True):
        # P6-A: Two-axis verdict card — Row 1
        ac_color = _VERDICT_COLOR_MAP.get(analytical_conclusion, _VERDICT_COLOR_MAP.get(analytical_conclusion.lower(), "#6c757d"))
        ac_label = analytical_conclusion.replace("_", " ").title()
        pp_color = _POSTURE_COLOR_MAP.get(package_posture, _POSTURE_COLOR_MAP.get(package_posture.lower(), "#6c757d"))
        pp_label = package_posture.replace("_", " ").title()

        # Verdict cards per DESIGN.md: muted tint bg, colored left-border, dark text
        _tint_map = {"#28a745":"#defbe6","#ff9800":"#fdf6dd","#dc3545":"#fff1f1","#17a2b8":"#edf5ff","#ffc107":"#fdf6dd","#6c757d":"#f4f4f4"}
        _ac_bg = _tint_map.get(ac_color, "#f4f4f4")
        _pp_bg = _tint_map.get(pp_color, "#f4f4f4")
        st.markdown(f"""
        <div style="display:flex;gap:8px;margin-bottom:8px;">
            <div style="flex:1;background:{_ac_bg};padding:12px 16px;border-left:4px solid {ac_color};">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#525252;font-weight:600;">{_axis1_label}</div>
                <div style="font-size:16px;font-weight:600;color:#161616;margin-top:4px;">{ac_label}</div>
            </div>
            <div style="flex:1;background:{_pp_bg};padding:12px 16px;border-left:4px solid {pp_color};">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#525252;font-weight:600;">{_axis2_label}</div>
                <div style="font-size:16px;font-weight:600;color:#161616;margin-top:4px;">{pp_label}</div>
            </div>
        </div>
        <div style="color:#6f6f6f;font-size:12px;font-style:italic;margin-bottom:8px;">
            For decision support only. Not regulatory advice.
        </div>
        """, unsafe_allow_html=True)

        # Row 3: Posture rationale highlighted card
        if posture_rationale:
            _rationale_bg = {"ready_to_file": "#d4edda", "needs_supplement": "#fff3cd", "not_ready": "#f8d7da"}.get(package_posture, "#e2e3e5")
            _rationale_fg = {"ready_to_file": "#155724", "needs_supplement": "#856404", "not_ready": "#721c24"}.get(package_posture, "#383d41")
            st.markdown(f"""<div style="background-color: {_rationale_bg}; color: {_rationale_fg}; padding: 0.6rem 1rem; border-radius: 0.4rem; margin-bottom: 0.5rem; font-size: 0.95rem;">
{posture_rationale}
</div>""", unsafe_allow_html=True)

        # ---------------------------------------------------------------
        # Three-State Evidence Panel: PRESENT (green), UNCERTAIN (amber),
        # CONFIRMED_ABSENT (red). Separate Evidence Gaps from Uncertainties.
        # ---------------------------------------------------------------
        _ev_data = overview.get("extracted_evidence", {})

        # Build three-state CQA summary from evidence data
        _three_state_fields = {
            "HMW %": _ev_data.get("hmw", {}),
            "Main Charge Peak %": _ev_data.get("main_charge_peak", {}),
            "Afucosylation %": _ev_data.get("afucosylation", {}),
            "Relative Potency %": _ev_data.get("relative_potency", {}),
        }
        _present_items = []
        _uncertain_items = []
        _absent_items = []
        for _fname, _fdata in _three_state_fields.items():
            if isinstance(_fdata, dict):
                _state = _fdata.get("state", "")
                _val = _fdata.get("value")
                _reason = _fdata.get("uncertainty_reason", "")
                if _state == "present":
                    _present_items.append((_fname, _val))
                elif _state == "uncertain":
                    _uncertain_items.append((_fname, _reason))
                elif _state == "confirmed_absent":
                    _absent_items.append((_fname,))

        if _present_items or _uncertain_items or _absent_items:
            with st.expander("Evidence Gaps & Extraction Status", expanded=True):
                for _name, _val in _present_items:
                    st.markdown(
                        f'<div style="background:#defbe6;color:#0e6027;padding:12px 16px;'
                        f'border-left:4px solid #24a148;margin-bottom:8px;font-size:14px;">'
                        f'<strong>{_name}</strong>: {_val:.2f}</div>',
                        unsafe_allow_html=True,
                    )
                for _name, _reason in _uncertain_items:
                    _display_reason = _reason.replace("_", " ") if _reason else "Could not extract — verify manually"
                    st.markdown(
                        f'<div style="background:#fdf6dd;color:#735c0f;padding:12px 16px;'
                        f'border-left:4px solid #f1c21b;margin-bottom:8px;font-size:14px;">'
                        f'<strong>{_name}</strong>: {_display_reason}</div>',
                        unsafe_allow_html=True,
                    )
                for (_name,) in _absent_items:
                    st.markdown(
                        f'<div style="background:#fff1f1;color:#750e13;padding:12px 16px;'
                        f'border-left:4px solid #da1e28;margin-bottom:8px;font-size:14px;">'
                        f'<strong>{_name}</strong>: Not found in document</div>',
                        unsafe_allow_html=True,
                    )

        # Critical gaps (separate panel)
        _critical_gaps = _ev_data.get("critical_gaps", [])
        if _critical_gaps:
            st.markdown("##### Critical Gaps")
            for _gap in _critical_gaps:
                st.markdown(
                    f'<div style="background:#f8d7da;color:#721c24;padding:0.4rem 0.8rem;'
                    f'border-radius:0.3rem;margin-bottom:0.3rem;font-size:0.9rem;">'
                    f'&#10060; {_gap}</div>',
                    unsafe_allow_html=True,
                )

        # Extraction uncertainties (collapsed by default)
        _uncertainties = _ev_data.get("extraction_uncertainties", [])
        if _uncertainties:
            with st.expander("Extraction Uncertainties", expanded=False):
                for _unc in _uncertainties:
                    _parts = _unc.split(":", 1)
                    _field = _parts[0].strip() if len(_parts) > 1 else ""
                    _detail = _parts[1].strip() if len(_parts) > 1 else _unc
                    st.markdown(
                        f'<div style="background:#fff3cd;color:#856404;padding:0.4rem 0.8rem;'
                        f'border-radius:0.3rem;margin-bottom:0.3rem;font-size:0.9rem;">'
                        f'&#9888; <strong>{_field}</strong>: {_detail.replace("_", " ")}</div>'
                        if _field else
                        f'<div style="background:#fff3cd;color:#856404;padding:0.4rem 0.8rem;'
                        f'border-radius:0.3rem;margin-bottom:0.3rem;font-size:0.9rem;">'
                        f'&#9888; {_unc}</div>',
                        unsafe_allow_html=True,
                    )

        # Predicted Reviewer Concerns (expanded) with source cross-linking
        _reviewer_concerns = _ev_data.get("reviewer_concerns", [])
        if _reviewer_concerns:
            with st.expander("Predicted Reviewer Questions", expanded=True):
                for _idx, _rc in enumerate(_reviewer_concerns, 1):
                    if "CRITICAL" in _rc:
                        _badge = '<span style="background:#dc3545;color:white;padding:0.1rem 0.4rem;border-radius:0.2rem;font-size:0.75rem;margin-right:0.4rem;">CRITICAL</span>'
                    elif "may" in _rc.lower() or "could" in _rc.lower():
                        _badge = '<span style="background:#ffc107;color:#212529;padding:0.1rem 0.4rem;border-radius:0.2rem;font-size:0.75rem;margin-right:0.4rem;">MAJOR</span>'
                    else:
                        _badge = '<span style="background:#6c757d;color:white;padding:0.1rem 0.4rem;border-radius:0.2rem;font-size:0.75rem;margin-right:0.4rem;">MINOR</span>'
                    # Extract keywords for document cross-linking
                    _rq_keywords = []
                    for _kw in ["potency", "HOS", "structure", "glycosylation", "aggregation",
                                "charge", "purity", "reference standard", "stability",
                                "method", "OOS", "shelf life", "afucosylation"]:
                        if _kw.lower() in _rc.lower():
                            _rq_keywords.append(_kw)
                    _source_hint = ""
                    if _rq_keywords:
                        _source_hint = (
                            f'<div style="font-size:0.75rem;color:#666;margin-top:0.2rem;">'
                            f'Related sections: {", ".join(_rq_keywords)} '
                            f'&mdash; check Document Preview tab for source context</div>'
                        )
                    st.markdown(
                        f'<div style="background:#f0f0f0;border-left:4px solid '
                        f'{"#dc3545" if "CRITICAL" in _rc else "#ffc107"};'
                        f'padding:0.4rem 0.8rem;margin-bottom:0.3rem;font-size:0.9rem;">'
                        f'{_badge}{_idx}. {_rc}{_source_hint}</div>',
                        unsafe_allow_html=True,
                    )
                    # Jump-to-source button
                    if _rq_keywords:
                        if st.button(f"Highlight in preview: {_rq_keywords[0]}", key=f"rq_jump_{_idx}"):
                            st.session_state.selected_anchor = f"docx-section-{_idx}"

        # P6-B: Confidence Display with 3 Components
        ac_conf = confidence_breakdown.get("analytical_confidence", 0.0)
        pr_conf = confidence_breakdown.get("package_readiness", 0.0)
        ec_conf = confidence_breakdown.get("evidence_completeness", 0.0)
        deriv_summary = confidence_breakdown.get("derivation_summary", "")

        cols = st.columns(3)
        cols[0].metric("Analytical", f"{ac_conf:.0%}")
        cols[1].metric("Package Readiness", f"{pr_conf:.0%}")
        cols[2].metric("Evidence Completeness", f"{ec_conf:.0%}")
        if deriv_summary:
            st.caption(deriv_summary)

        # Abstain flag
        jc_abstain = judgment.get("abstain_flag")
        if jc_abstain:
            st.warning("System abstained from judgment -- insufficient evidence.")

        # Key finding
        key_finding = judgment.get("key_finding", "")
        if key_finding:
            st.caption(key_finding)

        # Decision Rule Trace (collapsible within this section)
        rule_ids = judgment.get("decision_rule_ids")
        if rule_ids is not None:
            st.markdown("**Decision Rule Trace:**")
            for rule_id in rule_ids:
                st.markdown(f"- `{rule_id}`")

    # -----------------------------------------------------------------
    # Section B: Blocking Issues / Decision Drivers (top 3-5)
    # -----------------------------------------------------------------
    blocking_clusters = overview.get("blocking_clusters")
    has_blocking = blocking_clusters is not None and len(blocking_clusters) > 0

    with st.expander("Blocking Issues / Decision Drivers", expanded=has_blocking):
        if blocking_clusters is not None and len(blocking_clusters) > 0:
            # P10-A: Resolve what-would-change lookup for counterfactual enrichment
            _wwc_map = {}
            _wwc_list = overview.get("what_would_change") or []
            for _wc in _wwc_list:
                _wwc_map[_wc.get("cluster_id", "")] = _wc

            for bc in blocking_clusters[:5]:
                concern = bc.get("concern_level", "unknown")
                cluster_id = bc.get("category", bc.get("cluster_id", "Unknown"))
                if concern in ("critical", "major"):
                    card_class = "danger-card"
                else:
                    card_class = "warning-card"

                # P6-D + P9-D: left-right panel linkage — clicking sets selected_cluster + anchor
                if st.button(f"Highlight: {cluster_id}", key=f"cluster_btn_{cluster_id}"):
                    st.session_state.selected_cluster = cluster_id
                    st.session_state.selected_anchor = cluster_id

                # P10-A: Enriched blocking issue display
                # 1. Trigger facts from evidence_trace
                evidence_trace = bc.get("evidence_trace", {})
                trigger_facts = evidence_trace.get("trigger_facts", [])
                trigger_html = ""
                if trigger_facts:
                    facts_str = "; ".join(str(f) for f in trigger_facts[:4])
                    trigger_html = f"<br><strong>Trigger facts:</strong> {facts_str}"

                # Also show specific attribute values/deltas if available
                attr_deltas = bc.get("attribute_deltas", [])
                if attr_deltas:
                    delta_parts = []
                    for ad in attr_deltas[:3]:
                        if isinstance(ad, dict):
                            delta_parts.append(
                                f"{ad.get('name', '?')}: {ad.get('delta_pct', 0):+.1f}%"
                            )
                        else:
                            delta_parts.append(str(ad))
                    if delta_parts:
                        trigger_html += f"<br><strong>Key deltas:</strong> {', '.join(delta_parts)}"

                # 2. Rule-specific evidence basis
                rule_basis = bc.get("rule_evidence_basis", bc.get("evidence_basis", ""))
                rule_html = ""
                if rule_basis:
                    rule_html = f"<br><strong>Why this rule fired:</strong> {rule_basis}"

                # 3. Counterfactual: what resolves it
                counterfactual_html = ""
                wwc_entry = _wwc_map.get(cluster_id, {})
                if_resolved = wwc_entry.get("if_gap_resolved", "")
                verdict_would_become = wwc_entry.get("verdict_would_become", "")
                if if_resolved:
                    counterfactual_html = f"<br><strong>To resolve:</strong> {if_resolved}"
                    if verdict_would_become:
                        counterfactual_html += f" (verdict would become: {verdict_would_become})"

                st.markdown(f"""
                <div class="{card_class}">
                <strong>{cluster_id}</strong> &mdash;
                {bc.get('risk_semantics', '')}
                <br>{bc.get('reason', '')}
                {trigger_html}
                {rule_html}
                {counterfactual_html}
                <br><em>Concern: {concern}</em>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.success("No blocking clusters detected.")

    # -----------------------------------------------------------------
    # Section C: Required Evidence & Recommended Actions — DEFAULT EXPANDED
    # -----------------------------------------------------------------
    with st.expander("Required Evidence & Recommended Actions", expanded=True):
        if gaps_data is not None:
            gaps = gaps_data.get("gaps", [])
            if gaps:
                # Summary metrics
                g_col1, g_col2, g_col3 = st.columns(3)
                with g_col1:
                    st.metric("Total Gaps", gaps_data.get("total_gaps", 0))
                with g_col2:
                    st.metric("Critical", gaps_data.get("critical_count", 0))
                with g_col3:
                    st.metric("High", gaps_data.get("high_count", 0))

                # P10-B: Group gaps by action level (DEFER first, then INVESTIGATE, etc.)
                _action_order = {"DEFER": 0, "INVESTIGATE": 1, "SUPPLEMENT": 2, "MONITOR": 3, "PROCEED": 4}

                def _gap_sort_key(g):
                    action = g.get("action_level", "")
                    sev = g.get("severity", "medium")
                    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(sev, 99)
                    action_rank = _action_order.get(action, 5)
                    return (action_rank, sev_rank)

                sorted_gaps = sorted(gaps, key=_gap_sort_key)

                # P10-B: Collect narrative signal context from report if available
                _report_signals = []
                if report:
                    _report_signals = report.get("_narrative_signals", [])

                current_action_group = None
                for gap in sorted_gaps:
                    severity = gap.get("severity", "medium")
                    action_level = gap.get("action_level", "")

                    # P10-B: Show action-level group header when it changes
                    if action_level and action_level != current_action_group:
                        current_action_group = action_level
                        _action_color = ACTION_COLORS.get(action_level, "#6c757d")
                        st.markdown(f"""
                        <div style="background-color: {_action_color}; color: white; padding: 0.3rem 0.8rem;
                                    border-radius: 0.3rem; margin: 0.8rem 0 0.3rem 0; font-size: 0.85rem;">
                            <strong>{action_level}</strong>
                        </div>
                        """, unsafe_allow_html=True)

                    if severity == "critical":
                        card_css = "danger-card"
                    elif severity == "high":
                        card_css = "warning-card"
                    else:
                        card_css = "metric-card"

                    counterfactual = gap.get("counterfactual_action_if_filled", "")

                    # P10-B: Show specific evidence needed per gap
                    evidence_needed = gap.get("evidence_needed", "")
                    evidence_html = ""
                    if evidence_needed:
                        evidence_html = f"<br><strong>Evidence needed:</strong> {evidence_needed}"

                    # P10-B: Add narrative signal context if related signals exist
                    signal_html = ""
                    gap_attr = gap.get("attribute", "").lower()
                    related_signals = [
                        s for s in _report_signals
                        if isinstance(s, dict) and gap_attr and gap_attr in s.get("context", "").lower()
                    ]
                    if related_signals:
                        sig_parts = [
                            f"{s.get('signal_type', 'signal').upper()}: {s.get('keyword_matched', '')}"
                            for s in related_signals[:2]
                        ]
                        signal_html = (
                            f"<br><span style='background-color: #fd7e14; color: white; padding: 2px 6px; "
                            f"border-radius: 3px; font-size: 0.75rem;'>SIGNAL</span> "
                            f"{'; '.join(sig_parts)}"
                        )

                    st.markdown(f"""
                    <div class="{card_css}">
                    <strong>{gap.get('attribute', 'General')}</strong> ({severity.upper()})
                    <br><strong>What's missing:</strong> {gap.get('why_important', '')}
                    <br><strong>Why it matters:</strong> {gap.get('what_to_collect', '')}
                    {evidence_html}
                    {signal_html}
                    <br><em>If resolved: {counterfactual}</em>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.success("No evidence gaps detected.")
        else:
            st.info("Gap analysis not available.")

    # -----------------------------------------------------------------
    # Section D: What Would Change The Verdict (counterfactual)
    # -----------------------------------------------------------------
    what_would_change = overview.get("what_would_change")
    has_counterfactuals = what_would_change is not None and len(what_would_change) > 0

    with st.expander("What Would Change The Verdict", expanded=has_counterfactuals):
        if has_counterfactuals:
            for wc in what_would_change:
                st.markdown(f"""
                <div class="info-card">
                <strong>Cluster:</strong> {wc.get('cluster_id', 'N/A')}<br>
                <strong>Current Gap:</strong> {wc.get('current_gap', 'N/A')}<br>
                <strong>If Resolved:</strong> {wc.get('if_gap_resolved', 'N/A')}<br>
                <strong>Verdict Would Become:</strong> {wc.get('verdict_would_become', 'N/A')}
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No counterfactual scenarios available.")

    # -----------------------------------------------------------------
    # Section E: Reviewer Questions (collapsed, grouped by cluster)
    # -----------------------------------------------------------------
    with st.expander("Reviewer Questions", expanded=False):
        reviewer_risk = overview.get("reviewer_risk", {})
        questions = reviewer_risk.get("predicted_questions", [])

        if questions:
            # Group by affected attributes (cluster proxy)
            from collections import defaultdict
            clustered_qs = defaultdict(list)
            for q in questions:
                affected = q.get("affected_attributes", [])
                cluster_key = ", ".join(affected) if affected else "General"
                clustered_qs[cluster_key].append(q)

            for cluster_key, cluster_qs in clustered_qs.items():
                st.markdown(f"**{cluster_key}**")
                for q in cluster_qs:
                    q_text = q.get("question", "")
                    impact = q.get("impact", "medium")
                    is_primary = q.get("is_primary", False)

                    # Source type badge
                    affected = q.get("affected_attributes", [])
                    precedent = q.get("precedent", "")
                    if precedent:
                        source_badge = "Precedent-based"
                    elif affected:
                        source_badge = "Attribute-driven"
                    else:
                        source_badge = "Computed"

                    # Confidence level
                    prob = q.get("probability", 0.5)
                    if prob >= 0.85:
                        conf_label = "HIGH"
                        conf_color = "#dc3545"
                    elif prob >= 0.60:
                        conf_label = "MODERATE"
                        conf_color = "#ff9800"
                    else:
                        conf_label = "LOW"
                        conf_color = "#6c757d"

                    impact_color = "#dc3545" if impact == "high" else "#ff9800" if impact == "medium" else "#28a745"

                    st.markdown(f"""
                    <div class="metric-card" style="border-left-color: {impact_color};">
                    <span style="background-color: {conf_color}; color: white; padding: 2px 8px;
                                 border-radius: 3px; font-size: 0.75rem;">{conf_label}</span>
                    <span style="background-color: #17a2b8; color: white; padding: 2px 8px;
                                 border-radius: 3px; font-size: 0.75rem; margin-left: 4px;">{source_badge}</span>
                    {"<span style='background-color: #6f42c1; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.75rem; margin-left: 4px;'>PRIMARY</span>" if is_primary else ""}
                    <br><strong style="margin-top: 0.5rem; display: inline-block;">{q_text}</strong>
                    </div>
                    """, unsafe_allow_html=True)

                    if precedent:
                        st.caption(f"Precedent: {precedent}")
        else:
            st.info("No predicted questions.")

    # -----------------------------------------------------------------
    # Section F: Full Attribute Assessment (collapsed, supporting detail)
    # -----------------------------------------------------------------
    with st.expander("Full Attribute Assessment", expanded=False):
        critical_attrs = overview.get("critical_attributes", [])
        if critical_attrs:
            # P6-D: Highlight rows linked to selected cluster
            selected_cluster = st.session_state.get("selected_cluster")

            # Summary row per attribute
            rows = []
            for attr in critical_attrs:
                rows.append({
                    "Attribute": attr["name"],
                    "Score": f"{attr.get('score', 0):.0f}/100",
                    "Concern": attr.get("action", ""),
                    "CQA": "Yes" if attr.get("is_cqa") else "",
                    "Action": attr.get("action", ""),
                })
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )

            # P6-F: Attribute Detail as Traceability Surface
            for attr in critical_attrs:
                attr_name = attr["name"]
                detail = backend.get_attribute_detail(st.session_state.case_id, attr_name)
                if detail is not None:
                    # Highlight if linked to selected cluster
                    highlight = ""
                    if selected_cluster and selected_cluster.lower() in attr.get("category", "").lower():
                        highlight = " style='border: 2px solid #007bff; border-radius: 0.5rem; padding: 0.5rem;'"

                    with st.expander(f"{attr_name} -- Traceability"):
                        if highlight:
                            st.markdown(f"<div{highlight}>", unsafe_allow_html=True)

                        # P6-F: Restructured detail
                        # 1. Scientific status
                        concern = detail.get("concern", "N/A")
                        comparable = detail.get("comparable", False)
                        status_label = "Comparable" if comparable else f"Flagged ({concern})"
                        st.markdown(f"**Scientific Status:** {status_label}")

                        # 2. Decision effect
                        action = detail.get("action", concern)
                        st.markdown(f"**Decision Effect:** {action}")

                        # 3. Evidence basis (score_breakdown formula)
                        score = detail.get("score", 0)
                        delta = detail.get("delta_pct", 0)
                        uncertainty = detail.get("uncertainty", 0)
                        st.markdown(
                            f"**Evidence Basis:** Score={score:.0f}/100, "
                            f"Delta={delta:+.1f}%, Uncertainty={uncertainty:.1f}%"
                        )

                        # 4. Missing evidence
                        next_ev = detail.get("next_best_evidence", "")
                        if next_ev:
                            st.markdown(f"**Missing Evidence:** {next_ev}")

                        # 5. Recommended next step
                        reasoning = detail.get("action_with_reasoning", "")
                        if reasoning:
                            st.markdown(f"**Recommended Next Step:** {reasoning}")

                        # 6. P6-E: Regulatory basis (compressed refs)
                        reg_ref = detail.get("regulatory_reference", "")
                        if reg_ref:
                            # Compress: show primary as bold tag, full in expander
                            refs = [r.strip() for r in reg_ref.split(";") if r.strip()]
                            if refs:
                                primary = refs[0]
                                supporting = refs[1:] if len(refs) > 1 else []
                                tags_html = f'<span style="background-color: #343a40; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.8rem; font-weight: bold;">{primary}</span>'
                                for s in supporting:
                                    tags_html += f' <span style="background-color: #6c757d; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem;">{s}</span>'
                                st.markdown(f"**Regulatory Basis:** {tags_html}", unsafe_allow_html=True)
                                if len(refs) > 2:
                                    with st.expander("Full citations"):
                                        for r in refs:
                                            st.markdown(f"- {r}")

                        # Evidence chain (compressed)
                        evidence_chain = detail.get("provenance", [])
                        if evidence_chain:
                            with st.expander("Evidence chain"):
                                for ev in evidence_chain:
                                    if isinstance(ev, dict):
                                        st.markdown(f"- {ev.get('source_id', '')} ({ev.get('source_type', '')})")
                                    else:
                                        st.markdown(f"- {ev}")

                        if highlight:
                            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.info("No attributes to display.")


# =========================================================================
# BOTTOM: Export (DOCX + CSV only -- P9-F: include UserOverride history)
# =========================================================================
st.markdown("---")
st.markdown("### Export")

export_col1, export_col2 = st.columns(2)

with export_col1:
    # DOCX Export — supports both comparability pipeline and non-comparability ingestion
    _doc_type = ""
    if st.session_state.docx_ingestion_result is not None:
        _ingestion = st.session_state.docx_ingestion_result
        _doc_type = getattr(_ingestion.document_classification, "document_type", "")

    if report is None and overview is None:
        st.info("No assessment report available for export.")
    elif report is not None and report.get("attribute_results"):
        # Comparability pipeline DOCX export
        try:
            from reports.comparability_report import generate_comparability_report
            from pipelines.schemas import ComparabilityReport as CR, AttributeResult as AR

            if st.button("Export DOCX", type="primary", use_container_width=True):
                with st.spinner("Generating ICH Q5E report..."):
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

                    case_obj = backend.get_case(st.session_state.case_id)
                    product_name = case_obj["product_name"] if case_obj else "report"

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

                    out_path = f"/tmp/{product_name.replace(' ', '_')}_comparability_report.docx"
                    generate_comparability_report(report_obj, out_path)

                    with open(out_path, "rb") as f:
                        docx_bytes = f.read()

                    st.download_button(
                        label="Download DOCX",
                        data=docx_bytes,
                        file_name=Path(out_path).name,
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="docx_download",
                    )
                    st.success("DOCX report generated.")
        except ImportError:
            if st.button("Export DOCX", type="primary", use_container_width=True, key="docx_unavail"):
                st.warning("DOCX export requires the `python-docx` package. Install with: `pip install python-docx`")
    elif overview is not None:
        # BUG-002 fix: Non-comparability document export (CHARACTERIZATION, STABILITY, etc.)
        if st.button("Export Analysis DOCX", type="primary", use_container_width=True, key="export_analysis_docx"):
            _ev = overview.get("extracted_evidence", {})
            _analysis_lines = []
            _analysis_lines.append(f"Document Type: {_doc_type}")
            _analysis_lines.append(f"Classification Confidence: {overview.get('classification_confidence', 'N/A')}")
            _analysis_lines.append(f"Completeness Score: {_ev.get('completeness_score', 'N/A')}")
            _analysis_lines.append("")
            _analysis_lines.append("=== Sections Found ===")
            for _s in _ev.get("sections_found", []):
                _analysis_lines.append(f"  + {_s}")
            _analysis_lines.append("")
            _analysis_lines.append("=== Sections Missing ===")
            for _s in _ev.get("sections_missing", []):
                _analysis_lines.append(f"  - {_s}")
            _analysis_lines.append("")
            _analysis_lines.append("=== Key CQA Values ===")
            for _k in ["hmw_pct", "main_charge_peak_pct", "afucosylation_pct",
                        "potency_relative_pct", "acidic_variants_pct", "basic_variants_pct"]:
                _v = _ev.get(_k)
                _analysis_lines.append(f"  {_k}: {f'{_v:.3f}' if _v is not None else 'Not extracted'}")
            _analysis_lines.append("")
            _analysis_lines.append("=== Critical Gaps ===")
            for _g in _ev.get("critical_gaps", []):
                _analysis_lines.append(f"  ! {_g}")
            if not _ev.get("critical_gaps"):
                _analysis_lines.append("  (none)")
            _analysis_lines.append("")
            _analysis_lines.append("=== Extraction Uncertainties ===")
            for _u in _ev.get("extraction_uncertainties", []):
                _analysis_lines.append(f"  ? {_u}")
            _analysis_lines.append("")
            _analysis_lines.append("=== Reviewer Concerns ===")
            for _rc in _ev.get("reviewer_concerns", []):
                _analysis_lines.append(f"  > {_rc}")
            _analysis_lines.append("")
            _analysis_lines.append("---")
            _analysis_lines.append("For decision support only. Not regulatory advice.")

            _txt_content = "\n".join(_analysis_lines)
            st.download_button(
                label="Download Analysis Report",
                data=_txt_content,
                file_name=f"{_doc_type.lower()}_analysis_report.txt",
                mime="text/plain",
                key="analysis_txt_download",
            )
            st.success("Analysis report exported.")

with export_col2:
    # CSV Export (P9-F: includes UserOverride history in export)
    if st.button("Export CSV", type="secondary", use_container_width=True):
        attrs = report.get("attribute_results", []) if report is not None else []
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
            case_obj = backend.get_case(st.session_state.case_id)
            product_name = case_obj["product_name"] if case_obj else "report"

            st.download_button(
                label="Download CSV",
                data=csv_str,
                file_name=f"{product_name.replace(' ', '_')}_attributes.csv",
                mime="text/csv",
                key="csv_download",
            )
            st.success("CSV export ready.")
        else:
            st.warning("No attribute data to export.")

# P9-F: UserOverride history export
_user_overrides = st.session_state.get("docx_user_overrides", [])
if _user_overrides:
    st.markdown("#### User Override History")
    _override_df = pd.DataFrame(_user_overrides)
    st.dataframe(_override_df, use_container_width=True, hide_index=True)

    _override_csv = io.StringIO()
    _override_fields = [
        "override_id", "attribute_name", "field_name",
        "original_value", "corrected_value", "corrected_by", "reason", "timestamp",
    ]
    _ov_writer = csv.DictWriter(_override_csv, fieldnames=_override_fields)
    _ov_writer.writeheader()
    for _ov in _user_overrides:
        _ov_writer.writerow({k: _ov.get(k, "") for k in _override_fields})

    st.download_button(
        label="Download Override History (CSV)",
        data=_override_csv.getvalue(),
        file_name="user_override_history.csv",
        mime="text/csv",
        key="override_csv_download",
    )

# =========================================================================
# Audit Trail Panel
# =========================================================================
if _audit.count > 0:
    with st.expander(f"Audit Trail ({_audit.count} events)", expanded=False):
        st.dataframe(pd.DataFrame(_audit.to_records()), use_container_width=True, hide_index=True)
        _acol1, _acol2 = st.columns(2)
        with _acol1:
            st.download_button(
                "Export Audit Trail (CSV)",
                data=_audit.to_csv(),
                file_name=f"audit_trail_{_audit.session_id}.csv",
                mime="text/csv",
                key="audit_csv_dl",
            )
        with _acol2:
            st.download_button(
                "Export Audit Trail (JSON)",
                data=_audit.to_json(),
                file_name=f"audit_trail_{_audit.session_id}.json",
                mime="application/json",
                key="audit_json_dl",
            )

# =========================================================================
# UI-3: Disclaimer footer -- shown on every analysis page
# =========================================================================
st.markdown("""
<div style="margin-top:2rem;padding:1rem 1.5rem;background:#f8fafc;border-top:1px solid #e2e8f0;
            text-align:center;font-size:0.78rem;color:#64748b;line-height:1.6;">
    <strong style="color:#475569;">CMC Decision Workspace</strong> &middot; v1.0-mvp<br>
    For decision support only. Not regulatory advice.
    Verify all findings with source documents and a qualified regulatory professional.<br>
    <span style="opacity:0.7;">Single-document analysis. Cross-document consistency and live regulatory lookup deferred to future release.</span>
</div>
""", unsafe_allow_html=True)
