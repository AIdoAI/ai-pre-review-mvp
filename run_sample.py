#!/usr/bin/env python3
"""非交互式预审入口（单样本 / 批量）。

适合批量 / 给 Claude / CI 使用：传入一个或多个样本文件夹（或一个父目录），自动完成
（无现成 MinerU JSON 时）分层抽取 → 规则审查 → 打印结论；多样本时额外产出
batch_summary.md（含「牵头单位/申报主体」列）。交互式版本见 run_folder_review.py。

用法：
  # 单样本
  python3.13 run_sample.py --input "/路径/样本A" --form form.json
  # 多样本
  python3.13 run_sample.py --input "/路径/样本A" "/路径/样本B"
  # 一个父目录，下面每个子文件夹是一个样本
  python3.13 run_sample.py --input "/路径/所有样本" --parent

表单解析优先级（每个样本各自判定）：
  1) 样本文件夹内的 *form*.json（每样本独立填，最准）
  2) 命令行 --form 指定的 JSON（对所有样本生效）
  3) 脚本内置 DEFAULT_FORM（独立申报示例，仅兜底并告警）
--form 的 JSON 可为 form_answers 本身，或 {"mode":..,"submission_id":..,"form_answers":{...}}。
字段取值见 config/form_answers_example.json。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from review_mvp.extract_orchestrator import orchestrate_folder
from review_mvp.folder_review import safe_submission_id, scan_sample_folder
from review_mvp.pipeline import run_manifest

# 未找到任何表单时兜底使用；建议每个样本放 form.json 或用 --form。
DEFAULT_FORM: dict[str, Any] = {
    "is_joint_declaration": False,
    "joint_declaration_material_type": None,
    "project_stage": "planned",
    "applicants": [
        {"entity_id": "E01", "entity_name": "申报单位",
         "entity_type": "state_owned", "is_lead": True, "is_independent_legal_person": True}
    ],
}


def load_form(form_path: Path) -> tuple[dict[str, Any], str | None, str | None]:
    """返回 (form_answers, mode, submission_id)。后两者可能为 None。"""
    data = json.loads(form_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "form_answers" in data:
        return data["form_answers"], data.get("mode"), data.get("submission_id")
    return data, None, None


def find_form_in_folder(folder: Path) -> Path | None:
    """在样本文件夹里找 *form*.json（每样本独立表单）。"""
    candidates = sorted(
        p for p in folder.glob("*.json")
        if "form" in p.name.lower() and p.is_file()
    )
    return candidates[0] if candidates else None


def resolve_form(
    folder: Path,
    global_form: Path | None,
    excel_map: dict[str, tuple[dict[str, Any], str]] | None = None,
) -> tuple[dict[str, Any], str | None, str | None, str]:
    """按优先级解析样本表单：Excel > 样本内 *form*.json > --form > 内置默认。"""
    if excel_map and folder.name in excel_map:
        fa, mode = excel_map[folder.name]
        return fa, mode, None, "Excel 明细"
    in_folder = find_form_in_folder(folder)
    if in_folder:
        fa, mode, sid = load_form(in_folder)
        return fa, mode, sid, f"样本内 {in_folder.name}"
    if global_form:
        fa, mode, sid = load_form(global_form)
        return fa, mode, sid, f"--form {global_form.name}"
    return DEFAULT_FORM, None, None, "内置 DEFAULT_FORM（⚠️ 兜底）"


def build_submission(
    folder: Path, form_answers: dict[str, Any], mode: str, submission_id: str, output: Path,
) -> dict[str, Any]:
    """扫描或（无 JSON 时）抽取，构造一个 submission。"""
    scan = scan_sample_folder(folder)
    if scan["mineru_files"]:
        files = scan["mineru_files"]
    else:
        print(f'  [{folder.name}] 无现成 MinerU JSON，启动抽取编排层...')
        files, log = orchestrate_folder(folder, output / "_mineru_cache" / folder.name)
        for line in log:
            print(line)
        if not files:
            raise SystemExit(f"样本 {folder.name} 无可处理文件（PDF/图片）或 MinerU JSON。")
    return {
        "submission_id": submission_id, "name": folder.name, "mode": mode,
        "form_answers": form_answers, "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, nargs="+", required=True, help="一个或多个样本文件夹")
    parser.add_argument("--parent", action="store_true", help="把唯一的 --input 当父目录，其每个子文件夹为一个样本")
    parser.add_argument("--form", type=Path, help="全局表单 JSON；样本内 *form*.json 优先")
    parser.add_argument("--excel", type=Path, help="参赛明细 Excel，自动按文件夹生成各家表单（最高优先级）")
    parser.add_argument("--excel-password", help="Excel 打开密码（加密时）")
    parser.add_argument("--mode", choices=["partial", "complete"], help="审查模式；默认 partial")
    parser.add_argument("--output", type=Path, help="输出目录；默认 output_folder_review")
    args = parser.parse_args()

    # 解析样本文件夹列表
    if args.parent:
        if len(args.input) != 1 or not args.input[0].is_dir():
            raise SystemExit("--parent 模式下 --input 须为单个父目录。")
        folders = sorted(p for p in args.input[0].iterdir() if p.is_dir())
    else:
        folders = list(args.input)
    folders = [f for f in folders if f.is_dir()]
    if not folders:
        raise SystemExit("未找到任何样本文件夹。")

    output = (args.output or Path(__file__).parent / "output_folder_review").resolve()
    output.mkdir(parents=True, exist_ok=True)

    excel_map: dict[str, tuple[dict[str, Any], str]] = {}
    if args.excel:
        from review_mvp.excel_forms import build_forms_from_excel
        excel_parent = args.input[0] if args.parent else (folders[0].parent if folders else args.input[0])
        excel_map, _ = build_forms_from_excel(
            args.excel, excel_parent, args.excel_password, args.mode or "complete",
        )
        print(f"已从 Excel 读取 {len(excel_map)} 家表单：{', '.join(excel_map) or '（无匹配文件夹）'}")

    submissions = []
    for folder in folders:
        form_answers, form_mode, form_sid, source = resolve_form(folder, args.form, excel_map)
        if source.endswith("兜底）"):
            print(f'⚠️ {folder.name}: 未找到表单，使用内置 DEFAULT_FORM（独立申报示例），结果可能不准。')
        mode = args.mode or form_mode or "partial"
        submission_id = form_sid or safe_submission_id(folder.name)
        print(f'样本 {folder.name}　表单来源：{source}　模式：{mode}')
        submissions.append(build_submission(folder, form_answers, mode, submission_id, output))

    manifest = {"submissions": submissions}
    manifest_path = output / "batch_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    results = run_manifest(manifest_path, output)

    if len(results) == 1:
        res = results[0]
        print("\n" + res["conclusion"])
        print("\n" + res["per_file_report"])
        print(f'\n完整报告：{res["output_dir"]}/review_report.md')
    else:
        print("\n========== 批量结果 ==========")
        for res in results:
            rr = res["rule_results"]
            print(f'- {res["name"]}：{rr["overall_status"]}'
                  f'（通过{rr["counts"]["pass"]} 不通过{rr["counts"]["fail"]}'
                  f' 待人工{rr["counts"]["manual_review"]} 无法判断{rr["counts"]["not_assessable"]}）')
        print("\n========== batch_summary.md ==========")
        print((output / "batch_summary.md").read_text(encoding="utf-8"))
        print(f'批量摘要：{output}/batch_summary.md')
        print(f'各样本报告：{output}/<样本名>/review_report.md')


if __name__ == "__main__":
    main()
