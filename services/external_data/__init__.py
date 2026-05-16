"""
P0-A: Regulatory Data Source Integration — Abstract Interface

Defines the common data models and abstract base class for all regulatory
data connectors (openFDA, eCFR, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EnforcementRecord:
    record_id: str
    product_name: str
    action_type: str  # 'warning_letter' | 'recall' | 'consent_decree'
    date: str
    reason: str
    source_url: str
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalRecord:
    record_id: str
    product_name: str
    application_number: str  # BLA number
    approval_date: str
    applicant: str
    application_type: str
    source_url: str
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AdverseEventRecord:
    record_id: str
    product_name: str
    event_date: str
    reaction: str
    outcome: str
    source_url: str
    raw_data: Dict[str, Any] = field(default_factory=dict)


class RegulatoryDataSource(ABC):
    """Abstract base class for all regulatory data connectors."""

    @abstractmethod
    def fetch_enforcement_actions(
        self, query: str, limit: int = 10
    ) -> List[EnforcementRecord]:
        ...

    @abstractmethod
    def fetch_drug_approvals(
        self, query: str, limit: int = 10
    ) -> List[ApprovalRecord]:
        ...

    @abstractmethod
    def fetch_adverse_events(
        self, query: str, limit: int = 10
    ) -> List[AdverseEventRecord]:
        ...
