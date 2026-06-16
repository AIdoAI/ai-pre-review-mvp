"""Build a review manifest from one sample folder and interactive form answers."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable


InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


JOINT_MATERIAL_OPTIONS = {
    "1": "stamped_project_cooperation_agreement",
    "2": "stamped_joint_declaration_agreement",
    "3": "stamped_lead_declaration",
}

def _plain_menu(prompt: str, options: list[tuple[str, str]], default_key: str) -> str:
    """无 termios（Windows）或非 tty 时的退化菜单：打印编号 + input 选择。"""
    print(prompt)
    for index, (key, label) in enumerate(options, 1):
        mark = "（默认）" if key == default_key else ""
        print(f"  {index}. {label}{mark}")
    raw = input("输入序号后回车（直接回车用默认）：").strip()
    if not raw:
        return default_key
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1][0]
    for key, _ in options:
        if raw == key:
            return key
    return default_key


def terminal_menu(prompt: str, options: list[tuple[str, str]], default_key: str) -> str:
    """Select one option with arrow keys and Enter in an interactive terminal.

    termios/tty 为 Unix 专有；Windows 或非交互输入时退回 _plain_menu。
    """
    try:
        import termios
        import tty
    except ImportError:
        return _plain_menu(prompt, options, default_key)
    if not sys.stdin.isatty():
        return _plain_menu(prompt, options, default_key)
    selected = next(
        (index for index, (key, _) in enumerate(options) if key == default_key),
        0,
    )
    stream = sys.stdin
    output = sys.stdout
    old_settings = termios.tcgetattr(stream.fileno())
    output.write(f"{prompt}\n")
    try:
        tty.setraw(stream.fileno())
        while True:
            for index, (_, label) in enumerate(options):
                marker = ">" if index == selected else " "
                output.write(f"\r\033[2K{marker} {index + 1}. {label}\n")
            output.flush()

            key = stream.read(1)
            if key == "\x03":
                raise KeyboardInterrupt
            if key in {"\r", "\n"}:
                output.write("\r")
                output.flush()
                return options[selected][0]
            if key == "\x1b":
                sequence = stream.read(2)
                if sequence == "[A":
                    selected = (selected - 1) % len(options)
                elif sequence == "[B":
                    selected = (selected + 1) % len(options)
            elif key.isdigit() and 1 <= int(key) <= len(options):
                selected = int(key) - 1
            output.write(f"\033[{len(options)}A")
    finally:
        termios.tcsetattr(stream.fileno(), termios.TCSADRAIN, old_settings)
        output.write("\n")
        output.flush()


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
    # MinerU middle.json (local pipeline): {"pdf_info": [...]}
    if isinstance(data, dict) and isinstance(data.get("pdf_info"), list):
        return True
    # MinerU Open API content_list: [{"page_idx":..,"type":..,"text":..}, ...]
    if isinstance(data, dict) and isinstance(data.get("content_list"), list):
        data = data["content_list"]
    if isinstance(data, list):
        return any(isinstance(block, dict) and "page_idx" in block for block in data[:20])
    return False


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
    menu_options: list[tuple[str, str]] | None = None,
) -> Any:
    if input_fn is input and sys.stdin.isatty() and menu_options:
        return choices[terminal_menu(prompt, menu_options, default)]
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
    return ask_choice(
        prompt,
        choices,
        default_key,
        input_fn,
        output_fn,
        [("y", "是"), ("n", "否")],
    )


def ask_legal_person_status(
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> bool:
    return ask_choice(
        "请选择申报单位法人类型（上下方向键移动，回车确认）：",
        {"1": True, "2": False},
        "1",
        input_fn,
        output_fn,
        [
            ("1", "独立法人"),
            ("2", "非独立法人（已获具有独立法人资格的上级单位授权）"),
        ],
    )


def collect_form_answers(
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
) -> tuple[str, dict[str, Any]]:
    output_fn("请设置本次测试的表单选项。直接回车使用括号中的默认值。")
    mode = ask_choice(
        "请选择审查模式（上下方向键移动，回车确认）：",
        {"1": "partial", "2": "complete"},
        "1",
        input_fn,
        output_fn,
        [("1", "局部测试样本"), ("2", "完整材料包")],
    )
    is_joint = ask_yes_no("是否联合申报（上下方向键移动，回车确认）？", False, input_fn, output_fn)
    applicant_count = 1
    if is_joint:
        applicant_count = ask_choice(
            "联合申报单位数量：2或3 [2]：",
            {"2": 2, "3": 3},
            "2",
            input_fn,
            output_fn,
            [("2", "2家"), ("3", "3家")],
        )
    lead_index = 1
    if is_joint:
        lead_index = ask_choice(
            f"牵头单位序号：1-{applicant_count} [1]：",
            {str(index): index for index in range(1, applicant_count + 1)},
            "1",
            input_fn,
            output_fn,
            [(str(index), f"第{index}家申报单位") for index in range(1, applicant_count + 1)],
        )
        joint_material_type = ask_choice(
            "请选择联合申报材料（上下方向键移动，回车确认）：",
            JOINT_MATERIAL_OPTIONS,
            "2",
            input_fn,
            output_fn,
            [
                ("1", "盖章项目合作协议"),
                ("2", "盖章联合申报协议"),
                ("3", "盖章的牵头方申报声明"),
            ],
        )
    else:
        joint_material_type = None

    applicants = []
    for index in range(1, applicant_count + 1):
        output_fn(f"\n设置第{index}家申报单位：")
        name = input_fn(f"申报单位名称 [申报单位{index}]：").strip() or f"申报单位{index}"
        independent = ask_legal_person_status(input_fn, output_fn)
        applicant = {
            "entity_id": f"E{index:02d}",
            "entity_name": name,
            "is_independent_legal_person": independent,
        }
        if is_joint:
            applicant["is_lead"] = index == lead_index
        if not independent:
            applicant["parent_entity"] = {
                "entity_id": f"E{index:02d}-PARENT",
            }
        applicants.append(applicant)

    project_stage = ask_choice(
        "\n请选择项目当前进展（上下方向键移动，回车确认）：",
        {"0": None, "1": "building", "2": "planned", "3": "other"},
        "0",
        input_fn,
        output_fn,
        [
            ("0", "暂不设置"),
            ("1", "正在建设阶段"),
            ("2", "计划实施阶段"),
            ("3", "其他（原则上不允许，转人工复核）"),
        ],
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
