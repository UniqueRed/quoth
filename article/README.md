# Medium article: posting guide

- `article.md` is the article text, the source to edit.
- `medium-paste.html` is the version to copy into Medium.
- `images/` holds every figure in the order it appears.
- Rebuild the figures from the recorded data: `pip install -e ".[article]" && python article/make_figures.py`.

## Images, in order

| File | Where | Caption to paste |
|---|---|---|
| `00-cover.png` | Top (the Medium cover image) | none |
| `01-pipeline.png` | "How Quoth works" | The LLM proposes. Deterministic code decides. Every step is logged. |
| `07-recording-studio.png` | "Test 2", after the scenario list | A small local web app runs each scripted session live and scores it the moment it ends. |
| `02-exact-orders-quiet-vs-noisy.png` | "Test 2", the headline result | Same speaker, same scripts. Noise barely touches Quoth and wrecks the naive agent. |
| `03-tradeoff-errors-vs-questions.png` | End of "Test 2" | Lower-left is better. In noise, Quoth gets close to confirm-everything's safety with fewer than half its questions. |
| `04-what-the-questions-were-about.png` | "The surprise" | none |
| `06-noisy-conversation.png` | "The surprise" | From a real noisy session. The recognizer was certain about "handovers", "collars" and "polos". |
| `05-confidence-vs-correctness.png` | "The surprise" | Across both tests, 17 of 22 misheard words scored at or above the 0.80 bar. |

The images are rendered at 2x for sharp display. They are each under 600 KB, well within Medium's upload limit.

## Pasting into Medium

Medium does not read Markdown; pasting `article.md` leaves raw `**` and `##` in the text. Use the paste-ready page instead:

1. Open `article/medium-paste.html` in Chrome. Rebuild it after editing the article with `python article/build_paste.py`.
2. In a new Medium story, copy the **title** and **subtitle** from the page into Medium's title and subtitle lines.
3. Click **Copy article body**, click on the empty line under the subtitle in Medium, and paste. Headings, bold, italics, lists and the code block come through as Medium formatting.
4. Images: each one is a yellow "IMAGE: upload …" line. Click that line, delete its text, press `+`, choose the image icon, and upload the named file from `article/images/`. Paste the caption from the same line into the image's caption field. Upload `00-cover.png` first, just above the first paragraph; Medium uses the first image as the story preview.
5. Replace `[link to repository]` with your repo URL.

## Things to decide before posting

- **Byline and voice:** the article is written in the first person, as you.
- **Tags:** for example *Voice AI*, *LLM*, *Speech Recognition*, *Software Engineering*, *Machine Learning*.
- **SLURP:** it is cited as a public dataset. Its audio licence (CC BY-NC 4.0) allows non-commercial write-ups; keep the credit line at the end.
