from helpers import op, utterance

from quoth.log import EventLog
from quoth.reducer import initial_state, reduce
from quoth.verifier import verify


def _state_with_lines(*items):
    log = EventLog()
    for i, item in enumerate(items, start=1):
        log.append(
            "op_applied",
            0.0,
            {
                "utterance_id": "u_000",
                "op": {"op": "add", "entity": f"line_{i}", "field": "item", "value": item},
                "status": "confirmed",
                "confidence": 0.95,
                "evidence": {},
            },
        )
    return reduce(log.events)


def test_valid_add(domain):
    u = utterance("one cheeseburger please")
    v = verify(op("add", "line_1", "item", "cheeseburger", [1, 1], "cheeseburger"), u, initial_state(), domain)
    assert v.outcome == "ok"
    assert v.evidence["quote"] == "cheeseburger"
    assert v.evidence["start_sec"] == 0.4


def test_fabricated_quote_is_rejected(domain):
    u = utterance("one cheeseburger please")
    v = verify(op("add", "line_1", "item", "cheeseburger", [1, 1], "hamburger"), u, initial_state(), domain)
    assert v.outcome == "reject"
    assert "does not match" in v.reason


def test_span_out_of_range_is_rejected(domain):
    u = utterance("one cheeseburger")
    for span in ([1, 5], [2, 1], [-1, 0], [0]):
        v = verify(op("add", "line_1", "item", "cheeseburger", span, "cheeseburger"), u, initial_state(), domain)
        assert v.outcome == "reject", span


def test_number_must_appear_in_span(domain):
    u = utterance("two cheeseburgers")
    assert verify(op("add", "line_1", "quantity", 2, [0, 0], "two"), u, initial_state(), domain).outcome == "ok"
    v = verify(op("add", "line_1", "quantity", 3, [0, 0], "two"), u, initial_state(), domain)
    assert v.outcome == "reject"
    assert "does not appear" in v.reason
    assert verify(op("add", "line_1", "quantity", 2, [0, 0], "2"), utterance("2 fries"), initial_state(), domain).outcome == "ok"


def test_value_outside_allowed_list_is_pending(domain):
    u = utterance("a valuable shake")
    v = verify(op("add", "line_1", "item", "valuable shake", [1, 2], "valuable shake"), u, initial_state(), domain)
    assert v.outcome == "pending"


def test_ambiguous_correction_target(domain):
    state = _state_with_lines("cheeseburger", "fries")
    u = utterance("make that two")
    v = verify(op("update", None, "quantity", 2, [2, 2], "two", correction=["line_1", "line_2"]), u, state, domain)
    assert v.outcome == "ambiguous"
    assert v.candidates == ["line_1", "line_2"]


def test_correction_to_missing_target_is_rejected(domain):
    state = _state_with_lines("cheeseburger")
    v = verify(op("update", None, "quantity", 2, [2, 2], "two", correction=["line_9"]), utterance("make that two"), state, domain)
    assert v.outcome == "reject"


def test_update_requires_existing_entity_and_add_requires_unset_field(domain):
    state = _state_with_lines("cheeseburger")
    u = utterance("fries")
    assert verify(op("update", "line_2", "item", "fries", [0, 0], "fries"), u, state, domain).outcome == "reject"
    assert verify(op("add", "line_1", "item", "fries", [0, 0], "fries"), u, state, domain).outcome == "reject"


def test_confirm_without_field_needs_open_target_question(domain):
    v = verify(op("confirm", "line_1", None, None, [0, 0], "yes"), utterance("yes"), initial_state(), domain)
    assert v.outcome == "reject"
