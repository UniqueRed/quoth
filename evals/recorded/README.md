# Recorded sessions: real multi-turn orders

SLURP only has single commands, so it can't test corrections, removals, or the clarification loop. These 12 scripted scenarios cover those. You speak each script into the mic, the agent responds live, and each session is scored against the order you meant to place.

## Scenarios

| id | what it tests |
|---|---|
| s01 | clean order; the agent should ask nothing |
| s02 | "two, uh, actually three" in the same sentence |
| s03 | swapping an item in a later turn |
| s04 | fixing a quantity in a later turn |
| s05 | "make that two" when it could mean either item; the agent should ask which |
| s06 | removing an item |
| s07 | taking back one modifier and keeping another |
| s08 | fillers, plus a wrong word replaced mid-sentence ("vanilla, no, strawberry") |
| s09 | a cut-off word and several modifiers in one breath |
| s10 | an item that isn't on the menu, then replacing it |
| s11 | correcting a size you said wrong |
| s12 | a longer order with several separate changes |

## Recording

The easiest way is the studio, a local web page:

```bash
python evals/recorded/studio.py        # opens http://127.0.0.1:8000
```

- **Recording a scenario:** pick one from the list, choose the condition and speaker at the top, and press record (or Space). The current script line is highlighted as you go. Agent questions appear in a highlighted box; answer them out loud. A live panel shows the order as Quoth sees it.
- **After each session:** it is saved and scored straight away, Quoth against the naive baseline. You can play it back, discard it and redo it, or move on to the next scenario.
- **Results tab:** totals per condition and every session, each with playback and delete.

Sessions go to `data/recorded/`, the same place and format as the terminal recorder below.

Terminal alternative:

```bash
pip install -e ".[mic]"                # once
python evals/recorded/record.py --list
python evals/recorded/record.py s05 --condition quiet --speaker me
```

The script prints the lines to say and the order you actually want, then listens.

- **Pacing:** say one line per turn and wait for the agent before saying the next.
- **Questions:** if the agent asks something, answer truthfully for the intended order. Say "yes", or "no" plus the right item. If it asks "which one", say the item's name. Then carry on with the script.
- **Speaking style:** talk naturally. Hesitations and stumbles are what's being tested, so don't over-enunciate.
- **Saving:** each session is saved with its audio under `data/recorded/`, which git ignores.

Suggested set, about 36 sessions:

| condition | who | how |
|---|---|---|
| `quiet` | you | all 12 scenarios |
| `noisy` | you | all 12, with a TV, music, or a kitchen fan on |
| `car` or `outdoor` | you, or a friend with a different accent | a few scenarios |

Pass `--speaker <name>` to record who was speaking.

Each session costs about $0.01 per turn in Claude, so roughly $0.03–0.05 per session. A 36-session set costs about $1.50.

## Scoring

```bash
python evals/recorded/score.py
```

For each session, the score shows whether Quoth's final order was exactly right, how many wrong values it committed, how many it flagged, and how many questions it asked. It also scores a naive baseline from the same logs (the interpreter's proposals applied directly), and gives totals per condition.

Three scoring rules:

- A quantity that was never stated counts as 1.
- A field that is still pending or needs review is counted as flagged, not as committed.
- **The naive baseline skips any turn that answered one of Quoth's questions.** A naive agent asks nothing, so those turns would never have been spoken. Without this rule, naive gets credit for corrections that Quoth's questions drew out of the speaker. The exception is a reply to "X isn't available": the s10 script replaces the off-menu item anyway, so that turn is kept.

The studio works in Chrome. Safari's microphone capture did not reach Deepgram in testing.

## Results so far

### Quiet vs noisy, one speaker, 25 sessions (October 2026)

The noisy set was recorded with a café-ambience track (chatter and dishes) playing from a phone about 1–2 m from the laptop.

| | Quoth quiet | Naive quiet | Quoth noisy | Naive noisy |
|---|---|---|---|---|
| Exact orders | 13 / 13 | 12 / 13 | 11 / 12 | 5 / 12 |
| Wrong values committed | 0 | 2 | 1 | 6 |
| Questions asked | 2 | 0 | 15 | 0 |

The quiet row includes the re-recorded s10 and s12. The originals are discussed below.

**Noise multiplies naive errors and Quoth adapts.**
- Naive drops from 12 of 13 exact orders to 5 of 12. Quoth stays at 11 of 12.
- Quoth asks about one question per session in noise and almost none in quiet. The questions go where the audio is bad.

**What the 15 noisy questions were about:**

