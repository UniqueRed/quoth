"""Run every selected SLURP recording through Deepgram Flux + Quoth, one session per recording.

Resumable: finalized sessions are skipped. Stops starting new sessions once estimated
Claude spend reaches --max-usd.

Usage: python evals/slurp/run_eval.py data/slurp-audio/slurp_real data/slurp-runs/v1 [--limit N] [--max-usd 3]
"""

import argparse
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from quoth.domain import load_domain
from quoth.interpreter import ClaudeInterpreter
from quoth.log import EventLog, read_events
from quoth.session import Session, run
from quoth.transcribe import FluxTranscriber, wav_chunks

HERE = Path(__file__).parent
PRICE_IN, PRICE_OUT = 4e-6, 20e-6  # claude-opus-5-5, $ per token
CONCURRENCY = 6


def to_wav(flac: Path, out_dir: Path) -> Path:
    wav = out_dir / (flac.stem + ".wav")
    if not wav.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(flac), "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(wav)],
            check=True,
        )
    return wav


def spend(session_dir: Path) -> float:
    total = 0.0
    for e in read_events(session_dir):
        if e["type"] == "ops_proposed" and e.get("usage"):
            total += e["usage"]["input_tokens"] * PRICE_IN + e["usage"]["output_tokens"] * PRICE_OUT
    return total


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--limit", type=int, default=None, help="only the first N sentences")
    parser.add_argument("--max-usd", type=float, default=3.0)
    args = parser.parse_args()

    load_dotenv()
    domain = load_domain(str(HERE / "takeaway.json"))
    interpreter = ClaudeInterpreter()
    selection = json.loads((HERE / "selection.json").read_text())[: args.limit]
    jobs = [(row, mic, file) for row in selection for mic, file in row["recordings"].items()]

    spent = sum(spend(d) for d in args.out_dir.glob("*") if d.is_dir())
    semaphore = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def one(row: dict, mic: str, file: str) -> None:
        nonlocal spent, done
        session_dir = args.out_dir / Path(file).stem
        if session_dir.exists() and any(e["type"] == "session_finalized" for e in read_events(session_dir)):
            return
        async with semaphore:
            if spent >= args.max_usd:
                return
            shutil.rmtree(session_dir, ignore_errors=True)  # an unfinished earlier attempt
            wav = await asyncio.to_thread(to_wav, args.audio_dir / file, args.out_dir.parent / "wav")
            log = EventLog(session_dir)
            session = Session(domain, interpreter, log)
            transcriber = FluxTranscriber(wav_chunks(wav), 16000, on_raw=log.append_raw)
            try:
                await run(session, transcriber)
            except Exception as exc:  # keep going; the failure is reported by score.py as missing
                print(f"FAILED {file}: {exc!r}")
                return
            spent += spend(session_dir)
            done += 1
            print(f"[{done}] {mic:8} ${spent:.2f}  {row['sentence']!r}")

    await asyncio.gather(*(one(*job) for job in jobs))
    print(f"Finished. Estimated Claude spend for this run directory: ${spent:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
