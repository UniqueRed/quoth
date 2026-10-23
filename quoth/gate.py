"""Confidence gate: decides whether a verified operation is confirmed, tentative, pending, or rejected.

Confidence is Deepgram's per-word score on final transcripts, taken as the minimum over the
words that carry the value. Transcript-level confidence is never used: it is a median and can
hide one bad word. High confidence can still be wrong (accents, domain mismatch); the gate
reduces silent errors, it does not eliminate them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quoth.domain import Domain
from quoth.verifier import NUMBER_WORDS, Verdict, normalize, span_words

FILLER = {
    "um", "uh", "er", "erm", "like", "the", "a", "an", "and", "please", "make", "that", "it",
    "actually", "wait", "sorry", "i", "i'd", "want", "with", "of", "some", "can", "get",
}


@dataclass(frozen=True)
class Decision:
    event_type: str  # op_applied | op_downgraded | op_rejected
    status: str | None  # confirmed | tentative | pending, or None when rejected
    confidence: float | None
    reason: str = ""


def field_confidence(utterance: dict[str, Any], span: list[int], numeric: bool = False) -> float:
    """Minimum word confidence over the value-carrying words of a span (filler excluded)."""
    words = span_words(utterance, span)
    if numeric:
        carrying = [w for w in words if normalize(w["word"]).isdigit() or normalize(w["word"]) in NUMBER_WORDS]
    else:
        carrying = [w for w in words if normalize(w["word"]) not in FILLER]
    return min(w["confidence"] for w in carrying or words)


def decide(verdict: Verdict, utterance: dict[str, Any], domain: Domain, criticality: str | None = None) -> Decision:
    """Gate one verified operation. `criticality` overrides the op field's (used for target choices)."""
    if verdict.outcome == "reject":
        return Decision("op_rejected", None, None, verdict.reason)

    op = verdict.op
    numeric = op["field"] in domain.fields and domain.fields[op["field"]]["type"] == "integer"
    confidence = field_confidence(utterance, verdict.evidence["word_span"], numeric=numeric and op["op"] != "deny")
    decision = classify(confidence, criticality or domain.criticality(op["field"]), domain)

    if verdict.outcome in ("ambiguous", "pending"):
        return Decision("op_downgraded", "pending", confidence, verdict.reason)
    if op["op"] == "remove":
        return Decision("op_applied", "confirmed", confidence)
    # Answers to questions are applied only when heard clearly; otherwise the item stays pending.
    if op["op"] in ("confirm", "deny") and decision.status != "confirmed":
        return Decision("op_rejected", None, confidence, f"answer unclear: {decision.reason}")
    return decision


def classify(confidence: float, criticality: str, domain: Domain) -> Decision:
    """The criticality table: confirmed above the bar; below it, pending if critical, else tentative."""
    threshold = domain.thresholds.get(criticality, 0.0)
    if criticality == "low" or confidence >= threshold:
        return Decision("op_applied", "confirmed", confidence)
    if criticality == "critical":
        return Decision("op_downgraded", "pending", confidence, f"critical field: confidence {confidence:.2f} < {threshold}")
    return Decision("op_downgraded", "tentative", confidence, f"confidence {confidence:.2f} < {threshold}")
