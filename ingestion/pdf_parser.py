"""
A2: PDF Document Parser.

Parses PDF files into the common parsed-document schema defined in
specs/cross_document_bridge.py. Uses pdfplumber for text and table extraction.

Output schema matches DOCXDocumentParser so downstream extractors are
format-agnostic.

Handles scanned PDFs gracefully -- issues a warning, does not crash.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from specs.cross_document_bridge import DocumentParser, DocumentType

logger = logging.getLogger(__name__)


class PDFDocumentParser(DocumentParser):
    """Parse a PDF file into the common parsed-document schema.

    Uses pdfplumber to extract:
    - Text per page
    - Tables with headers and rows
    - Document metadata (title, author, creation date)

    Output schema is identical to DOCXDocumentParser so that downstream
    extractors (classifiers, attribute extractors) are format-agnostic.
    """

    def parse(self, document_path: str) -> Dict[str, Any]:
        """Parse a PDF and return structured data.

        Parameters
        ----------
        document_path : str
            Path to a .pdf file.

        Returns
        -------
        dict
            Common parsed-document schema with pages, paragraphs,
            sections, and metadata.
        """
        if not os.path.isfile(document_path):
            raise FileNotFoundError(f"PDF not found: {document_path}")

        try:
            import pdfplumber
        except ImportError:
            raise ImportError(
                "pdfplumber is required for PDF parsing. "
                "Install with: pip install pdfplumber"
            )

        pages_data: List[Dict[str, Any]] = []
        all_paragraphs: List[Dict[str, Any]] = []
        metadata: Dict[str, Any] = {
            "source_format": "pdf",
            "title": "",
            "author": "",
            "created_date": "",
            "modified_date": "",
        }
        para_index = 0
        scanned_page_count = 0

        try:
            with pdfplumber.open(document_path) as pdf:
                # Extract metadata from PDF info dict
                if pdf.metadata:
                    metadata["title"] = pdf.metadata.get("Title", "") or ""
                    metadata["author"] = pdf.metadata.get("Author", "") or ""
                    created = pdf.metadata.get("CreationDate", "")
                    metadata["created_date"] = str(created) if created else ""
                    modified = pdf.metadata.get("ModDate", "")
                    metadata["modified_date"] = str(modified) if modified else ""

                for page_num, page in enumerate(pdf.pages, start=1):
                    # Extract text
                    page_text = ""
                    try:
                        page_text = page.extract_text() or ""
                    except Exception as e:
                        logger.warning(
                            "Failed to extract text from page %d of %s: %s",
                            page_num, document_path, e,
                        )

                    # Detect scanned/image-only pages
                    if not page_text.strip():
                        scanned_page_count += 1

                    # Extract tables
                    tables = []
                    try:
                        raw_tables = page.extract_tables() or []
                        for t_idx, raw_table in enumerate(raw_tables):
                            if not raw_table or len(raw_table) < 1:
                                continue
                            table_data = self._parse_raw_table(
                                raw_table, page_num, t_idx
                            )
                            if table_data:
                                tables.append(table_data)
                    except Exception as e:
                        logger.warning(
                            "Failed to extract tables from page %d of %s: %s",
                            page_num, document_path, e,
                        )

                    pages_data.append({
                        "page_number": page_num,
                        "text": page_text,
                        "tables": tables,
                    })

                    # Build paragraph-like entries from text lines
                    # (PDF doesn't have heading styles, so we approximate)
                    if page_text.strip():
                        lines = page_text.split("\n")
                        for line in lines:
                            stripped = line.strip()
                            if not stripped:
                                continue
                            # Heuristic: short ALL-CAPS or title-case lines
                            # may be headings
                            is_heading = (
                                len(stripped) < 120
                                and (stripped.isupper() or stripped.istitle())
                                and not stripped.endswith(".")
                            )
                            heading_level = 1 if is_heading else None
                            all_paragraphs.append({
                                "index": para_index,
                                "text": stripped,
                                "style": "Heading" if is_heading else "Normal",
                                "heading_level": heading_level,
                                "is_heading": is_heading,
                                "runs": [{"text": stripped, "bold": is_heading, "italic": False}],
                            })
                            para_index += 1

        except Exception as e:
            # Handle corrupted/unreadable PDFs gracefully
            logger.error("Failed to parse PDF %s: %s", document_path, e)
            return {
                "document_path": document_path,
                "document_type": DocumentType.OTHER,
                "pages": [],
                "paragraphs": [],
                "sections": [],
                "metadata": {
                    **metadata,
                    "parse_error": str(e),
                },
            }

        # Warn about scanned pages
        if scanned_page_count > 0:
            total_pages = len(pages_data)
            if scanned_page_count == total_pages:
                logger.warning(
                    "All %d pages in %s appear to be scanned/image-only. "
                    "OCR is not supported -- text extraction will be empty.",
                    total_pages, document_path,
                )
            else:
                logger.info(
                    "%d of %d pages in %s appear to be scanned/image-only.",
                    scanned_page_count, total_pages, document_path,
                )

        # Build section hierarchy from heading-like paragraphs
        sections = self._build_sections(all_paragraphs)

        # Combine all page text for the full-text field
        full_text = "\n".join(
            p["text"] for p in pages_data if p["text"].strip()
        )

        return {
            "document_path": document_path,
            "document_type": DocumentType.OTHER,
            "pages": pages_data,
            "paragraphs": all_paragraphs,
            "sections": sections,
            "metadata": metadata,
        }

    def supported_formats(self) -> List[str]:
        return [".pdf"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_raw_table(
        self,
        raw_table: List[List[Any]],
        page_num: int,
        table_idx: int,
    ) -> Dict[str, Any]:
        """Convert pdfplumber raw table (list of lists) into our schema."""
        if not raw_table or len(raw_table) < 2:
            return {}

        # First row as headers
        raw_headers = raw_table[0]
        headers = [
            str(h).strip() if h is not None else f"col_{i}"
            for i, h in enumerate(raw_headers)
        ]

        # Remaining rows as data
        rows_data = []
        for row in raw_table[1:]:
            row_dict = {}
            for c_idx, cell in enumerate(row):
                cell_text = str(cell).strip() if cell is not None else ""
                if c_idx < len(headers):
                    row_dict[headers[c_idx]] = cell_text
                else:
                    row_dict[f"col_{c_idx}"] = cell_text
            rows_data.append(row_dict)

        table_id = f"pdf_p{page_num}_table_{table_idx + 1}"
        return {
            "id": table_id,
            "table_index": table_idx,
            "headers": headers,
            "rows": rows_data,
            "n_rows": len(rows_data),
            "n_cols": len(headers),
            "page_number": page_num,
        }

    def _build_sections(
        self, paragraphs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build section hierarchy from heading paragraphs."""
        sections = []
        current_section = None

        for para in paragraphs:
            if para.get("is_heading"):
                section = {
                    "title": para["text"],
                    "level": para.get("heading_level", 1),
                    "paragraph_index": para["index"],
                    "children": [],
                }
                if current_section is None:
                    sections.append(section)
                    current_section = section
                else:
                    current_section["children"].append(section)

        return sections
