#!/usr/bin/env python3
"""
Precedent & guideline verification report.

Counts entries by content_type, verified status, and document_status.
Reports distribution across all evidence registry YAML files.

Usage:
    python3 scripts/verify_precedents.py
"""

import os
import sys
from collections import Counter, defaultdict

# Use standard import (editable install)
from evidence_registry import EvidenceRegistry


def main():
    registry = EvidenceRegistry()
    entries = registry._entries

    print(f"Total entries: {len(entries)}\n")

    # --- Verification status ---
    verified_count = Counter()
    for e in entries:
        verified_count[e.verified] += 1

    print("=== Verified Status ===")
    for status, count in sorted(verified_count.items(), key=lambda x: -x[1]):
        label = "verified" if status else "not verified"
        print(f"  {label}: {count}")
    print()

    # --- Content type distribution ---
    content_type_count = Counter()
    for e in entries:
        content_type_count[e.content_type] += 1

    print("=== Content Type ===")
    for ct, count in sorted(content_type_count.items(), key=lambda x: -x[1]):
        print(f"  {ct}: {count}")
    print()

    # --- Verification method ---
    method_count = Counter()
    for e in entries:
        method_count[e.verification_method] += 1

    print("=== Verification Method ===")
    for method, count in sorted(method_count.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count}")
    print()

    # --- Document status (guideline entries only) ---
    doc_status_count = Counter()
    for e in entries:
        if e.document_status:
            doc_status_count[e.document_status] += 1

    if doc_status_count:
        print("=== Document Status (guidelines) ===")
        for status, count in sorted(doc_status_count.items(), key=lambda x: -x[1]):
            print(f"  {status}: {count}")
        print()

    # --- By entry type ---
    type_x_verified = defaultdict(Counter)
    type_x_content = defaultdict(Counter)
    for e in entries:
        type_x_verified[e.entry_type][e.verified] += 1
        type_x_content[e.entry_type][e.content_type] += 1

    print("=== By Entry Type ===")
    for etype in sorted(type_x_verified.keys()):
        v = type_x_verified[etype]
        c = type_x_content[etype]
        total = sum(v.values())
        print(f"  {etype} ({total} entries):")
        print(f"    verified: {v.get(True, 0)}, not verified: {v.get(False, 0)}")
        for ct, count in sorted(c.items(), key=lambda x: -x[1]):
            print(f"    content_type={ct}: {count}")
    print()

    print("Done.")


if __name__ == "__main__":
    main()
