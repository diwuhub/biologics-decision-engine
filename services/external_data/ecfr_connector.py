"""
P0-A.3: eCFR Connector

Fetches regulatory text from the Electronic Code of Federal Regulations
(eCFR) public API.

Key CFR parts for biologics:
  - 21 CFR 210/211  — cGMP for drugs
  - 21 CFR 600-680  — biologics
  - 21 CFR 820      — quality system regulation
  - 21 CFR 1271     — HCT/Ps

All errors are swallowed — methods return empty strings / dicts on failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STRUCTURE_BASE = "https://www.ecfr.gov/api/versioner/v1/structure/current"
_CONTENT_BASE = "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current"

# Rate-limit: be polite to the public API
_MIN_INTERVAL = 0.5


class ECFRConnector:
    """Connector for the eCFR public API (Title 21 focus)."""

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}
        self._last_request_ts: float = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)

    def _get_json(self, url: str) -> Dict[str, Any]:
        key = self._cache_key(url)
        if key in self._cache and isinstance(self._cache[key], dict):
            return self._cache[key]

        logger.debug("eCFR JSON request: %s", url)
        self._rate_limit()
        self._last_request_ts = time.monotonic()

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            logger.warning("eCFR request failed for %s", url, exc_info=True)
            return {}

        self._cache[key] = data
        return data

    def _get_text(self, url: str) -> str:
        key = self._cache_key(url)
        if key in self._cache and isinstance(self._cache[key], str):
            return self._cache[key]

        logger.debug("eCFR text request: %s", url)
        self._rate_limit()
        self._last_request_ts = time.monotonic()

        try:
            req = urllib.request.Request(url, headers={"Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
        except Exception:
            logger.warning("eCFR request failed for %s", url, exc_info=True)
            return ""

        # Strip HTML tags for a plain-text approximation
        text = self._strip_html(raw)
        self._cache[key] = text
        return text

    @staticmethod
    def _strip_html(html: str) -> str:
        """Naive HTML tag stripper — good enough for CFR prose."""
        import re

        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_section(self, title: str, part: str, section: str) -> str:
        """
        Fetch the text of a specific CFR section.

        Example: fetch_section('21', '211', '100') retrieves 21 CFR 211.100.
        Returns plain text (HTML tags stripped). Returns '' on error.
        """
        url = (
            f"{_CONTENT_BASE}"
            f"/title-{title}"
            f"?part={part}&section={part}.{section}"
        )
        return self._get_text(url)

    def fetch_part_structure(self, title: str, part: str) -> dict:
        """
        Fetch the table-of-contents structure for a CFR part.

        Example: fetch_part_structure('21', '211') returns the JSON
        structure describing all subparts and sections under 21 CFR 211.
        Returns {} on error.
        """
        url = f"{_STRUCTURE_BASE}/title-{title}.json?part={part}"
        return self._get_json(url)
