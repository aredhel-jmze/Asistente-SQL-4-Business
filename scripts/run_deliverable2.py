#!/usr/bin/env python3
"""Run one Deliverable 2 question with the structured assistant."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sql4business.model import HuggingFaceGenerator  # noqa: E402
from sql4business.pipeline import BusinessAssistant, save_trace  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--model", default="Qwen/Qwen2.5-Coder-3B-Instruct")
    parser.add_argument("--db", default=str(ROOT / "data" / "business.db"))
    parser.add_argument("--trace", default=str(ROOT / "results" / "deliverable2_trace.json"))
    parser.add_argument("--max-retries", type=int, default=2)
    args = parser.parse_args()

    generator = HuggingFaceGenerator(args.model)
    assistant = BusinessAssistant(args.db, generator, max_retries=args.max_retries)
    result = assistant.answer(args.question)
    save_trace(result, args.trace)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
