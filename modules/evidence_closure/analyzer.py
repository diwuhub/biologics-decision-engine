"""
Evidence Closure Tracker -- Core Analyzer.

Accepts a list of findings and optional resolution notes, then produces a
``ClosureReport`` with closure status, dependency graph, gap analysis, and
prioritised actions.

Extracted from bio-cmc-ai-suite ``apps/evidence-closure-tracker/services/analyzer.py``
with SDK, Streamlit, and orchestrator dependencies removed.
"""

from __future__ import annotations

import re
import sys
import os
from typing import Optional

# Allow importing shared utilities from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from utils.confidence import compute_confidence  # noqa: E402

from .schemas import (
    ClosureFinding,
    ClosureReport,
    ClosureStatus,
    EvidenceDependency,
    FindingRecord,
    ResolutionNote,
    TrackedIssue,
)


# ======================================================================
# Public API
# ======================================================================

def analyze(
    findings: list[FindingRecord],
    resolution_notes: Optional[list[ResolutionNote]] = None,
) -> ClosureReport:
    """Run evidence closure analysis on a list of findings.

    Parameters
    ----------
    findings : list[FindingRecord]
        Upstream findings to evaluate for closure.
    resolution_notes : list[ResolutionNote] or None
        Optional notes that may resolve some findings.

    Returns
    -------
    ClosureReport
        Full closure assessment including covered/uncovered findings,
        dependency graph, and priority actions.
    """
    if not findings:
        return ClosureReport(
            status="completed",
            findings=[],
            closure_summary={
                "resolved": 0,
                "partially_resolved": 0,
                "unresolved": 0,
                "conflicting": 0,
                "blocked": 0,
            },
            covered=[],
            uncovered_gaps=[],
            dependency_graph={},
            priority_actions=[],
            human_review_required=False,
            human_review_triggers=[],
            confidence=compute_confidence(1.0),
            exceptions=[],
        )

    # Step 1: convert input findings to tracked issues
    issues = _build_issues(findings)

    # Step 2: apply resolution notes
    if resolution_notes:
        _apply_resolutions(issues, resolution_notes)

    # Step 3: detect inter-issue dependencies
    _detect_dependencies(issues)

    # Step 4: assess closure status
    _assess_closure(issues)

    # Step 5: build the report
    return _build_report(issues)


# ======================================================================
# Issue construction
# ======================================================================

_SEVERITY_PRIORITY = {
    "blocker": 100,
    "critical": 90,
    "error": 80,
    "major": 60,
    "warning": 40,
    "clarification": 20,
    "info": 0,
}


def _build_issues(findings: list[FindingRecord]) -> list[TrackedIssue]:
    """Convert ``FindingRecord`` inputs into ``TrackedIssue`` objects."""
    issues: list[TrackedIssue] = []
    for idx, f in enumerate(findings, start=1):
        issues.append(TrackedIssue(
            issue_id=f"EC-{idx:03d}",
            source_app=f.source or "unknown",
            source_finding=f.text,
            source_category=f.category,
            source_severity=f.severity,
            source_evidence=f.evidence,
            priority=_SEVERITY_PRIORITY.get(f.severity, 10),
        ))
    return issues


# ======================================================================
# Resolution matching
# ======================================================================

def _apply_resolutions(issues: list[TrackedIssue], notes: list[ResolutionNote]):
    """Match resolution notes to issues and update status."""
    for note in notes:
        target_pattern = note.matches.lower() if note.matches else ""
        for issue in issues:
            matched = False
            if note.issue_id and issue.issue_id == note.issue_id:
                matched = True
            elif target_pattern and target_pattern in issue.source_finding.lower():
                matched = True

            if matched:
                issue.resolution_evidence = note.resolution
                if note.status:
                    issue.closure_status = ClosureStatus(note.status)
                elif note.resolution:
                    issue.closure_status = ClosureStatus.PARTIALLY_RESOLVED


# ======================================================================
# Dependency detection
# ======================================================================

_RELATED_CATEGORIES: dict[frozenset[str], frozenset[str]] = {
    frozenset({"gap", "missing"}): frozenset({"stability", "degradation_signal", "specification"}),
    frozenset({"oos", "non_conforming_result"}): frozenset({"stability", "degradation_signal"}),
    frozenset({"consistency", "entity_contradiction"}): frozenset({"gap", "missing"}),
}

