"""Minimal web demo: browser mic -> WebSocket -> Deepgram Flux -> Quoth session -> live state."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from quoth.domain import load_domain
from quoth.interpreter import ClaudeInterpreter
from quoth.log import AUDIO_FILE, EventLog
from quoth.session import Session
from quoth.transcribe import FluxTranscriber, recording

STATIC = Path(__file__).parent / "demo_static"
SAMPLE_RATE = 16000


def session_audio(sessions_dir: Path, session_id: str) -> FileResponse:
    path = sessions_dir / session_id / AUDIO_FILE
    if not re.fullmatch(r"[\w-]+", session_id) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


async def stream_session(ws: WebSocket, session: Session, save_audio: bool) -> bool:
    """Pipe browser PCM through Flux into the session, sending each reply as JSON.

    Binary messages are 16 kHz 16-bit mono PCM; any text message means "stop". The session is
    finalized when the user says a finalization phrase or presses stop. Returns False if the
    browser disconnected first (the session is then left unfinalized).
    """
    log = session.log
    chunks: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def mic() -> AsyncIterator[bytes]:
        while (chunk := await chunks.get()) is not None:
            yield chunk

    async def receive() -> None:
        try:
            while True:
                message = await ws.receive()
                if message.get("bytes"):
                    chunks.put_nowait(message["bytes"])
                elif message["type"] == "websocket.disconnect" or message.get("text"):
                    break
        except WebSocketDisconnect:
            pass
        chunks.put_nowait(None)

    pending_sends: set[asyncio.Task] = set()

    def on_raw(message: dict) -> None:
        log.append_raw(message)
        # Forward in-progress transcripts so the page can show what is being heard.
        if message.get("type") == "TurnInfo" and message.get("event") != "EndOfTurn" and message.get("transcript"):
            task = asyncio.create_task(ws.send_json({"partial": message["transcript"]}))
            pending_sends.add(task)
            task.add_done_callback(pending_sends.discard)

    audio_in = recording(mic(), log.session_dir / AUDIO_FILE, SAMPLE_RATE) if save_audio else mic()
    transcriber = FluxTranscriber(audio_in, SAMPLE_RATE, session.domain.keyterms(), on_raw=on_raw)
    receiver = asyncio.create_task(receive())

    async def send(transcript: str, reply: dict) -> None:
        await ws.send_json(
            {
                "session_id": log.session_dir.name,
                "has_audio": save_audio,
                "transcript": transcript,
                "reply": reply,
                "state": session.state,
                "events": log.events,
            }
        )

    try:
        await send("", {"question": None, "readback": None, "finalized": False})
        async for turn in transcriber.turns():
            reply = await asyncio.to_thread(session.handle_turn, turn)
            await send(turn["transcript"], reply)
            if reply["finalized"]:
                break
        if not session.finalized:
            text = session.finalize(log.events[-1]["t_sec"] if log.events else 0.0)
            await send("", {"question": None, "readback": text, "finalized": True})
        return True
    except WebSocketDisconnect:
        return False
    finally:
        receiver.cancel()


def create_app(sessions_dir: Path, pack: str, model: str, save_audio: bool) -> FastAPI:
    app = FastAPI(title="quoth demo")
    domain = load_domain(pack)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/sessions/{session_id}/audio")
    def audio(session_id: str) -> FileResponse:
        return session_audio(sessions_dir, session_id)

    @app.websocket("/ws")
    async def ws_session(ws: WebSocket) -> None:
        await ws.accept()
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        session = Session(domain, ClaudeInterpreter(model), EventLog(sessions_dir / session_id))
        if await stream_session(ws, session, save_audio):
            await ws.close()

    return app
