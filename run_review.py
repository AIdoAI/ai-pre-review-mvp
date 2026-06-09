#!/usr/bin/env python3
"""Run the local AI pre-review MVP against a manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from review_mvp.pipeline import run_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    items = run_manifest(args.manifest.resolve(), args.output.resolve())
    for item in items:
        print(
            f'{item["submission_id"]}: {item["rule_results"]["overall_status"]} '
            f'({len(item["materials"])} materials) -> {item["output_dir"]}'
        )


if __name__ == "__main__":
    main()
