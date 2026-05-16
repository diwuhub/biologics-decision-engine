"""
Evidence Closure Tracker -- Data Schemas.

Defines input/output dataclasses for the evidence closure analysis pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ClosureStatus(str, Enum):
    """Possible closure states for a tracked finding."""
    RESOLVED = "resolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    UNRESOLVED = "unresolved"
    CONFLICTING = "conflicting"
    BLOCKED = "blocked"


class Severity(str, Enum):
    """Severity levels, ordered from least to most critical."""
    INFO = "info"
    CLARIFICATION = "clarification"
    WARNING = "warning"
    MAJOR = "major"
    ERROR = "error"
    BLOCKER = "blocker"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

@dataclass
class FindingRecord:
    """A single finding from an upstream analysis module.

    Parameters
    ----------
    text : str
        Description of the finding.
    category : str
        Classification tag (e.g. "gap", "consistency", "non_conforming_result").
    severity : str
        Severity level as a string (maps to ``Severity`` values).
    source : str
        Name or identifier of the upstream module that produced this finding.
    evidence : str or None
        Supporting evidence snippet, if available.
    """
    text: str
    category: str = ""
    severity: str = "info"
    source: str = ""
    evidence: Optional[str] = None


@dataclass
class ResolutionNote:
    """A resolution note that may close or partially close a finding.

    Parameters
    ----------
    matches : str
        Substring or keyword used to match this note to findings.
    resolution : str
        Description of how the finding was addressed.
    issue_id : str
        If provided, directly links to a specific issue ID.
    status : str or None
        Optional override status (e.g. "conflicting").
    """
    matches: str = ""
    resolution: str = ""
    issue_id: str = ""
    status: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal / intermediate schemas
# ---------------------------------------------------------------------------

@dataclass
class EvidenceDependency:
    """A dependency link between two tracked issues."""
    depends_on: str
    reason: str = ""


@dataclass
class TrackedIssue:
    """Internal representation of an issue being tracked through closure."""
    issue_id: str
    source_app: str
    source_finding: str
    source_category: str = ""
    source_severity: str = "info"
    source_evidence: Optional[str] = None
    closure_status: ClosureStatus = ClosureStatus.UNRESOLVED
    resolution_evidence: str = ""
    missing_evidence: list[str] = field(default_factory=list)
    dependencies: list[EvidenceDependency] = field(default_factory=list)
    priority: int = 0
    confidence: dict = field(default_factory=lambda: {"score": 0.5, "qualifier": "medium"})


# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------

@dataclass
class ClosureFinding:
    """A single finding in the closure report."""
    finding_id: str
    issue_id: str
    source_app: str
    source_finding: str
    closure_status: str
    description: str
    severity: str
    missing_evidence: list[str] = field(default_factory=list)
    dependencies: list[dict] = field(default_factory=list)
    priority: int = 0
    confidence: dict = field(default_factory=lambda: {"score": 0.5, "qualifier": "medium"})
    evidence: str = ""
    action: str = ""


@dataclass
class ClosureReport:
    """Complete output of the evidence closure analysis.

    Attributes
    ----------
    status : str
        Overall pipeline status ("completed", "no_input").
    findings : list[ClosureFinding]
        Individual closure findings, sorted by priority (highest first).
    closure_summary : dict
        Counts by closure status, e.g. {"resolved": 2, "unresolved": 1, ...}.
    covered : list[str]
        Issue IDs that are fully resolved.
    uncovered_gaps : list[str]
        Issue IDs that remain unresolved or only partially resolved.
    dependency_graph : dict[str, list[str]]
        Mapping of issue_id -> list of issue_ids it depends on.
    priority_actions : list[str]
        Ordered list of recommended next actions.
    human_review_required : bool
        Whether any finding triggers mandatory human review.
    human_review_triggers : list[str]
        Descriptions of what triggered human review.
    confidence : dict
        Overall confidence {"score": float, "qualifier": str}.
    exceptions : list[dict]
        Any errors or warnings from the pipeline itself.
    """
    status: str = "completed"
    findings: list[ClosureFinding] = field(default_factory=list)
    closure_summary: dict = field(default_factory=dict)
    covered: list[str] = field(default_factory=list)
    uncovered_gaps: list[str] = field(default_factory=list)
    dependency_graph: dict = field(default_factory=dict)
    priority_actions: list[str] = field(default_factory=list)
    human_review_required: bool = False
    human_review_triggers: list[str] = field(default_factory=list)
    confidence: dict = field(default_factory=lambda: {"score": None, "qualifier": "unknown"})
    exceptions: list[dict] = field(default_factory=list)
