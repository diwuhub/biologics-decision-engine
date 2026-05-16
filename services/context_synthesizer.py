"""
CaseContext Synthesizer -- DEPRECATED, renamed to case_context_factory.py.

Phase 5: This module is a backward-compatible shim. All new code should
import from services.case_context_factory instead.
"""
from services.case_context_factory import synthesize_case_context  # noqa: F401

__all__ = ["synthesize_case_context"]
