"""Upload component — handles single file and multi-document package upload."""

import os
import re
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st


def render_upload_section(backend, _audit):
    """Render the upload section. Returns (needs_assessment, active_case_id)."""
    needs_assessment = False
    active_case_id = st.session_state.case_id

    _upload_mode = st.radio(
        "Upload mode",
        ["Single Document", "Multi-Document Package"],
        horizontal=True,
        key="upload_mode",
        label_visibility="collapsed",
    )

    if _upload_mode == "Multi-Document Package":
        needs_assessment, active_case_id = _render_multi_upload(_audit)
        uploaded_file = None
    else:
        st.markdown("#### Upload Document or Data")
        uploaded_file = st.file_uploader(
            'Upload a regulatory document or data file',
            type=['docx', 'pdf', 'csv'],
            help='Supports: DOCX/PDF regulatory reports, CSV attribute tables',
            key="file_uploader",
        )

    if _upload_mode != "Multi-Document Package" and uploaded_file is not None:
        needs_assessment, active_case_id = _handle_single_upload(
            uploaded_file, backend, _audit, needs_assessment, active_case_id,
        )

    return needs_assessment, active_case_id, uploaded_file if _upload_mode != "Multi-Document Package" else None


def _render_multi_upload(_audit):
    """Render multi-document package upload."""
    st.markdown("#### Upload CMC Package (Multiple Documents)")
    uploaded_files = st.file_uploader(
        'Upload multiple regulatory documents for package assessment',
        type=['docx', 'pdf'],
        accept_multiple_files=True,
        help='Upload characterization + stability + analytical method documents together',
        key="multi_file_uploader",
    )

    if uploaded_files and len(uploaded_files) > 0:
        if st.button("Run Package Assessment", type="primary", use_container_width=True, key="run_package_btn"):
            from ingestion import ingest_document as _pkg_ingest
            from services.package_assessor import assess_package, build_package_overview

            _pkg_results = []
            _pkg_names = []
            _progress = st.progress(0, text="Preparing documents...")
            for _file_idx, uf in enumerate(uploaded_files):
                _pct = int((_file_idx / len(uploaded_files)) * 100)
                _progress.progress(_pct, text=f"Analyzing {uf.name} ({_file_idx+1}/{len(uploaded_files)})...")

                _fsize_mb = len(uf.getvalue()) / (1024 * 1024)
                if _fsize_mb > 10:
                    st.caption(f"{uf.name}: {_fsize_mb:.1f} MB — large file, may take longer")

                _tmp = tempfile.NamedTemporaryFile(suffix=Path(uf.name).suffix, delete=False)
                _tmp.write(uf.getvalue())
                _tmp.close()
                try:
                    _r = _pkg_ingest(_tmp.name)
                    if Path(uf.name).suffix.lower() == ".pdf" and hasattr(_r, "parsed_doc") and _r.parsed_doc:
                        _total_chars = sum(len(p.get("text", "")) for p in _r.parsed_doc.get("pages", []))
                        if _total_chars < 100:
                            st.warning(f"{uf.name}: appears to be a scanned PDF. Skipping.")
                            continue
                    _pkg_results.append(_r)
                    _pkg_names.append(uf.name)
                except Exception as _e:
                    _err_msg = str(_e)
                    if "password" in _err_msg.lower() or "corrupt" in _err_msg.lower():
                        st.warning(f"{uf.name}: file may be corrupted or password-protected. Skipping.")
                    else:
                        st.warning(f"{uf.name}: processing failed. Skipping.")

            _progress.progress(100, text="Assessment complete.")

            if _pkg_results:
                _pkg = assess_package(_pkg_results, _pkg_names)
                _pkg_overview = build_package_overview(_pkg)
                st.session_state.package_overview = _pkg_overview
                st.session_state.case_id = _pkg.package_id
                st.session_state.overview_data = None
                st.session_state.report_dict = None
                _audit.log("PACKAGE", f"Package assessment: {len(_pkg_results)} documents",
                           metadata={"verdict": _pkg_overview["package_verdict"]})
                st.rerun()

    return False, st.session_state.case_id


def _handle_single_upload(uploaded_file, backend, _audit, needs_assessment, active_case_id):
    """Handle single file upload (DOCX/PDF/CSV)."""
    _file_ext = Path(uploaded_file.name).suffix.lower()
    _file_id = f"{uploaded_file.name}_{uploaded_file.size}"
    _prev_file_id = st.session_state.get("_last_uploaded_file_id")

    if _file_id == _prev_file_id and st.session_state.get("docx_review_confirmed"):
        pass  # Already processed
    elif _file_ext in (".docx", ".pdf"):
        needs_assessment, active_case_id = _handle_docx_pdf(
            uploaded_file, _file_ext, _file_id, _audit,
        )
    elif _file_ext == ".csv":
        needs_assessment, active_case_id = _handle_csv(uploaded_file, backend)
    else:
        st.error(f"File format **{_file_ext}** is not supported. Please upload a DOCX, PDF, or CSV file.")

    return needs_assessment, active_case_id


