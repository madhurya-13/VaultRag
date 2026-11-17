"""Run the deterministic offline RAG benchmark.

Usage (Windows PowerShell, repository root):
    .\\.venv\\Scripts\\python.exe tests\\evaluation\\run_evaluation.py

This script needs no database, model download or API key.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.tests.evaluation.rag_eval_harness import (  # noqa: E402
    benchmark,
    render_markdown,
)


def main() -> int:
    results = benchmark()
    print(render_markdown(results))
    print()
    print("Per-category recall@5 (hybrid + reranking):")
    for category in sorted(results["dataset"]["categories"]):
        rows = [
            row
            for row in results["per_query"]["hybrid_reranked"]
            if row["category"] == category
        ]
        average = sum(row["recall_at_k"] for row in rows) / len(rows)
        print(f"  {category}: {average:.3f} ({len(rows)} queries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
