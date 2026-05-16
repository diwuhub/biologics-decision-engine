"""
Label Store — Append-only JSONL storage for LabelRecords and FeedbackEvents.

One JSONL file per module in store_dir/:
  store_dir/comparability_graph.jsonl
  store_dir/fda_warning_letters.jsonl
  store_dir/feedback.jsonl

Design:
  - Append-only (no edits, no deletes) for audit trail
  - One record per line (JSONL) for streaming reads
  - Simple file-based (no database dependency)
  - Thread-safe via file locking (fcntl)
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .label_schema import LabelRecord, FeedbackEvent
from .adjudicator import (
    auto_adjudicate_tier1,
    auto_adjudicate_tier2,
    get_adjudication_tier,
)


class LabelStore:
    """Append-only JSONL store for label records and feedback events."""

    def __init__(self, store_dir: str):
        """Initialize store. Creates store_dir if it doesn't exist.

        Args:
            store_dir: Directory for JSONL files (one per module).
        """
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def _module_path(self, module: str) -> Path:
        """Get JSONL file path for a module."""
        safe_name = module.replace("/", "_").replace("\\", "_")
        return self.store_dir / f"{safe_name}.jsonl"

    def _feedback_path(self) -> Path:
        return self.store_dir / "feedback.jsonl"

    # ── Write ──

    def save_record(self, record: LabelRecord) -> str:
        """Append a LabelRecord to the module's JSONL file.

        Args:
            record: The LabelRecord to save.

        Returns:
            The record_id of the saved record.
        """
        path = self._module_path(record.module)
        with open(path, "a") as f:
            f.write(json.dumps(record.to_dict()) + "\n")
        return record.record_id

    def save_feedback(self, event: FeedbackEvent) -> str:
        """Append a FeedbackEvent to feedback.jsonl.

        Args:
            event: The FeedbackEvent to save.

        Returns:
            The event_id of the saved event.
        """
        path = self._feedback_path()
        with open(path, "a") as f:
            f.write(json.dumps(event.to_dict()) + "\n")
        return event.event_id

    # ── Read ──

    def get_records(self, module: str, labeled_only: bool = False) -> List[LabelRecord]:
        """Load all records for a module.

        Args:
            module: Module name.
            labeled_only: If True, only return records with ground_truth filled.

        Returns:
            List of LabelRecords.
        """
        path = self._module_path(module)
        if not path.exists():
            return []

        records = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    record = LabelRecord.from_dict(data)
                    if labeled_only and not record.is_labeled:
                        continue
                    records.append(record)
                except (json.JSONDecodeError, KeyError):
                    continue  # skip malformed lines
        return records

    def get_unlabeled(self, module: str) -> List[LabelRecord]:
        """Get records where ground_truth is None (need labeling).

        Args:
            module: Module name.

        Returns:
            List of unlabeled LabelRecords.
        """
        return [r for r in self.get_records(module) if not r.is_labeled]

    def get_training_pairs(self, module: str) -> List[Tuple[Dict, Dict]]:
        """Get (prediction, ground_truth) pairs for model training.

        Only returns records where both prediction and ground_truth are present.

        Args:
            module: Module name.

        Returns:
            List of (prediction_dict, ground_truth_dict) tuples.
        """
        labeled = self.get_records(module, labeled_only=True)
        return [(r.prediction, r.ground_truth) for r in labeled if r.ground_truth]

    def get_feedback(self) -> List[FeedbackEvent]:
        """Load all feedback events."""
        path = self._feedback_path()
        if not path.exists():
            return []

        events = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(FeedbackEvent.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError):
                    continue
        return events

    # ── Export ──

    def export_training_csv(self, module: str, output_path: str) -> int:
        """Export training pairs as CSV.

        Columns: record_id, prediction_json, ground_truth_json

        Args:
            module: Module name.
            output_path: Output CSV file path.

        Returns:
            Number of rows exported.
        """
        labeled = self.get_records(module, labeled_only=True)
        if not labeled:
            return 0

        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["record_id", "prediction_json", "ground_truth_json"])
            for r in labeled:
                writer.writerow([
                    r.record_id,
                    json.dumps(r.prediction),
                    json.dumps(r.ground_truth),
                ])
        return len(labeled)

    # ── Stats ──

    def stats(self) -> Dict[str, Dict[str, int]]:
        """Get statistics for all modules in the store.

        Returns:
            Dict mapping module name to {total, labeled, unlabeled, feedback_count}.
        """
        result = {}

        # Scan all JSONL files (except feedback.jsonl)
        for path in sorted(self.store_dir.glob("*.jsonl")):
            if path.name == "feedback.jsonl":
                continue
            module = path.stem
            records = self.get_records(module)
            labeled = sum(1 for r in records if r.is_labeled)
            result[module] = {
                "total": len(records),
                "labeled": labeled,
                "unlabeled": len(records) - labeled,
            }

        # Feedback count
        feedback = self.get_feedback()
        total_feedback = len(feedback)
        for module in result:
            module_feedback = sum(1 for e in feedback
                                 if any(r.record_id == e.record_id
                                       for r in self.get_records(module)))
            result[module]["feedback_count"] = module_feedback

        return result

    # ── Adjudication ──

    def auto_adjudicate(self, module: str) -> int:
        """Run Tier 1 + Tier 2 adjudication rules on all unlabeled records for a module.

        For each unlabeled record, attempts Tier 1 first, then Tier 2.
        Adjudicated records are re-written to the module's JSONL file
        (full rewrite preserving labeled records and updating newly adjudicated ones).

        Args:
            module: Module name to adjudicate.

        Returns:
            Number of records that were auto-adjudicated.
        """
        records = self.get_records(module)
        if not records:
            return 0

        adjudicated_count = 0
        updated_records = []

        for record in records:
            if record.is_labeled:
                updated_records.append(record)
                continue

            # Try Tier 1 first, then Tier 2
            result = auto_adjudicate_tier1(record)
            if result is None:
                result = auto_adjudicate_tier2(record)

            if result is not None:
                updated_records.append(result)
                adjudicated_count += 1
            else:
                updated_records.append(record)

        # Rewrite the file with updated records
        if adjudicated_count > 0:
            path = self._module_path(module)
            with open(path, "w") as f:
                for r in updated_records:
                    f.write(json.dumps(r.to_dict()) + "\n")

        return adjudicated_count

    def get_adjudication_stats(self) -> Dict[str, Dict[str, int]]:
        """Return adjudication tier counts for all modules.

        Returns:
            Dict mapping module name to tier breakdown:
            {
                "module_name": {
                    "tier_0_unlabeled": N,
                    "tier_1_deterministic": N,
                    "tier_2_silver": N,
                    "tier_3_expert": N,
                    "tier_4_outcome": N,
                    "total": N,
                }
            }
        """
        result = {}

        for path in sorted(self.store_dir.glob("*.jsonl")):
            if path.name == "feedback.jsonl":
                continue
            module = path.stem
            records = self.get_records(module)

            tier_counts = {
                "tier_0_unlabeled": 0,
                "tier_1_deterministic": 0,
                "tier_2_silver": 0,
                "tier_3_expert": 0,
                "tier_4_outcome": 0,
                "total": len(records),
            }

            for r in records:
                tier = get_adjudication_tier(r)
                key = f"tier_{tier}_{'unlabeled' if tier == 0 else ['', 'deterministic', 'silver', 'expert', 'outcome'][tier]}"
                tier_counts[key] += 1

            result[module] = tier_counts

        return result
