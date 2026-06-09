#!/usr/bin/env python3
"""Generate a filename-only qualification profile for expert review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from review_mvp.qualification_profile import (
    load_qualification_rules,
    render_qualification_batch,
    render_qualification_profile,
    scan_qualification_batch,
    scan_qualification_folder,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Folder containing qualification files")
    parser.add_argument("--output", type=Path, required=True, help="Output folder")
    parser.add_argument("--entity-name", help="Entity name shown in the expert summary")
    parser.add_argument(
        "--group-by-first-level",
        action="store_true",
        help="Treat each immediate child folder as a separate entity",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(__file__).parent / "config" / "qualification_profile_rules.json",
    )
    args = parser.parse_args()

    rules = load_qualification_rules(args.rules.resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.group_by_first_level:
        batch = scan_qualification_batch(args.input, rules)
        (output / "qualification_profiles.json").write_text(
            json.dumps(batch, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output / "qualification_profiles.md").write_text(
            render_qualification_batch(batch),
            encoding="utf-8",
        )
        print(f'{batch["entity_count"]} entity profiles -> {output}')
        return

    profile = scan_qualification_folder(args.input, rules, args.entity_name)
    (output / "qualification_profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "qualification_profile.md").write_text(
        render_qualification_profile(profile),
        encoding="utf-8",
    )

    stats = profile["stats"]
    print(
        f'{profile["entity_name"]}: {stats["raw_file_count"]} files, '
        f'{stats["deduplicated_item_count"]} deduplicated candidates, '
        f'{stats["ai_related_candidate_count"]} AI-related -> {output}'
    )


if __name__ == "__main__":
    main()
