"""
A7: Base Extractor ABC.

Defines the common interface for all document type extractors.
Every extractor must implement extract_attributes() and extract_evidence(),
and neither method may raise unhandled exceptions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from specs.cross_document_bridge import ExtractedAttribute


class BaseExtractor(ABC):
    """Abstract base class for document type extractors.

    Contract:
    - extract_attributes() MUST NOT raise unhandled exceptions.
    - extract_evidence() MUST NOT raise unhandled exceptions.
    - On failure, return empty list / empty dict with diagnostic info.
    """

    @abstractmethod
    def extract_attributes(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Extract structured attributes from a parsed document.

        Must never raise. On error, returns an empty list.
        """

    @abstractmethod
    def extract_evidence(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract type-specific evidence payload from a parsed document.

        Must never raise. On error, returns a dict with an 'error' key.
        """

    def supported_categories(self) -> List[str]:
        """Return the attribute categories this extractor handles."""
        return []
