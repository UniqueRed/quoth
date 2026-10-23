"""Pure, deterministic reducer: state = fold(events).

No I/O, no clock, no randomness. Every decision (status, confidence, evidence)
is already recorded in the event payloads; the reducer only applies them.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable

STATE_CHANGING = {"op_applied", "op_downgraded"}


def initial_state() -> dict[str, Any]:
    return {
        "entities": {},
        "pending_targets": {},
        "questions_asked": 0,
        "open_question": None,
        "finalized": False,
    }


def reduce(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    state = initial_state()
    for event in events:
        state = apply(state, event)
    return state


def apply(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Return a new state with one event applied. The input state is not mutated."""
    state = copy.deepcopy(state)
    kind = event["type"]
    if kind in STATE_CHANGING:
        if "candidates" in event:
            _add_pending_target(state, event)
        else:
            _apply_op(state, event)
    elif kind == "clarification_asked":
        state["questions_asked"] += 1
        state["open_question"] = {
            "event_id": event["id"],
            **{k: event[k] for k in ("kind", "question", "field_ids", "target_id", "candidates", "at_finalization")},
        }
    elif kind == "clarification_answered":
        state["open_question"] = None
    elif kind == "session_finalized":
        for field_id in event["needs_review"]:
            entity_id, name = field_id.split(".", 1)
            state["entities"][entity_id]["fields"][name]["status"] = "needs_review"
        for target_id in event["unresolved_targets"]:
            state["pending_targets"][target_id]["status"] = "needs_review"
        state["open_question"] = None
        state["finalized"] = True
    return state


def _add_pending_target(state: dict[str, Any], event: dict[str, Any]) -> None:
    state["pending_targets"][event["id"]] = {
        "op": event["op"],
        "candidates": event["candidates"],
        "confidence": event["confidence"],
        "evidence": event["evidence"],
        "status": "pending",
    }


def _apply_op(state: dict[str, Any], event: dict[str, Any]) -> None:
    op = event["op"]
    entity_id = op["entity"]
    entities = state["entities"]
    if event.get("resolves"):
        state["pending_targets"].pop(event["resolves"], None)

    if op["op"] == "remove":
        entities[entity_id]["removed"] = True
        return

    entity = entities.setdefault(entity_id, {"removed": False, "fields": {}})
    name = op["field"]
    field = entity["fields"].get(name)

    if op["op"] == "confirm":
        field["status"] = "confirmed"
        return

    if field is not None and field["value"] is not None:
        field["history"].append(
            {"event_id": field["event_id"], "value": field["value"], "status": "superseded"}
        )
    history = field["history"] if field is not None else []
    entity["fields"][name] = {
        "field_id": f"{entity_id}.{name}",
        "value": None if op["op"] == "deny" else op["value"],
        "status": "pending" if op["op"] == "deny" else event["status"],
        "confidence": None if op["op"] == "deny" else event["confidence"],
        "evidence": event["evidence"],
        "event_id": event["id"],
        "history": history,
    }


def iter_fields(state: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Fields of entities that have not been removed, in insertion order."""
    for entity in state["entities"].values():
        if not entity["removed"]:
            yield from entity["fields"].values()


def get_field(state: dict[str, Any], field_id: str) -> dict[str, Any] | None:
    entity_id, _, name = field_id.partition(".")
    entity = state["entities"].get(entity_id)
    return entity["fields"].get(name) if entity else None
