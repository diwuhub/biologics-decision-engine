"""
CaseContext — Immutable case-level context object.

Created at pipeline entry. Immutable after construction. All downstream
modules share this object; none may reconstruct their own context from
raw inputs.

Step 0A: Judgment Core Refactor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Optional


def _generate_case_id() -> str:
    return f"CASE-{uuid.uuid4().hex[:12].upper()}"


@dataclass
class CaseContext:
    """Immutable case context shared by all pipeline stages.

    MVP-Required fields are mandatory at construction time.
    Future-reserved fields are optional and documented for forward
    compatibility. They must NOT be required parameters or pipeline
    contract dependencies.

    Immutability is enforced via ``__setattr__`` and ``__delattr__``
    overrides after ``__init__`` completes.
    """

    # --- MVP-Required Fields ---
    molecule_class: str
    change_type: str
    change_description: str
    lifecycle_stage: str
    flagged_attribute_ids: List[str]
    flagged_categories: List[str]
    identified_gaps: List[str]

    # MVP-Required with default
    target_geography: str = "global"

    # Auto-generated
    case_id: str = field(default_factory=_generate_case_id)

    # --- Future-Reserved Fields (optional, NOT pipeline contracts) ---
    molecule_name: Optional[str] = None
    modality: Optional[str] = None
    intended_regulatory_outcome: Optional[str] = None
    normalized_attribute_ids: Optional[List[str]] = None
    input_completeness_ratio: Optional[float] = None
    current_action_ceiling: Optional[str] = None

    # Internal flag to track whether __init__ has completed.
    _frozen: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Validate MVP-required fields are non-empty strings where applicable.
        for fname in (
            "molecule_class",
            "change_type",
            "change_description",
            "lifecycle_stage",
        ):
            val = getattr(self, fname)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"CaseContext.{fname} must be a non-empty string")

        # Validate list fields are lists.
        for fname in ("flagged_attribute_ids", "flagged_categories", "identified_gaps"):
            val = getattr(self, fname)
            if not isinstance(val, list):
                raise TypeError(f"CaseContext.{fname} must be a list")

        # Freeze after init.
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(
                f"CaseContext is immutable after construction. "
                f"Cannot set '{name}'."
            )
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(
                f"CaseContext is immutable after construction. "
                f"Cannot delete '{name}'."
            )
        object.__delattr__(self, name)
