"""End-to-end scenarios: scripted transcriber turns + scripted interpreter, no network."""

import asyncio

from helpers import flux_turn, op

from quoth.interpreter import ScriptedInterpreter
from quoth.log import EventLog, dump_state, read_events
from quoth.reducer import reduce
from quoth.session import Session, run
from quoth.transcribe import FakeTranscriber


def play(tmp_path, domain, turns, ops, max_questions=None):
    """Run a scripted session. Returns (session, replies)."""
    session = Session(domain, ScriptedInterpreter(ops), EventLog(tmp_path), max_questions)
    replies = []
    asyncio.run(run(session, FakeTranscriber(turns), on_reply=lambda turn, reply: replies.append(reply)))
    return session, replies


def questions(session):
    return [e["question"] for e in session.log.events if e["type"] == "clarification_asked"]


def field(session, field_id):
    entity, name = field_id.split(".")
    return session.state["entities"][entity]["fields"][name]


def test_plain_order_asks_nothing(tmp_path, domain):
    turns = [
        flux_turn(0, "I'd like two cheeseburgers and fries"),
        flux_turn(1, "that's all"),
    ]
    ops = [
        [
            op("add", "line_1", "item", "cheeseburger", [3, 3], "cheeseburgers"),
            op("add", "line_1", "quantity", 2, [2, 2], "two"),
            op("add", "line_2", "item", "fries", [5, 5], "fries"),
        ],
        [],
    ]
    session, replies = play(tmp_path, domain, turns, ops)
    assert questions(session) == []
    assert session.finalized
    assert replies[-1]["readback"] == "Got it, your order is complete."
    assert {f["status"] for e in session.state["entities"].values() for f in e["fields"].values()} == {"confirmed"}
    # Replay from disk gives byte-identical state.
    assert dump_state(reduce(read_events(tmp_path))) == (tmp_path / "state.json").read_text()


def test_self_correction_keeps_history(tmp_path, domain):
    turns = [flux_turn(0, "can I get two uh actually three cheeseburgers")]
    ops = [
        [
            op("add", "line_1", "item", "cheeseburger", [7, 7], "cheeseburgers"),
            op("add", "line_1", "quantity", 2, [3, 3], "two"),
            op("update", "line_1", "quantity", 3, [6, 6], "three", correction=["line_1"]),
        ]
    ]
    session, _ = play(tmp_path, domain, turns, ops)
    quantity = field(session, "line_1.quantity")
    assert quantity["value"] == 3
    assert [h["value"] for h in quantity["history"]] == [2]
    assert questions(session) == []


def test_low_confidence_critical_item_asks_once(tmp_path, domain):
    turns = [
        flux_turn(0, "a vanilla shake", low={1: 0.5}),
        flux_turn(1, "yes"),
        flux_turn(2, "that's all"),
    ]
    ops = [
        [op("add", "line_1", "item", "vanilla shake", [1, 2], "vanilla shake")],
        [op("confirm", "line_1", "item", None, [0, 0], "yes")],
        [],
    ]
    session, replies = play(tmp_path, domain, turns, ops)
    assert questions(session) == ["Was that vanilla shake?"]
    assert replies[0]["question"] == "Was that vanilla shake?"
    assert field(session, "line_1.item")["status"] == "confirmed"
    assert session.finalized


def test_ambiguous_correction_asks_for_target(tmp_path, domain):
    turns = [
        flux_turn(0, "a cheeseburger and a vanilla shake"),
        flux_turn(1, "make that two"),
        flux_turn(2, "the shake"),
    ]
    ops = [
        [
            op("add", "line_1", "item", "cheeseburger", [1, 1], "cheeseburger"),
            op("add", "line_1", "quantity", 1, [0, 0], "a"),
            op("add", "line_2", "item", "vanilla shake", [4, 5], "vanilla shake"),
            op("add", "line_2", "quantity", 1, [3, 3], "a"),
        ],
        [op("update", None, "quantity", 2, [2, 2], "two", correction=["line_1", "line_2"])],
        [op("confirm", "line_2", None, None, [0, 1], "the shake")],
    ]
    session, replies = play(tmp_path, domain, turns, ops)
    assert replies[1]["question"] == "Which one did you mean: the cheeseburger or the vanilla shake?"
    assert field(session, "line_1.quantity")["value"] == 1
    assert field(session, "line_2.quantity")["value"] == 2
    assert [h["value"] for h in field(session, "line_2.quantity")["history"]] == [1]
    assert session.state["pending_targets"] == {}


def test_budget_exhausted_marks_needs_review(tmp_path, domain):
    turns = [
        flux_turn(0, "a vanilla shake and onion rings", low={1: 0.5, 4: 0.4}),
        flux_turn(1, "yes"),
        flux_turn(2, "that's all"),
    ]
    ops = [
        [
            op("add", "line_1", "item", "vanilla shake", [1, 2], "vanilla shake"),
            op("add", "line_2", "item", "onion rings", [4, 5], "onion rings"),
        ],
        [op("confirm", "line_1", "item", None, [0, 0], "yes")],
        [],
    ]
    session, replies = play(tmp_path, domain, turns, ops, max_questions=1)
    assert questions(session) == ["Was that vanilla shake?"]
    assert field(session, "line_1.item")["status"] == "confirmed"
    assert field(session, "line_2.item")["status"] == "needs_review"
    assert "onion rings" in replies[-1]["readback"]


