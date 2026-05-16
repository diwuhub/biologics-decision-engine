"""
P8-D: Narrative Signal Detector.

Keyword-based detection of regulatory and quality signals in document
narrative text. No LLM -- pure pattern matching.

Detects: OOS, bridging study, CAPA, deviation, trend, specification change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NarrativeSignal:
    """A signal detected in document narrative text."""
    signal_type: str           # e.g., "oos", "bridging_study", "capa", "deviation", "trend", "spec_change"
    keyword_matched: str       # the actual keyword/phrase matched
    context: str               # surrounding text (sentence or paragraph)
    paragraph_index: int       # index in document paragraph list
    severity: str = "info"     # "info", "warning", "critical"
    confidence: float = 1.0    # detection confidence


# ---------------------------------------------------------------------------
# Signal detection patterns
# ---------------------------------------------------------------------------

_SIGNAL_PATTERNS: List[Dict[str, Any]] = [
    {
        "signal_type": "oos",
        "severity": "critical",
        "patterns": [
            r"\bOOS\b",
            r"\bout[\s-]of[\s-]spec(ification)?\b",
            r"\bout[\s-]of[\s-]range\b",
            r"\bOOT\b",
            r"\bout[\s-]of[\s-]trend\b",
            r"\bexceed(s|ed|ing)?\s+(the\s+)?spec(ification)?\b",
        ],
    },
    {
        "signal_type": "bridging_study",
        "severity": "warning",
        "patterns": [
            r"\bbridging\s+stud(y|ies)\b",
            r"\bbridging\s+data\b",
            r"\bbridging\s+exercise\b",
            r"\bcomparability\s+bridging\b",
        ],
    },
    {
        "signal_type": "capa",
        "severity": "warning",
        "patterns": [
            r"\bCAPA\b",
            r"\bcorrective\s+(and\s+)?preventive\s+action\b",
            r"\bcorrective\s+action\b",
            r"\bpreventive\s+action\b",
            r"\broot\s+cause\s+(analysis|investigation)\b",
        ],
    },
    {
        "signal_type": "deviation",
        "severity": "warning",
        "patterns": [
            r"\bdeviation\b",
            r"\bnon[\s-]conformance\b",
            r"\bnon[\s-]compliance\b",
            r"\banomaly\b",
            r"\bincident\b",
            r"\bexcursion\b",
        ],
    },
    {
        "signal_type": "trend",
        "severity": "info",
        "patterns": [
            r"\btrend(s|ing|ed)?\b",
            r"\bdrift(s|ing|ed)?\b",
            r"\bprogressiv(e|ely)\s+(increase|decrease|change)\b",
            r"\bgradual\s+(increase|decrease|shift)\b",
            r"\bstability\s+trend\b",
        ],
    },
    {
        "signal_type": "spec_change",
        "severity": "warning",
        "patterns": [
            r"\bspecification\s+change\b",
            r"\bspec(ification)?\s+(revision|update|modification|widening|tightening)\b",
            r"\breleas(e|ing)\s+limit\s+(change|revision)\b",
            r"\bacceptance\s+criteri(a|on)\s+(change|revision|update)\b",
        ],
    },
]


class NarrativeSignalDetector:
    """Detect regulatory/quality signals in document narrative text.

    Keyword-based detection for:
    - OOS (out of specification)
    - Bridging study
    - CAPA (corrective and preventive action)
    - Deviation / non-conformance
    - Trend / drift
    - Specification change
    """

    def detect_signals(self, parsed_doc: Dict[str, Any]) -> List[NarrativeSignal]:
        """Scan document text and detect narrative signals."""
        signals: List[NarrativeSignal] = []
        seen: set = set()  # deduplicate by (signal_type, paragraph_index)

        # Scan paragraphs
        for para in parsed_doc.get("paragraphs", []):
            text = para.get("text", "")
            if not text.strip():
                continue

            idx = para.get("index", 0)

            for signal_def in _SIGNAL_PATTERNS:
                for pattern in signal_def["patterns"]:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        key = (signal_def["signal_type"], idx)
                        if key in seen:
                            continue
                        seen.add(key)

                        # Extract surrounding context (up to 300 chars around match)
                        start = max(0, match.start() - 100)
                        end = min(len(text), match.end() + 200)
                        context = text[start:end].strip()

                        signals.append(NarrativeSignal(
                            signal_type=signal_def["signal_type"],
                            keyword_matched=match.group(0),
                            context=context,
                            paragraph_index=idx,
                            severity=signal_def["severity"],
                        ))
                        break  # one signal per type per paragraph

        # Also scan page-level text for tables' surrounding context
        for page in parsed_doc.get("pages", []):
            page_text = page.get("text", "")
            if not page_text:
                continue

            # Split into sentences for finer-grained context
            sentences = re.split(r'(?<=[.!?])\s+', page_text)
            for s_idx, sentence in enumerate(sentences):
                for signal_def in _SIGNAL_PATTERNS:
                    for pattern in signal_def["patterns"]:
                        match = re.search(pattern, sentence, re.IGNORECASE)
                        if match:
                            key = (signal_def["signal_type"], f"page_{s_idx}")
                            if key in seen:
                                continue
                            seen.add(key)

                            signals.append(NarrativeSignal(
                                signal_type=signal_def["signal_type"],
                                keyword_matched=match.group(0),
                                context=sentence.strip()[:300],
                                paragraph_index=-1,
                                severity=signal_def["severity"],
                            ))
                            break

        return signals
