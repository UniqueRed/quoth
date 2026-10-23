"""Record one scripted ordering session from the microphone.

Shows the lines to say and the intended order, then runs a live Quoth session
(Deepgram Flux + Claude) and saves it, with audio, under data/recorded/.

Usage: python evals/recorded/record.py s05 --condition noisy --speaker alex
       python evals/recorded/record.py --list
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from quoth.domain import load_domain
from quoth.interpreter import ClaudeInterpreter
from quoth.log import AUDIO_FILE, EventLog
from quoth.session import Session, run
from quoth.transcribe import FluxTranscriber, mic_chunks, recording, wav_chunks, wav_sample_rate

HERE = Path(__file__).parent
CONDITIONS = ("quiet", "noisy", "car", "outdoor")


def load_scenarios() -> list[dict]:
    return json.loads((HERE / "scenarios.json").read_text())


def show(scenario: dict) -> None:
    print(f"\n=== {scenario['id']}: {scenario['tests']} ===\n")
    print("Say, one line per turn (pause after each until the agent responds):")
    for i, line in enumerate(scenario["say"], start=1):
        print(f"  {i}. {line}")
    print("\nThe order you actually want:")
    for line in scenario["expected"]:
        mods = f" ({', '.join(line['modifiers'])})" if line["modifiers"] else ""
        print(f"  - {line['quantity']} x {line['item']}{mods}")
    print("\nIf the agent asks a question, answer it truthfully for that order: \"yes\", or \"no\" plus the right")
    print("item. If it asks which one you meant, say the item's name. Then carry on with the script.\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?", help="scenario id or prefix, e.g. s05")
    parser.add_argument("--condition", choices=CONDITIONS, default="quiet")
    parser.add_argument("--speaker", default="me", help="who is speaking (a name or label)")
    parser.add_argument("--audio", type=Path, help="use a 16-bit mono WAV instead of the microphone")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    scenarios = load_scenarios()
    if args.list or not args.scenario:
        for s in scenarios:
            print(f"{s['id']:<28} {s['tests']}")
        return
    matches = [s for s in scenarios if s["id"].startswith(args.scenario)]
    if len(matches) != 1:
        raise SystemExit(f"no unique scenario matches {args.scenario!r}")
    scenario = matches[0]

    load_dotenv()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_dir = Path("data/recorded") / f"{scenario['id']}__{args.condition}__{args.speaker}__{stamp}"
    log = EventLog(session_dir)
    (session_dir / "meta.json").write_text(
        json.dumps({"scenario": scenario["id"], "condition": args.condition, "speaker": args.speaker}) + "\n"
    )

    sample_rate = wav_sample_rate(args.audio) if args.audio else 16000
    audio = wav_chunks(args.audio) if args.audio else mic_chunks(sample_rate)
    domain = load_domain("order")
    session = Session(domain, ClaudeInterpreter(), log)
    transcriber = FluxTranscriber(
        recording(audio, session_dir / AUDIO_FILE, sample_rate), sample_rate, domain.keyterms(), on_raw=log.append_raw
    )

    def on_reply(turn: dict, reply: dict) -> None:
        if turn["transcript"]:
            print(f"you:   {turn['transcript']}")
        if reply["question"] or reply["readback"]:
            print(f"agent: {reply['question'] or reply['readback']}")

    show(scenario)
    input("Press Enter, then start speaking...")
    print("Listening. (Ctrl-C to abort)\n")
    try:
        asyncio.run(run(session, transcriber, on_reply))
    except KeyboardInterrupt:
        print("\nAborted; the session was not finalized and will be skipped when scoring.")
        return
    print(f"\nSaved {session_dir}")


if __name__ == "__main__":
    main()
