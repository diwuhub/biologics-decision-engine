"""
Evidence Registry — Typed YAML-based Evidence Store (Layer 3).

Not a database. Not a graph. A flat, typed, queryable collection of
regulatory evidence entries that Layer 2 services can reference.

Each entry has: id, type, source, applicable_categories, content, confidence.
"""

from __future__ import annotations

import os
import yaml
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from schemas.provenance import ProvenanceRecord


@dataclass
class RegistryEntry:
    """One entry in the evidence registry.

    Extended with ROL decision metadata for contextual evidence filtering,
    authority classification, and escalation triggering.
    """
    id: str
    entry_type: str          # 'guideline_clause', 'precedent', 'method_standard', 'issue_taxonomy'
    decision_type: str = ""  # [PATCH 1] ROL semantic type: Normative|Precedent|Method|Concern Pattern
    source: str = ""         # e.g., "ICH Q5E", "FDA BLA 761024"
    title: str = ""
    content: str = ""        # the actual regulatory text or summary
    applicable_categories: List[str] = None  # e.g., ["purity", "identity"]
    confidence: float = 0.5  # how authoritative (1.0 = direct guideline)

    # ROL Decision Metadata (P0-1, updated for v1.1 patches)
    applies_to_change_types: List[str] = None          # e.g., ["process_change", "all"], default ["all"]
    applies_to_molecule_classes: List[str] = None       # e.g., ["mAb", "all"], default ["all"]
    applies_to_lifecycle_stages: List[str] = None       # e.g., ["CMC", "preclinical", "all"], default ["all"]
    geography: List[str] = None                         # e.g., ["US", "EU", "global"], default ["global"]
    temporal_status: str = "current"                    # one of: current, dated_but_informative, historical_only, superseded_by:<id>
    evidence_weight: str = "normative"                  # one of: binding, normative, advisory, informative, contextual
    likely_concern_categories: List[str] = None         # e.g., ["CQA_purity", "CQA_potency"]
    triggers_escalation: bool = False                   # If True, flag for human review
    display_tier: str = "secondary"                     # one of: primary, secondary, appendix, internal

    # [PATCH 3] authority_quality_tier
    authority_quality_tier: str = "contextual"           # one of: primary, strong_secondary, contextual

    # [PATCH 2] Structured recommended_followup (replaces free-text string)
    recommended_followup: Optional[Dict] = None

    # [PATCH 12] Structured risk_if_skipped (P2 placeholder)
    risk_if_skipped: Optional[Dict] = None

    # Evidence verification metadata (P0-B)
    verified: bool = False
    verification_method: str = "not_verified"  # "openFDA drugsfda endpoint" | "BLA number confirmed" | "manual_review" | "not_verified"
    content_type: str = "paraphrase"           # "original_text" | "paraphrase" | "synthetic" | "structured_metadata"
    confidence_basis: str = ""                 # explains why confidence has its value

    # Document status for FDA guidance entries (P0-C)
    document_status: str = ""                  # "final" | "draft" | "withdrawn" | "superseded"

    # Legacy fields
    url: str = ""
    year: int = 0
    tags: List[str] = None

    # [PATCH 1] Legacy type -> decision_type mapping (runs at load time, immutable)
    _LEGACY_TYPE_MAP = {
        "guideline_clause": "Normative",
        "precedent": "Precedent",
        "method_standard": "Method",
        "issue_taxonomy": "Concern Pattern",
    }

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.applicable_categories is None:
            self.applicable_categories = []
        # [PATCH 1] Auto-map decision_type from entry_type if not set
        if not self.decision_type:
            self.decision_type = self._LEGACY_TYPE_MAP.get(self.entry_type, "Concern Pattern")
        # Set defaults for new ROL fields
        if self.applies_to_change_types is None:
            self.applies_to_change_types = ["all"]
        if self.applies_to_molecule_classes is None:
            self.applies_to_molecule_classes = ["all"]
        if self.applies_to_lifecycle_stages is None:
            self.applies_to_lifecycle_stages = ["all"]
        if self.geography is None:
            self.geography = ["global"]
        if self.likely_concern_categories is None:
            self.likely_concern_categories = []

    def to_provenance(self, module: str, context: str = "") -> ProvenanceRecord:
        """Convert to a ProvenanceRecord for the provenance chain."""
        return ProvenanceRecord(
            source_type="guideline" if self.entry_type == "guideline_clause" else "precedent",
            source_id=f"{self.source}: {self.id}",
            source_url=self.url,
            confidence=self.confidence,
            module=module,
            context=context or self.title,
        )


