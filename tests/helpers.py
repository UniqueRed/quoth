"""Builders for scripted Flux-style turns and interpreter operations."""

from typing import Any

WORD_SEC = 0.4


def flux_turn(index: int, text: str, low: dict[int, float] | None = None, start: float | None = None) -> dict[str, Any]:
    """A Flux EndOfTurn TurnInfo message. `low` maps word index -> confidence (default 0.95).
    Numeric fields are strings, as Flux sends them."""
    low = low or {}
    t0 = start if start is not None else index * 10.0
    words = [
        {
            "word": w,
            "start": str(round(t0 + i * WORD_SEC, 2)),
            "end": str(round(t0 + (i + 1) * WORD_SEC, 2)),
            "confidence": str(low.get(i, 0.95)),
        }
        for i, w in enumerate(text.split())
    ]
    return {
        "type": "TurnInfo",
        "event": "EndOfTurn",
        "turn_index": index,
        "audio_window_start": str(t0),
        "audio_window_end": words[-1]["end"] if words else str(t0),
        "transcript": text,
        "words": words,
        "end_of_turn_confidence": "0.9",
        "trigger": "model",
    }


def op(kind: str, entity: str | None, field: str | None, value: Any, span: list[int], quote: str,
       correction: list[str] | None = None) -> dict[str, Any]:
    return {
        "op": kind,
        "target": {"entity": entity},
        "field": field,
        "value": value,
        "word_span": span,
        "quote": quote,
        "is_correction": correction is not None,
        "correction_target_candidates": correction or [],
    }


def utterance(text: str, low: dict[int, float] | None = None) -> dict[str, Any]:
    from quoth.transcribe import turn_from_flux

    return {"utterance_id": "u_001", **turn_from_flux(flux_turn(0, text, low))}
