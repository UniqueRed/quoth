"""Domain packs: field schema, allowed values, and criticality, loaded from JSON data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKS_DIR = Path(__file__).parent / "packs"
CRITICALITY_RANK = {"critical": 0, "normal": 1, "low": 2}


@dataclass(frozen=True)
class Domain:
    name: str
    description: str
    entity_prefix: str
    label_field: str
    fields: dict[str, dict[str, Any]]
    thresholds: dict[str, float]
    max_questions: int
    finalization_triggers: list[str]

    def criticality(self, field: str | None) -> str:
        return self.fields[field]["criticality"] if field in self.fields else "critical"

    def allowed_values(self, field: str) -> list[str] | None:
        return self.fields[field].get("allowed_values")

    def keyterms(self) -> list[str]:
        """Words worth boosting in the transcriber: every allowed value."""
        return [v for spec in self.fields.values() for v in spec.get("allowed_values", [])]


def load_domain(name_or_path: str) -> Domain:
    """Load a bundled pack by name (e.g. "order") or any pack JSON by path."""
    path = Path(name_or_path)
    if not path.suffix:
        path = PACKS_DIR / f"{name_or_path}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return Domain(
        name=data["name"],
        description=data.get("description", ""),
        entity_prefix=data["entity_prefix"],
        label_field=data["label_field"],
        fields=data["fields"],
        thresholds=data.get("thresholds", {"critical": 0.8, "normal": 0.65}),
        max_questions=data.get("max_questions", 3),
        finalization_triggers=[t.lower() for t in data.get("finalization_triggers", [])],
    )
