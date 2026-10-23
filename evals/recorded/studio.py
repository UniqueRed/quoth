"""Recording studio: a local web page for recording and scoring the scripted sessions.

Pick a scenario, press record, read the lines; the agent replies live and the finished
session is saved under data/recorded/ (same layout as record.py) and scored on the spot.

Usage: python evals/recorded/studio.py [--port 8000] [--no-browser]
"""

import argparse
import json
import os
import re
import shutil
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

import score
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse

from quoth.demo import session_audio, stream_session
from quoth.domain import load_domain
from quoth.interpreter import ClaudeInterpreter
from quoth.log import EventLog
from quoth.session import Session

HERE = Path(__file__).parent
DATA = HERE.parents[1] / "data" / "recorded"
CONDITIONS = ("quiet", "noisy", "car", "outdoor")
NAME = re.compile(r"[\w-]+")


def create_app(sessions_dir: Path = DATA) -> FastAPI:
    app = FastAPI(title="quoth studio")
    domain = load_domain("order")
    scenarios = score.load_scenarios()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(HERE / "studio.html")

    @app.get("/api/scenarios")
    def list_scenarios() -> dict:
        return {"conditions": CONDITIONS, "scenarios": list(scenarios.values())}

    @app.get("/api/results")
    def results() -> dict:
        rows, totals = score.summarize(sessions_dir)
        return {
            "sessions": [{"name": name, **meta, "quoth": q, "naive": n} for name, meta, q, n in rows],
            "totals": totals,
        }

    @app.delete("/api/sessions/{name}")
    def discard(name: str) -> dict:
        path = sessions_dir / name
        if not NAME.fullmatch(name) or not (path / "meta.json").exists():
            raise HTTPException(404)
        shutil.rmtree(path)
        return {"deleted": name}

    @app.get("/sessions/{name}/audio")
    def audio(name: str) -> FileResponse:
        return session_audio(sessions_dir, name)

    @app.websocket("/ws")
    async def record(ws: WebSocket, scenario: str, condition: str = "quiet", speaker: str = "me") -> None:
        await ws.accept()
        speaker = "".join(NAME.findall(speaker))[:24] or "me"
        if scenario not in scenarios or condition not in CONDITIONS:
            await ws.close(code=1008, reason="unknown scenario or condition")
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_dir = sessions_dir / f"{scenario}__{condition}__{speaker}__{stamp}"
        session = Session(domain, ClaudeInterpreter(), EventLog(session_dir))
        (session_dir / "meta.json").write_text(
            json.dumps({"scenario": scenario, "condition": condition, "speaker": speaker}) + "\n"
        )
        try:
            finished = await stream_session(ws, session, save_audio=True)
        except Exception as exc:  # transcriber or interpreter failure: report it, keep no half session
            traceback.print_exc()
            shutil.rmtree(session_dir, ignore_errors=True)
            await ws.send_json({"error": f"{type(exc).__name__}: {exc}"})
            await ws.close()
            return
        if not finished:
            print(f"{session_dir.name}: page disconnected before the session finished; discarded")
            shutil.rmtree(session_dir, ignore_errors=True)
            return
        q, n = score.score_events(scenarios[scenario]["expected"], session.log.events)
        await ws.send_json({"session_id": session_dir.name, "score": {"quoth": q, "naive": n}})
        await ws.close()

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    load_dotenv(HERE.parents[1] / ".env")
    missing = [k for k in ("DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing {', '.join(missing)} (put them in .env).")
    url = f"http://127.0.0.1:{args.port}"
    print(f"Quoth Studio on {url}  (Ctrl-C to quit)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
