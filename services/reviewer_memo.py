"""
Offline-safe reviewer memo generation.

The memo generator treats deterministic engine output as locked facts and
allows an optional LLM-like rewriter to improve phrasing only. Verdict,
confidence, and evidence citations are restored from deterministic inputs if
the rewriter omits citations or attempts to change the verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence


_VERDICT_VALUES = {
    "proceed",
    "proceed_with_conditions",
    "supplement_required",
    "investigation_required",
    "defer_package",
    "PACKAGE_READY",
    "PACKAGE_NEEDS_SUPPLEMENT",
    "PACKAGE_NOT_READY",
    "PACKAGE_INCOMPLETE",
    "NO_DOCUMENTS",
}


@dataclass(frozen=True)
class EvidenceCitation:
    """A deterministic evidence item cited by one or more memo sections."""

    evidence_id: str
    source: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def token(self) -> str:
        return f"[{self.evidence_id}]"


@dataclass(frozen=True)
class MemoSection:
    """A memo section plus the evidence IDs that must appear in its body."""

    title: str
    body: str
    citation_ids: List[str]


@dataclass(frozen=True)
class ReviewerMemo:
    """Evidence-cited memo derived from locked deterministic engine output."""

    case_id: str
    package_verdict: str
    confidence: float
    confidence_band: str
    analytical_conclusion: str
    package_posture: str
    evidence: List[EvidenceCitation]
    sections: List[MemoSection]

    def as_markdown(self) -> str:
        lines = [
            f"# CMC/Regulatory Reviewer Memo: {self.case_id}",
            "",
            f"**Locked deterministic verdict:** `{self.package_verdict}`",
            f"**Confidence:** {self.confidence:.2f} ({self.confidence_band})",
            f"**Analytical conclusion:** {self.analytical_conclusion}",
            f"**Package posture:** {self.package_posture}",
            "",
        ]
        for section in self.sections:
            lines.extend([f"## {section.title}", section.body, ""])
        lines.append("## Evidence Ledger")
        for item in self.evidence:
            lines.append(f"- {item.token} {item.source}: {item.text}")
        return "\n".join(lines).rstrip() + "\n"


class ReviewerMemoRewriter(Protocol):
    """Optional offline/mock LLM interface for section-level style edits."""

    def revise_section(
        self,
        section_title: str,
        draft: str,
        locked_facts: Mapping[str, Any],
        citations: Sequence[EvidenceCitation],
    ) -> str:
        """Return revised section text without changing locked facts."""


class NoOpReviewerMemoRewriter:
    """Default mock rewriter: deterministic, offline, and API-free."""

    def revise_section(
        self,
        section_title: str,
        draft: str,
        locked_facts: Mapping[str, Any],
        citations: Sequence[EvidenceCitation],
    ) -> str:
        return draft


def build_reviewer_memo(
    deterministic_output: Mapping[str, Any],
    rewriter: Optional[ReviewerMemoRewriter] = None,
) -> ReviewerMemo:
    """Build a reviewer memo from deterministic engine output.

    Parameters
    ----------
    deterministic_output:
        Assessor/package overview-like dict containing judgment, conclusion,
        rationale, critical attributes, and reviewer questions.
    rewriter:
        Optional mock or LLM-like object. It may only rephrase section prose.
        If it drops required citations or changes the locked verdict, the
        deterministic draft section is used instead.
    """
    locked = _locked_facts(deterministic_output)
    evidence = _build_evidence_ledger(deterministic_output, locked)
    evidence_by_id = {item.evidence_id: item for item in evidence}
    rewriter = rewriter or NoOpReviewerMemoRewriter()

    drafts = _draft_sections(deterministic_output, locked)
    sections: List[MemoSection] = []
    for title, draft, citation_ids in drafts:
        citations = [evidence_by_id[cid] for cid in citation_ids]
        revised = rewriter.revise_section(title, draft, locked, citations)
        body = _accept_or_restore_section(
            candidate=revised,
            fallback=draft,
            required_citation_ids=citation_ids,
            locked_facts=locked,
        )
        sections.append(MemoSection(title=title, body=body, citation_ids=list(citation_ids)))

    return ReviewerMemo(
        case_id=locked["case_id"],
        package_verdict=locked["package_verdict"],
        confidence=locked["confidence"],
        confidence_band=locked["confidence_band"],
        analytical_conclusion=locked["analytical_conclusion"],
        package_posture=locked["package_posture"],
        evidence=evidence,
        sections=sections,
    )


def _locked_facts(output: Mapping[str, Any]) -> Dict[str, Any]:
    judgment = _as_mapping(output.get("judgment"))
    confidence = _as_float(judgment.get("confidence"), default=0.0)
    package_verdict = str(
        judgment.get("package_verdict")
        or output.get("package_verdict")
        or output.get("package_verdict_display")
        or "unknown"
    )
    return {
        "case_id": str(output.get("case_id") or output.get("package_id") or "DEMO-CASE"),
        "package_verdict": package_verdict,
        "confidence": confidence,
        "confidence_band": str(judgment.get("confidence_band") or "unknown"),
        "analytical_conclusion": str(output.get("analytical_conclusion") or "Not provided"),
        "package_posture": str(output.get("package_posture") or "Not provided"),
        "posture_rationale": str(
            output.get("posture_rationale")
            or judgment.get("key_finding")
            or output.get("package_rationale")
            or "No deterministic rationale provided."
        ),
    }


def _build_evidence_ledger(
    output: Mapping[str, Any],
    locked: Mapping[str, Any],
) -> List[EvidenceCitation]:
    confidence_breakdown = _as_mapping(output.get("confidence_breakdown"))
    critical_attributes = _as_list(output.get("critical_attributes"))
    reviewer_risk = _as_mapping(output.get("reviewer_risk"))
    reviewer_questions = _as_list(reviewer_risk.get("predicted_questions"))
    blocking_clusters = _as_list(output.get("blocking_clusters"))

    critical_summary = _summarize_critical_attributes(critical_attributes)
    if blocking_clusters:
        cluster_summary = f"{len(blocking_clusters)} blocking/critical cluster(s): {_summarize_clusters(blocking_clusters)}"
    else:
        cluster_summary = "No blocking clusters were reported by the deterministic engine."

    if reviewer_questions:
        question_summary = "; ".join(
            str(q.get("question", q)) for q in reviewer_questions[:3] if isinstance(q, Mapping)
        )
    else:
        question_summary = "No reviewer questions were reported by the deterministic engine."

    return [
        EvidenceCitation(
            evidence_id="E1",
            source="deterministic_verdict",
            text=(
                f"Verdict={locked['package_verdict']}; confidence={locked['confidence']:.2f} "
                f"({locked['confidence_band']}); analytical conclusion="
                f"{locked['analytical_conclusion']}; package posture={locked['package_posture']}."
            ),
        ),
        EvidenceCitation(
            evidence_id="E2",
            source="deterministic_rationale",
            text=locked["posture_rationale"],
        ),
        EvidenceCitation(
            evidence_id="E3",
            source="confidence_breakdown",
            text=_format_confidence_breakdown(confidence_breakdown),
            metadata=dict(confidence_breakdown),
        ),
        EvidenceCitation(
            evidence_id="E4",
            source="attribute_and_cluster_findings",
            text=f"{critical_summary} {cluster_summary}",
        ),
        EvidenceCitation(
            evidence_id="E5",
            source="reviewer_risk",
            text=question_summary,
        ),
    ]


def _draft_sections(
    output: Mapping[str, Any],
    locked: Mapping[str, Any],
) -> List[tuple[str, str, tuple[str, ...]]]:
    critical_attributes = _as_list(output.get("critical_attributes"))
    reviewer_risk = _as_mapping(output.get("reviewer_risk"))
    reviewer_questions = _as_list(reviewer_risk.get("predicted_questions"))
    decision_rules = _as_list(_as_mapping(output.get("judgment")).get("decision_rule_ids"))

    if decision_rules:
        rule_text = f" Decision rules surfaced by the engine: {', '.join(map(str, decision_rules[:5]))}."
    else:
        rule_text = " No explicit decision rule IDs were surfaced."

    if critical_attributes:
        attr_text = _summarize_critical_attributes(critical_attributes)
    else:
        attr_text = "No critical attributes requiring review were reported."

    if reviewer_questions:
        questions = "; ".join(
            str(q.get("question", q)) for q in reviewer_questions[:3] if isinstance(q, Mapping)
        )
    else:
        questions = "No reviewer questions were predicted."

    next_action = _next_action_for_verdict(locked["package_verdict"])

    return [
        (
            "Deterministic Verdict",
            (
                f"The deterministic engine verdict is `{locked['package_verdict']}` with "
                f"confidence {locked['confidence']:.2f} ({locked['confidence_band']}). "
                f"The analytical conclusion is {locked['analytical_conclusion']} and the "
                f"package posture is {locked['package_posture']}. This memo does not revise "
                f"that verdict. [E1]"
            ),
            ("E1",),
        ),
        (
            "Evidence Basis",
            f"The engine rationale is: {locked['posture_rationale']} [E2] { _format_confidence_breakdown(_as_mapping(output.get('confidence_breakdown'))) } [E3]",
            ("E2", "E3"),
        ),
        (
            "CMC/Regulatory Assessment",
            f"Assessment focus: {attr_text} [E4]{rule_text} [E1]",
            ("E4", "E1"),
        ),
        (
            "Likely Reviewer Questions",
            f"Predicted reviewer pressure: {questions} [E5] Attribute and cluster context should be used to scope responses. [E4]",
            ("E5", "E4"),
        ),
        (
            "Recommended Next Actions",
            f"{next_action} The action is constrained by the locked deterministic verdict and reviewer-risk evidence. [E1] [E5]",
            ("E1", "E5"),
        ),
    ]


def _accept_or_restore_section(
    candidate: Any,
    fallback: str,
    required_citation_ids: Sequence[str],
    locked_facts: Mapping[str, Any],
) -> str:
    if not isinstance(candidate, str) or not candidate.strip():
        return fallback
    if not _contains_required_citations(candidate, required_citation_ids):
        return fallback
    if _appears_to_override_verdict(candidate, locked_facts):
        return fallback
    return candidate


def _contains_required_citations(text: str, citation_ids: Sequence[str]) -> bool:
    return all(f"[{citation_id}]" in text for citation_id in citation_ids)


def _appears_to_override_verdict(text: str, locked_facts: Mapping[str, Any]) -> bool:
    searchable_text = _searchable_text(text)
    locked_verdict = _normalize_verdict_text(str(locked_facts["package_verdict"]))

    for verdict in _VERDICT_VALUES:
        normalized_verdict = _normalize_verdict_text(verdict)
        if normalized_verdict == locked_verdict:
            continue
        verdict_phrase = normalized_verdict.replace("_", r"\s+")
        verdict_near_decision_word = re.search(
            rf"\b(?:verdict|decision|recommendation|conclusion)\b(?:\s+\w+){{0,12}}\s+{verdict_phrase}\b",
            searchable_text,
        )
        explicit_assignment = re.search(
            rf"\b(?:package\s+verdict|final\s+verdict|locked\s+verdict)\s+{verdict_phrase}\b",
            searchable_text,
        )
        if verdict_near_decision_word or explicit_assignment:
            return True
    return False


def _normalize_verdict_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _searchable_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _format_confidence_breakdown(confidence_breakdown: Mapping[str, Any]) -> str:
    if not confidence_breakdown:
        return "No confidence breakdown was provided."
    parts = []
    for key in ("analytical_confidence", "package_readiness", "evidence_completeness"):
        if key in confidence_breakdown:
            parts.append(f"{key}={confidence_breakdown[key]}")
    return "Confidence basis: " + (", ".join(parts) if parts else str(dict(confidence_breakdown))) + "."


def _summarize_critical_attributes(attributes: Sequence[Any]) -> str:
    if not attributes:
        return "No critical attributes requiring review were reported."
    rendered = []
    for attr in attributes[:4]:
        if isinstance(attr, Mapping):
            rendered.append(
                f"{attr.get('name', 'attribute')} ({attr.get('category', 'uncategorized')}, "
                f"concern={attr.get('concern', 'unknown')}, score={attr.get('score', 'n/a')})"
            )
        else:
            rendered.append(str(attr))
    return "Critical/review attributes: " + "; ".join(rendered) + "."


def _summarize_clusters(clusters: Sequence[Any]) -> str:
    rendered = []
    for cluster in clusters[:3]:
        if isinstance(cluster, Mapping):
            rendered.append(
                f"{cluster.get('cluster_id', 'cluster')} in "
                f"{cluster.get('dominant_category', 'unknown category')}"
            )
        else:
            rendered.append(str(cluster))
    return "; ".join(rendered)


def _next_action_for_verdict(package_verdict: str) -> str:
    normalized = _normalize_verdict_text(package_verdict)
    if normalized in {"proceed", "package_ready"}:
        return "Document the deterministic basis and prepare submission-ready reviewer responses."
    if normalized in {"proceed_with_conditions", "package_needs_supplement"}:
        return "Prepare targeted response packages for the identified conditions before submission."
    if normalized in {"supplement_required", "package_incomplete"}:
        return "Generate supplement plan and close missing evidence before relying on the package."
    if normalized in {"investigation_required", "defer_package", "package_not_ready"}:
        return "Hold the claim and resolve blocking evidence gaps before submission."
    return "Review deterministic output and define follow-up actions before submission use."


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
