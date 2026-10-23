"""Pick the SLURP takeaway_order sample: sentences with at least one scored entity and both a
headset (close-talk) and a distant-microphone recording. Writes selection.json and filelist.txt.

Usage: python evals/slurp/select_sample.py data/slurp-repo/dataset/slurp [--n 75]
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).parent
FIELDS = ("food_type", "business_name", "order_type")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--n", type=int, default=75)
    args = parser.parse_args()

    rows = []
    for split in ("test", "devel", "train"):
        for line in (args.dataset_dir / f"{split}.jsonl").read_text().splitlines():
            r = json.loads(line)
            if r["intent"] != "takeaway_order":
                continue
            gold = {f: [] for f in FIELDS}
            for e in r["entities"]:
                if e["type"] in gold:
                    gold[e["type"]].append(" ".join(r["tokens"][i]["surface"] for i in e["span"]))
            headset = [x["file"] for x in r["recordings"] if x["file"].endswith("-headset.flac")]
            distant = [x["file"] for x in r["recordings"] if not x["file"].endswith("-headset.flac")]
            if any(gold.values()) and headset and distant:
                rows.append({"slurp_id": r["slurp_id"], "split": split, "sentence": r["sentence"], "gold": gold,
                             "recordings": {"headset": headset[0], "distant": distant[0]}})

    rows.sort(key=lambda r: r["slurp_id"])
    sample = rows[: args.n]
    (HERE / "selection.json").write_text(json.dumps(sample, indent=1) + "\n")
    files = sorted(f"slurp_real/{f}" for r in sample for f in r["recordings"].values())
    (HERE / "filelist.txt").write_text("\n".join(files) + "\n")
    print(f"{len(rows)} eligible sentences, selected {len(sample)} ({len(files)} recordings)")


if __name__ == "__main__":
    main()
