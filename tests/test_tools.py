"""Replay, reinterpret, show, delete, transcriber parsing, and the demo app."""

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import pytest
from helpers import flux_turn, op

from quoth.cli import main
from quoth.interpreter import ScriptedInterpreter
from quoth.log import EventLog
from quoth.session import Session, diff_states, reinterpret, run
from quoth.transcribe import FakeTranscriber, FluxTranscriber

TURNS = [flux_turn(0, "two cheeseburgers"), flux_turn(1, "that's all")]
OPS = [
    [
        op("add", "line_1", "item", "cheeseburger", [1, 1], "cheeseburgers"),
        op("add", "line_1", "quantity", 2, [0, 0], "two"),
    ],
    [],
]


@pytest.fixture
def recorded(tmp_path, domain):
    session_dir = tmp_path / "s1"
    log = EventLog(session_dir)
    session = Session(domain, ScriptedInterpreter(OPS), log)
    asyncio.run(run(session, FakeTranscriber(TURNS, on_raw=log.append_raw)))
    return session_dir


def test_replay_matches(recorded, capsys):
    assert main(["replay", str(recorded)]) == 0
    assert "OK" in capsys.readouterr().out


def test_replay_detects_tampering(recorded):
    state_file = recorded / "state.json"
    state_file.write_text(state_file.read_text().replace('"cheeseburger"', '"hamburger"'))
    assert main(["replay", str(recorded)]) == 1


def test_raw_transcriber_messages_are_stored(recorded):
    raw = [json.loads(line) for line in (recorded / "raw_transcriber.jsonl").read_text().splitlines()]
    assert raw == TURNS


def test_reinterpret_diffs_fields(recorded, tmp_path, domain):
    changed = [
        [
            op("add", "line_1", "item", "hamburger", [1, 1], "cheeseburgers"),
            op("add", "line_1", "quantity", 2, [0, 0], "two"),
        ],
        [],
    ]
    original = Session(domain, ScriptedInterpreter([]), EventLog.open(recorded)).state
    session = reinterpret(recorded, tmp_path / "s1-new", domain, ScriptedInterpreter(changed))
    assert diff_states(original, session.state) == [
        ("line_1.item", ("cheeseburger", "confirmed"), ("hamburger", "confirmed"))
    ]
    with pytest.raises(FileExistsError):
        reinterpret(recorded, tmp_path / "s1-new", domain, ScriptedInterpreter(changed))


def test_show_field(recorded, capsys):
    assert main(["show", str(recorded), "--field", "line_1.quantity"]) == 0
    out = capsys.readouterr().out
    assert 'line_1.quantity = 2  [confirmed]' in out
    assert '"two"' in out


def test_delete(recorded):
    assert main(["delete", str(recorded), "--yes"]) == 0
    assert not recorded.exists()


def test_flux_parsing_and_url():
    message = flux_turn(3, "one fries", low={1: 0.42})
    t = FluxTranscriber(audio=None, keyterms=["vanilla shake", "fries"], api_key="k")
    query = parse_qs(urlparse(t.url()).query)
    assert query["model"] == ["flux-general-en"]
    assert query["keyterm"] == ["vanilla shake", "fries"]
    turns = asyncio.run(_collect(FakeTranscriber([{"type": "TurnInfo", "event": "Update"}, message])))
    assert len(turns) == 1
    assert turns[0]["turn_index"] == 3
    assert turns[0]["words"][1]["confidence"] == 0.42


def test_flux_sends_trailing_silence_before_ending_turn():
    class FakeSocket:
        def __init__(self):
            self.sent = []

        async def send(self, data):
            self.sent.append(data)

    async def audio():
        yield b"\x01\x02"

    ws = FakeSocket()
    asyncio.run(FluxTranscriber(audio(), api_key="k")._send_audio(ws))
    assert ws.sent[0] == b"\x01\x02"
    assert all(chunk == bytes(2560) for chunk in ws.sent[1:-2])  # 80 ms of 16 kHz silence each
    assert len(ws.sent[1:-2]) == 12
    assert [json.loads(m)["type"] for m in ws.sent[-2:]] == ["ForceEndTurn", "CloseStream"]


async def _collect(transcriber):
    return [t async for t in transcriber.turns()]


def test_demo_routes(tmp_path):
    from fastapi.testclient import TestClient

    from quoth.demo import create_app

    client = TestClient(create_app(tmp_path, "order", "claude-opus-5-5", save_audio=False))
    assert "Quoth" in client.get("/").text
    assert client.get("/sessions/nope/audio").status_code == 404
