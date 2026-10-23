"""Build the article's charts and images into article/images/.

Charts are computed from the recorded sessions in data/recorded/ (and the published SLURP
numbers in evals/slurp/README.md). Diagrams are HTML pages in article/src/, rendered to PNG
with headless Chrome.

Usage: pip install -e ".[article]" && python article/make_figures.py
"""

import json
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

ROOT = Path(__file__).parents[1]
OUT = Path(__file__).parent / "images"
SRC = Path(__file__).parent / "src"
sys.path.insert(0, str(ROOT / "evals" / "recorded"))
import score  # noqa: E402

from quoth.log import read_events  # noqa: E402
from quoth.reducer import reduce  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Reference palette, light mode (validated: blue/orange/aqua pass all-pairs CVD checks).
SURFACE = "#ffffff"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e6e5e0"
QUOTH = "#2a78d6"
NAIVE = "#eb6834"
CONFIRM_ALL = "#1baf7a"

# Published SLURP results (evals/slurp/README.md, lenient matching, 150 recordings).
SLURP = {"naive": (0, 33), "quoth": (26, 22), "confirm-all": (197, 0)}
# Misheard words that reached the interpreter, with Deepgram word confidence.
SLURP_MISHEARD = [("the liverwhip", 0.94), ("the minus", 0.97), ("the Venus", 1.00), ("Jason's daddy", 0.98),
                  ("Jason Staley", 0.93), ("Carpacking Garden", 0.98), ("takeover", 1.00)]
RECORDED_MISHEARD = [("full roll", 0.60), ("roll", 1.00), ("scratched", 0.82), ("other ones", 0.54), ("pick those", 0.80),
                     ("those", 0.96), ("takeaways", 0.85), ("milk", 0.27), ("butter", 1.00), ("handovers", 1.00),
                     ("collars", 1.00), ("polos", 1.00), ("whole ones", 0.18), ("provolone", 0.37), ("Tacolas", 0.93)]
THRESHOLD = 0.80

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 11,
    "text.color": INK,
    "axes.labelcolor": INK_2,
    "axes.edgecolor": GRID,
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})