class EvidenceRegistry:
    """Query interface for the evidence registry."""

    def __init__(self, registry_dir: Optional[str] = None):
        if registry_dir is None:
            registry_dir = os.path.join(os.path.dirname(__file__), "entries")
        self._entries: List[RegistryEntry] = []
        self._load(registry_dir)

    def _load(self, directory: str):
        """Load all YAML files from the entries directory."""
        if not os.path.isdir(directory):
            return
        for fname in sorted(os.listdir(directory)):
            if fname.endswith((".yaml", ".yml")):
                path = os.path.join(directory, fname)
                with open(path) as f:
                    data = yaml.safe_load(f)
                if isinstance(data, list):
                    for item in data:
                        self._entries.append(self._parse_entry(item))
                elif isinstance(data, dict) and "entries" in data:
                    for item in data["entries"]:
                        self._entries.append(self._parse_entry(item))

    def _parse_entry(self, d: Dict) -> RegistryEntry:
        """Parse a YAML entry dict into a RegistryEntry.

        Backward-compatible: YAML files without new ROL fields get sensible defaults.
        """
        return RegistryEntry(
            id=d.get("id", ""),
            entry_type=d.get("type", "guideline_clause"),
            source=d.get("source", ""),
            title=d.get("title", ""),
            content=d.get("content", ""),
            applicable_categories=d.get("applicable_categories", []),
            confidence=d.get("confidence", 0.5),
            # Evidence verification metadata (P0-B/C)
            verified=d.get("verified", False),
            verification_method=d.get("verification_method", "not_verified"),
            content_type=d.get("content_type", "paraphrase"),
            confidence_basis=d.get("confidence_basis", ""),
            document_status=d.get("document_status", ""),
            url=d.get("url", ""),
            year=d.get("year", 0),
            tags=d.get("tags", []),
            # ROL Decision Metadata (defaults if not in YAML)
            applies_to_change_types=d.get("applies_to_change_types", None),
            applies_to_molecule_classes=d.get("applies_to_molecule_classes", None),
            applies_to_lifecycle_stages=d.get("applies_to_lifecycle_stages", None),
            geography=d.get("geography", None),
            temporal_status=d.get("temporal_status", "current"),
            evidence_weight=d.get("evidence_weight", "normative"),
            likely_concern_categories=d.get("likely_concern_categories", None),
            triggers_escalation=d.get("triggers_escalation", False),
            display_tier=d.get("display_tier", "secondary"),
            # [PATCH 1] decision_type -- auto-mapped from entry_type if absent
            decision_type=d.get("decision_type", ""),
            # [PATCH 3] authority_quality_tier -- default to contextual (conservative)
            authority_quality_tier=d.get("authority_quality_tier", "contextual"),
            # [PATCH 2] Structured followup -- None if absent (backward-compatible)
            recommended_followup=d.get("recommended_followup", None),
            # [PATCH 12] risk_if_skipped -- P2, None if absent
            risk_if_skipped=d.get("risk_if_skipped", None),
        )

    def query(self, category: str = "", entry_type: str = "", tags: List[str] = None, keyword: str = "") -> List[RegistryEntry]:
        """Query entries by category, type, tags, or keyword."""
        results = self._entries
        if category:
            results = [e for e in results if category in e.applicable_categories]
        if entry_type:
            results = [e for e in results if e.entry_type == entry_type]
        if tags:
            results = [e for e in results if any(t in e.tags for t in tags)]
        if keyword:
            kw = keyword.lower()
            results = [e for e in results if kw in e.title.lower() or kw in e.content.lower()]
        return results

    def get(self, entry_id: str) -> Optional[RegistryEntry]:
        """Get a specific entry by ID."""
        for e in self._entries:
            if e.id == entry_id:
                return e
        return None

    @property
    def count(self) -> int:
        return len(self._entries)
