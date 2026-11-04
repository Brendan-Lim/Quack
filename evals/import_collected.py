"""Add a bundle exported from the /collect page to the real-handwriting dataset.

    python evals/import_collected.py bundle.json --reviewed

The label is the prompt the contributor copied. Look at the images first: --reviewed states
that a person has checked each one really shows its prompt. Bundles without recorded consent
are refused. Images stay in evals/dataset/private, which is not committed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "dataset" / "private"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--reviewed", action="store_true", help="a person has checked the images against their labels")
    args = parser.parse_args()

    bundle = json.loads(args.bundle.read_text())
    if bundle.get("consent") is not True:
        print("Refused: this bundle has no recorded consent.")
        return 1
    if not args.reviewed:
        print("Refused: check the images against their labels, then pass --reviewed.")
        return 1

    contributor = str(bundle["contributor"])[:40]
    (OUT / "images").mkdir(parents=True, exist_ok=True)
    manifest = OUT / "manifest.jsonl"
    existing = set()
    if manifest.exists():
        existing = {json.loads(line)["id"] for line in manifest.read_text().splitlines() if line.strip()}

    added = 0
    with manifest.open("a") as handle:
        for sample in bundle["samples"]:
            sample_id = f"real_{contributor}_{sample['id']}"
            if sample_id in existing:
                continue
            raw = base64.b64decode(sample["image"].split(";base64,", 1)[1])
            path = OUT / "images" / f"{sample_id}.png"
            path.write_bytes(raw)
            digest = int(hashlib.sha256(sample_id.encode()).hexdigest(), 16)
            handle.write(
                json.dumps(
                    {
                        "id": sample_id,
                        "provenance": "real",
                        "contributor": contributor,
                        "consent": True,
                        "split": "heldout" if digest % 4 == 0 else "dev",
                        "image": str(path.relative_to(ROOT)),
                        "page_image": None,
                        "lines": sample["lines"],
                        "expected": sample["expected"],
                        "error_line": sample["errorLine"],
                        "operation": sample["operation"],
                        "group": sample["group"],
                        "tags": sample["tags"],
                        "style": "real",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            added += 1
    print(f"Added {added} samples from {contributor} to {manifest.relative_to(ROOT.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
