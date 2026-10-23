# Quoth — v1 Spec

Name: `quoth` (confirm PyPI availability with `pip index versions quoth` before publishing; backup name: `vouch`).

**One-line description:** a state engine for voice agents that records every spoken fact as an event with its audio evidence and calibrated confidence, applies corrections as patches, and asks the user to confirm only the uncertain, high-stakes items.

## 1. Problem
Voice agents usually send a transcript to an LLM and act on it as if every word were equally certain. Two failure modes follow:
1. **Silent errors.** A low-confidence word ("vanilla" vs. "valuable") becomes a committed fact (an order line, a dosage, a measurement) and nobody notices.
2. **Messy corrections.** "Make that three... no, hold the onions" forces the LLM to untangle text, and nothing records what changed or why.

Agents that compensate by confirming everything are tedious to use.

## 2. Core idea
- The conversation is an **append-only event log**. Current state is computed by replaying the log, never edited in place.
- Every field in the state carries **evidence** (the exact words and timestamps it came from) and a **confidence** (Deepgram's calibrated per-word score).
- A **correction** is an event that patches an earlier field. The old value stays in the log.
- A **confidence gate** decides what is safe to accept. Uncertain critical fields become `pending`, and the agent asks about only those.

Position honestly: confidence-targeted LLM correction, self-correction handling, and confidence-gated confirmation each have prior work. The contribution here is the combination in one replayable, auditable engine for tool-using voice agents. Do not claim any single piece is new.

## 3. Users
- Developers building voice agents or voice-to-form products on Deepgram.
- Reference demo user: a person placing an order by voice (drive-thru style), chosen because corrections, noise, and uncertain words all occur naturally.

## 4. v1 scope
In scope:
1. Python package with a CLI.
2. Event log, deterministic reducer, and state with provenance.
3. Interpreter (LLM) that turns each finalized turn into state operations.
4. Verification of interpreter output against the transcript.
5. Confidence gate with per-field criticality and a question budget.
6. Clarification loop (templated questions, answers fed back through the same pipeline).
7. Deepgram streaming transcriber (Flux) behind a small interface, plus a fake transcriber for tests.
8. Replay tool: rebuild state from a log, and re-run the interpreter over stored utterances to diff results.
9. Minimal web demo: mic input, live state panel, per-field evidence, event timeline.

Out of scope for v1: multi-speaker handling, partial/interim interpretation (act only on finalized turns), custom TTS voices (text output first; Aura-2 playback is a stretch goal), accounts, hosting, any non-Deepgram transcriber implementation.

## 5. Architecture decisions
- **Language:** Python 3.11+, FastAPI + WebSockets for the demo server, plain TypeScript or vanilla JS for the demo page.
- **Reducer is pure and deterministic.** No LLM, network, or clock inside it. State = `fold(events)`. This is what makes replay trustworthy.
- **LLM only proposes; code decides.** The interpreter outputs operations; the verifier and gate accept, downgrade, or reject them.
- **Provider interfaces:** `Transcriber` (default: Deepgram Flux) and `Interpreter` (configurable LLM). Keep minimal.
- **Storage:** a session is a directory containing `events.jsonl` (the log), `audio/` (optional), and `state.json` (derived, regenerable).

## 6. Event log

Every line of `events.jsonl` is one event with `id`, `type`, `t_sec` (session-relative), and a payload.

| Event | Meaning |
|---|---|
| `utterance_finalized` | A finished user turn from the transcriber: transcript, words with start/end/confidence, turn index, end-of-turn trigger |
| `ops_proposed` | Interpreter's proposed operations for that utterance, with interpreter metadata (model, prompt hash) |
| `op_applied` | An operation that passed verification and the gate |
| `op_downgraded` | An operation accepted as `pending` or `tentative`, with the reason |
| `op_rejected` | An operation that failed verification, with the reason |
| `clarification_asked` | Question text, field ids, why it was asked |
| `clarification_answered` | The user turn that answered, and the resulting resolutions |
| `session_finalized` | The user is done; final state computed |

Rules: events are never modified or deleted. Derived state can always be rebuilt from the log.

## 7. State and provenance

Each entity (for the demo: an order line) has fields. Each field looks like:

```json
{
  "field_id": "line_2.item",
  "value": "vanilla shake",
  "status": "confirmed | tentative | pending | superseded | needs_review",
  "confidence": 0.58,
  "evidence": {
    "utterance_id": "u_007",
    "word_span": [14, 15],
    "start_sec": 6.31,
    "end_sec": 6.92,
    "quote": "vanilla shake",
    "min_word_confidence": 0.58
  },
  "history": [
    {"event_id": "e_031", "value": "valuable shake", "status": "superseded"}
  ]
}
```

Status meanings:
- `confirmed`: high confidence, or the user explicitly confirmed it.
- `tentative`: accepted but below the comfort bar; not worth interrupting the user over.
- `pending`: critical and uncertain; must be resolved by a question or flagged at the end.
- `superseded`: replaced by a later correction (kept in history).
- `needs_review`: could not be resolved within the question budget; flagged for a human in the output.

## 8. Interpretation

For each `utterance_finalized`, the interpreter receives: the transcript with word indices, the current state, the domain schema, and the allowed-values lists. It returns structured operations:

```json
{
  "ops": [
    {
      "op": "add | update | remove | confirm | deny",
      "target": {"entity": "line_1"},
      "field": "quantity",
      "value": 3,
      "word_span": [20, 23],
      "is_correction": true,
      "correction_target_candidates": ["line_1"]
    }
  ]
}
```

Rules in the prompt:
- Use only what the user said; never invent values.
- Report the word span supporting each operation.
- For corrections, list every entity the correction could refer to. If more than one fits, say so.
- If unsure, say so in the output instead of guessing.

## 9. Verification (deterministic, no LLM)
Before an operation is applied:
- The word span exists and lies within the utterance.
- The quoted words are really the words at that span.
- Numeric values appear in the evidence span (as digits or number words).
- The value is in the field's allowed-values list when one is defined; otherwise it is marked `pending`.
- A correction has exactly one resolvable target; if it has several, it is turned into a clarification about the target, not applied.

Failures become `op_rejected` or `op_downgraded` with a stated reason. They are never silently fixed.

## 10. Confidence gate

For each accepted operation, compute `field_confidence` = the minimum Deepgram word confidence across the words that carry the value (the item, number, or name, not filler words).

Each field in the domain schema has a `criticality`: `critical`, `normal`, or `low`.

| Criticality | Gate rule (starting values, tune on real data) |
|---|---|
| critical | `field_confidence >= 0.80` → confirmed; otherwise `pending` |
| normal | `>= 0.65` → confirmed; otherwise `tentative` |
| low | always accepted; never triggers a question |

Notes:
- Deepgram documents word confidence as calibrated and suggests around 0.65 as a precision-oriented error threshold, which is why 0.65 is the baseline. Critical fields use a higher bar; the numbers here are starting points, not validated.
- Use confidence from **final** transcripts only, never interim.
- Do not rely on the transcript-level confidence; Deepgram computes it as a median, so one bad word can hide inside it. Always check word-level confidence on the words that matter.
- Confidence can be high and still wrong (heavy accents, domain mismatch). The gate reduces errors; it does not eliminate them. Say so in the docs.

**Question budget:** a session setting `max_questions` (default 3). When the budget is spent, remaining `pending` fields become `needs_review` in the output instead of more questions.

## 11. Clarification loop
- Ask only about `pending` fields, most critical first, batched at a natural point (end of the user's turn when the agent would respond, or at finalization).
- One question at a time. Questions are **templated**, not LLM-generated, for predictability:
  - Single value: "Was that {value}?"
  - With candidates: "{a} or {b}?"
  - Ambiguous correction target: "Which one did you mean: {x} or {y}?"
- Candidates come from the allowed-values list ranked by string and phonetic similarity to the uncertain word.
- The user's reply goes through the same pipeline as any other turn and is interpreted as `confirm`, `deny`, or a new value. A confirm sets the field to `confirmed`. A deny supersedes it.
- Never re-ask a resolved field.
- On "that's all" (or a configured finalization trigger), read back only unresolved items, then emit `session_finalized`.

## 12. Deepgram integration
- Use Flux streaming (`flux-general-en`, listen v2) for turn-based input. Each `EndOfTurn` message supplies the final transcript and words with timestamps and confidence; build `utterance_finalized` from it.
- Do not enable eager end-of-turn in v1; act on finalized turns only.
- Keep the `Transcriber` interface minimal so Nova-3 (listen v1, `is_final` results) can be added later.
- Vocabulary boosting: Deepgram documents keyterm prompting for its models. **Verify whether Flux supports it** before relying on it; if not, use Nova-3 for the demo or skip boosting in v1.
- Verify the exact Flux message fields and parameter names against the current docs before building; they have changed over time.
- Store the raw transcriber messages alongside the events so a session can be replayed offline without calling Deepgram.

## 13. Replay and debugging
CLI commands:
- `quoth run` starts a live session (mic, or an audio file streamed to Deepgram).
- `quoth replay <session>` rebuilds state from `events.jsonl` and checks it matches the stored `state.json` byte for byte.
- `quoth reinterpret <session> --model X --prompt Y` re-runs the interpreter over stored utterances, writes a new log, and prints a field-by-field diff against the original.
- `quoth show <session> --field line_2.item` prints the evidence, confidence, and history for one field.

`reinterpret` is the practical payoff of event sourcing: when you change a prompt or model, you can see exactly which fields change without re-recording anything.

## 14. Demo domain pack (order taking)
A small pack: menu items (allowed values), quantities, modifiers ("no onions", "extra cheese"), and a criticality map (item and quantity critical, modifiers normal, nothing low or everything critical as you prefer to test). The pack is data, not code, so a second domain (inspector notes, from the earlier toolkit spec) can be added later with no core changes.

## 15. Demo UI (keep it minimal)
- Mic button, live user transcript, agent's questions as text.
- State panel: each field color-coded by status (confirmed, tentative, pending, needs_review).
- Click a field to see its evidence: quote, timestamps, confidence, history, and an audio snippet if audio is stored.
- Event timeline underneath. No styling work beyond readability.

## 16. Testing
- **Reducer tests:** replaying the same log always gives the same state; corrections patch correctly; superseded values stay in history.
- **Verifier tests:** fabricated quotes, wrong spans, numbers not in the evidence, and ambiguous correction targets are all caught.
- **Gate tests:** boundary values, criticality handling, question budget exhaustion.
- **Scenario tests** using a fake transcriber that plays scripted `TurnInfo`-style sequences, no network needed:
  1. Plain order with high confidence → no questions.
  2. "Two... actually three" → one update, history preserved.
  3. Low-confidence critical item → exactly one question.
  4. Ambiguous correction ("make that two" with two candidate lines) → target clarification.
  5. Budget exhausted → remaining items `needs_review`.
  6. Deny then re-state → correct final value.
- **Real-audio check:** a handful of recorded clips (quiet, noisy car, accented) run end to end, with expected final state written down.
These are regression tests that protect the behavior; they are not a benchmark project.

## 17. How to know it works
1. **Real use:** you and a few friends place real orders by voice, including with background noise. Track committed wrong values, questions asked, and whether the questions felt reasonable.
2. **Comparison for the writeup:** same sessions run through (a) a naive "LLM reads the transcript" baseline and (b) confirm-everything vs. (c) Quoth. Report wrong commits and number of questions for each. This is the article's central result.
3. **Replay holds:** every session replays to identical state.
4. **Developer test:** someone else gets the quickstart running in under 15 minutes.

## 18. Milestones (build in this order; each must pass its tests before the next)
1. Event log format, pure reducer, state model with provenance, reducer tests.
2. Fake transcriber, scripted scenarios, `replay` command.
3. Interpreter interface with a stub and a real LLM implementation; operation schema; verifier with tests.
4. Confidence gate, criticality, question budget, with tests.
5. Clarification loop with templated questions and answer handling.
6. Deepgram Flux transcriber; raw message storage; live CLI session.
7. `reinterpret` and `show` commands.
8. Demo UI and the order-taking pack.
9. Real-audio sessions, threshold tuning, comparison runs, README, publish.

## 19. Instructions for Claude Code
- Build milestone by milestone; do not start a milestone until the previous one's tests pass.
- Keep the reducer free of I/O and randomness. If a bug involves state, fix it in the reducer or log format, never by patching state directly.
- Never let LLM output bypass the verifier or the gate.
- Write the scenario tests (section 16) before the code they exercise where practical.
- Do not add features outside the v1 scope. Ask before expanding scope.
- Keep modules small: `log`, `reducer`, `interpreter`, `verifier`, `gate`, `clarify`, `transcribe`, `cli`, `demo`.

## 20. Risks and open questions
- **Flux features:** keyterm support and exact message fields need verifying against current docs.
- **Calibration limits:** confidence can be wrong on accents and noisy domains; thresholds need tuning on real recordings. Report this honestly.
- **Interpreter errors:** the LLM can mis-resolve corrections; the verifier catches structural problems but not every semantic one.
- **Latency:** one LLM call per finalized turn adds delay. Measure it; keep the prompt small.
- **Question burden:** if the gate is too strict the agent becomes annoying. The budget and per-field criticality are the controls, and the comparison in section 17 measures this.
- **Novelty:** prior work exists for each ingredient. Frame the project as an engineering combination with measured results, not a new technique.
- **Privacy:** sessions may contain voices and personal data. Provide a command to delete a session fully.
