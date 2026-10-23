import importlib.util
from pathlib import Path

from helpers import flux_turn, op

from quoth.interpreter import ScriptedInterpreter
from quoth.log import EventLog
from quoth.session import Session

_spec = importlib.util.spec_from_file_location(
    "recorded_score", Path(__file__).parents[1] / "evals" / "recorded" / "score.py"
)
recorded = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recorded)

EXPECTED = [
    {"item": "cheeseburger", "quantity": 1, "modifiers": []},
    {"item": "chocolate shake", "quantity": 2, "modifiers": []},
]


def _events(domain, turns, ops):
    session = Session(domain, ScriptedInterpreter(ops), EventLog())
    for turn in turns:
        from quoth.transcribe import turn_from_flux

        session.handle_turn(turn_from_flux(turn))
    return session.log.events


def test_ambiguous_correction_scores_exact_for_quoth_but_not_naive(domain):
    events = _events(
        domain,
        [flux_turn(0, "a cheeseburger and a chocolate shake"), flux_turn(1, "make that two"),
         flux_turn(2, "the shake"), flux_turn(3, "that's all")],
        [
            [op("add", "line_1", "item", "cheeseburger", [1, 1], "cheeseburger"),
             op("add", "line_2", "item", "chocolate shake", [4, 5], "chocolate shake")],
            [op("update", None, "quantity", 2, [2, 2], "two", correction=["line_1", "line_2"])],
            [op("confirm", "line_2", None, None, [0, 1], "the shake")],
            [],
        ],
    )
    q = recorded.score(EXPECTED, recorded.quoth_lines(events))
    n = recorded.score(EXPECTED, recorded.naive_lines(events))
    assert q["exact"] == 1 and q["wrong_commits"] == 0
    # Naive applies "make that two" to the first candidate, the cheeseburger.
    assert n["exact"] == 0 and n["wrong_commits"] == 2


def test_extra_and_pending_lines(domain):
    events = _events(
        domain,
        [flux_turn(0, "a cheeseburger and a valuable shake and fries"), flux_turn(1, "that's all")],
        [
            [op("add", "line_1", "item", "cheeseburger", [1, 1], "cheeseburger"),
             op("add", "line_2", "item", "valuable shake", [4, 5], "valuable shake"),
             op("add", "line_3", "item", "fries", [7, 7], "fries")],
            [],
        ],
    )
    q = recorded.score(EXPECTED, recorded.quoth_lines(events))
    assert q["flagged"] == 1 and q["flagged_wrong"] == 1  # "valuable shake" held for review
    assert q["extra_lines"] == 1 and q["wrong_commits"] == 2  # fries committed as an extra line; shake quantity 1 != 2


def _studio(sessions_dir):
    import sys

    from fastapi.testclient import TestClient

    sys.path.insert(0, str(Path(__file__).parents[1] / "evals" / "recorded"))
    import studio

    return TestClient(studio.create_app(sessions_dir))


def test_studio_lists_scores_and_discards(tmp_path, domain):
    import json

    session_dir = tmp_path / "s01-plain__quiet__me__20261021-120000"
    log = EventLog(session_dir)
    session = Session(domain, ScriptedInterpreter([
        [op("add", "line_1", "item", "cheeseburger", [4, 4], "cheeseburgers"),
         op("add", "line_1", "quantity", 2, [3, 3], "two"),
         op("add", "line_2", "item", "cola", [7, 7], "cola")],
        [],
    ]), log)
    from quoth.transcribe import turn_from_flux

    for i, text in enumerate(["can i get two cheeseburgers and a cola", "that's all"]):
        session.handle_turn(turn_from_flux(flux_turn(i, text)))
    (session_dir / "meta.json").write_text(json.dumps({"scenario": "s01-plain", "condition": "quiet", "speaker": "me"}))

    client = _studio(tmp_path)
    assert "Quoth Studio" in client.get("/").text
    assert len(client.get("/api/scenarios").json()["scenarios"]) == 12
    results = client.get("/api/results").json()
    assert results["sessions"][0]["quoth"]["exact"] == 1
    assert results["totals"]["quiet"]["quoth"]["sessions"] == 1
    assert client.delete("/api/sessions/nope").status_code == 404
    assert client.delete(f"/api/sessions/{session_dir.name}").status_code == 200
    assert not session_dir.exists()


def test_studio_rejects_unknown_scenario(tmp_path):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    client = _studio(tmp_path)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?scenario=s99&condition=quiet") as ws:
            ws.receive_json()
    assert not any(tmp_path.iterdir())
