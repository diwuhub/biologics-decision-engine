"""
Label Emitter — Convenience helper for modules to emit LabelRecords.

Usage in any module:
    from schemas.label_emitter import emit_label
    emit_label("comparability_graph", {"score": 0.72}, store_dir="labels/")
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

# Ensure schemas package is importable
_SCHEMA_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCHEMA_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from schemas.label_schema import LabelRecord
from schemas.label_store import LabelStore

_DEFAULT_STORE_DIR = os.path.join(_REPO_ROOT, "labels")


def emit_label(
    module: str,
    prediction: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    store_dir: Optional[str] = None,
) -> str:
    """Emit a LabelRecord with prediction and empty ground_truth.

    Args:
        module: Source module name.
        prediction: The module's output dict.
        metadata: Optional context (input params, version, etc.).
        store_dir: Override label store directory.

    Returns:
        record_id of the saved record.
    """
    store = LabelStore(store_dir or _DEFAULT_STORE_DIR)
    record = LabelRecord(
        module=module,
        prediction=prediction,
        metadata=metadata or {},
    )
    return store.save_record(record)
