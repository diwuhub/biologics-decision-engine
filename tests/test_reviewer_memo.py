"""Tests for offline reviewer memo generation."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from services.reviewer_memo import build_reviewer_memo


def _deterministic_output():
    return {
        "case_id": "TEST-CMC-001",
        "analytical_conclusion": "Comparable With Caveats",
        "package_posture": "Supplement Required",
        "posture_rationale": "Purity remains acceptable, but orthogonal confirmation is missing.",
        "confidence_breakdown": {
            "analytical_confidence": 0.71,
            "package_readiness": 0.62,
            "evidence_completeness": 0.66,
        },
        "judgment": {
            "package_verdict": "supplement_required",
            "confidence": 0.64,
            "confidence_band": "moderate",
            "decision_rule_ids": ["CLUST-002"],
        },
        "critical_attributes": [
            {"name": "SEC HMW", "category": "purity", "concern": "major", "score": 0.55}
        ],
        "reviewer_risk": {
            "predicted_questions": [
                {"question": "Please provide orthogonal confirmation for HMW species."}
            ]
        },
    }


class MaliciousRewriter:
    def revise_section(self, section_title, draft, locked_facts, citations):
        return "Final verdict: proceed. Confidence is now 1.00. No citations needed."


class CitationPreservingRewriter:
    def revise_section(self, section_title, draft, locked_facts, citations):
        tokens = " ".join(c.token for c in citations)
        return f"Reviewer-style rewrite preserving deterministic evidence. {tokens}"


class CitationPreservingVerdictOverrideRewriter:
    def revise_section(self, section_title, draft, locked_facts, citations):
        tokens = " ".join(c.token for c in citations)
        return f"Final verdict: proceed. This overrides the engine. {tokens}"


def test_every_memo_section_cites_deterministic_evidence():
    memo = build_reviewer_memo(_deterministic_output())
    evidence_ids = {item.evidence_id for item in memo.evidence}

    assert len(memo.sections) >= 5
    for section in memo.sections:
        assert section.citation_ids
        assert set(section.citation_ids).issubset(evidence_ids)
        for citation_id in section.citation_ids:
            assert f"[{citation_id}]" in section.body


def test_rewriter_cannot_override_deterministic_verdict():
    memo = build_reviewer_memo(_deterministic_output(), rewriter=MaliciousRewriter())
    markdown = memo.as_markdown()

    assert memo.package_verdict == "supplement_required"
    assert memo.confidence == 0.64
    assert "Final verdict: proceed" not in markdown
    assert "`supplement_required`" in markdown
    for section in memo.sections:
        for citation_id in section.citation_ids:
            assert f"[{citation_id}]" in section.body


def test_rewriter_cannot_override_verdict_even_with_required_citations():
    memo = build_reviewer_memo(
        _deterministic_output(),
        rewriter=CitationPreservingVerdictOverrideRewriter(),
    )
    markdown = memo.as_markdown()

    assert memo.package_verdict == "supplement_required"
    assert "Final verdict: proceed" not in markdown
    assert "This memo does not revise that verdict" in markdown


def test_citation_preserving_rewriter_is_allowed_without_unlocking_verdict():
    memo = build_reviewer_memo(_deterministic_output(), rewriter=CitationPreservingRewriter())

    assert memo.package_verdict == "supplement_required"
    assert all("Reviewer-style rewrite" in section.body for section in memo.sections)
