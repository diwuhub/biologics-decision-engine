"""
Shadow Divergence Logger -- Logs comparison between OLD and NEW judgment paths.

Phase 1: Shadow Integration.
Writes one JSONL line per pipeline run. The 'divergent' field does a
best-effort semantic comparison using the verdict translation table.

DEPRECATED (Phase 3): Shadow infrastructure is no longer needed now that
Judgment Core is the primary path. This module is retained for log analysis
only. Will be archived in Phase 5.
"""
import warnings as _warnings
_warnings.warn(
    "services.shadow_logger is deprecated since Phase 3 (Judgment Core cutover). "
    "Shadow logging is no longer needed.",
    DeprecationWarning,
    stacklevel=2,
)

import json
import os
import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / 'logs'
LOG_FILE = LOG_DIR / 'shadow_divergence.jsonl'


def log_shadow_divergence(**kwargs):
    """Append a shadow divergence entry to the JSONL log file."""
    LOG_DIR.mkdir(exist_ok=True)
    entry = {
        'timestamp': datetime.datetime.now().isoformat(),
        **kwargs,
        'divergent': _is_divergent(kwargs),
    }
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(entry) + '\n')


def _is_divergent(kwargs):
    """Check if OLD and NEW verdicts diverge semantically.

    Uses the verdict translation map from Part I of the convergence spec.
    """
    VERDICT_MAP = {
        'proceed': 'Comparable',
        'proceed_with_conditions': 'Comparable With Caveats',
        'supplement_required': 'Not Comparable',
        'investigation_required': 'Not Comparable',
        'defer_package': 'Insufficient Evidence',
    }
    new_v = kwargs.get('new_verdict', '')
    old_v = kwargs.get('old_overall', '')
    return VERDICT_MAP.get(new_v, '') != old_v
