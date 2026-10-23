# Real-speech check: SLURP takeaway orders

This check runs Quoth on real people's speech, using the takeaway-order subset of [SLURP](https://github.com/pswietojanski/slurp).

- **Recordings:** each sentence was recorded twice, once on a headset and once on a distant microphone (room noise, lower audio quality).
- **Labels:** SLURP marks the food, the business, and the order type in each sentence. Quoth's final state is scored against those labels.
- **Licence:** SLURP audio is CC BY-NC 4.0 and its text is CC BY 4.0, so these results can be used for research and write-ups, not commercially.

**What this does not test:** each SLURP recording is a single command. It exercises transcription, interpretation, the verifier and the confidence gate, but not corrections or the clarification loop. Those still need recorded multi-turn sessions (spec §17).

## Reproduce

```bash
git clone --depth 1 https://github.com/pswietojanski/slurp.git data/slurp-repo
python evals/slurp/select_sample.py data/slurp-repo/dataset/slurp --n 75       # writes selection.json, filelist.txt
curl -L -C - -o data/slurp_real.tar.gz https://zenodo.org/records/4274930/files/slurp_real.tar.gz   # 3.9 GB
mkdir -p data/slurp-audio && tar -xzf data/slurp_real.tar.gz -C data/slurp-audio -T evals/slurp/filelist.txt
python evals/slurp/run_eval.py data/slurp-audio/slurp_real data/slurp-runs/v1 --max-usd 2.5
python evals/slurp/score.py data/slurp-runs/v1 [--lenient]
```

The run uses the domain pack `takeaway.json`. It has no allowed-values lists, so the vocabulary is open.

## Results (2026-10-21)

Run details:

| | |
|---|---|
| Sessions | 150 (75 sentences × 2 microphones) |
| Transcriber | Deepgram `flux-general-en` |
| Interpreter | `claude-opus-5-5`, effort low |
| Claude cost | about $1.48, roughly $0.01 per recording |

Policies compared on the same sessions:

- **naive**: the interpreter's output is applied as-is.
- **confirm-all**: every extracted field is asked about.
- **quoth**: values are committed or asked about according to the confidence gate.

**Lenient matching** skips fields SLURP left unlabelled and accepts differences in detail ("two hamburgers" counts as matching "hamburgers"):

| mic | labelled fields | correct | missed | naive wrong commits | confirm-all questions | quoth wrong commits | quoth questions (on wrong values) |
|---|---|---|---|---|---|---|---|
| headset | 114 | 90 | 10 | 14 | 104 | 9 | 12 (5) |
| distant | 114 | 74 | 21 | 19 | 93 | 13 | 14 (6) |
| all | 228 | 164 | 31 | 33 | 197 | 22 | 26 (11) |

**Strict matching** gives 91 naive wrong commits and 72 for Quoth. Most of that difference comes from labelling, not recognition: SLURP often leaves spoken entities unlabelled (for example "takeout" 12 times) and labels at a different level of detail. Use the lenient table when reading the results.

Of Quoth's 22 lenient wrong commits, about 10 are still labelling disagreements: the speaker clearly said "takeaway" but the label is "carryout", or the order type was labelled as "pepperoni pizza". The remaining 12 or so are real errors:

| Said | Heard | Field confidence |
|---|---|---|
| deliveroo | the liverwhip | 0.94 |
| dominos | the minus | 0.97 |
| dominos | the Venus | 1.00 |
| Jason's Deli | Jason's daddy | 0.98 |
| Jason's Deli | Jason Staley | 0.93 |
| Peking Garden | Carpacking Garden | 0.98 |
| takeaway | takeover | 1.00 |

### Threshold sweep (critical fields, lenient)

| threshold | wrong commits | questions | questions on wrong values |
|---|---|---|---|
| 0.50 | 21 | 7 | 5 |
| 0.70 | 18 | 16 | 8 |
| **0.80 (default)** | 15 | 26 | 11 |
| 0.90 | 12 | 35 | 14 |
| 0.95 | 10 | 45 | 16 |
| 0.99 | 5 | 67 | 21 |

## Findings

1. **The gate helps, but only partly.** Compared with naive, Quoth committed a third fewer wrong values (33 → 22). It asked 26 questions where confirm-all would have asked 197.
2. **Confidence misses misheard names.** Restaurant names were misheard with confidence of 0.93–1.00. This is the calibration limit noted in spec §20. Raising the threshold buys little: going from 0.80 to 0.90 prevents 3 more wrong commits and costs 9 more questions.
3. **For names, the allowed-values check matters more than confidence.** In a closed-menu domain like the drive-thru pack, "the liverwhip" is not on the list, so the verifier marks it pending whatever its confidence. This eval deliberately uses open vocabulary, so it shows the gate working alone, which is the worst case.
4. **The default thresholds stay.** The curve is flat around 0.80, and this data is single commands with open vocabulary. Tune on real recordings of the product's own domain before changing them.
5. **Distant microphones hurt mostly by losing content, not by committing errors.** Missed fields roughly doubled (10 → 21). Wrong commits rose only slightly (9 → 13).

## Bug found by this check

When audio came from a file, the transcriber sent its end-of-turn signal as soon as the audio ran out, so Flux dropped a word spoken in the last fraction of a second ("…sushi for ~~dinner~~"). The transcriber now sends one second of silence first. A test covers this.
