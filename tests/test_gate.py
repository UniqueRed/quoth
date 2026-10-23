import pytest
from helpers import op, utterance

from quoth.clarify import next_question
from quoth.gate import decide, field_confidence
from quoth.log import EventLog
from quoth.reducer import initial_state, reduce
from quoth.verifier import verify


def _gate(domain, text, proposed, low):
    u = utterance(text, low)
    return decide(verify(proposed, u, initial_state(), domain), u, domain)


@pytest.mark.parametrize(
    "conf, status",
    [(0.80, "confirmed"), (0.7999, "pending"), (0.99, "confirmed"), (0.1, "pending")],
)
def test_critical_boundary(domain, conf, status):
    d = _gate(domain, "fries", op("add", "line_1", "item", "fries", [0, 0], "fries"), {0: conf})
    assert d.status == status


@pytest.mark.parametrize("conf, status", [(0.65, "confirmed"), (0.6499, "tentative")])
def test_normal_boundary(domain, conf, status):
    d = _gate(domain, "no onions", op("add", "line_1", "modifiers", ["no onions"], [0, 1], "no onions"), {1: conf})
    assert d.status == status


def test_low_criticality_always_confirmed(domain):
    domain.fields["modifiers"]["criticality"] = "low"
    d = _gate(domain, "no onions", op("add", "line_1", "modifiers", ["no onions"], [0, 1], "no onions"), {1: 0.1})
    assert d.status == "confirmed"


def test_filler_words_do_not_count(domain):
    u = utterance("uh the fries", {0: 0.2, 1: 0.3})
    assert field_confidence(u, [0, 2]) == 0.95


def test_numeric_fields_use_number_words(domain):
    u = utterance("make that two", {0: 0.1, 2: 0.9})
    assert field_confidence(u, [0, 2], numeric=True) == 0.9


def test_unclear_answer_is_rejected(domain):
    d = _gate(domain, "yes", op("confirm", "line_1", "item", None, [0, 0], "yes"), {0: 0.3})
    assert d.event_type == "op_rejected"


def test_question_budget_exhaustion(domain):
    log = EventLog()
    log.append(
        "op_downgraded",
        0.0,
        {
            "utterance_id": "u_001",
            "op": {"op": "add", "entity": "line_1", "field": "item", "value": "fries"},
            "status": "pending",
            "confidence": 0.5,
            "evidence": {},
            "reason": "low",
        },
    )
    state = reduce(log.events)
    assert next_question(state, domain, max_questions=1)["question"] == "Was that fries?"
    state["questions_asked"] = 1
    assert next_question(state, domain, max_questions=1) is None
