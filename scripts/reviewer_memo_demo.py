#!/usr/bin/env python3
"""Offline reviewer memo demo.

Examples:
    python scripts/reviewer_memo_demo.py
    python scripts/reviewer_memo_demo.py --input assessor_output.json --output memo.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.reviewer_memo import build_reviewer_memo


def _demo_engine_output() -> Dict[str, Any]:
    return {
        "case_id": "DEMO-CMC-001",
        "analytical_conclusion": "Comparable With Caveats",
        "package_posture": "Supplement Required",
        "posture_rationale": (
            "Potency is within range, but orthogonal purity confirmation and "
            "updated stability trend justification are needed before relying on "
            "the comparability claim."
        ),
        "confidence_breakdown": {
            "analytical_confidence": 0.72,
            "package_readiness": 0.60,
            "evidence_completeness": 0.65,
        },
        "judgment": {
            "package_verdict": "supplement_required",
            "confidence": 0.66,
            "confidence_band": "moderate",
            "decision_rule_ids": ["CLUST-002", "PKG-006"],
        },
        "critical_attributes": [
            {
                "name": "SEC HMW",
                "category": "purity",
                "concern": "major",
                "score": 0.58,
            },
            {
                "name": "Accelerated Stability Trend",
                "category": "stability",
                "concern": "minor",
                "score": 0.70,
            },
        ],
        "blocking_clusters": [
            {
                "cluster_id": "CLU-PURITY-001",
                "dominant_category": "purity",
                "concern_level": "major",
            }
        ],
        "reviewer_risk": {
            "predicted_questions": [
                {
                    "question": (
                        "Please justify why the observed HMW trend does not "
                        "affect safety or efficacy."
                    ),
                    "source": "cluster_policy",
                }
            ]
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="JSON file containing deterministic engine output.")
    parser.add_argument("--output", type=Path, help="Optional markdown output path.")
    args = parser.parse_args()

    if args.input:
        deterministic_output = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        deterministic_output = _demo_engine_output()

    memo = build_reviewer_memo(deterministic_output)
    markdown = memo.as_markdown()

    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
