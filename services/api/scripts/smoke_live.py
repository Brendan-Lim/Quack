"""One real call to Meta's API through the real router. Spends a small amount of credit.

Run: python scripts/smoke_live.py path/to/image.png
Prints what the model read and the verifier's verdict. Never prints the key.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quack.config import get_settings  # noqa: E402
from quack.llm.ledger import Ledger  # noqa: E402
from quack.llm.router import ModelError, ModelRouter  # noqa: E402
from quack.services.handwriting import read_handwriting, sanitise_image, to_line_inputs  # noqa: E402
from quack.services.math_verifier import verify_lines  # noqa: E402


async def main(image_path: Path) -> int:
    settings = get_settings()
    ledger = Ledger(
        settings.ledger_path, settings.budget_project_usd, settings.budget_session_usd, settings.budget_request_usd
    )
    router = ModelRouter(settings, ledger)
    print(
        f"key configured: {router.configured}; spend so far: ${ledger.project_spend:.4f}; "
        f"ceiling: ${settings.budget_project_usd}"
    )
    raw = base64.b64encode(image_path.read_bytes()).decode()
    image, _ = sanitise_image(f"data:image/png;base64,{raw}")
    try:
        result = await read_handwriting(router, image, run_id="smoke")
    except ModelError as exc:
        print(f"FAILED: category={exc.category} status={exc.status} ({exc})")
        return 1
    for line in result.value.lines:
        print(json.dumps(line.model_dump(exclude={"raw_text", "y_top", "y_bottom"}), ensure_ascii=False))
    report = verify_lines(to_line_inputs(result.value))
    print("verifier:", report.status, report.first_invalid.error_type if report.first_invalid else "")
    record = result.record
    print(
        f"{record.model} effort={record.reasoning_effort} {record.elapsed_ms:.0f}ms "
        f"input={record.input_tokens} cached={record.cached_input_tokens} output={record.output_tokens} "
        f"reasoning={record.reasoning_tokens} cost=${record.estimated_usd:.5f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