def style(ax, grid_axis="y"):
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0)
    ax.grid(axis=grid_axis, color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def title(fig, text, sub):
    fig.text(0.04, 0.95, text, fontsize=15, fontweight="bold", va="top")
    fig.text(0.04, 0.885, sub, fontsize=11, color=INK_2, va="top")


def save(fig, name):
    fig.savefig(OUT / name, dpi=200)
    plt.close(fig)
    print("wrote", OUT / name)


def rounded_barh(ax, y, width, height, color):
    """Horizontal bar, square at the baseline with a rounded data end (4 px)."""
    fig_w, fig_h = ax.figure.get_size_inches() * ax.figure.dpi
    bbox = ax.get_position()
    x_per_px = (ax.get_xlim()[1] - ax.get_xlim()[0]) / (bbox.width * fig_w)
    y_per_px = (ax.get_ylim()[1] - ax.get_ylim()[0]) / (bbox.height * fig_h)
    r = 4 * x_per_px
    ax.add_patch(FancyBboxPatch((0, y - height / 2), width, height, boxstyle=f"round,pad=0,rounding_size={r}",
                                mutation_aspect=y_per_px / x_per_px, facecolor=color, edgecolor="none"))
    ax.add_patch(Rectangle((0, y - height / 2), min(width, 2 * r), height, facecolor=color, edgecolor="none"))


# ---------- data from the recorded sessions ----------

def recorded():
    rows, totals = score.summarize(ROOT / "data" / "recorded")
    noisy = [ROOT / "data" / "recorded" / name for name, meta, _, _ in rows if meta["condition"] == "noisy"]
    # Confirm-all asks once per field the agent filled in.
    confirm_all = sum(len(line) for d in noisy for line in score.quoth_lines(read_events(d)))
    return totals, confirm_all


# ---------- charts ----------

def fig_exact_orders(totals):
    fig, ax = plt.subplots(figsize=(7, 4.4))
    fig.subplots_adjust(left=0.1, right=0.78, top=0.76, bottom=0.12)
    title(fig, "Noise breaks the naive agent. Quoth holds.", "Orders that came out exactly right, same speaker and scripts")
    xs = [0, 1]
    for key, color, label in (("quoth", QUOTH, "Quoth"), ("naive", NAIVE, "Naive")):
        counts = [(totals[c][key]["exact"], totals[c][key]["sessions"]) for c in ("quiet", "noisy")]
        ys = [100 * e / n for e, n in counts]
        ax.plot(xs, ys, color=color, linewidth=2, solid_capstyle="round", label=label, zorder=2)
        ax.scatter(xs, ys, s=70, color=color, edgecolor=SURFACE, linewidth=2, zorder=3)
        for x, y, (e, n) in zip(xs, ys, counts):
            ha, dx = ("right", -14) if x == 0 else ("left", 14)
            text = f"{e}/{n}" if x == 0 else f"{label}  {e}/{n}"
            ax.annotate(text, (x, y), xytext=(dx, 0), textcoords="offset points", ha=ha, va="center",
                        fontsize=11, color=INK, fontweight="bold" if x == 1 else "normal")
    ax.set_xlim(-0.35, 1.15)
    ax.set_ylim(0, 105)
    ax.set_xticks(xs, ["Quiet room\n(13 sessions)", "Café noise\n(12 sessions)"])
    ax.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    style(ax)
    ax.legend(loc="lower left", frameon=False, fontsize=10)
    save(fig, "02-exact-orders-quiet-vs-noisy.png")


def fig_tradeoff(totals, confirm_all_noisy):
    noisy = {"naive": (0, totals["noisy"]["naive"]["wrong_commits"]),
             "quoth": (totals["noisy"]["quoth"]["questions"], totals["noisy"]["quoth"]["wrong_commits"]),
             "confirm-all": (confirm_all_noisy, 0)}
    fig, axes = plt.subplots(1, 2, figsize=(7.6, 4.4))
    fig.subplots_adjust(left=0.09, right=0.97, top=0.74, bottom=0.2, wspace=0.28)
    title(fig, "Most of the safety for a fraction of the questions",
          "Wrong values committed vs questions asked. Lower-left is better.")
    panels = [(axes[0], SLURP, "SLURP: 150 real one-shot orders"), (axes[1], noisy, "Recorded orders in café noise (12)")]
    colors = {"naive": NAIVE, "quoth": QUOTH, "confirm-all": CONFIRM_ALL}
    names = {"naive": "Naive", "quoth": "Quoth", "confirm-all": "Confirm everything*"}
    for ax, data, label in panels:
        ax.set_title(label, fontsize=10.5, color=INK_2, loc="left", pad=10)
        xmax = max(x for x, _ in data.values()) * 1.15
        ymax = max(y for _, y in data.values()) * 1.3
        # (dx, dy, ha, va) per point, so labels stay clear of each other in both panels.
        place = {"naive": (10, 8, "left", "bottom"), "quoth": (10, 8, "left", "bottom"),
                 "confirm-all": (-4, 14, "right", "bottom")}
        if data is not SLURP:  # Quoth sits close to confirm-all here; put its label on the other side
            place["quoth"] = (-10, 46, "center", "bottom")
        for key, (x, y) in data.items():
            ax.scatter([x], [y], s=90, color=colors[key], edgecolor=SURFACE, linewidth=2, zorder=3)
            dx, dy, ha, va = place[key]
            leader = {"arrowstyle": "-", "color": MUTED, "linewidth": 0.8, "shrinkB": 6} if abs(dy) > 20 else None
            ax.annotate(f"{names[key]}\n{y} wrong · {x} questions", (x, y), xytext=(dx, dy), textcoords="offset points",
                        ha=ha, va=va, fontsize=9, color=INK, arrowprops=leader)
        ax.set_xlim(-xmax * 0.05, xmax)
        ax.set_ylim(-ymax * 0.06, ymax)
        ax.set_xlabel("questions asked")
        style(ax, grid_axis="both")
    axes[0].set_ylabel("wrong values committed")
    fig.text(0.04, 0.02, "* Confirm-everything assumes every wrong value is caught when confirmed.", fontsize=8.5, color=MUTED)
    save(fig, "03-tradeoff-errors-vs-questions.png")


def fig_questions():
    # The 15 questions asked in the noisy set, by template, and whether they targeted a real problem.
    rows = [("“Could you say it again?”", 9, True), ("“Which one did you mean?”", 1, True),
            ("“X isn't available…”", 1, True), ("“Was that X?” (low confidence)", 4, False)]
    fig, ax = plt.subplots(figsize=(7, 3.9))
    fig.subplots_adjust(left=0.36, right=0.93, top=0.7, bottom=0.06)
    title(fig, "11 of 15 questions caught a real problem",
          "Every question Quoth asked in café noise, by template")
    ax.set_xlim(0, 10.5)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    for i, (label, n, real) in enumerate(reversed(rows)):
        rounded_barh(ax, i, n, 0.5, QUOTH if real else NAIVE)
        ax.annotate(str(n), (n, i), xytext=(6, 0), textcoords="offset points", va="center", fontsize=11, color=INK)
    ax.set_yticks(range(len(rows)), [r[0] for r in reversed(rows)], fontsize=10.5, color=INK)
    ax.set_xticks([])
    style(ax, grid_axis="x")
    ax.spines["bottom"].set_visible(False)
    ax.scatter([], [], marker="s", s=60, color=QUOTH, label="about something misheard or ambiguous")
    ax.scatter([], [], marker="s", s=60, color=NAIVE, label="about something heard correctly")
    fig.legend(loc="upper left", bbox_to_anchor=(0.035, 0.84), ncol=2, frameon=False, fontsize=9.5, handletextpad=0.3)
    save(fig, "04-what-the-questions-were-about.png")


def fig_confidence():
    flagged = [("cola", 0.63), ("iced tea", 0.53), ("chicken sandwich", 0.46), ("iced tea", 0.76)]
    groups = [("Misheard\n(SLURP)", SLURP_MISHEARD, NAIVE), ("Misheard\n(recorded)", RECORDED_MISHEARD, NAIVE),
              ("Heard correctly,\nbut flagged", flagged, QUOTH)]
    # word -> label offset in points
    show = {"the Venus": (0, 10), "handovers": (0, 10), "takeaways": (-6, -16), "milk": (0, 10), "whole ones": (0, 10),
            "provolone": (0, -16), "chicken sandwich": (0, 10), "cola": (0, 10)}
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    fig.subplots_adjust(left=0.2, right=0.96, top=0.74, bottom=0.2)
    title(fig, "Word confidence doesn't tell you what's wrong",
          "Deepgram word confidence. Most mishearings scored above the 0.80 bar.")
    ax.axvspan(THRESHOLD, 1.03, color="#f3f2ee", zorder=0)
    ax.axvline(THRESHOLD, color=INK_2, linewidth=1, zorder=1)
    ax.text(THRESHOLD + 0.012, 2.62, "committed\nwithout asking", fontsize=8.5, color=INK_2, va="center")
    ax.text(THRESHOLD - 0.012, 2.62, "asked about", fontsize=8.5, color=INK_2, va="center", ha="right")
    for row, (_, points, color) in enumerate(reversed(groups)):
        n = len(points)
        for j, (word, conf) in enumerate(points):
            jitter = ((j % 5) - 2) * 0.09 if n > 4 else 0
            ax.scatter(conf, row + jitter, s=70, color=color, edgecolor=SURFACE, linewidth=2, zorder=3)
            if word in show:
                ax.annotate(word, (conf, row + jitter), xytext=show[word], textcoords="offset points",
                            ha="right" if show[word][0] < 0 else "center", fontsize=8.5, color=INK)
    ax.set_yticks(range(len(groups)), [g[0] for g in reversed(groups)], fontsize=10, color=INK)
    ax.set_xlim(0.1, 1.03)
    ax.set_ylim(-0.5, 2.85)
    ax.set_xlabel("word confidence")
    style(ax, grid_axis="x")
    above = sum(c >= THRESHOLD for _, c in SLURP_MISHEARD + RECORDED_MISHEARD)
    total = len(SLURP_MISHEARD) + len(RECORDED_MISHEARD)
    fig.text(0.04, 0.02, f"{above} of {total} misheard words scored ≥ {THRESHOLD:.2f}; all 4 correct words that were flagged scored below it.",
             fontsize=8.5, color=MUTED)
    save(fig, "05-confidence-vs-correctness.png")


# ---------- HTML-rendered images ----------

def studio_page():
    """A copy of the studio page that replays the noisy s05 session instead of using the mic."""
    session = next((ROOT / "data" / "recorded").glob("s05-*__noisy__*"))
    events = read_events(session)
    messages, seen = [], []
    for i, e in enumerate(events):
        seen.append(e)
        if e["type"] != "utterance_finalized":
            continue
        end = next((k for k in range(i + 1, len(events)) if events[k]["type"] == "utterance_finalized"), len(events))
        turn = events[i:end]
        ask = next((x["question"] for x in turn if x["type"] == "clarification_asked"), None)
        final = next((x["readback"] for x in turn if x["type"] == "session_finalized"), None)
        messages.append({"transcript": e["transcript"], "reply": {"question": ask, "readback": final, "finalized": bool(final)},
                         "state": reduce(events[:end])})
    expected = score.load_scenarios()["s05-ambiguous-correction"]["expected"]
    q, n = score.score_events(expected, events)
    scenarios = list(score.load_scenarios().values())
    rows, _ = score.summarize(ROOT / "data" / "recorded")
    results = {"sessions": [{"name": nm, **meta, "quoth": qq, "naive": nn} for nm, meta, qq, nn in rows], "totals": {}}
    stub = f"""
<script>
const FAKE = {{"/api/scenarios": {json.dumps({"conditions": ["quiet", "noisy", "car", "outdoor"], "scenarios": scenarios})},
              "/api/results": {json.dumps(results)}}};
window.fetch = async (url) => ({{ json: async () => FAKE[url] }});
try {{ localStorage.setItem("condition", "noisy"); }} catch {{}}
</script>"""
    replay = f"""
init().then(() => {{
  select(scenarios.findIndex((s) => s.id === "s05-ambiguous-correction"));
  busy = true;
  for (const m of {json.dumps(messages)}) onMessage(m);
  showResult({{ session_id: "demo", score: {json.dumps({"quoth": q, "naive": n})} }});
}});
</script>"""
    html = (ROOT / "evals" / "recorded" / "studio.html").read_text()
    html = html.replace("<script>", stub + "\n<script>", 1).replace("init();\n</script>", replay)
    (SRC / "studio-replay.html").write_text(html)
    return SRC / "studio-replay.html"


def screenshot(page: Path, name: str, width: int, height: int) -> None:
    if not Path(CHROME).exists():
        print("skipped", name, "(Chrome not found)")
        return
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                    f"--window-size={width},{height}", "--virtual-time-budget=4000", f"--screenshot={OUT / name}",
                    page.as_uri()], check=True, capture_output=True)
    print("wrote", OUT / name)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    totals, confirm_all = recorded()
    fig_exact_orders(totals)
    fig_tradeoff(totals, confirm_all)
    fig_questions()
    fig_confidence()
    screenshot(SRC / "cover.html", "00-cover.png", 1400, 700)
    screenshot(SRC / "pipeline.html", "01-pipeline.png", 1200, 664)
    screenshot(SRC / "conversation.html", "06-noisy-conversation.png", 1100, 900)
    screenshot(studio_page(), "07-recording-studio.png", 1440, 900)


if __name__ == "__main__":
    main()
