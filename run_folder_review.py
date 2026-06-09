#!/usr/bin/env python3
"""Interactively review all MinerU JSON files in one sample folder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from review_mvp.folder_review import build_folder_manifest, collect_form_answers, scan_sample_folder
from review_mvp.pipeline import run_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="One folder containing one test sample")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output folder; defaults to local_review/output_folder_review",
    )
    args = parser.parse_args()

    scan_result = scan_sample_folder(args.input)
    print(f'样本名称：{scan_result["sample_name"]}')
    print(f'发现MinerU JSON：{len(scan_result["mineru_files"])}个')
    print(f'其他文件（当前不会解析）：{len(scan_result["ignored_files"])}个')
    if not scan_result["mineru_files"]:
        raise SystemExit("未发现可审查的MinerU JSON。请先用MinerU解析PDF，再运行本入口。")

    mode, form_answers = collect_form_answers()
    manifest = build_folder_manifest(scan_result, mode, form_answers)
    output = (args.output or Path(__file__).parent / "output_folder_review").resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "generated_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    result = run_manifest(manifest_path, output)[0]
    print("")
    print(f'审查完成：{result["rule_results"]["overall_status"]}')
    print(f'识别材料数：{len(result["materials"])}')
    print(f'报告位置：{result["output_dir"]}/review_report.md')
    print(f'本次手动选项记录：{manifest_path}')


if __name__ == "__main__":
    main()
