"""Interpreters turn a finalized utterance into proposed operations.

The interpreter only proposes. Every operation goes through the verifier and the gate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from quoth.domain import Domain
from quoth.reducer import iter_fields

DEFAULT_PROMPT = Path(__file__).parent / "prompts" / "interpreter.md"
DEFAULT_MODEL = "claude-opus-5-5"

_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}

OPS_SCHEMA = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "update", "remove", "confirm", "deny"]},
                    "target": {
                        "type": "object",
                        "properties": {"entity": _NULLABLE_STRING},
                        "required": ["entity"],
                        "additionalProperties": False,
                    },
                    "field": _NULLABLE_STRING,
                    "value": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "integer"},
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "null"},
                        ]
                    },
                    "word_span": {"type": "array", "items": {"type": "integer"}},
                    "quote": {"type": "string"},
                    "is_correction": {"type": "boolean"},
                    "correction_target_candidates": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "op", "target", "field", "value", "word_span", "quote",
                    "is_correction", "correction_target_candidates",
                ],
                "additionalProperties": False,
            },
        },
        "unclear": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["ops", "unclear", "notes"],
    "additionalProperties": False,
}


class Interpreter(Protocol):
    meta: dict[str, Any]

    def interpret(self, utterance: dict[str, Any], state: dict[str, Any], domain: Domain) -> dict[str, Any]:
        """Return {"ops": [...], "unclear": bool, "notes": str}."""
        ...


class ScriptedInterpreter:
    """Returns pre-written operations per utterance, in order. For tests and offline runs.

    Each response is a list of ops, or a full {"ops", "unclear"} proposal."""

    meta = {"model": "scripted", "prompt_hash": None}

    def __init__(self, responses: list[list[dict[str, Any]]]):
        self.responses = list(responses)

    def interpret(self, utterance: dict[str, Any], state: dict[str, Any], domain: Domain) -> dict[str, Any]:
        response = self.responses.pop(0) if self.responses else []
        return {"notes": "", **response} if isinstance(response, dict) else {"ops": response, "notes": ""}


def compact_state(state: dict[str, Any]) -> dict[str, Any]:
    """Only what the interpreter needs: values and statuses, no evidence or history."""
    entities: dict[str, dict[str, Any]] = {}
    for field in iter_fields(state):
        entity_id, name = field["field_id"].split(".", 1)
        entities.setdefault(entity_id, {})[name] = {"value": field["value"], "status": field["status"]}
    return entities


def build_request(utterance: dict[str, Any], state: dict[str, Any], domain: Domain) -> str:
    question = state["open_question"]
    return json.dumps(
        {
            "domain": {"description": domain.description, "entity_id_format": f"{domain.entity_prefix}_<n>", "fields": domain.fields},
            "state": compact_state(state),
            "open_question": question and {k: question[k] for k in ("question", "field_ids", "candidates")},
            "words": [[i, w["word"]] for i, w in enumerate(utterance["words"])],
        }
    )


class ClaudeInterpreter:
    """Interpreter backed by Claude with structured outputs."""

    def __init__(self, model: str = DEFAULT_MODEL, prompt_path: Path = DEFAULT_PROMPT, client: Any = None):
        import anthropic

        self.model = model
        self.prompt = prompt_path.read_text(encoding="utf-8")
        self.client = client or anthropic.Anthropic()
        self.meta = {"model": model, "prompt_hash": hashlib.sha256(self.prompt.encode()).hexdigest()[:12]}

    def interpret(self, utterance: dict[str, Any], state: dict[str, Any], domain: Domain) -> dict[str, Any]:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.prompt,
            messages=[{"role": "user", "content": build_request(utterance, state, domain)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": OPS_SCHEMA}},
            # On a safety decline, re-run on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return {"ops": [], "unclear": False, "notes": "interpreter declined this turn"}
        if response.stop_reason == "max_tokens":
            raise RuntimeError("interpreter output was truncated (max_tokens)")
        text = next(block.text for block in response.content if block.type == "text")
        result = json.loads(text)
        result["served_by"] = response.model
        result["usage"] = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        return result
