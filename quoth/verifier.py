"""Deterministic verification of interpreter operations against the transcript. No LLM.

Failures are reported with a reason; nothing is silently fixed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from quoth.domain import Domain

OP_TYPES = {"add", "update", "remove", "confirm", "deny"}

NUMBER_WORDS = {
    word: n
    for n, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}
NUMBER_WORDS |= {"a": 1, "an": 1, "single": 1, "couple": 2, "pair": 2, "dozen": 12}


@dataclass
class Verdict:
    """outcome: "ok", "pending" (accept but must ask), "ambiguous" (ask which target), "target_choice"
    (answers an open target question), or "reject"."""

    outcome: str
    reason: str = ""
    op: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)


def normalize(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower()).strip("'")


def span_words(utterance: dict[str, Any], span: list[int]) -> list[dict[str, Any]]:
    """Words in an inclusive [start, end] span."""
    return utterance["words"][span[0] : span[1] + 1]


def build_evidence(utterance: dict[str, Any], span: list[int]) -> dict[str, Any]:
    words = span_words(utterance, span)
    return {
        "utterance_id": utterance["utterance_id"],
        "word_span": list(span),
        "start_sec": words[0]["start"],
        "end_sec": words[-1]["end"],
        "quote": " ".join(w["word"] for w in words),
        "min_word_confidence": min(w["confidence"] for w in words),
    }


def verify(
    proposed: dict[str, Any],
    utterance: dict[str, Any],
    state: dict[str, Any],
    domain: Domain,
) -> Verdict:
    kind = proposed.get("op")
    if kind not in OP_TYPES:
        return Verdict("reject", f"unknown op type: {kind!r}")

    span = proposed.get("word_span")
    n_words = len(utterance["words"])
    if (
        not isinstance(span, list)
        or len(span) != 2
        or not all(isinstance(i, int) for i in span)
        or not 0 <= span[0] <= span[1] < n_words
    ):
        return Verdict("reject", f"word span {span!r} is not within the utterance ({n_words} words)")

    actual = [normalize(w["word"]) for w in span_words(utterance, span)]
    quoted = [normalize(w) for w in str(proposed.get("quote", "")).split()]
    if quoted != actual:
        return Verdict("reject", f"quote {proposed.get('quote')!r} does not match words at span: {' '.join(actual)!r}")

    evidence = build_evidence(utterance, span)
    name = proposed.get("field")
    value = proposed.get("value")
    entity_id = (proposed.get("target") or {}).get("entity")

    if kind == "confirm" and name is None:
        return _verify_target_choice(entity_id, state, evidence)
    if kind != "remove" and name not in domain.fields:
        return Verdict("reject", f"unknown field: {name!r}")

    if kind in ("add", "update"):
        problem = _check_value(name, value, actual, domain)
        if problem:
            return Verdict("reject", problem)

    live = {eid for eid, e in state["entities"].items() if not e["removed"]}
    candidates = proposed.get("correction_target_candidates") or []
    if proposed.get("is_correction") and candidates:
        missing = [c for c in candidates if c not in live]
        if missing:
            return Verdict("reject", f"correction targets do not exist: {missing}")
        if len(set(candidates)) > 1:
            op = {"op": kind, "entity": None, "field": name, "value": value}
            return Verdict("ambiguous", "ambiguous correction target", op, evidence, sorted(set(candidates)))
        entity_id = candidates[0]

    op = {"op": kind, "entity": entity_id, "field": name, "value": value}
    problem = _check_target(op, state, domain, live)
    if problem:
        return Verdict("reject", problem)

    allowed = domain.allowed_values(name) if kind in ("add", "update") else None
    if allowed is not None:
        values = value if isinstance(value, list) else [value]
        outside = [v for v in values if v not in allowed]
        if outside:
            return Verdict("pending", f"not in allowed values: {outside}", op, evidence)
    return Verdict("ok", "", op, evidence)


def _verify_target_choice(entity_id: str | None, state: dict[str, Any], evidence: dict[str, Any]) -> Verdict:
    question = state["open_question"]
    if not question or question["kind"] != "choose_target":
        return Verdict("reject", "confirm without a field, but no target question is open")
    if entity_id not in question["candidates"]:
        return Verdict("reject", f"{entity_id!r} is not one of the offered targets {question['candidates']}")
    op = {"op": "confirm", "entity": entity_id, "field": None, "value": None}
    return Verdict("target_choice", "", op, evidence, [question["target_id"]])


def _check_value(name: str, value: Any, span: list[str], domain: Domain) -> str | None:
    spec = domain.fields[name]
    if spec["type"] == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{name} must be an integer, got {value!r}"
        if not spec.get("min", value) <= value <= spec.get("max", value):
            return f"{name}={value} is outside [{spec.get('min')}, {spec.get('max')}]"
        if not any(w == str(value) or NUMBER_WORDS.get(w) == value for w in span):
            return f"number {value} does not appear in the evidence span"
    elif spec["type"] == "string":
        if not isinstance(value, str) or not value.strip():
            return f"{name} must be a non-empty string, got {value!r}"
    elif spec["type"] == "list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return f"{name} must be a list of strings, got {value!r}"
    return None


def _check_target(op: dict[str, Any], state: dict[str, Any], domain: Domain, live: set[str]) -> str | None:
    entity_id, name = op["entity"], op["field"]
    if not isinstance(entity_id, str) or not re.fullmatch(rf"{domain.entity_prefix}_\d+", entity_id):
        return f"invalid entity id: {entity_id!r}"
    if op["op"] == "add":
        if entity_id in state["entities"] and entity_id not in live:
            return f"{entity_id} was removed"
        existing = state["entities"].get(entity_id, {}).get("fields", {}).get(name)
        if existing and existing["value"] is not None:
            return f"{entity_id}.{name} already has a value; expected update"
        return None
    if entity_id not in live:
        return f"{entity_id} does not exist"
    if op["op"] in ("confirm", "deny"):
        existing = state["entities"][entity_id]["fields"].get(name)
        if not existing or existing["value"] is None:
            return f"{entity_id}.{name} has no value to {op['op']}"
    return None
