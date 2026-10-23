import json

from quoth.log import EventLog, dump_state, read_events
from quoth.reducer import apply, initial_state, reduce


def _evidence(utt="u_001", span=(0, 0), quote="x", conf=0.95):
    return {
        "utterance_id": utt,
        "word_span": list(span),
        "start_sec": 0.0,
        "end_sec": 1.0,
        "quote": quote,
        "min_word_confidence": conf,
    }


def _set(log, op, entity, field, value, status="confirmed", conf=0.95, **extra):
    return log.append(
        "op_applied",
        1.0,
        {
            "utterance_id": "u_001",
            "op": {"op": op, "entity": entity, "field": field, "value": value},
            "status": status,
            "confidence": conf,
            "evidence": _evidence(quote=str(value), conf=conf),
            **extra,
        },
    )


def test_replay_is_deterministic(tmp_path):
    log = EventLog(tmp_path)
    _set(log, "add", "line_1", "item", "cheeseburger")
    _set(log, "add", "line_1", "quantity", 2)
    _set(log, "update", "line_1", "quantity", 3)
    events = read_events(tmp_path)
    assert dump_state(reduce(events)) == dump_state(reduce(json.loads(json.dumps(events))))


def test_correction_patches_and_keeps_history():
    log = EventLog()
    first = _set(log, "add", "line_1", "quantity", 2)
    _set(log, "update", "line_1", "quantity", 3)
    field = reduce(log.events)["entities"]["line_1"]["fields"]["quantity"]
    assert field["value"] == 3
    assert field["history"] == [{"event_id": first["id"], "value": 2, "status": "superseded"}]


def test_deny_supersedes_and_leaves_field_pending():
    log = EventLog()
    _set(log, "add", "line_1", "item", "vanilla shake", status="pending", conf=0.5)
    _set(log, "deny", "line_1", "item", None)
    field = reduce(log.events)["entities"]["line_1"]["fields"]["item"]
    assert field["value"] is None
    assert field["status"] == "pending"
    assert field["history"][0]["value"] == "vanilla shake"


def test_confirm_and_remove():
    log = EventLog()
    _set(log, "add", "line_1", "item", "fries", status="pending", conf=0.5)
    _set(log, "confirm", "line_1", "item", None)
    state = reduce(log.events)
    assert state["entities"]["line_1"]["fields"]["item"]["status"] == "confirmed"
    assert state["entities"]["line_1"]["fields"]["item"]["value"] == "fries"
    _set(log, "remove", "line_1", None, None)
    assert reduce(log.events)["entities"]["line_1"]["removed"] is True


def test_apply_does_not_mutate_input():
    state = initial_state()
    event = {
        "id": "e_001",
        "type": "op_applied",
        "t_sec": 0.0,
        "utterance_id": "u_001",
        "op": {"op": "add", "entity": "line_1", "field": "item", "value": "fries"},
        "status": "confirmed",
        "confidence": 0.9,
        "evidence": _evidence(),
    }
    apply(state, event)
    assert state == initial_state()


def test_target_clarification_lifecycle():
    log = EventLog()
    _set(log, "add", "line_1", "quantity", 1)
    _set(log, "add", "line_2", "quantity", 1)
    ambiguous = log.append(
        "op_downgraded",
        2.0,
        {
            "utterance_id": "u_002",
            "op": {"op": "update", "entity": None, "field": "quantity", "value": 2},
            "status": "pending",
            "reason": "ambiguous correction target",
            "candidates": ["line_1", "line_2"],
            "confidence": 0.9,
            "evidence": _evidence(),
        },
    )
    assert ambiguous["id"] in reduce(log.events)["pending_targets"]
    _set(log, "update", "line_2", "quantity", 2, resolves=ambiguous["id"])
    state = reduce(log.events)
    assert state["pending_targets"] == {}
    assert state["entities"]["line_2"]["fields"]["quantity"]["value"] == 2