| Question type | Count | Was the heard value actually wrong? |
|---|---|---|
| "Could you say it again?" (interpreter marked the turn `unclear`) | 9 | yes, all 9: "three handovers… three collars", "I do want takeaways", "a veggie burger with milk and butter" |
| "Which one did you mean?" (ambiguous correction) | 1 | yes, the cheeseburger/shake ambiguity |
| "X isn't available" (off-menu) | 1 | yes, pepperoni pizza |
| "Was that X?" (low word confidence) | 4 | no, all 4 were heard correctly (0.46–0.76 confidence) |

So 11 of the 15 questions (73%) were about something genuinely misheard or ambiguous, compared with 42% on SLURP.

**The most interesting result: the interpreter's sense check caught the errors, not word confidence.**
- Many mishearings came back with high confidence: "handovers" for hamburgers, "collars" and "polos" for colas, "takeaways" for pickles.
- The confidence threshold could not catch these. What caught them was the interpreter noticing that the words did not fit the menu or the order, and saying so.
- All four questions triggered by low confidence were about words heard correctly.
- This agrees with SLURP, where misheard names scored 0.93–1.00 confidence. **A closed vocabulary plus "does this make sense?" is a stronger error signal than confidence thresholds.** The confidence gate is a backstop that occasionally costs an extra question.

**The remaining error (noisy s12).**
- Three garbled tries at "three hamburgers, two fries and three colas" used up the whole question budget of 3.
- The next garbled turn, "make the provolone lemonade", could not be asked about, so the colas were committed.
- **Fix:** unclear turns that the budget never covered are now listed in the final readback ("Please double-check: … which I didn't catch") and logged as `unheard` in `session_finalized`. They are no longer dropped silently.

**Caveats:**
- **Small sample:** one speaker, reading scripts they know, 25 sessions.
- **Naive here means the same cautious interpreter with no questions.** An interpreter told to guess would sometimes guess right, for example "handovers" → hamburger. So naive's misses partly reflect the prompt.
- **Not shown yet:** different voices and accents, and single competing voices such as a TV.

### Quiet room: first takes, before the fixes

| | Quoth | Naive |
|---|---|---|
| Exact orders | 12 / 13 | 11 / 13 |
| Wrong values committed | 1 | 3 |
| Questions asked | 2 | 0 |

Naive was scored here before the answer-turn rule was added.

- **Self-corrections:** every one in the scripts was handled correctly, both within a sentence and in a later turn. That covers corrected quantities, swapped items, removals, a taken-back modifier, a false start, and a corrected size.
- **Ambiguous correction (s05):** "Make that two" after "a cheeseburger and a chocolate shake". The agent asked "Which one did you mean?" and got the order right. The naive baseline gave the 2 to the cheeseburger.
- **Default quantity:** Deepgram heard the "a" in "a cheeseburger" at 0.55 confidence. A quantity left unsaid now takes the pack's default instead of producing a pointless question.

**Two failures, both now fixed:**

1. **A correction was dropped silently (s12).** Deepgram heard "Make the colas lemonades" as "Make Tacolas lemonades", with confidence 1.0.
   - Claude saw the turn didn't make sense and said so in its notes, but it had no way to ask. It returned no operations, and the colas stayed in the order.
   - The verifier and the gate only look at operations that were proposed, so a turn that produced none slipped past both. This is the kind of silent failure Quoth exists to prevent.
   - **Fix:** the interpreter can now mark a turn `unclear`, and the agent asks the user to repeat it. This uses up one of the question budget.
2. **The off-menu question made no sense (s10).** For "pepperoni pizza" the agent asked "onion rings or strawberry shake?", because it offered the two nearest menu items even when nothing was near.
   - **Fix:** when nothing on the menu is a close match, the agent asks "Sorry, pepperoni pizza isn't available. What would you like instead?"
   - The cutoff is a similarity of 0.5. Mishearings scored 0.75 or higher (e.g. "valuable shake" against vanilla shake). Real off-menu items scored 0.45 or lower (pizza, burrito, coffee).

**Checked by replay:** Claude was re-run with the updated prompt on all 13 sessions' stored transcripts (`quoth reinterpret`).
- s12 now asks the user to repeat at the misheard turn, and s10 asks for a replacement.
- No other turn was flagged as unclear, so the fix adds no extra questions on clean speech.
- s12 still scores wrong in the replay, because the recorded next line is the following script line, not a repeat. A new live recording of s12 will show the whole loop.
- The original s10 and s12 sessions, from before the fixes, are kept in `data/recorded-archive/` with their audio. To score them, run `python evals/recorded/score.py data/recorded-archive`.

