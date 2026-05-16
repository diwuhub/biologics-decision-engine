# Changelog

## [1.0.0-mvp] - 2026-04-15

### Added
- **Document ingestion pipeline**: DOCX/PDF upload with automatic classification (Characterization, Stability, Analytical Method, Comparability, Unknown)
- **Three-state evidence model**: Present / Uncertain / Confirmed Absent for all CQA fields (HMW%, main charge peak%, afucosylation%, potency%)
- **Lot-aware table interpretation**: Prefers RM lots over PS lots when extracting values from multi-lot tables
- **Attribute name quality gate**: Filters nonsense table extractions (pure-numeric, blocklisted labels)
- **ICH Q6B section detection**: 8-section coverage scoring for characterization reports
- **Stability assessment**: Shelf-life support, OOS/OOT detection, storage condition extraction per ICH Q1A/Q5C
- **Analytical method assessment**: ICH Q2(R2) validation study completeness scoring
- **Gold standard benchmarking**: gold_standard.yaml with 4 real-document benchmarks (NISTmAb, Xbonzy, Certificate, ICH Q14)
- **Value correctness probe**: qa/value_correctness_probe.py comparing extracted values against gold standard within tolerance
- **QA agent**: 5 capabilities (CAP-001 through CAP-006), all passing, 0 drift
- **Three-state evidence gaps panel**: Green (present) / amber (uncertain) / red (absent) visual distinction in UI
- **Spec compliance columns**: CQA data table with Spec Limit, Value, Within Spec columns
- **Predicted Reviewer Questions panel**: Numbered list with CRITICAL/MAJOR/MINOR severity badges
- **Extraction Uncertainties panel**: Collapsed expander with field-level detail
- **Disclaimer**: Visible on every analysis page (top of Decision Panel + footer)
- **Error handling**: Friendly messages for corrupt files, scanned PDFs, unsupported formats, low classification confidence
- **First-time onboarding**: Dismissible welcome banner, sample document dropdown in sidebar
- **Non-comparability DOCX export**: Text-based analysis report for characterization/stability/analytical documents (BUG-002 fix)
- **README.md**: Full user-facing documentation with installation, first-run walkthrough, supported formats, data privacy, disclaimer

### Fixed
- **BUG-002**: DOCX export no longer crashes when user uploads DOCX/PDF (report_dict was None for non-comparability paths)
- **BUG-006**: Attribute name quality gate filters nonsense names ('Rack', 'Homogeneity UV', pure numerics)
- **False potency critical gap**: NISTmAb no longer shows "Missing potency" (three-state refinement for mAb and reference material documents)
- **PS/RM lot confusion**: Table interpreter now classifies lot columns and prefers RM 8671 lots over PS 8670

### Changed
- Pivoted table detection threshold lowered from 2 to 1 (catches single-attribute pivoted tables)
- INVERSE_ATTRIBUTES expanded: charge purity, main species, M1, fucose/fucosylated inverse derivations
- Vision spec updated with value_correctness_passes and false_critical_gaps assertions
- Sidebar renamed from "Developer Tools" to "Example Cases" with real document samples

## [0.1.0] - 2026-03-27

### Added
- Initial scaffold and module structure
- JSON schemas for evidence nodes
- Working MVP engines with demo scripts
- Benchmark cases for testing

### Fixed
- QA audit fixes (schema conformance, scoring logic, input validation)
