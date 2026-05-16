"""
P0-A.2: openFDA Connector

Implements RegulatoryDataSource against the openFDA public API.

Endpoints:
  - drug/enforcement.json  — warning letters, recalls
  - drug/drugsfda.json     — BLA approvals
  - drug/event.json        — adverse events

Rate limit: 240 req/min (0.25 s between calls).
Results are cached in memory to avoid redundant network calls.
All errors are swallowed — methods return empty lists on failure.
"""

from __future__ import annotations

import hashlib
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import json
from typing import Any, Dict, List, Optional

from services.external_data import (
    AdverseEventRecord,
    ApprovalRecord,
    EnforcementRecord,
    RegulatoryDataSource,
)

logger = logging.getLogger(__name__)

_BASE = "https://api.fda.gov/drug"

# Minimum interval between requests (240 req/min => 0.25 s)
_MIN_INTERVAL = 0.25


class OpenFDAConnector(RegulatoryDataSource):
    """Concrete openFDA implementation of RegulatoryDataSource."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key
        self._cache: Dict[str, Any] = {}
        self._last_request_ts: float = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_key(self, endpoint: str, query: str, limit: int) -> str:
        raw = f"{endpoint}|{query}|{limit}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)

    def _get(self, endpoint: str, search: str, limit: int) -> Dict[str, Any]:
        """Execute a GET against openFDA and return the parsed JSON dict."""
        key = self._cache_key(endpoint, search, limit)
        if key in self._cache:
            return self._cache[key]

        params: Dict[str, str] = {"search": search, "limit": str(limit)}
        if self._api_key:
            params["api_key"] = self._api_key

        url = f"{_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
        logger.debug("openFDA request: %s", url)

        self._rate_limit()
        self._last_request_ts = time.monotonic()

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            logger.warning("openFDA request failed for %s", url, exc_info=True)
            return {}

        self._cache[key] = data
        return data

    # ------------------------------------------------------------------
    # Public API — enforcement actions
    # ------------------------------------------------------------------

    def fetch_enforcement_actions(
        self, query: str, limit: int = 10
    ) -> List[EnforcementRecord]:
        search = f'product_type:"Biologics"+AND+reason_for_recall:"{query}"'
        data = self._get("enforcement.json", search, limit)
        results: List[EnforcementRecord] = []
        for item in data.get("results", []):
            try:
                # Determine action type from classification
                classification = item.get("classification", "")
                if "Class I" in classification:
                    action_type = "recall"
                elif "warning" in item.get("reason_for_recall", "").lower():
                    action_type = "warning_letter"
                else:
                    action_type = "recall"

                results.append(
                    EnforcementRecord(
                        record_id=item.get("recall_number", item.get("event_id", "")),
                        product_name=item.get("product_description", "")[:200],
                        action_type=action_type,
                        date=item.get("report_date", item.get("recall_initiation_date", "")),
                        reason=item.get("reason_for_recall", ""),
                        source_url=f"https://api.fda.gov/drug/enforcement.json?search=recall_number:\"{item.get('recall_number', '')}\"",
                        raw_data=item,
                    )
                )
            except Exception:
                logger.debug("Skipping malformed enforcement record", exc_info=True)
        return results

    # ------------------------------------------------------------------
    # Public API — drug / biologic approvals
    # ------------------------------------------------------------------

    def fetch_drug_approvals(
        self, query: str, limit: int = 10
    ) -> List[ApprovalRecord]:
        search = f'openfda.generic_name:"{query}"'
        data = self._get("drugsfda.json", search, limit)
        results: List[ApprovalRecord] = []
        for item in data.get("results", []):
            try:
                app_number = item.get("application_number", "")
                sponsor = item.get("sponsor_name", "")
                app_type = item.get("application_type", "")
                # openFDA nests product info inside "products"
                products = item.get("products", [])
                product_name = (
                    products[0].get("brand_name", query) if products else query
                )
                # Approval date lives inside submissions
                approval_date = ""
                submissions = item.get("submissions", [])
                for sub in submissions:
                    if sub.get("submission_type") == "ORIG":
                        approval_date = sub.get("submission_status_date", "")
                        break
                if not approval_date and submissions:
                    approval_date = submissions[0].get("submission_status_date", "")

                results.append(
                    ApprovalRecord(
                        record_id=app_number,
                        product_name=product_name,
                        application_number=app_number,
                        approval_date=approval_date,
                        applicant=sponsor,
                        application_type=app_type,
                        source_url=f"https://api.fda.gov/drug/drugsfda.json?search=application_number:\"{app_number}\"",
                        raw_data=item,
                    )
                )
            except Exception:
                logger.debug("Skipping malformed approval record", exc_info=True)
        return results

    # ------------------------------------------------------------------
    # Public API — adverse events
    # ------------------------------------------------------------------

    def fetch_adverse_events(
        self, query: str, limit: int = 10
    ) -> List[AdverseEventRecord]:
        search = f'patient.drug.openfda.generic_name:"{query}"'
        data = self._get("event.json", search, limit)
        results: List[AdverseEventRecord] = []
        for item in data.get("results", []):
            try:
                safety_report_id = item.get("safetyreportid", "")
                receive_date = item.get("receivedate", "")
                # Reactions are nested
                reactions = item.get("patient", {}).get("reaction", [])
                reaction_str = ", ".join(
                    r.get("reactionmeddrapt", "") for r in reactions[:5]
                )
                # Outcome
                outcome_raw = item.get("serious", "")
                if str(outcome_raw) == "1":
                    outcome = "serious"
                else:
                    outcome = "non-serious"

                # Product name from the drug list
                drugs = item.get("patient", {}).get("drug", [])
                product_name = query
                for d in drugs:
                    gn = d.get("openfda", {}).get("generic_name", [])
                    if gn:
                        product_name = gn[0]
                        break

                results.append(
                    AdverseEventRecord(
                        record_id=safety_report_id,
                        product_name=product_name,
                        event_date=receive_date,
                        reaction=reaction_str,
                        outcome=outcome,
                        source_url=f"https://api.fda.gov/drug/event.json?search=safetyreportid:\"{safety_report_id}\"",
                        raw_data=item,
                    )
                )
            except Exception:
                logger.debug("Skipping malformed adverse-event record", exc_info=True)
        return results
