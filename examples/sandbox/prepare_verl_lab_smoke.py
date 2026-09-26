#!/usr/bin/env python3
"""Prepare a tool-use smoke dataset from verl-lab's existing SWE-Bench POC."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import pandas as pd


SYSTEM_PROMPT = (
    "You are a coding agent in the SWE-Bench repository at the given base commit. "
    "First call read_source with path network/check.py, start_line 434, end_line 440. "
    "Then call edit_and_test with path network/check.py, old exactly "
    '"self.logger.error", and new exactly "self.log.error". '
    "The tool applies the edit to a clean base and runs the Docker regression grader. "
    "Copy the patch returned by the tool as your final answer, without a code fence."
)
TASK_ID = "DataDog__integrations-core-698"


def prepare(source: Path, output: Path, *, repeat: int) -> int:
    if repeat < 1:
        raise ValueError("repeat must be positive")
    frame = pd.read_parquet(source)
    if frame.empty or "prompt" not in frame:
        raise ValueError("input must contain SWE-Bench prompts")
    rows = []
    for _ in range(repeat):
        for original in frame.to_dict("records"):
            row = deepcopy(original)
            if not isinstance(row.get("extra_info"), dict) or row["extra_info"].get("task_id") != TASK_ID:
                raise ValueError(f"smoke prompt only supports {TASK_ID}")
            messages = row["prompt"]
            if len(messages) == 0 or messages[0].get("role") != "system":
                raise ValueError("each prompt must start with a system message")
            messages[0]["content"] = SYSTEM_PROMPT
            if isinstance(row.get("extra_info"), dict):
                row["extra_info"]["index"] = len(rows)
            rows.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(output, index=False)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    print(f"Wrote {prepare(args.input, args.output, repeat=args.repeat)} smoke samples to {args.output}")


if __name__ == "__main__":
    main()
