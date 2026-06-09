"""Build a review manifest from one sample folder and interactive form answers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable


InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


ENTITY_TYPE_OPTIONS = {
    "1": "state_owned",
    "2": "private",
    "3": "research_institute",
    "4": "government_public",
    "5": "other",
}

JOINT_MATERIAL_OPTIONS = {
    "1": "stamped_project_cooperation_agreement",
    "2": "stamped_joint_declaration_agreement",
    "3": "stamped_lead_declaration",
}


def infer_original_file(json_path: Path) -> str:
    stem = json_path.stem
    if stem.startswith("MinerU_"):
        stem = stem[len("MinerU_"):]
    stem = re.sub(r"__\d{12,}$", "", stem)
    return f"{stem}.pdf"


def is_mineru_json(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("pdf_info"), list)


def scan_sample_folder(folder: Path) -> dict[str, Any]:
    folder = folder.resolve()
    if not folder.is_dir():
        raise ValueError(f"Sample folder does not exist: {folder}")

    mineru_files = []
    ignored_files = []
    for path in sorted((item for item in folder.rglob("*") if item.is_file())):
        if path.suffix.lower() == ".json" and is_mineru_json(path):
            mineru_files.append(
                {
                    "path": str(path),
                    "original_file": infer_original_file(path),
                    "parse_status": "success",
                }
            )
        else:
            ignored_files.append(str(path.relative_to(folder)))
    return {
        "folder": str(folder),
        "sample_name": folder.name,
        "mineru_files": mineru_files,
        "ignored_files": ignored_files,
    }


def ask_choice(
    prompt: str,
    choices: dict[str, Any],
    default: str,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> Any:
    while True:
        answer = input_fn(prompt).strip() or default
        if answer in choices:
            return choices[answer]
        output_fn(f"请输入以下选项之一：{', '.join(choices)}")


def ask_yes_no(
    prompt: str,
    default: bool,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> bool:
    choices = {"y": True, "yes": True, "是": True, "n": False, "no": False, "否": False}
    default_key = "y" if default else "n"
    return ask_choice(prompt, choices, default_key, input_fn, output_fn)


def ask_entity_type(
    prompt: str,
    default: str,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> str:
    return ask_choice(prompt, ENTITY_TYPE_OPTIONS, default, input_fn, output_fn)


def collect_form_answers(
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
) -> tuple[str, dict[str, Any]]:
    output_fn("请设置本次测试的表单选项。直接回车使用括号中的默认值。")
    mode = ask_choice(
        "审查模式：1=局部样本partial，2=完整材料包complete [1]：",
        {"1": "partial", "2": "complete"},
        "1",
        input_fn,
        output_fn,
    )
    is_joint = ask_yes_no("是否联合申报？y/n [n]：", False, input_fn, output_fn)
    applicant_count = 1
    if is_joint:
        applicant_count = ask_choice(
            "联合申报单位数量：2或3 [2]：",
            {"2": 2, "3": 3},
            "2",
            input_fn,
            output_fn,
        )
    lead_index = 1
    if is_joint:
        lead_index = ask_choice(
            f"牵头单位序号：1-{applicant_count} [1]：",
            {str(index): index for index in range(1, applicant_count + 1)},
            "1",
            input_fn,
            output_fn,
        )
        joint_material_type = ask_choice(
            "联合申报材料：1=盖章项目合作协议，2=盖章联合申报协议，"
            "3=盖章的牵头方申报声明 [2]：",
            JOINT_MATERIAL_OPTIONS,
            "2",
            input_fn,
            output_fn,
        )
    else:
        joint_material_type = None

    applicants = []
    for index in range(1, applicant_count + 1):
        output_fn(f"\n设置第{index}家申报单位：")
        name = input_fn(f"单位名称 [申报单位{index}]：").strip() or f"申报单位{index}"
        entity_type = ask_entity_type(
            "单位性质：1=国企，2=民企，3=高校/科研院所，4=机关/事业单位，5=其他 [1]：",
            "1",
            input_fn,
            output_fn,
        )
        independent = ask_yes_no("是否独立法人？y/n [y]：", True, input_fn, output_fn)
        applicant = {
            "entity_id": f"E{index:02d}",
            "entity_name": name,
            "entity_type": entity_type,
            "is_independent_legal_person": independent,
        }
        if is_joint:
            applicant["is_lead"] = index == lead_index
        if not independent:
            parent_name = input_fn("具有独立法人资格的上级单位名称 [上级单位]：").strip() or "上级单位"
            parent_type = ask_entity_type(
                "上级单位性质：1=国企，2=民企，3=高校/科研院所，4=机关/事业单位，5=其他 [1]：",
                "1",
                input_fn,
                output_fn,
            )
            applicant["parent_entity"] = {
                "entity_id": f"E{index:02d}-PARENT",
                "entity_name": parent_name,
                "entity_type": parent_type,
            }
        applicants.append(applicant)

    project_stage = ask_choice(
        "\n项目当前进展：0=暂不设置，1=正在建设阶段，2=计划实施阶段，3=其他 [0]：",
        {"0": None, "1": "building", "2": "planned", "3": "other"},
        "0",
        input_fn,
        output_fn,
    )
    return mode, {
        "is_joint_declaration": is_joint,
        "joint_declaration_material_type": joint_material_type,
        "project_stage": project_stage,
        "applicants": applicants,
    }


def safe_submission_id(name: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name).strip("-_")
    return value or "folder-sample"


def build_folder_manifest(
    scan_result: dict[str, Any],
    mode: str,
    form_answers: dict[str, Any],
) -> dict[str, Any]:
    if not scan_result["mineru_files"]:
        raise ValueError("No MinerU JSON files were found in the sample folder")
    return {
        "submissions": [
            {
                "submission_id": safe_submission_id(scan_result["sample_name"]),
                "name": scan_result["sample_name"],
                "mode": mode,
                "form_answers": form_answers,
                "files": scan_result["mineru_files"],
                "folder_scan": {
                    "source_folder": scan_result["folder"],
                    "mineru_json_count": len(scan_result["mineru_files"]),
                    "ignored_files": scan_result["ignored_files"],
                },
            }
        ]
    }
