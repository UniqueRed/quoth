"""Clarification: pick the next templated question about pending items, within the question budget."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from quoth.domain import CRITICALITY_RANK, Domain
from quoth.reducer import iter_fields

_PHONETIC_CODES = {
    **dict.fromkeys("bfpv", "1"),
    **dict.fromkeys("cgjkqsxz", "2"),
    **dict.fromkeys("dt", "3"),
    "l": "4",
    **dict.fromkeys("mn", "5"),
    "r": "6",
}


def phonetic_key(text: str) -> str:
    """A Soundex-style key per word, without truncation: first letter, then consonant classes."""
    keys = []
    for word in text.lower().split():
        letters = [c for c in word if c.isalpha()]
        if not letters:
            continue
        key, last = letters[0], _PHONETIC_CODES.get(letters[0])
        for c in letters[1:]:
            code = _PHONETIC_CODES.get(c)
            if code and code != last:
                key += code
            last = code
        keys.append(key)
    return " ".join(keys)


# Below this, nothing allowed sounds like what was heard (e.g. "pepperoni pizza" on a burger menu),
# so offering the two nearest values would be noise. Mishearings score 0.75+, off-list items under 0.5.
CLOSE_MATCH = 0.5

UNCLEAR_QUESTION = "Sorry, I didn't catch that. Could you say it again?"


def similarity(heard: str, option: str) -> float:
    """Average of string and phonetic similarity, 0..1."""
    text = SequenceMatcher(None, heard.lower(), option.lower()).ratio()
    sound = SequenceMatcher(None, phonetic_key(heard), phonetic_key(option)).ratio()
    return (text + sound) / 2


def rank_candidates(heard: str, options: list[str], exclude: set[str] = frozenset()) -> list[str]:
    """Rank allowed values by string and phonetic similarity to what was heard."""
    return sorted((o for o in options if o not in exclude), key=lambda o: similarity(heard, o), reverse=True)


def entity_label(state: dict[str, Any], entity_id: str, domain: Domain) -> str:
    label = state["entities"][entity_id]["fields"].get(domain.label_field)
    return f"the {label['value']}" if label and label["value"] else entity_id.replace("_", " ")


def _format_value(value: Any) -> str:
    return ", ".join(value) if isinstance(value, list) else str(value)


def _order(event_id: str) -> int:
    return int(event_id.split("_")[1])


def repeat_question() -> dict[str, Any]:
    """Asked when the interpreter flags a turn it could not turn into operations."""
    return {
        "kind": "repeat",
        "question": UNCLEAR_QUESTION,
        "field_ids": [],
        "target_id": None,
        "candidates": [],
        "reason": "the interpreter could not map the turn to operations",
    }


def next_question(state: dict[str, Any], domain: Domain, max_questions: int) -> dict[str, Any] | None:
    """The clarification_asked payload for the most critical pending item, or None."""
    if state["questions_asked"] >= max_questions:
        return None

    items = [
        (CRITICALITY_RANK[domain.criticality(f["field_id"].split(".", 1)[1])], _order(f["event_id"]), "field", f)
        for f in iter_fields(state)
        if f["status"] == "pending"
    ]
    items += [
        (CRITICALITY_RANK[domain.criticality(t["op"]["field"])], _order(target_id), "target", (target_id, t))
        for target_id, t in state["pending_targets"].items()
        if t["status"] == "pending"
    ]
    if not items:
        return None
    _, _, kind, item = min(items, key=lambda i: i[:2])
    if kind == "target":
        return _target_question(state, domain, *item)
    return _field_question(state, domain, item)


def _target_question(state: dict[str, Any], domain: Domain, target_id: str, target: dict[str, Any]) -> dict[str, Any]:
    labels = [entity_label(state, c, domain) for c in target["candidates"]]
    options = " or ".join([", ".join(labels[:-1]), labels[-1]]) if len(labels) > 1 else labels[0]
    return {
        "kind": "choose_target",
        "question": f"Which one did you mean: {options}?",
        "field_ids": [],
        "target_id": target_id,
        "candidates": target["candidates"],
        "reason": "correction could apply to more than one item",
    }


def _field_question(state: dict[str, Any], domain: Domain, field: dict[str, Any]) -> dict[str, Any]:
    entity_id, name = field["field_id"].split(".", 1)
    allowed = domain.allowed_values(name)
    value = field["value"]
    base = {"field_ids": [field["field_id"]], "target_id": None, "candidates": []}

    if value is not None and (allowed is None or all(v in allowed for v in _as_list(value))):
        shown = _format_value(value)
        if name != domain.label_field:
            shown += f" for {entity_label(state, entity_id, domain)}"
        reason = f"{name} is critical and confidence {field['confidence']:.2f} is below the bar"
        return {**base, "kind": "confirm_value", "question": f"Was that {shown}?", "reason": reason}

    if allowed:
        denied = {h["value"] for h in field["history"] if isinstance(h["value"], str)}
        heard = value if value is not None else field["history"][-1]["value"]
        top = rank_candidates(_format_value(heard), allowed, exclude=denied)[:2]
        if value is not None and (not top or similarity(_format_value(value), top[0]) < CLOSE_MATCH):
            question = f"Sorry, {_format_value(value)} isn't available. What would you like instead?"
            return {**base, "kind": "replace_value", "question": question, "reason": f"{value!r} is not a known {name}"}
        reason = "user denied the value" if value is None else f"{value!r} is not a known {name}"
        return {**base, "kind": "choose_value", "candidates": top, "question": f"{' or '.join(top)}?", "reason": reason}

    question = f"What {name} for {entity_label(state, entity_id, domain)}?"
    return {**base, "kind": "ask_value", "question": question, "reason": "user denied the value"}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def readback(state: dict[str, Any], domain: Domain, unheard: list[str] = ()) -> str:
    """Read back only the items that are still unresolved, plus turns that were never understood."""
    unresolved = [f for f in iter_fields(state) if f["status"] in ("pending", "tentative")]
    unresolved_targets = [t for t in state["pending_targets"].values() if t["status"] == "pending"]
    if not unresolved and not unresolved_targets and not unheard:
        return "Got it, your order is complete."
    parts = []
    for f in unresolved:
        entity_id, name = f["field_id"].split(".", 1)
        value = "unknown" if f["value"] is None else _format_value(f["value"])
        parts.append(f"{name} {value} for {entity_label(state, entity_id, domain)}")
    for t in unresolved_targets:
        parts.append(f"a change of {t['op']['field']} to {_format_value(t['op']['value'])}")
    for text in unheard:
        parts.append(f'"{text.strip().rstrip(".?!")}", which I didn\'t catch')
    return "Please double-check: " + "; ".join(parts) + "."
