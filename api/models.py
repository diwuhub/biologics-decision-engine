"""
Case and assessment data models for the decision workspace.
"""
from __future__ import annotations

import json
import uuid
import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class CaseStatus(str, Enum):
    """Workflow state for a case."""
    DRAFT = "draft"
    DATA_LOADED = "data_loaded"
    VALIDATION_COMPLETE = "validation_complete"
    ASSESSMENT_COMPLETE = "assessment_complete"
    READY_FOR_SUBMISSION = "ready_for_submission"
    SUBMITTED = "submitted"


class VerdictCategory(str, Enum):
    """Overall comparability verdict."""
    COMPARABLE = "comparable"
    COMPARABLE_WITH_CAVEATS = "comparable_with_caveats"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass
class CaseMetadata:
    """High-level case metadata."""
    case_id: str
    product_name: str
    product_type: str  # mAb, recombinant protein, etc.
    molecule_class: str
    change_type: str  # formulation, manufacturing site, process, etc.
    product_stage: str  # clinical, commercial, legacy
    status: CaseStatus
    created_at: str
    updated_at: str
    created_by: str = "system"
    batch_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CaseData:
    """Full case with metadata + assessment results."""
    metadata: CaseMetadata
    raw_batch_data: Dict[str, Any]  # Original CSV/JSON batch data
    comparability_report: Optional[Dict[str, Any]] = None
    gap_memo_result: Optional[Dict[str, Any]] = None
    validation_errors: List[str] = None

    def __post_init__(self):
        if self.validation_errors is None:
            self.validation_errors = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "raw_batch_data": self.raw_batch_data,
            "comparability_report": self.comparability_report,
            "gap_memo_result": self.gap_memo_result,
            "validation_errors": self.validation_errors or [],
        }


class CaseStore:
    """In-memory case storage (defer persistent DB to Phase B)."""

    def __init__(self):
        self.cases: Dict[str, CaseData] = {}

    def create(self, metadata: CaseMetadata, batch_data: Dict[str, Any]) -> str:
        """Create a new case. Returns case_id."""
        case_id = metadata.case_id
        self.cases[case_id] = CaseData(
            metadata=metadata,
            raw_batch_data=batch_data,
            validation_errors=[],
        )
        return case_id

    def get(self, case_id: str) -> Optional[CaseData]:
        """Retrieve a case by ID."""
        return self.cases.get(case_id)

    def list_all(self) -> List[CaseMetadata]:
        """List all cases (for Case List endpoint)."""
        return [c.metadata for c in self.cases.values()]

    def update_status(self, case_id: str, status: CaseStatus) -> None:
        """Update case status."""
        if case_id in self.cases:
            self.cases[case_id].metadata.status = status
            self.cases[case_id].metadata.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def update_comparability_report(self, case_id: str, report: Dict[str, Any]) -> None:
        """Store comparability assessment result."""
        if case_id in self.cases:
            self.cases[case_id].comparability_report = report
            self.update_status(case_id, CaseStatus.ASSESSMENT_COMPLETE)

    def update_gap_memo(self, case_id: str, memo: Dict[str, Any]) -> None:
        """Store gap memo result."""
        if case_id in self.cases:
            self.cases[case_id].gap_memo_result = memo

    def add_validation_error(self, case_id: str, error: str) -> None:
        """Append validation error to case."""
        if case_id in self.cases:
            self.cases[case_id].validation_errors.append(error)


# Global case store (use FastAPI dependency injection in main.py)
_store = CaseStore()

def get_case_store() -> CaseStore:
    return _store
