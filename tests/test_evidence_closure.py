"""
Tests for the evidence_closure module.

Covers: empty input, single finding, multiple findings with dependencies,
and ClosureReport schema validation.
"""

import sys
from pathlib import Path

# Ensure repo root is importable
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.evidence_closure import (
    analyze,
    ClosureFinding,
    ClosureReport,
    ClosureStatus,
    FindingRecord,
    ResolutionNote,
)


# ------------------------------------------------------------------
# test_empty_input
# ------------------------------------------------------------------

class TestEmptyInput:
    def test_no_findings_returns_empty_report(self):
        report = analyze([])
        assert isinstance(report, ClosureReport)
        assert report.status == "completed"
        assert report.findings == []
        assert report.covered == []
        assert report.uncovered_gaps == []
        assert report.dependency_graph == {}
        assert report.priority_actions == []
        assert report.human_review_required is False
        assert report.confidence["score"] == 1.0

    def test_empty_closure_summary_counts(self):
        report = analyze([])
        for key in ("resolved", "partially_resolved", "unresolved", "conflicting", "blocked"):
            assert report.closure_summary[key] == 0


# ------------------------------------------------------------------
# test_single_finding
# ------------------------------------------------------------------

class TestSingleFinding:
    def test_single_unresolved_finding(self):
        findings = [
            FindingRecord(
                text="Manufacturer address not provided",
                category="gap",
                severity="clarification",
                source="module3-reviewer",
            ),
        ]
        report = analyze(findings)
        assert report.status == "completed"
        assert len(report.findings) == 1

        f = report.findings[0]
        assert f.closure_status == ClosureStatus.UNRESOLVED.value
        assert f.issue_id == "EC-001"
        assert "UNRESOLVED" in f.description
        assert len(f.missing_evidence) > 0

        assert report.uncovered_gaps == ["EC-001"]
        assert report.covered == []
        assert report.closure_summary["unresolved"] == 1

    def test_single_finding_with_resolution(self):
        findings = [
            FindingRecord(
                text="Manufacturer address not provided",
                category="gap",
                severity="clarification",
                source="module3-reviewer",
            ),
        ]
        notes = [
            ResolutionNote(
                matches="manufacturer address",
                resolution="Added manufacturer address to Section S.2.1 per reviewer request. Address: BioPharm GmbH, Basel, Switzerland.",
            ),
        ]
        report = analyze(findings, resolution_notes=notes)
        assert len(report.findings) == 1

        f = report.findings[0]
        assert f.closure_status == ClosureStatus.RESOLVED.value
        assert "RESOLVED" in f.description
        assert report.covered == ["EC-001"]
        assert report.uncovered_gaps == []
        assert report.closure_summary["resolved"] == 1

    def test_single_finding_with_brief_resolution_is_partial(self):
        """A resolution shorter than 20 chars should be 'partially_resolved'."""
        findings = [
            FindingRecord(text="Missing data", category="gap", severity="warning"),
        ]
        notes = [
            ResolutionNote(matches="missing data", resolution="Added."),
        ]
        report = analyze(findings, resolution_notes=notes)
        f = report.findings[0]
        assert f.closure_status == ClosureStatus.PARTIALLY_RESOLVED.value

    def test_conflicting_status_override(self):
        findings = [
            FindingRecord(
                text="Viral inactivation hold time discrepancy",
                category="consistency",
                severity="major",
            ),
        ]
        notes = [
            ResolutionNote(
                matches="viral inactivation",
                resolution="Corrected to 60 min",
                status="conflicting",
            ),
        ]
        report = analyze(findings, resolution_notes=notes)
        f = report.findings[0]
        assert f.closure_status == ClosureStatus.CONFLICTING.value
        assert report.human_review_required is True


# ------------------------------------------------------------------
# test_multiple_findings_with_dependencies
# ------------------------------------------------------------------

