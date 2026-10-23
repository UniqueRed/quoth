"""Session: runs each finalized turn through interpret -> verify -> gate -> log -> clarify.

All state changes go through events; the in-memory state is always reduce(log.events).
Event times come from the audio (word timestamps), never the wall clock, so a log
produced from the same utterances is reproducible.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from quoth.clarify import next_question, readback, repeat_question
from quoth.domain import Domain
from quoth.gate import classify, decide
from quoth.interpreter import Interpreter
from quoth.log import EventLog, read_events
from quoth.reducer import apply, iter_fields, reduce
from quoth.transcribe import Transcriber
from quoth.verifier import normalize, verify


class Session:
    def __init__(self, domain: Domain, interpreter: Interpreter, log: EventLog, max_questions: int | None = None):
        self.domain = domain
        self.interpreter = interpreter
        self.log = log
        self.max_questions = domain.max_questions if max_questions is None else max_questions
        self.state = reduce(log.events)

    @property
    def finalized(self) -> bool:
        return self.state["finalized"]

    def _emit(self, type: str, t_sec: float, payload: dict[str, Any]) -> dict[str, Any]:
        event = self.log.append(type, t_sec, payload)
        self.state = apply(self.state, event)
        self.log.write_state(self.state)
        return event

    def handle_turn(self, turn: dict[str, Any]) -> dict[str, Any]:
        """Process one finalized turn. Returns {"question", "readback", "finalized"} for the agent."""
        if self.finalized:
            raise RuntimeError("session is already finalized")
        n = sum(e["type"] == "utterance_finalized" for e in self.log.events) + 1
        t = turn["words"][-1]["end"] if turn["words"] else 0.0
        utterance = {
            "utterance_id": f"u_{n:03d}",
            "turn_index": turn["turn_index"],
            "transcript": turn["transcript"],
            "trigger": turn.get("trigger"),
            "words": turn["words"],
        }
        self._emit("utterance_finalized", t, utterance)

        proposal = self.interpreter.interpret(utterance, self.state, self.domain) if turn["words"] else {"ops": []}
        self._emit(
            "ops_proposed",
            t,
            {
                "utterance_id": utterance["utterance_id"],
                "ops": proposal["ops"],
                "unclear": bool(proposal.get("unclear")),
                "notes": proposal.get("notes", ""),
                "interpreter": {**self.interpreter.meta, "served_by": proposal.get("served_by")},
                "usage": proposal.get("usage"),
            },
        )
        for proposed in proposal["ops"]:
            self._process(proposed, utterance, t)

        question = self.state["open_question"]
        if question:
            self._emit(
                "clarification_answered",
                t,
                {
                    "utterance_id": utterance["utterance_id"],
                    "question_id": question["event_id"],
                    "resolved": self._resolved(question),
                },
            )

        spoken = " ".join(normalize(w) for w in turn["transcript"].split())
        finalizing = any(trigger in spoken for trigger in self.domain.finalization_triggers) or bool(
            question and question["at_finalization"]
        )
        ask = next_question(self.state, self.domain, self.max_questions)
        # A turn the interpreter could not map would otherwise be dropped silently; ask about it first.
        if proposal.get("unclear") and not finalizing and self.state["questions_asked"] < self.max_questions:
            ask = repeat_question()
        if ask:
            self._emit("clarification_asked", t, {**ask, "at_finalization": finalizing})
            return {"question": ask["question"], "readback": None, "finalized": False}
        if finalizing:
            return {"question": None, "readback": self.finalize(t), "finalized": True}
        return {"question": None, "readback": None, "finalized": False}

    def finalize(self, t_sec: float) -> str:
        """Pending items that were not resolved within the budget become needs_review.

        Turns the interpreter flagged as unclear but the agent never asked about (the budget
        ran out) are listed as `unheard` and read back, so they are not lost silently."""
        unheard = self._unheard()
        text = readback(self.state, self.domain, [u["transcript"] for u in unheard])
        self._emit(
            "session_finalized",
            t_sec,
            {
                "unheard": unheard,
                "needs_review": [f["field_id"] for f in iter_fields(self.state) if f["status"] == "pending"],
                "unresolved_targets": [
                    tid for tid, t in self.state["pending_targets"].items() if t["status"] == "pending"
                ],
                "readback": text,
            },
        )
        return text

    def _unheard(self) -> list[dict[str, str]]:
        unheard: dict[str, str] = {}
        transcripts: dict[str, str] = {}
        last = None
        for e in self.log.events:
            if e["type"] == "utterance_finalized":
                transcripts[e["utterance_id"]] = e["transcript"]
            elif e["type"] == "ops_proposed" and e.get("unclear"):
                last = e["utterance_id"]
                unheard[last] = transcripts[last]
            elif e["type"] == "clarification_asked" and e["kind"] == "repeat":
                unheard.pop(last, None)
        return [{"utterance_id": uid, "transcript": text} for uid, text in unheard.items()]

    def _resolved(self, question: dict[str, Any]) -> list[str]:
        if question["kind"] == "choose_target":
            return [] if question["target_id"] in self.state["pending_targets"] else [question["target_id"]]
        fields = {f["field_id"]: f for f in iter_fields(self.state)}
        return [fid for fid in question["field_ids"] if fid in fields and fields[fid]["status"] != "pending"]

    def _process(self, proposed: dict[str, Any], utterance: dict[str, Any], t: float) -> None:
        uid = utterance["utterance_id"]
        verdict = verify(proposed, utterance, self.state, self.domain)

        if verdict.outcome == "target_choice":
            target_id = verdict.candidates[0]
            target = self.state["pending_targets"][target_id]
            criticality = self.domain.criticality(target["op"]["field"])
            choice = decide(verdict, utterance, self.domain, criticality=criticality)
            if choice.event_type == "op_rejected":
                self._emit("op_rejected", t, {"utterance_id": uid, "proposed": proposed, "reason": choice.reason})
                return
            # Apply the original correction to the chosen entity, gated on its original evidence.
            op = {**target["op"], "entity": verdict.op["entity"]}
            decision = classify(target["confidence"], criticality, self.domain)
            self._emit(
                decision.event_type,
                t,
                {
                    "utterance_id": uid,
                    "proposed": proposed,
                    "op": op,
                    "status": decision.status,
                    "confidence": decision.confidence,
                    "evidence": target["evidence"],
                    "reason": decision.reason,
                    "resolves": target_id,
                    "answer_evidence": verdict.evidence,
                },
            )
            return

        decision = decide(verdict, utterance, self.domain)
        if decision.event_type == "op_rejected":
            self._emit("op_rejected", t, {"utterance_id": uid, "proposed": proposed, "reason": decision.reason})
            return
        payload = {
            "utterance_id": uid,
            "proposed": proposed,
            "op": verdict.op,
            "status": decision.status,
            "confidence": decision.confidence,
            "evidence": verdict.evidence,
            "reason": decision.reason,
        }
        if verdict.outcome == "ambiguous":
            payload["candidates"] = verdict.candidates
        self._emit(decision.event_type, t, payload)


async def run(
    session: Session,
    transcriber: Transcriber,
    on_reply: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> None:
    """Feed finalized turns into the session until it finalizes or the audio ends."""
    async for turn in transcriber.turns():
        reply = await asyncio.to_thread(session.handle_turn, turn)
        if on_reply:
            on_reply(turn, reply)
        if reply["finalized"]:
            return
    if not session.finalized:
        text = session.finalize(session.log.events[-1]["t_sec"] if session.log.events else 0.0)
        if on_reply:
            on_reply({"transcript": ""}, {"question": None, "readback": text, "finalized": True})


def utterances(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "utterance_finalized"]


def reinterpret(
    source: Path, out: Path, domain: Domain, interpreter: Interpreter, max_questions: int | None = None
) -> Session:
    """Re-run a new interpreter over the stored utterances of a session, writing a new log."""
    if (out / "events.jsonl").exists():
        raise FileExistsError(f"{out} already has a log")
    session = Session(domain, interpreter, EventLog(out), max_questions)
    for utterance in utterances(read_events(source)):
        if session.finalized:
            break
        session.handle_turn(utterance)
    return session


def field_values(state: dict[str, Any]) -> dict[str, tuple[Any, str]]:
    return {f["field_id"]: (f["value"], f["status"]) for f in iter_fields(state)}


def diff_states(old: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Field-by-field differences as (field_id, (old value, status), (new value, status))."""
    a, b = field_values(old), field_values(new)
    return [(fid, a.get(fid), b.get(fid)) for fid in sorted(a.keys() | b.keys()) if a.get(fid) != b.get(fid)]
