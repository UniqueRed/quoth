"""quoth command line: run, replay, reinterpret, show, delete, demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from quoth.domain import load_domain
from quoth.interpreter import DEFAULT_MODEL, DEFAULT_PROMPT, ClaudeInterpreter
from quoth.log import AUDIO_FILE, STATE_FILE, EventLog, delete_session, dump_state, read_events
from quoth.reducer import get_field, iter_fields, reduce
from quoth.session import Session, diff_states, reinterpret, run
from quoth.transcribe import (
    FakeTranscriber,
    FluxTranscriber,
    mic_chunks,
    recording,
    wav_chunks,
    wav_sample_rate,
)


def cmd_run(args: argparse.Namespace) -> int:
    session_dir = Path(args.session)
    if (session_dir / "events.jsonl").exists():
        sys.exit(f"{session_dir} already has a log; use a new session directory")
    domain = load_domain(args.pack)
    log = EventLog(session_dir)
    session = Session(domain, ClaudeInterpreter(args.model, Path(args.prompt)), log, args.max_questions)

    if args.script:
        transcriber = FakeTranscriber.from_file(Path(args.script), on_raw=log.append_raw)
    else:
        sample_rate = wav_sample_rate(Path(args.audio)) if args.audio else 16000
        audio = wav_chunks(Path(args.audio)) if args.audio else mic_chunks(sample_rate)
        if args.save_audio:
            audio = recording(audio, session_dir / AUDIO_FILE, sample_rate)
        transcriber = FluxTranscriber(audio, sample_rate, domain.keyterms(), on_raw=log.append_raw)

    def on_reply(turn: dict[str, Any], reply: dict[str, Any]) -> None:
        if turn["transcript"]:
            print(f"you:   {turn['transcript']}")
        if reply["question"] or reply["readback"]:
            print(f"agent: {reply['question'] or reply['readback']}")

    print("Listening. Say \"that's all\" when done (Ctrl-C to stop).")
    try:
        asyncio.run(run(session, transcriber, on_reply))
    except KeyboardInterrupt:
        if not session.finalized:
            print(f"agent: {session.finalize(log.events[-1]['t_sec'] if log.events else 0.0)}")
    print(f"\nSession saved to {session_dir}")
    print_state(session.state)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    session_dir = Path(args.session)
    rebuilt = dump_state(reduce(read_events(session_dir)))
    stored = (session_dir / STATE_FILE).read_text(encoding="utf-8")
    if rebuilt != stored:
        print("MISMATCH: state rebuilt from events.jsonl differs from state.json")
        return 1
    print(f"OK: {len(read_events(session_dir))} events replay to identical state")
    return 0


def cmd_reinterpret(args: argparse.Namespace) -> int:
    source = Path(args.session)
    out = Path(args.out) if args.out else source.with_name(f"{source.name}-reinterpret")
    interpreter = ClaudeInterpreter(args.model, Path(args.prompt))
    session = reinterpret(source, out, load_domain(args.pack), interpreter, args.max_questions)
    original = reduce(read_events(source))
    changes = diff_states(original, session.state)
    print(f"New log written to {out} ({interpreter.meta['model']}, prompt {interpreter.meta['prompt_hash']})")
    if not changes:
        print("No field changed.")
    for field_id, old, new in changes:
        print(f"  {field_id}: {_fmt(old)}  ->  {_fmt(new)}")
    return 0


def _fmt(value_status: tuple[Any, str] | None) -> str:
    return "(absent)" if value_status is None else f"{json.dumps(value_status[0])} [{value_status[1]}]"


def cmd_show(args: argparse.Namespace) -> int:
    state = reduce(read_events(Path(args.session)))
    if not args.field:
        print_state(state)
        return 0
    field = get_field(state, args.field)
    if field is None:
        print(f"No field {args.field}")
        return 1
    ev = field["evidence"]
    print(f"{field['field_id']} = {json.dumps(field['value'])}  [{field['status']}]")
    print(f"  confidence: {field['confidence']}")
    print(f"  evidence:   \"{ev['quote']}\" ({ev['utterance_id']}, words {ev['word_span']}, "
          f"{ev['start_sec']:.2f}s-{ev['end_sec']:.2f}s, min word confidence {ev['min_word_confidence']})")
    print(f"  set by:     {field['event_id']}")
    for h in field["history"]:
        print(f"  history:    {json.dumps(h['value'])} [{h['status']}] from {h['event_id']}")
    return 0


def print_state(state: dict[str, Any]) -> None:
    for field in iter_fields(state):
        print(f"  {field['field_id']:<20} {json.dumps(field['value']):<24} {field['status']}")
    for target_id, target in state["pending_targets"].items():
        print(f"  correction {target_id}: {target['op']['field']} -> {target['op']['value']} [{target['status']}]")


def cmd_delete(args: argparse.Namespace) -> int:
    session_dir = Path(args.session)
    if not args.yes and input(f"Delete {session_dir} and all its audio and logs? [y/N] ").lower() != "y":
        return 1
    delete_session(session_dir)
    print(f"Deleted {session_dir}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    import uvicorn

    from quoth.demo import create_app

    app = create_app(Path(args.sessions_dir), args.pack, args.model, args.save_audio)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quoth", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def interpreter_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--pack", default="order", help="domain pack name or path (default: order)")
        p.add_argument("--model", default=DEFAULT_MODEL)
        p.add_argument("--prompt", default=str(DEFAULT_PROMPT), help="interpreter prompt file")
        p.add_argument("--max-questions", type=int, default=None, help="question budget (default: from pack)")

    p = sub.add_parser("run", help="start a live session")
    p.add_argument("session", help="new session directory")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--mic", action="store_true", help="stream from the microphone")
    source.add_argument("--audio", help="stream a 16-bit mono WAV file to Deepgram")
    source.add_argument("--script", help="play a JSON list of Flux TurnInfo messages instead of audio")
    p.add_argument("--save-audio", action="store_true", help="store the audio in the session")
    interpreter_options(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("replay", help="rebuild state from the log and check it matches state.json")
    p.add_argument("session")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("reinterpret", help="re-run the interpreter over stored utterances and diff")
    p.add_argument("session")
    p.add_argument("--out", help="directory for the new log (default: <session>-reinterpret)")
    interpreter_options(p)
    p.set_defaults(func=cmd_reinterpret)

    p = sub.add_parser("show", help="print state, or one field's evidence and history")
    p.add_argument("session")
    p.add_argument("--field", help="e.g. line_2.item")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("delete", help="delete a session and its audio")
    p.add_argument("session")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("demo", help="start the web demo")
    p.add_argument("--sessions-dir", default="sessions")
    p.add_argument("--pack", default="order")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--save-audio", action="store_true")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # API keys from ./.env; real environment variables take precedence
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