class TestMultipleFindingsWithDependencies:
    def test_category_dependency_creates_blocked_status(self):
        """A gap finding should block a related stability finding."""
        findings = [
            FindingRecord(
                text="No stability data for proposed shelf life",
                category="gap",
                severity="blocker",
                source="module3-reviewer",
            ),
            FindingRecord(
                text="Specification table incomplete -- missing stability-indicating methods",
                category="gap",
                severity="major",
                source="module3-reviewer",
            ),
            FindingRecord(
                text="Degradation trend: purity decrease 6.9% from T0 to T6M",
                category="degradation_signal",
                severity="warning",
                source="cmc-harmonizer",
            ),
        ]
        report = analyze(findings)

        # The degradation_signal should be blocked by the gap findings
        statuses = {f.issue_id: f.closure_status for f in report.findings}

        # Blocker and major gap findings are unresolved
        assert statuses["EC-001"] == ClosureStatus.UNRESOLVED.value
        assert statuses["EC-002"] == ClosureStatus.UNRESOLVED.value

        # Degradation signal should be blocked (gap blocks degradation_signal)
        assert statuses["EC-003"] == ClosureStatus.BLOCKED.value

        # Dependency graph should reflect this
        assert "EC-003" in report.dependency_graph
        assert report.human_review_required is True

    def test_mixed_resolved_and_unresolved(self):
        findings = [
            FindingRecord(
                text="No specification table provided",
                category="gap",
                severity="blocker",
            ),
            FindingRecord(
                text="Development history not provided",
                category="gap",
                severity="clarification",
            ),
        ]
        notes = [
            ResolutionNote(
                matches="development history",
                resolution="Added development history to S.2.6 describing process evolution from pilot to commercial scale.",
            ),
        ]
        report = analyze(findings, resolution_notes=notes)
        statuses = {f.issue_id: f.closure_status for f in report.findings}
        assert statuses["EC-001"] == ClosureStatus.UNRESOLVED.value
        assert statuses["EC-002"] == ClosureStatus.RESOLVED.value
        assert "EC-001" in report.uncovered_gaps
        assert "EC-002" in report.covered

    def test_priority_ordering(self):
        """Findings should be sorted by priority, highest first."""
        findings = [
            FindingRecord(text="Low priority item", severity="info"),
            FindingRecord(text="Critical item", severity="blocker"),
            FindingRecord(text="Medium item", severity="warning"),
        ]
        report = analyze(findings)
        priorities = [f.priority for f in report.findings]
        assert priorities == sorted(priorities, reverse=True)

    def test_oos_finding_triggers_human_review(self):
        findings = [
            FindingRecord(
                text="Non-conforming result: Purity 94.2% below spec >= 95.0%",
                category="non_conforming_result",
                severity="warning",
                source="cmc-harmonizer",
            ),
        ]
        report = analyze(findings)
        assert report.human_review_required is True
        assert any("OOS" in t for t in report.human_review_triggers)


# ------------------------------------------------------------------
# test_closure_report_schema
# ------------------------------------------------------------------

class TestClosureReportSchema:
    def test_report_has_all_required_fields(self):
        report = analyze([
            FindingRecord(text="Test finding", category="gap", severity="major"),
        ])
        assert hasattr(report, "status")
        assert hasattr(report, "findings")
        assert hasattr(report, "closure_summary")
        assert hasattr(report, "covered")
        assert hasattr(report, "uncovered_gaps")
        assert hasattr(report, "dependency_graph")
        assert hasattr(report, "priority_actions")
        assert hasattr(report, "human_review_required")
        assert hasattr(report, "human_review_triggers")
        assert hasattr(report, "confidence")
        assert hasattr(report, "exceptions")

    def test_findings_are_closure_finding_instances(self):
        report = analyze([
            FindingRecord(text="Test", category="gap", severity="warning"),
        ])
        for f in report.findings:
            assert isinstance(f, ClosureFinding)

    def test_closure_summary_keys(self):
        report = analyze([
            FindingRecord(text="Test", severity="info"),
        ])
        expected_keys = {"resolved", "partially_resolved", "unresolved", "conflicting", "blocked"}
        assert set(report.closure_summary.keys()) == expected_keys

    def test_confidence_structure(self):
        report = analyze([
            FindingRecord(text="Test", severity="info"),
        ])
        assert "score" in report.confidence
        assert "qualifier" in report.confidence
        assert isinstance(report.confidence["score"], (int, float))
        assert report.confidence["qualifier"] in ("high", "medium", "low", "unknown")

    def test_priority_actions_non_empty_for_unresolved(self):
        report = analyze([
            FindingRecord(text="Unresolved item", category="gap", severity="major"),
        ])
        assert len(report.priority_actions) > 0
        assert report.priority_actions[0].startswith("[EC-001]")

    def test_exceptions_empty_on_valid_input(self):
        report = analyze([
            FindingRecord(text="Valid input", severity="info"),
        ])
        assert report.exceptions == []
