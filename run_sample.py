#!/usr/bin/env python3
"""非交互式单样本预审入口。

适合批量 / 给 Claude / CI 使用：传入样本文件夹与表单选项，自动完成
（无现成 MinerU JSON 时）分层抽取 → 规则审查 → 打印统一结论 + 逐文件报告。
交互式版本见 run_folder_review.py。

用法：
  python3.13 run_sample.py --input "/绝对路径/样本文件夹"            # 用内置 DEFAULT_FORM
  python3.13 run_sample.py --input "<样本>" --form form.json         # 表单从 JSON 读
  python3.13 run_sample.py --input "<样本>" --mode complete          # 完整材料包

--form 指向的 JSON 可以是 form_answers 对象本身，也可以是
{"mode":..,"submission_id":..,"form_answers":{...}} 包裹形式。
表单字段取值见 config/form_answers_example.json。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from review_mvp.extract_orchestrator import orchestrate_folder
from review_mvp.folder_review import safe_submission_id, scan_sample_folder
from review_mvp.pipeline import run_manifest

# 未传 --form 时使用；按实际样本修改，或改用 --form 传入。
DEFAULT_FORM: dict[str, Any] = {
    "is_joint_declaration": False,  # 联合申报 True / 独立 False
    # 联合时三选一: stamped_project_cooperation_agreement /
    #   stamped_joint_declaration_agreement / stamped_lead_declaration
    "joint_declaration_material_type": None,
    "project_stage": "planned",  # building 正在建设 / planned 计划实施 / other
    "applicants": [
        {
            "entity_id": "E01",
            "entity_name": "申报单位",
            "entity_type": "state_owned",  # state_owned / private / other
            "is_lead": True,
            "is_independent_legal_person": True,
        }
    ],
}


def load_form(form_path: Path | None) -> tuple[dict[str, Any], str | None, str | None]:
    """返回 (form_answers, mode, submission_id)。后两者可能为 None。"""
    if not form_path:
        return DEFAULT_FORM, None, None
    data = json.loads(form_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "form_answers" in data:
        return data["form_answers"], data.get("mode"), data.get("submission_id")
    return data, None, None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="样本文件夹（原始文件或 MinerU JSON）")
    parser.add_argument("--form", type=Path, help="表单选项 JSON；省略则用内置 DEFAULT_FORM")
    parser.add_argument("--mode", choices=["partial", "complete"], help="审查模式；默认 partial（局部样本不判缺失）")
    parser.add_argument("--output", type=Path, help="输出目录；默认 output_folder_review")
    parser.add_argument("--submission-id", help="样本编号；默认取文件夹名")
    args = parser.parse_args()

    if not args.input.is_dir():
        raise SystemExit(f"样本文件夹不存在：{args.input}")

    output = (args.output or Path(__file__).parent / "output_folder_review").resolve()
    output.mkdir(parents=True, exist_ok=True)

    form_answers, form_mode, form_sid = load_form(args.form)
    if not args.form:
        print("⚠️ 未传 --form，使用内置 DEFAULT_FORM（独立申报示例）。请用 --form 传入真实表单，或编辑本文件顶部 DEFAULT_FORM。")
    mode = args.mode or form_mode or "partial"
    submission_id = args.submission_id or form_sid or safe_submission_id(args.input.name)

    scan = scan_sample_folder(args.input)
    print(f'样本：{args.input.name}　发现 MinerU JSON：{len(scan["mineru_files"])} 个')
    if scan["mineru_files"]:
        files = scan["mineru_files"]
    else:
        print("无现成 MinerU JSON，启动抽取编排层（本地文字层 → OCR重试/分块 → 人工）...")
        cache_dir = output / "_mineru_cache" / args.input.name
        files, log = orchestrate_folder(args.input, cache_dir)
        print("\n".join(log))
        if not files:
            raise SystemExit("未发现可处理的原始文件（PDF/图片），也无 MinerU JSON。")

    manifest = {
        "submissions": [
            {
                "submission_id": submission_id,
                "name": args.input.name,
                "mode": mode,
                "form_answers": form_answers,
                "files": files,
            }
        ]
    }
    manifest_path = output / f"{submission_id}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    result = run_manifest(manifest_path, output)[0]
    print("\n" + result["conclusion"])
    print("\n" + result["per_file_report"])
    print(f'\n综合状态：{result["rule_results"]["overall_status"]}　识别材料数：{len(result["materials"])}')
    print(f'完整报告：{result["output_dir"]}/review_report.md')
    print(f'结论文件：{result["output_dir"]}/conclusion.md')
    print(f'逐文件报告：{result["output_dir"]}/per_file_report.md')


if __name__ == "__main__":
    main()
