"""CTD Reviewer — section classification, consistency checking, and checklist review for Module 3 submissions.

Extracted from bio-cmc-ai-suite/module3-reviewer. Standalone: requires only stdlib + re.

Public API:
    classify_sections(text) -> list[dict]
    check_consistency(sections) -> dict
    review_checklist(classifications) -> dict
"""

from .section_classifier import classify_sections
from .consistency_checker import check_consistency
from .checklist_reviewer import review_checklist

__all__ = ["classify_sections", "check_consistency", "review_checklist"]
