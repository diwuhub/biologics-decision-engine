"""
EvidenceTraceEntry — Traceability chain closure: rule → evidence.

Links each applied decision rule to its supporting evidence references
and their content snippets. This closes the gap between
verdict → decision_rule_ids → cluster → attribute and
rule → evidence.

Phase P1-A: Backend Logic Completion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class EvidenceTraceEntry:
    """Trace from a decision rule application to its supporting evidence.

    Fields:
        rule_id: Decision rule catalog ID (e.g., 'AGGR-002').
        trigger_description: Human-readable trigger (e.g., 'cluster CLU-003 concern_level=major').
        supporting_ref_ids: Reference IDs from evidence registry or authority pack.
        ref_content_snippets: Actual text snippets from each supporting reference.
        evidence_sufficient: Whether the evidence meets the rule's requirements.
    """
    rule_id: str
    trigger_description: str
    supporting_ref_ids: List[str] = field(default_factory=list)
    ref_content_snippets: List[str] = field(default_factory=list)
    evidence_sufficient: bool = True
    # P5-C: Evidence Trace Differentiation
    trigger_facts: List[str] = field(default_factory=list)
    source_object_ids: List[str] = field(default_factory=list)
    rule_specific_evidence_basis: str = ""
