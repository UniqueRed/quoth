"""Transcribers: yield finalized user turns.

A finalized turn is a dict:
    {"turn_index": int, "transcript": str, "trigger": str,
     "words": [{"word": str, "start": float, "end": float, "confidence": float}]}

Only final transcripts are used; interim/eager results are never surfaced.
"""

from __future__ import annotations

import asyncio
import json
import os
import wave
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Protocol
from urllib.parse import urlencode

RawCallback = Callable[[dict[str, Any]], None]

FLUX_URL = "wss://api.deepgram.com/v2/listen"
FLUX_MODEL = "flux-general-en"
CHUNK_MS = 80  # Deepgram recommends ~80 ms chunks for Flux
TRAILING_SILENCE_MS = 1000


class Transcriber(Protocol):
    def turns(self) -> AsyncIterator[dict[str, Any]]: ...


def turn_from_flux(message: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Flux EndOfTurn TurnInfo message. Flux sends numeric fields as strings."""
    return {
        "turn_index": int(message["turn_index"]),
        "transcript": message["transcript"],
        "trigger": message.get("trigger", "model"),
        "words": [
            {
                "word": w["word"],
                "start": float(w["start"]),
                "end": float(w["end"]),
                "confidence": float(w["confidence"]),
            }
            for w in message["words"]
        ],
    }


def _is_end_of_turn(message: dict[str, Any]) -> bool:
    return message.get("type") == "TurnInfo" and message.get("event") == "EndOfTurn"


class FakeTranscriber:
    """Plays a scripted sequence of Flux-style messages. No network."""

    def __init__(self, messages: Iterable[dict[str, Any]], on_raw: RawCallback | None = None):
        self.messages = list(messages)
        self.on_raw = on_raw

    @classmethod
    def from_file(cls, path: Path, on_raw: RawCallback | None = None) -> FakeTranscriber:
        return cls(json.loads(path.read_text(encoding="utf-8")), on_raw)

    async def turns(self) -> AsyncIterator[dict[str, Any]]:
        for message in self.messages:
            if self.on_raw:
                self.on_raw(message)
            if _is_end_of_turn(message):
                yield turn_from_flux(message)


class FluxTranscriber:
    """Deepgram Flux (listen v2) streaming transcriber for 16-bit mono PCM audio."""

    def __init__(
        self,
        audio: AsyncIterator[bytes],
        sample_rate: int = 16000,
        keyterms: Iterable[str] = (),
        api_key: str | None = None,
        on_raw: RawCallback | None = None,
    ):
        self.audio = audio
        self.sample_rate = sample_rate
        self.keyterms = list(keyterms)
        self.api_key = api_key or os.environ.get("DEEPGRAM_API_KEY")
        self.on_raw = on_raw
        if not self.api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not set")

    def url(self) -> str:
        params = [
            ("model", FLUX_MODEL),
            ("encoding", "linear16"),
            ("sample_rate", self.sample_rate),
            *(("keyterm", term) for term in self.keyterms),
        ]
        return f"{FLUX_URL}?{urlencode(params)}"

    async def turns(self) -> AsyncIterator[dict[str, Any]]:
        from websockets.asyncio.client import connect

        headers = {"Authorization": f"Token {self.api_key}"}
        async with connect(self.url(), additional_headers=headers) as ws:
            sender = asyncio.create_task(self._send_audio(ws))
            try:
                async for raw in ws:
                    message = json.loads(raw)
                    if self.on_raw:
                        self.on_raw(message)
                    if message.get("type") == "Error":
                        raise RuntimeError(f"Deepgram error {message['code']}: {message['description']}")
                    if _is_end_of_turn(message):
                        yield turn_from_flux(message)
            finally:
                sender.cancel()

    async def _send_audio(self, ws: Any) -> None:
        async for chunk in self.audio:
            await ws.send(chunk)
        # Trailing silence lets Flux decode the last word before the turn is forced to end.
        silence = bytes(self.sample_rate * 2 * CHUNK_MS // 1000)
        for _ in range(TRAILING_SILENCE_MS // CHUNK_MS):
            await ws.send(silence)
        # CloseStream alone does not emit a final EndOfTurn, so end the turn first.
        await ws.send(json.dumps({"type": "ForceEndTurn"}))
        await ws.send(json.dumps({"type": "CloseStream"}))


async def wav_chunks(path: Path, realtime: bool = True) -> AsyncIterator[bytes]:
    """Stream a 16-bit mono WAV file in 80 ms chunks, paced like a live mic by default."""
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("expected a 16-bit mono WAV file")
        frames = wav.getframerate() * CHUNK_MS // 1000
        while chunk := wav.readframes(frames):
            yield chunk
            if realtime:
                await asyncio.sleep(CHUNK_MS / 1000)


def wav_sample_rate(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        return wav.getframerate()


async def mic_chunks(sample_rate: int = 16000) -> AsyncIterator[bytes]:
    """Stream 16-bit mono PCM from the default microphone until cancelled."""
    import sounddevice  # optional dependency: pip install quoth[mic]

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def callback(data, frames, time, status):  # noqa: ARG001 - sounddevice signature
        loop.call_soon_threadsafe(queue.put_nowait, bytes(data))

    blocksize = sample_rate * CHUNK_MS // 1000
    with sounddevice.RawInputStream(
        samplerate=sample_rate, channels=1, dtype="int16", blocksize=blocksize, callback=callback
    ):
        while True:
            yield await queue.get()


async def recording(audio: AsyncIterator[bytes], path: Path, sample_rate: int) -> AsyncIterator[bytes]:
    """Pass audio through while saving it to a WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        async for chunk in audio:
            wav.writeframes(chunk)
            yield chunk
