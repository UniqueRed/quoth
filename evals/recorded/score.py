"""Score recorded sessions against each scenario's intended order.

For every session, order lines are aligned to the expected lines (same item first, then
leftovers in order), and each field (item, quantity, modifiers) is checked. A quantity that
was never stated counts as 1; missing modifiers count as none.

Policies:
  naive    the interpreter's proposals applied directly (no verifier, gate, or questions),
           skipping turns that only happened because Quoth asked a question
  quoth    final state: confirmed/tentative fields are committed, pending/needs_review are flagged

Usage: python evals/recorded/score.py [data/recorded]
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from quoth.log import read_events
from quoth.reducer import reduce

HERE = Path(__file__).parent
FIELDS = ("item", "quantity", "modifiers")
COMMITTED = {"confirmed", "tentative"}
DEFAULTS = {"quantity": 1, "modifiers": []}


def quoth_lines(events: list[dict]) -> list[dict]:
    """Each live entity as {field: (value, status)}."""
    lines = []
    for entity in reduce(events)["entities"].values():
        if not entity["removed"]:
            lines.append({name: (f["value"], f["status"]) for name, f in entity["fields"].items()})
    return lines


def naive_lines(events: list[dict]) -> list[dict]:
    """Proposals applied as-is, every field committed.

    A naive agent asks nothing, so turns that answered one of Quoth's questions (a repeat,
    a confirmation, a choice) would never have been spoken; their proposals are skipped.
    Replies to "X isn't available" are kept: the scripts replace the off-menu item anyway.
    """
    entities: dict[str, dict] = {}
    asked = answering = False
    for e in events:
        if e["type"] == "clarification_asked":
            asked = e["kind"] != "replace_value"
        elif e["type"] == "utterance_finalized":
            answering, asked = asked, False
        if e["type"] != "ops_proposed" or answering:
            continue
        for op in e["ops"]:
            target = op["target"]["entity"] or (op["correction_target_candidates"] or [None])[0]
            if op["op"] == "remove":
                entities.pop(target, None)
            elif op["op"] in ("add", "update") and target:
                entities.setdefault(target, {})[op["field"]] = (op["value"], "confirmed")
    return list(entities.values())


def value(line: dict, field: str):
    v = line.get(field, (None, None))[0]
    if v is None:
        v = DEFAULTS.get(field)
    return sorted(v) if field == "modifiers" else v


def status(line: dict, field: str) -> str:
    # Fields never set (implicit quantity/modifiers) are treated as committed defaults.
    return line.get(field, (None, "confirmed"))[1]


def align(expected: list[dict], predicted: list[dict]) -> tuple[list[tuple], list[dict]]:
    pairs, left = [], list(predicted)
    unmatched = []
    for exp in expected:
        hit = next((p for p in left if value(p, "item") == exp["item"]), None)
        if hit is not None:
            left.remove(hit)
            pairs.append((exp, hit))
        else:
            unmatched.append(exp)
    for exp in unmatched:
        pairs.append((exp, left.pop(0) if left else None))
    return pairs, left


def score(expected: list[dict], predicted: list[dict]) -> Counter:
    c = Counter()
    pairs, extra = align(expected, predicted)
    for exp, got in pairs:
        for field in FIELDS:
            c["fields"] += 1
            want = sorted(exp[field]) if field == "modifiers" else exp[field]
            if got is None:
                c["missed"] += 1
                continue
            right = value(got, field) == want
            if status(got, field) in COMMITTED:
                c["wrong_commits"] += not right
            else:
                c["flagged"] += 1
                c["flagged_wrong"] += not right
            c["wrong_values"] += not right
    for got in extra:
        c["extra_lines"] += 1
        if status(got, "item") in COMMITTED:
            c["wrong_commits"] += 1
    c["exact"] = int(c["wrong_values"] == c["missed"] == c["extra_lines"] == c["flagged"] == 0)
    return c


def load_scenarios() -> dict[str, dict]:
    return {s["id"]: s for s in json.loads((HERE / "scenarios.json").read_text())}


def score_events(expected: list[dict], events: list[dict]) -> tuple[Counter, Counter]:
    """(quoth, naive) scores for one finalized session."""
    q, n = score(expected, quoth_lines(events)), score(expected, naive_lines(events))
    q["questions"] = sum(e["type"] == "clarification_asked" for e in events)
    q["sessions"] = n["sessions"] = 1
    return q, n


def summarize(sessions_dir: Path) -> tuple[list[tuple[str, dict, Counter, Counter]], dict[str, dict[str, Counter]]]:
    """Per-session rows (name, meta, quoth, naive) and totals per condition plus "all"."""
    scenarios = load_scenarios()
    by_condition: dict[str, dict[str, Counter]] = defaultdict(lambda: {"quoth": Counter(), "naive": Counter()})
    rows = []
    for meta_file in sorted(sessions_dir.glob("*/meta.json")):
        meta = json.loads(meta_file.read_text())
        events = read_events(meta_file.parent)
        if not any(e["type"] == "session_finalized" for e in events):
            continue
        q, n = score_events(scenarios[meta["scenario"]]["expected"], events)
        for group in (meta["condition"], "all"):
            by_condition[group]["quoth"].update(q)
            by_condition[group]["naive"].update(n)
        rows.append((meta_file.parent.name, meta, q, n))
    return rows, dict(by_condition)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sessions_dir", nargs="?", type=Path, default=Path("data/recorded"))
    args = parser.parse_args()

    rows, by_condition = summarize(args.sessions_dir)
    if not rows:
        print(f"No finalized sessions in {args.sessions_dir}. Record some with evals/recorded/record.py.")
        return

    print("| session | quoth exact | quoth wrong commits | flagged | questions | naive exact | naive wrong commits |")
    print("|---|---|---|---|---|---|---|")
    for name, _, q, n in rows:
        print(f"| {name} | {'yes' if q['exact'] else 'no'} | {q['wrong_commits']} | {q['flagged']} | {q['questions']} "
              f"| {'yes' if n['exact'] else 'no'} | {n['wrong_commits']} |")

    print("\n| condition | sessions | fields | quoth exact | quoth wrong commits | quoth questions | flagged (wrong) | missed | naive exact | naive wrong commits |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for group, t in sorted(by_condition.items(), key=lambda kv: kv[0] == "all"):
        q, n = t["quoth"], t["naive"]
        print(f"| {group} | {q['sessions']} | {q['fields']} | {q['exact']} | {q['wrong_commits']} | {q['questions']} "
              f"| {q['flagged']} ({q['flagged_wrong']}) | {q['missed']} | {n['exact']} | {n['wrong_commits']} |")


if __name__ == "__main__":
    main()
