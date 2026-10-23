"""Score SLURP runs: wrong commits vs. questions for three policies, plus an offline threshold sweep.

Policies, all computed from the same sessions:
  naive           apply the interpreter's proposals directly (no verifier, no gate, no questions)
  confirm-all     ask about every extracted field (no wrong commits, one question per field)
  quoth           verifier + gate: confirmed/tentative fields are committed, pending ones are asked

Strict: a value matches the gold label after normalization (case, punctuation, plural "s") or when
its phonetic key is equal ("dominos" vs "domino's").
Lenient (--lenient): SLURP leaves many spoken entities unlabeled and labels at a different
granularity ("hamburgers" vs "two hamburgers"), so fields without a gold label are skipped and a
value also matches when one normalized form contains the other.

Usage: python evals/slurp/score.py data/slurp-runs/v1 [--lenient]
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from quoth.clarify import phonetic_key
from quoth.log import read_events
from quoth.reducer import iter_fields, reduce

HERE = Path(__file__).parent
FIELDS = ("food_type", "business_name", "order_type")
CRITICAL = {"food_type", "business_name"}
COMMITTED = {"confirmed", "tentative"}


def key(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))
    return "".join(w.rstrip("s") for w in words)


def same(a: str, b: str) -> bool:
    return key(a) == key(b) or phonetic_key(a) == phonetic_key(b)


def contains(a: str, b: str) -> bool:
    return same(a, b) or key(a) in key(b) or key(b) in key(a)


def lenient_matches(gold: list[str], predicted: list[str]) -> bool:
    """Every gold value overlaps a predicted one and every predicted value overlaps a gold one."""
    return all(any(contains(g, p) for p in predicted) for g in gold) and all(
        any(contains(g, p) for g in gold) for p in predicted
    )


def matches(gold: list[str], predicted: list[str]) -> bool:
    if len(gold) != len(predicted):
        return False
    remaining = list(predicted)
    for g in gold:
        hit = next((p for p in remaining if same(g, p)), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return True


def as_list(value) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def quoth_fields(events: list[dict]) -> dict[str, dict]:
    """Per field: values across entities, worst status, and minimum field confidence."""
    out: dict[str, dict] = {}
    rank = {"needs_review": 0, "pending": 1, "tentative": 2, "confirmed": 3}
    for f in iter_fields(reduce(events)):
        name = f["field_id"].split(".", 1)[1]
        entry = out.setdefault(name, {"values": [], "status": "confirmed", "confidence": 1.0})
        entry["values"] += as_list(f["value"])
        entry["status"] = min(entry["status"], f["status"], key=rank.__getitem__)
        if f["confidence"] is not None:
            entry["confidence"] = min(entry["confidence"], f["confidence"])
    return out


def naive_fields(events: list[dict]) -> dict[str, list[str]]:
    """The interpreter's proposals applied as-is: last proposed value per entity and field."""
    latest: dict[tuple, list[str]] = {}
    for e in events:
        if e["type"] == "ops_proposed":
            for op in e["ops"]:
                if op["field"] in FIELDS and op["op"] in ("add", "update"):
                    latest[(op["target"]["entity"], op["field"])] = as_list(op["value"])
    out: dict[str, list[str]] = {}
    for (_, name), values in latest.items():
        out.setdefault(name, []).extend(values)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--lenient", action="store_true")
    args = parser.parse_args()
    match = lenient_matches if args.lenient else matches

    selection = json.loads((HERE / "selection.json").read_text())
    tally = {mic: Counter() for mic in ("headset", "distant", "all")}
    sweep_rows = []  # (field confidence, correct, critical) for every extracted field
    examples = []
    missing = 0

    for row in selection:
        for mic, file in row["recordings"].items():
            session_dir = args.run_dir / Path(file).stem
            events = read_events(session_dir)
            if not any(e["type"] == "session_finalized" for e in events):
                missing += 1
                continue
            quoth, naive = quoth_fields(events), naive_fields(events)
            transcript = " | ".join(e["transcript"] for e in events if e["type"] == "utterance_finalized")
            for name in FIELDS:
                gold = row["gold"][name]
                if args.lenient and not gold:
                    continue
                q = quoth.get(name)
                predicted = q["values"] if q else []
                correct = match(gold, predicted)
                for t in (tally[mic], tally["all"]):
                    t["fields"] += bool(gold or predicted)
                    t["gold_fields"] += bool(gold)
                    if naive.get(name) and not match(gold, naive[name]):
                        t["naive_wrong"] += 1
                    if predicted:
                        t["confirm_all_questions"] += 1
                        if q["status"] in COMMITTED:
                            t["quoth_committed"] += 1
                            t["quoth_wrong"] += not correct
                        else:
                            t["quoth_questions"] += 1
                            t["quoth_questions_on_wrong"] += not correct
                    t["missed"] += bool(gold) and not predicted
                    t["correct"] += bool(predicted) and correct
                if predicted:
                    sweep_rows.append((q["confidence"], correct, name in CRITICAL))
                    if not correct and q["status"] in COMMITTED:
                        examples.append(f"{mic:8} gold={gold} got={predicted} conf={q['confidence']:.2f}  heard: {transcript!r}")

    total = sum(len(r["recordings"]) for r in selection)
    print(f"Sessions scored: {total - missing}/{total} ({'lenient' if args.lenient else 'strict'} matching)\n")
    print("| mic | fields | correct | missed | naive wrong commits | confirm-all questions | quoth wrong commits | quoth questions (on wrong values) |")
    print("|---|---|---|---|---|---|---|---|")
    for mic, t in tally.items():
        print(f"| {mic} | {t['fields']} | {t['correct']} | {t['missed']} | {t['naive_wrong']} | {t['confirm_all_questions']} "
              f"| {t['quoth_wrong']} | {t['quoth_questions']} ({t['quoth_questions_on_wrong']}) |")

    print("\nThreshold sweep for critical fields (offline, from logged field confidence):")
    print("| threshold | wrong commits | questions | questions on wrong values |")
    print("|---|---|---|---|")
    critical = [(c, ok) for c, ok, crit in sweep_rows if crit]
    for thr in (0.0, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.99):
        wrong = sum(1 for c, ok in critical if c >= thr and not ok)
        asked = sum(1 for c, _ in critical if c < thr)
        asked_wrong = sum(1 for c, ok in critical if c < thr and not ok)
        print(f"| {thr:.2f} | {wrong} | {asked} | {asked_wrong} |")

    print("\nWrong values Quoth committed:")
    for line in examples:
        print("  " + line)


if __name__ == "__main__":
    sys.exit(main())
