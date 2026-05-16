"""
Evidence Closure Module
=======================

Standalone evidence closure tracker extracted from bio-cmc-ai-suite.
Evaluates whether upstream findings have sufficient evidence for closure,
identifies gaps, maps dependencies, and prioritises resolution actions.

Public API
----------
- ``analyze`` -- run closure analysis on a list of findings
- ``FindingRecord`` -- input dataclass for a single finding
- ``ResolutionNote`` -- input dataclass for a resolution note
- ``ClosureReport`` -- output dataclass with full closure assessment
- ``ClosureFinding`` -- a single finding within the report
- ``EvidenceDependency`` -- a dependency link between issues
- ``ClosureStatus`` -- enum of possible closure states
"""

from .analyzer import analyze
from .schemas import (
    ClosureFinding,
    ClosureReport,
    ClosureStatus,
    EvidenceDependency,
    FindingRecord,
    ResolutionNote,
    TrackedIssue,
)

__all__ = [
    "analyze",
    "ClosureFinding",
    "ClosureReport",
    "ClosureStatus",
    "EvidenceDependency",
    "FindingRecord",
    "ResolutionNote",
    "TrackedIssue",
]
