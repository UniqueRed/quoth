# Quoth

Quoth is a state engine for voice agents. It records every spoken fact as an event, together with its audio evidence and calibrated confidence. Corrections are applied as patches, and the user is asked to confirm only the items that are both uncertain and high-stakes.

Prior work exists for each piece: confidence-targeted correction, self-correction handling, and confidence-gated confirmation. What Quoth adds is the combination, in one engine that can be replayed and audited.

## How it works

```
Deepgram Flux EndOfTurn ─▶ utterance_finalized
                              │
                   interpreter (LLM) proposes ops ─▶ ops_proposed
                              │
             verifier (deterministic) ─▶ op_rejected
                              │
           confidence gate ─▶ op_applied / op_downgraded (tentative | pending)
                              │
     clarify: templated question about pending items, within budget ─▶ clarification_asked
```

- **The log is the source of truth.** `events.jsonl` is append-only. State is `reduce(events)`, computed by a pure reducer with no I/O, clock, or randomness. `state.json` is derived from the log and can be rebuilt at any time.
- **The LLM only proposes.** Every operation must name the word span it came from and quote those words. The verifier checks that the span exists, that the quote matches the words at that span, that numbers appear in the evidence, that values are in the allowed list, and that each correction has a single target.
- **The gate uses word-level confidence.** A field's confidence is the minimum Deepgram confidence over the words that carry its value, with filler words excluded. Thresholds are 0.80 for critical fields and 0.65 for normal fields. Fields marked low are always accepted. These numbers are starting points that still need tuning on real recordings.
- **Questions are templated and budgeted.** The templates are:
  - "Was that X?"
  - "A or B?", when a value is close to allowed values
  - "Sorry, X isn't available. What would you like instead?", when nothing allowed is close
  - "Which one did you mean: X or Y?"
  - "Sorry, I didn't catch that. Could you say it again?", when the interpreter marks a turn `unclear` instead of dropping it silently

  When the budget (`max_questions`, default 3) runs out, any remaining pending fields become `needs_review`.

**Limitation:** confidence can be high and the word still wrong, for example with heavy accents or vocabulary outside the domain. On real SLURP recordings, misheard restaurant names scored 0.93–1.00 (see [`evals/slurp/README.md`](evals/slurp/README.md)). The gate reduces silent errors but does not eliminate them. The verifier catches structural mistakes by the interpreter, not every semantic one.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[demo,mic,dev]"
export ANTHROPIC_API_KEY=...    # interpreter (Claude)
export DEEPGRAM_API_KEY=...     # transcriber (Flux)

pytest                                              # no network needed

quoth run sessions/s1 --script examples/order_script.json   # scripted transcript, real interpreter
quoth run sessions/s2 --audio clip.wav --save-audio         # 16-bit mono WAV, streamed to Flux
quoth run sessions/s3 --mic --save-audio                    # microphone
quoth demo --save-audio                                     # web demo on http://127.0.0.1:8000
```

## Commands

| Command | What it does |
|---|---|
| `quoth run <dir> --mic \| --audio F \| --script F` | Start a live session. |
| `quoth replay <dir>` | Rebuild state from `events.jsonl` and check that it matches `state.json` byte for byte. |
| `quoth reinterpret <dir> --model X --prompt P` | Re-run an interpreter over the stored utterances, write a new log, and print a field-by-field diff. |
| `quoth show <dir> [--field line_2.item]` | Print the state, or one field's evidence, confidence, and history. |
| `quoth delete <dir>` | Delete a session, including its audio. |
| `quoth demo` | Start the web demo: mic, live state panel, field evidence with audio snippets, and event timeline. |

## Evaluations

- [`evals/slurp/`](evals/slurp/README.md): 150 real recordings from the SLURP dataset, comparing naive, confirm-all, and Quoth.
- [`evals/recorded/`](evals/recorded/README.md): 12 scripted multi-turn orders to record yourself, covering self-corrections, ambiguous corrections, removals, and off-menu items.

## Session layout

```
sessions/<id>/
  events.jsonl            append-only event log
  state.json              derived state
  raw_transcriber.jsonl   raw Deepgram messages, for offline replay
  audio/session.wav       only with --save-audio
```

## Domain packs

A pack is a JSON file containing data only: the fields, their types, allowed values, criticality, thresholds, question budget, and finalization phrases. See `quoth/packs/order.json`. Pass `--pack path/to/pack.json` to use a different one. The pack's allowed values are also sent to Flux as `keyterm` boosts.

## Conventions

- Word spans are inclusive: `[first, last]`.
- Event times are session-relative and taken from the audio's word timestamps, not the wall clock. Running the same utterances again therefore produces the same log.
- Only Flux `EndOfTurn` messages are acted on. Eager end-of-turn is not enabled.

## Modules

`log` · `reducer` · `domain` · `interpreter` · `verifier` · `gate` · `clarify` · `session` · `transcribe` · `cli` · `demo`

## License

MIT. See [LICENSE](LICENSE). SLURP is not included in this repository; its audio is licensed CC BY-NC 4.0 by its authors.
