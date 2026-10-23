"""Append-only event log and session directory layout.

A session is a directory:
    events.jsonl          the log (source of truth)
    state.json            derived state, regenerable from the log
    raw_transcriber.jsonl raw transcriber messages, for offline replay
    audio/session.wav     optional recording
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

EVENT_TYPES = frozenset(
    {
        "utterance_finalized",
        "ops_proposed",
        "op_applied",
        "op_downgraded",
        "op_rejected",
        "clarification_asked",
        "clarification_answered",
        "session_finalized",
    }
)

EVENTS_FILE = "events.jsonl"
STATE_FILE = "state.json"
RAW_FILE = "raw_transcriber.jsonl"
AUDIO_FILE = "audio/session.wav"


def dump_state(state: dict[str, Any]) -> str:
    """Canonical serialization, so replay can compare byte for byte."""
    return json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


class EventLog:
    """In-memory view of a session's events, mirrored to events.jsonl when a directory is given."""

    def __init__(self, session_dir: Path | None = None, events: list[dict[str, Any]] | None = None):
        self.session_dir = session_dir
        self.events: list[dict[str, Any]] = list(events or [])
        if session_dir is not None:
            session_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def open(cls, session_dir: Path) -> EventLog:
        return cls(session_dir, read_events(session_dir))

    def append(self, type: str, t_sec: float, payload: dict[str, Any]) -> dict[str, Any]:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {type}")
        event = {"id": f"e_{len(self.events) + 1:03d}", "type": type, "t_sec": t_sec, **payload}
        self.events.append(event)
        if self.session_dir is not None:
            with open(self.session_dir / EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def write_state(self, state: dict[str, Any]) -> None:
        if self.session_dir is not None:
            (self.session_dir / STATE_FILE).write_text(dump_state(state), encoding="utf-8")

    def append_raw(self, message: dict[str, Any]) -> None:
        if self.session_dir is not None:
            with open(self.session_dir / RAW_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(message, ensure_ascii=False) + "\n")


def read_events(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / EVENTS_FILE
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def delete_session(session_dir: Path) -> None:
    """Remove a session and everything in it (log, state, raw messages, audio)."""
    if not (session_dir / EVENTS_FILE).exists():
        raise FileNotFoundError(f"{session_dir} is not a quoth session")
    shutil.rmtree(session_dir)
