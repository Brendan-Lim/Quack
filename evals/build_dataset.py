"""Render the synthetic dataset and the prompt list used by the /collect page.

Run: python evals/build_dataset.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cases import CASES  # noqa: E402
from synthetic.render import CASUAL, MESSY, NEAT, OVERLAPPING, render  # noqa: E402

OUT = ROOT / "dataset" / "synthetic"
STYLES = {"neat": NEAT, "casual": CASUAL, "messy": MESSY, "overlap": OVERLAPPING}
# Other work on the same page, for the "crop + page context" experiments.
PAGE_CONTEXT = ["3x+1", "=7"]


def split_for(case_id: str) -> str:
    """A stable split: about a quarter of cases are held out, whatever is added later."""

    return "heldout" if int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 4 == 0 else "dev"


def main() -> None:
    (OUT / "images").mkdir(parents=True, exist_ok=True)
    (OUT / "pages").mkdir(parents=True, exist_ok=True)
    entries = []
    for case in CASES:
        seed = int(hashlib.sha256(case.id.encode()).hexdigest(), 16) % 2**32
        drawn = list(case.draw) if case.draw else [{"text": line} for line in case.lines]
        style = STYLES[case.style]
        image = render(drawn, style, seed)
        page = render(drawn, style, seed, above=PAGE_CONTEXT)
        image_path = OUT / "images" / f"{case.id}.png"
        page_path = OUT / "pages" / f"{case.id}.png"
        image.save(image_path, optimize=True)
        page.save(page_path, optimize=True)
        entries.append(
            {
                "id": case.id,
                "provenance": "synthetic",
                "split": split_for(case.id),
                "image": str(image_path.relative_to(ROOT)),
                "page_image": str(page_path.relative_to(ROOT)),
                "lines": list(case.lines),
                "expected": case.expected,
                "error_line": case.error_line,
                "operation": case.operation,
                "group": case.group,
                "tags": list(case.tags),
                "style": case.style,
            }
        )
    with (OUT / "manifest.jsonl").open("w") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    prompts = [
        {"id": case.id, "lines": list(case.lines), "expected": case.expected, "errorLine": case.error_line, "operation": case.operation, "group": case.group, "tags": list(case.tags)}
        for case in CASES
        if case.style != "overlap" and not case.draw
    ]
    web = ROOT.parent / "apps" / "web" / "lib" / "collectionPrompts.json"
    web.write_text(json.dumps(prompts, indent=1, ensure_ascii=False) + "\n")
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["split"]] = counts.get(entry["split"], 0) + 1
    print(f"{len(entries)} synthetic cases {counts}; {len(prompts)} collection prompts")


if __name__ == "__main__":
    main()