def _handle_docx_pdf(uploaded_file, _file_ext, _file_id, _audit):
    """Handle DOCX/PDF upload with ingestion."""
    from ui.app import _cached_ingest  # Import the cached function from main app

    try:
        _audit.log("UPLOAD", f"File uploaded: {uploaded_file.name} ({_file_ext})",
                    document_name=uploaded_file.name)
        with st.spinner(f"Analyzing {uploaded_file.name} -- this may take 30-60 seconds for large PDFs..."):
            ingestion_result, _tmp_path = _cached_ingest(uploaded_file.getvalue(), _file_ext)
        st.session_state.docx_temp_path = _tmp_path
        st.session_state.docx_ingestion_result = ingestion_result
        st.session_state._last_uploaded_file_id = _file_id
        st.session_state.docx_review_confirmed = False
        st.session_state.docx_user_overrides = []

        _doc_classification = getattr(ingestion_result, "document_classification", None)
        if _doc_classification:
            _doc_type = _doc_classification.document_type
            _conf = _doc_classification.confidence
            _type_label = _doc_type.replace("_", " ").title()
            _conf_pct = int(_conf * 100)
            if _conf >= 0.80:
                _bg, _fg = "#defbe6", "#0e6027"
            elif _conf >= 0.50:
                _bg, _fg = "#fdf6dd", "#735c0f"
            else:
                _bg, _fg = "#fff1f1", "#750e13"
            st.markdown(
                f'<div style="background:{_bg};color:{_fg};padding:12px 16px;'
                f'border-left:4px solid {_fg};margin-bottom:8px;">'
                f'<strong>Document Type:</strong> {_type_label}  '
                f'<span style="opacity:0.7;">(Confidence: {_conf_pct}%)</span></div>',
                unsafe_allow_html=True,
            )
            st.session_state.detected_doc_type = _doc_type
            _audit.log("CLASSIFY", f"Classified as {_doc_type} ({_conf_pct}%)",
                        document_name=uploaded_file.name, document_type=_doc_type)
        else:
            st.session_state.detected_doc_type = "UNKNOWN"

        _n_attrs = len(ingestion_result.attributes) if ingestion_result.attributes else 0
        _n_tables = getattr(ingestion_result, "n_tables_found", 0)
        _n_signals = len(ingestion_result.signals) if ingestion_result.signals else 0
        st.success(
            f"{_file_ext.upper().strip('.')} ingested: {_n_attrs} attributes, "
            f"{_n_tables} tables, {_n_signals} signals detected."
        )

        # Scanned PDF check
        if _file_ext == ".pdf" and ingestion_result.parsed_doc:
            _total_text = sum(len(p.get("text", "")) for p in ingestion_result.parsed_doc.get("pages", []))
            if _total_text < 100:
                st.warning("This document appears to be a scanned image. OCR is not supported.")

        # Low confidence warning
        if _doc_classification and _doc_classification.confidence < 0.3:
            st.warning(f"Could not confidently identify document type. Proceeding with generic extraction.")

    except ImportError:
        st.error(f"{_file_ext.upper().strip('.')} ingestion requires the `python-docx` package.")
    except Exception as e:
        _err_msg = str(e)
        if "password" in _err_msg.lower() or "corrupt" in _err_msg.lower():
            st.error("Unable to read file. It may be corrupted or password-protected.")
        else:
            st.error("An error occurred while processing the document.")
            with st.expander("Error details (for support)", expanded=False):
                st.code(f"{type(e).__name__}: {e}")

    return False, st.session_state.case_id


def _handle_csv(uploaded_file, backend):
    """Handle CSV upload."""
    df_uploaded = pd.read_csv(uploaded_file)
    st.session_state.uploaded_df = df_uploaded
    st.session_state.docx_ingestion_result = None
    st.session_state.docx_review_confirmed = False

    from ui.app import _csv_to_batch_data
    batch_data = _csv_to_batch_data(df_uploaded)
    n_attrs = len(batch_data.get("attributes", []))

    st.info(f"Identified as: **CSV Attribute Table** (Confidence: 100%)")
    st.success(f"Detected {n_attrs} attributes from uploaded CSV.")

    case_id = backend.create_case(
        product_name="Uploaded Case",
        molecule_class="mAb",
        change_type="User Upload",
        change_description="Uploaded via CSV",
        batch_data=batch_data,
    )
    st.session_state.case_id = case_id
    return True, case_id