def test_deny_then_restate(tmp_path, domain):
    turns = [
        flux_turn(0, "a vanilla shake", low={1: 0.5}),
        flux_turn(1, "no"),
        flux_turn(2, "chocolate"),
        flux_turn(3, "that's all"),
    ]
    ops = [
        [op("add", "line_1", "item", "vanilla shake", [1, 2], "vanilla shake")],
        [op("deny", "line_1", "item", None, [0, 0], "no")],
        [op("update", "line_1", "item", "chocolate shake", [0, 0], "chocolate")],
        [],
    ]
    session, replies = play(tmp_path, domain, turns, ops)
    assert replies[0]["question"] == "Was that vanilla shake?"
    assert "chocolate shake" in replies[1]["question"]
    assert "vanilla shake" not in replies[1]["question"]
    item = field(session, "line_1.item")
    assert (item["value"], item["status"]) == ("chocolate shake", "confirmed")
    assert [h["value"] for h in item["history"]] == ["vanilla shake"]
    assert session.finalized and replies[-1]["readback"] == "Got it, your order is complete."


def test_finalization_asks_pending_first(tmp_path, domain):
    turns = [
        flux_turn(0, "a vanilla shake that's all", low={1: 0.5}),
        flux_turn(1, "yes"),
    ]
    ops = [
        [op("add", "line_1", "item", "vanilla shake", [1, 2], "vanilla shake")],
        [op("confirm", "line_1", "item", None, [0, 0], "yes")],
    ]
    session, replies = play(tmp_path, domain, turns, ops)
    assert replies[0]["question"] == "Was that vanilla shake?"
    assert replies[1]["finalized"]


def test_off_menu_item_asks_for_a_replacement(tmp_path, domain):
    turns = [
        flux_turn(0, "can i get a pepperoni pizza"),
        flux_turn(1, "a hamburger then"),
        flux_turn(2, "that's all"),
    ]
    ops = [
        [op("add", "line_1", "item", "pepperoni pizza", [4, 5], "pepperoni pizza")],
        [op("update", "line_1", "item", "hamburger", [1, 1], "hamburger")],
        [],
    ]
    session, replies = play(tmp_path, domain, turns, ops)
    assert questions(session) == ["Sorry, pepperoni pizza isn't available. What would you like instead?"]
    assert field(session, "line_1.item")["value"] == "hamburger"
    assert field(session, "line_1.item")["status"] == "confirmed"


def test_near_miss_still_offers_the_closest_items(tmp_path, domain):
    turns = [flux_turn(0, "a valuable shake")]
    ops = [[op("add", "line_1", "item", "valuable shake", [1, 2], "valuable shake")]]
    session, _ = play(tmp_path, domain, turns, ops)
    assert questions(session) == ["vanilla shake or strawberry shake?"]


def test_unclear_turn_asks_to_repeat_instead_of_dropping_it(tmp_path, domain):
    turns = [
        flux_turn(0, "three colas"),
        flux_turn(1, "make tacolas lemonades"),
        flux_turn(2, "make the colas lemonades"),
        flux_turn(3, "that's all"),
    ]
    ops = [
        [op("add", "line_1", "item", "cola", [1, 1], "colas"), op("add", "line_1", "quantity", 3, [0, 0], "three")],
        {"ops": [], "unclear": True},
        [op("update", "line_1", "item", "lemonade", [3, 3], "lemonades", correction=["line_1"])],
        [],
    ]
    session, replies = play(tmp_path, domain, turns, ops)
    assert questions(session) == ["Sorry, I didn't catch that. Could you say it again?"]
    assert replies[1]["question"] == "Sorry, I didn't catch that. Could you say it again?"
    assert field(session, "line_1.item")["value"] == "lemonade"
    assert session.finalized


def test_unclear_turns_respect_the_question_budget(tmp_path, domain):
    turns = [flux_turn(i, "mumble mumble") for i in range(3)]
    session, _ = play(tmp_path, domain, turns, [{"ops": [], "unclear": True}] * 3, max_questions=2)
    assert len(questions(session)) == 2


def test_unclear_turn_after_the_budget_is_read_back(tmp_path, domain):
    turns = [flux_turn(0, "three colas"), flux_turn(1, "make the provolone lemonade"), flux_turn(2, "that's all")]
    ops = [
        [op("add", "line_1", "item", "cola", [1, 1], "colas"), op("add", "line_1", "quantity", 3, [0, 0], "three")],
        {"ops": [], "unclear": True},
        [],
    ]
    session, replies = play(tmp_path, domain, turns, ops, max_questions=0)
    assert questions(session) == []
    final = session.log.events[-1]
    assert final["unheard"] == [{"utterance_id": "u_002", "transcript": "make the provolone lemonade"}]
    assert replies[-1]["readback"] == 'Please double-check: "make the provolone lemonade", which I didn\'t catch.'
