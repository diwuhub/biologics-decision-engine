"""
A3: Ingestion Dispatcher.

Routes parsed documents to the appropriate extractor based on document
classification. Currently supports:
- COMPARABILITY -> DOCXAttributeExtractor (existing)
- CHARACTERIZATION -> CharacterizationExtractor
- STABILITY -> StabilityExtractor
- ANALYTICAL_METHOD -> AnalyticalMethodExtractor
- Everything else -> GenericCMCExtractor (best-effort)
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from ingestion.base_extractor import BaseExtractor
from ingestion.document_classifier import DocTypeSpec
from ingestion.generic_extractor import GenericCMCExtractor

logger = logging.getLogger(__name__)


class IngestionDispatcher:
    """Route parsed documents to the right extractor based on document type.

    The dispatcher never crashes. If the specialized extractor fails to
    import or initialize, it falls back to GenericCMCExtractor.
    """

    def dispatch(
        self, parsed_doc: Dict[str, Any], doc_type: DocTypeSpec
    ) -> BaseExtractor:
        """Return the appropriate extractor for the given document type.

        Parameters
        ----------
        parsed_doc : dict
            The parsed document (used for future type-specific logic).
        doc_type : DocTypeSpec
            Classification result from DocumentClassifier.

        Returns
        -------
        BaseExtractor
            The extractor to use for this document.
        """
        try:
            return self._dispatch_impl(parsed_doc, doc_type)
        except Exception as e:
            logger.warning(
                "Dispatcher failed for type %s, falling back to GenericCMCExtractor: %s",
                doc_type.document_type, e,
            )
            return GenericCMCExtractor()

    def _dispatch_impl(
        self, parsed_doc: Dict[str, Any], doc_type: DocTypeSpec
    ) -> BaseExtractor:
        """Internal dispatch logic."""
        dtype = doc_type.document_type

        if dtype == "COMPARABILITY":
            return self._get_comparability_extractor()

        if dtype == "CHARACTERIZATION":
            return self._get_characterization_extractor()

        if dtype == "STABILITY":
            return self._get_stability_extractor()

        if dtype == "ANALYTICAL_METHOD":
            return self._get_analytical_method_extractor()

        if dtype == "CTD_MODULE_3":
            return self._get_ctd_module3_extractor()

        # All other types (including UNKNOWN) get the generic extractor
        # for now. Future phases will add specialized extractors:
        # - PROCESS_VALIDATION -> ProcessValidationExtractor
        logger.info(
            "Using GenericCMCExtractor for document type: %s (confidence: %.2f)",
            dtype, doc_type.confidence,
        )
        return GenericCMCExtractor()

    def _get_characterization_extractor(self) -> BaseExtractor:
        """Return the CharacterizationExtractor."""
        from ingestion.characterization_extractor import CharacterizationExtractor

        logger.info("Using CharacterizationExtractor for CHARACTERIZATION document")
        return CharacterizationExtractor()

    def _get_stability_extractor(self) -> BaseExtractor:
        """Return the StabilityExtractor."""
        from ingestion.stability_extractor import StabilityExtractor

        logger.info("Using StabilityExtractor for STABILITY document")
        return StabilityExtractor()

    def _get_analytical_method_extractor(self) -> BaseExtractor:
        """Return the AnalyticalMethodExtractor."""
        from ingestion.analytical_method_extractor import AnalyticalMethodExtractor

        logger.info("Using AnalyticalMethodExtractor for ANALYTICAL_METHOD document")
        return AnalyticalMethodExtractor()

    def _get_ctd_module3_extractor(self) -> BaseExtractor:
        """Return the CTDModule3Extractor."""
        from ingestion.ctd_module3_extractor import CTDModule3Extractor

        logger.info("Using CTDModule3Extractor for CTD_MODULE_3 document")
        return CTDModule3Extractor()

    def _get_comparability_extractor(self) -> BaseExtractor:
        """Wrap the existing DOCXAttributeExtractor in a BaseExtractor adapter."""
        from ingestion.docx_extractor import DOCXAttributeExtractor

        return _ComparabilityExtractorAdapter(DOCXAttributeExtractor())


class _ComparabilityExtractorAdapter(BaseExtractor):
    """Adapter that wraps DOCXAttributeExtractor to conform to BaseExtractor.

    Ensures extract_attributes() and extract_evidence() never raise.
    """

    def __init__(self, inner: Any):
        self._inner = inner

    def extract_attributes(self, parsed_doc: Dict[str, Any]):
        """Delegate to DOCXAttributeExtractor, catching all exceptions."""
        try:
            return self._inner.extract_attributes(parsed_doc)
        except Exception as e:
            logger.warning(
                "ComparabilityExtractor.extract_attributes failed: %s", e
            )
            return []

    def extract_evidence(self, parsed_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Extract comparability-specific evidence."""
        try:
            tables_found = 0
            pre_post_pairs = 0
            for page in parsed_doc.get("pages", []):
                for table in page.get("tables", []):
                    tables_found += 1
                    headers = [h.lower() for h in table.get("headers", [])]
                    has_pre = any("pre" in h for h in headers)
                    has_post = any("post" in h for h in headers)
                    if has_pre and has_post:
                        pre_post_pairs += 1
            return {
                "tables_found": tables_found,
                "pre_post_table_pairs": pre_post_pairs,
                "extractor": "DOCXAttributeExtractor",
            }
        except Exception as e:
            logger.warning(
                "ComparabilityExtractor.extract_evidence failed: %s", e
            )
            return {"error": str(e)}

    def supported_categories(self):
        try:
            return self._inner.supported_categories()
        except Exception:
            return []
