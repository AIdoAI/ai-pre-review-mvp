#!/usr/bin/env python3
"""Interactively review all MinerU JSON files in one sample folder."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from review_mvp.extract_orchestrator import orchestrate_folder
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

    if os.name == "nt":
        print("ℹ️ Windows 提示：本入口为交互式（箭头菜单在部分终端会退化为输入序号）。"
              "更省心可改用非交互入口：python run_sample.py --input <文件夹> --form form.json"
              "（支持批量 --parent，详见 DEPLOYMENT.md / 同事上手教程.md）。\n")

    scan_result = scan_sample_folder(args.input)
    print(f'样本名称：{scan_result["sample_name"]}')
    print(f'发现MinerU JSON：{len(scan_result["mineru_files"])}个')
    print(f'其他文件（当前不会解析）：{len(scan_result["ignored_files"])}个')

    output = (args.output or Path(__file__).parent / "output_folder_review").resolve()
    output.mkdir(parents=True, exist_ok=True)

    if not scan_result["mineru_files"]:
        # 无现成 MinerU JSON：启动抽取编排层（本地文字层→选择性OCR→人工）
        print("未发现现成 MinerU JSON，启动抽取编排层（本地文字层 → 选择性 OCR → 人工）...")
        cache_dir = output / "_mineru_cache" / scan_result["sample_name"]
        entries, log = orchestrate_folder(args.input, cache_dir)
        for line in log:
            print(line)
        if not entries:
            raise SystemExit("未发现可处理的原始文件（PDF/图片），也无 MinerU JSON。")
        scan_result["mineru_files"] = entries

    mode, form_answers = collect_form_answers()
    manifest = build_folder_manifest(scan_result, mode, form_answers)
    manifest_path = output / "generated_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    result = run_manifest(manifest_path, output)[0]
    print("")
    print(result["conclusion"])
    print("")
    print(result["per_file_report"])
    print("")
    print(f'识别材料数：{len(result["materials"])}')
    print(f'完整报告：{result["output_dir"]}/review_report.md')
    print(f'结论文件：{result["output_dir"]}/conclusion.md')
    print(f'逐文件报告：{result["output_dir"]}/per_file_report.md')
    print(f'本次手动选项记录：{manifest_path}')


if __name__ == "__main__":
    main()
