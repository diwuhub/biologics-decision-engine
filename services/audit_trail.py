"""
Audit Trail — foundation for 21 CFR Part 11 compliance.

Records every significant system action with timestamp, user identity,
action type, and details. Supports export for regulatory inspection.

This is the foundation layer. Full 21 CFR Part 11 requires:
- Electronic signatures (not yet implemented)
- Access controls (not yet implemented)
- Data integrity validation (not yet implemented)

What IS implemented:
- Immutable append-only audit log
- Timestamped entries with action classification
- Session-scoped tracking (persists across Streamlit reruns)
- Export to CSV/JSON for inspection
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    """One immutable audit trail entry."""
    entry_id: str
    timestamp: str
    action_type: str       # UPLOAD, CLASSIFY, EXTRACT, ASSESS, EXPORT, USER_EDIT, CONFIRM
    action_detail: str
    document_name: Optional[str] = None
    document_type: Optional[str] = None
    user_id: str = "system"
    session_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class AuditTrail:
    """Append-only audit trail with optional SQLite persistence.

    If db_path is provided, entries are written to SQLite for
    tamper-evident persistence across sessions. Each entry includes
    a SHA-256 hash chain (previous_hash + entry_data) for integrity.
    """

    def __init__(self, session_id: str = "", db_path: Optional[str] = None):
        self._entries: List[AuditEntry] = []
        self.session_id = session_id or f"SESSION-{uuid.uuid4().hex[:8].upper()}"
        self._db_path = db_path
        self._last_hash = "0" * 64  # genesis hash
        if db_path:
            self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database for persistent audit trail."""
        import sqlite3
        import os
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_entries (
                entry_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_detail TEXT NOT NULL,
                document_name TEXT,
                document_type TEXT,
                user_id TEXT DEFAULT 'system',
                session_id TEXT,
                metadata TEXT,
                hash_chain TEXT NOT NULL
            )
        """)
        conn.commit()
        # Load last hash for chain continuity
        row = conn.execute(
            "SELECT hash_chain FROM audit_entries ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row:
            self._last_hash = row[0]
        conn.close()

    def _compute_hash(self, entry: AuditEntry) -> str:
        """Compute SHA-256 hash chain entry."""
        import hashlib
        data = f"{self._last_hash}|{entry.entry_id}|{entry.timestamp}|{entry.action_type}|{entry.action_detail}"
        return hashlib.sha256(data.encode()).hexdigest()

    def log(
        self,
        action_type: str,
        action_detail: str,
        document_name: Optional[str] = None,
        document_type: Optional[str] = None,
        user_id: str = "system",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """Record an audit event. Returns the created entry."""
        entry = AuditEntry(
            entry_id=f"AUDIT-{uuid.uuid4().hex[:12].upper()}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            action_type=action_type,
            action_detail=action_detail,
            document_name=document_name,
            document_type=document_type,
            user_id=user_id,
            session_id=self.session_id,
            metadata=metadata or {},
        )
        self._entries.append(entry)
        logger.info("AUDIT [%s] %s: %s", action_type, document_name or "-", action_detail)

        # Persist to SQLite if configured
        if self._db_path:
            entry_hash = self._compute_hash(entry)
            self._last_hash = entry_hash
            try:
                import sqlite3
                conn = sqlite3.connect(self._db_path)
                conn.execute(
                    "INSERT INTO audit_entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (entry.entry_id, entry.timestamp, entry.action_type,
                     entry.action_detail, entry.document_name, entry.document_type,
                     entry.user_id, entry.session_id,
                     json.dumps(entry.metadata, default=str), entry_hash),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("Failed to persist audit entry: %s", e)

        return entry

    @property
    def entries(self) -> List[AuditEntry]:
        """Read-only access to all entries."""
        return list(self._entries)

    @property
    def count(self) -> int:
        return len(self._entries)

    def get_by_document(self, document_name: str) -> List[AuditEntry]:
        """Get all entries for a specific document."""
        return [e for e in self._entries if e.document_name == document_name]

    def get_by_type(self, action_type: str) -> List[AuditEntry]:
        """Get all entries of a specific action type."""
        return [e for e in self._entries if e.action_type == action_type]

    # --- Export ---

    def to_json(self) -> str:
        """Export audit trail as JSON string."""
        return json.dumps(
            [asdict(e) for e in self._entries],
            indent=2,
            default=str,
        )

    def to_csv(self) -> str:
        """Export audit trail as CSV string."""
        output = io.StringIO()
        if not self._entries:
            return ""
        fieldnames = [
            "entry_id", "timestamp", "action_type", "action_detail",
            "document_name", "document_type", "user_id", "session_id",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for entry in self._entries:
            row = {k: getattr(entry, k, "") for k in fieldnames}
            writer.writerow(row)
        return output.getvalue()

    def to_records(self) -> List[Dict[str, Any]]:
        """Export as list of dicts (for DataFrame display)."""
        return [
            {
                "Time": e.timestamp[:19].replace("T", " "),
                "Action": e.action_type,
                "Detail": e.action_detail[:80],
                "Document": e.document_name or "-",
                "Type": e.document_type or "-",
            }
            for e in self._entries
        ]


# ---------------------------------------------------------------------------
# Singleton for Streamlit session
# ---------------------------------------------------------------------------

_global_trail: Optional[AuditTrail] = None


def get_audit_trail(session_id: str = "") -> AuditTrail:
    """Get or create the session-scoped audit trail."""
    global _global_trail
    if _global_trail is None:
        _global_trail = AuditTrail(session_id=session_id)
    return _global_trail


def reset_audit_trail() -> None:
    """Reset the audit trail (for new sessions)."""
    global _global_trail
    _global_trail = None