# Words to exclude from keyword overlap matching
_STOPWORDS = frozenset({
    "this", "that", "with", "from", "have", "been",
    "should", "would", "could", "result", "section",
    "finding", "issue", "data", "value", "test",
})


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords (4+ char, no stopwords) from text."""
    return {
        w for w in re.findall(r"[a-z]{4,}", text.lower())
        if w not in _STOPWORDS
    }


def _detect_dependencies(issues: list[TrackedIssue]):
    """Detect dependencies between issues based on content and category."""
    # Pre-compute keywords
    kw_map = {issue.issue_id: _extract_keywords(issue.source_finding) for issue in issues}

    for i, issue_a in enumerate(issues):
        for j, issue_b in enumerate(issues):
            if i >= j:
                continue

            linked = False
            reason = ""

            # Method 1: keyword overlap (3+ shared terms)
            overlap = kw_map[issue_a.issue_id] & kw_map[issue_b.issue_id]
            if len(overlap) >= 3:
                linked = True
                reason = f"Related keywords: {', '.join(sorted(overlap)[:4])}"

            # Method 2: category-based dependency
            if not linked:
                cat_a = issue_a.source_category
                cat_b = issue_b.source_category
                for group_a, group_b in _RELATED_CATEGORIES.items():
                    if cat_a in group_a and cat_b in group_b:
                        linked = True
                        reason = f"Category dependency: {cat_a} blocks {cat_b}"
                        break
                    if cat_b in group_a and cat_a in group_b:
                        linked = True
                        reason = f"Category dependency: {cat_b} blocks {cat_a}"
                        break

            if linked:
                # Higher-priority issue is the dependency target
                if issue_a.priority > issue_b.priority:
                    issue_b.dependencies.append(
                        EvidenceDependency(depends_on=issue_a.issue_id, reason=reason)
                    )
                elif issue_b.priority > issue_a.priority:
                    issue_a.dependencies.append(
                        EvidenceDependency(depends_on=issue_b.issue_id, reason=reason)
                    )


# ======================================================================
# Closure assessment
# ======================================================================

def _assess_closure(issues: list[TrackedIssue]):
    """Assess final closure status for each issue."""
    resolved_ids = {
        i.issue_id for i in issues
        if i.closure_status == ClosureStatus.RESOLVED
    }

    for issue in issues:
        # Already overridden by user
        if issue.closure_status in (ClosureStatus.RESOLVED, ClosureStatus.CONFLICTING):
            if issue.closure_status == ClosureStatus.RESOLVED:
                issue.confidence = compute_confidence(0.95)
            continue

        has_resolution = bool(issue.resolution_evidence)
        has_deps = bool(issue.dependencies)

        # Check if dependencies are resolved
        if has_deps:
            unresolved_deps = [
                d for d in issue.dependencies
                if d.depends_on not in resolved_ids
            ]
            if unresolved_deps:
                issue.closure_status = ClosureStatus.BLOCKED
                issue.missing_evidence = [
                    f"Depends on {d.depends_on} which is not yet resolved"
                    for d in unresolved_deps
                ]
                issue.confidence = compute_confidence(0.8)
                continue

        if has_resolution:
            if len(issue.resolution_evidence) > 20:
                issue.closure_status = ClosureStatus.RESOLVED
                issue.confidence = compute_confidence(0.85)
            else:
                issue.closure_status = ClosureStatus.PARTIALLY_RESOLVED
                issue.missing_evidence = ["Resolution note is very brief -- may need more detail"]
                issue.confidence = compute_confidence(0.5)
        else:
            issue.closure_status = ClosureStatus.UNRESOLVED
            issue.missing_evidence = [_what_evidence_needed(issue)]
            issue.confidence = compute_confidence(0.9)


def _what_evidence_needed(issue: TrackedIssue) -> str:
    """Suggest what evidence is needed to close the issue."""
    cat = issue.source_category
    sev = issue.source_severity

    if cat in ("oos", "non_conforming_result"):
        return "OOS investigation report with root cause and CAPA"
    if cat in ("gap", "missing"):
        return "Missing content added to the document or justification for omission"
    if cat in ("consistency", "entity_contradiction", "numerical_contradiction"):
        return "Correction of the inconsistency or documented rationale for the difference"
    if cat in ("stability", "degradation_signal"):
        return "Statistical trend analysis or shelf life justification"
    if cat in ("mapping", "low_confidence_mapping"):
        return "Confirmation of correct field mapping or corrected column headers"
    if sev in ("blocker", "error", "critical"):
        return "Resolution documentation addressing the critical finding"
    return "Resolution note with supporting evidence"


# ======================================================================
# Report construction
# ======================================================================

def _closure_severity(status: ClosureStatus, source_sev: str, source_cat: str = "") -> str:
    """Map closure status + source severity to finding severity."""
    if status == ClosureStatus.CONFLICTING:
        return "error"
    if status == ClosureStatus.UNRESOLVED and source_sev in ("blocker", "error", "critical"):
        return "error"
    if status == ClosureStatus.UNRESOLVED and source_cat in ("non_conforming_result", "oos"):
        return "error"
    if status == ClosureStatus.UNRESOLVED and source_sev in ("major", "warning"):
        return "warning"
    if status == ClosureStatus.BLOCKED:
        return "warning"
    if status == ClosureStatus.PARTIALLY_RESOLVED:
        return "info"
    return "info"


def _closure_description(issue: TrackedIssue) -> str:
    """Generate a description for a closure finding."""
    cs = issue.closure_status
    src = issue.source_finding[:100]

    if cs == ClosureStatus.RESOLVED:
        return f"RESOLVED: {src}"
    if cs == ClosureStatus.PARTIALLY_RESOLVED:
        return f"PARTIALLY RESOLVED: {src} -- additional evidence may be needed."
    if cs == ClosureStatus.UNRESOLVED:
        return f"UNRESOLVED: {src}"
    if cs == ClosureStatus.CONFLICTING:
        return f"CONFLICTING: {src} -- resolution evidence contradicts other findings."
    if cs == ClosureStatus.BLOCKED:
        deps = ", ".join(d.depends_on for d in issue.dependencies)
        return f"BLOCKED: {src} -- waiting on: {deps}"
    return f"{cs.value.upper()}: {src}"


def _closure_action(status: ClosureStatus, issue: TrackedIssue) -> str:
    """Recommend action based on closure status."""
    if status == ClosureStatus.RESOLVED:
        return "No further action -- verified as resolved."
    if status == ClosureStatus.PARTIALLY_RESOLVED:
        if issue.missing_evidence:
            return f"Complete resolution: {issue.missing_evidence[0]}"
        return "Provide additional resolution evidence."
    if status == ClosureStatus.UNRESOLVED:
        if issue.missing_evidence:
            return issue.missing_evidence[0]
        return "Provide resolution evidence."
    if status == ClosureStatus.CONFLICTING:
        return "Investigate conflicting evidence and update resolution."
    if status == ClosureStatus.BLOCKED:
        deps = [d.depends_on for d in issue.dependencies]
        return f"Resolve dependencies first: {', '.join(deps)}"
    return "Review and address."


def _build_report(issues: list[TrackedIssue]) -> ClosureReport:
    """Assemble a ``ClosureReport`` from assessed issues."""
    closure_findings: list[ClosureFinding] = []
    hr_triggers: list[str] = []

    closure_counts = {
        "resolved": 0,
        "partially_resolved": 0,
        "unresolved": 0,
        "conflicting": 0,
        "blocked": 0,
    }

    for issue in issues:
        cs = issue.closure_status
        closure_counts[cs.value] = closure_counts.get(cs.value, 0) + 1

        severity = _closure_severity(cs, issue.source_severity, issue.source_category)

        cf = ClosureFinding(
            finding_id=f"closure-{issue.issue_id}",
            issue_id=issue.issue_id,
            source_app=issue.source_app,
            source_finding=issue.source_finding[:200],
            closure_status=cs.value,
            description=_closure_description(issue),
            severity=severity,
            missing_evidence=list(issue.missing_evidence),
            dependencies=[{"depends_on": d.depends_on, "reason": d.reason} for d in issue.dependencies],
            priority=issue.priority,
            confidence=dict(issue.confidence),
            evidence=issue.resolution_evidence,
            action=_closure_action(cs, issue),
        )
        closure_findings.append(cf)

        # Human review triggers
        if cs in (ClosureStatus.CONFLICTING, ClosureStatus.BLOCKED):
            hr_triggers.append(
                f"{issue.issue_id}: {cs.value} -- {issue.source_finding[:60]}"
            )
        elif cs == ClosureStatus.UNRESOLVED and issue.source_severity in (
            "error", "critical", "blocker", "major",
        ):
            hr_triggers.append(
                f"{issue.issue_id}: unresolved {issue.source_severity} finding"
            )
        elif cs == ClosureStatus.UNRESOLVED and issue.source_category in (
            "non_conforming_result", "oos",
        ):
            hr_triggers.append(
                f"{issue.issue_id}: unresolved OOS finding"
            )

    # Sort by priority (highest first)
    closure_findings.sort(key=lambda f: f.priority, reverse=True)

    # Derive convenience fields
    covered = [
        f.issue_id for f in closure_findings
        if f.closure_status == ClosureStatus.RESOLVED.value
    ]
    uncovered_gaps = [
        f.issue_id for f in closure_findings
        if f.closure_status in (
            ClosureStatus.UNRESOLVED.value,
            ClosureStatus.PARTIALLY_RESOLVED.value,
            ClosureStatus.BLOCKED.value,
        )
    ]
    dependency_graph: dict[str, list[str]] = {}
    for f in closure_findings:
        if f.dependencies:
            dependency_graph[f.issue_id] = [d["depends_on"] for d in f.dependencies]

    priority_actions = [
        f"[{f.issue_id}] {f.action}"
        for f in closure_findings
        if f.closure_status != ClosureStatus.RESOLVED.value
    ]

    # Overall confidence
    n_clear = closure_counts["resolved"] + closure_counts["unresolved"]
    n_total = max(len(issues), 1)
    conf_score = round(n_clear / n_total, 2)

    return ClosureReport(
        status="completed",
        findings=closure_findings,
        closure_summary=closure_counts,
        covered=covered,
        uncovered_gaps=uncovered_gaps,
        dependency_graph=dependency_graph,
        priority_actions=priority_actions,
        human_review_required=bool(hr_triggers),
        human_review_triggers=hr_triggers,
        confidence={
            "score": conf_score,
            "qualifier": "high" if conf_score >= 0.8 else "medium" if conf_score >= 0.5 else "low",
        },
        exceptions=[],
    )
