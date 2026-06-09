"""Execute deterministic and manual-review rules on parsed materials."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .subject_structure import entity_material_findings


def load_rules(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def result(
    rule_id: str,
    status: str,
    description: str,
    reason: str,
    evidence_pages: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "status": status,
        "description": description,
        "reason": reason,
        "evidence_pages": evidence_pages or [],
    }


def material_index(materials: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for material in materials:
        index.setdefault(material["document_type"], []).append(material)
    return index


def extracted_index(extracted: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for item in extracted:
        index.setdefault(item["document_type"], []).append(item)
    return index


def field_value(item: dict[str, Any], name: str) -> Any:
    return item.get("fields", {}).get(name, {}).get("value")


def run_rules(
    submission: dict[str, Any],
    materials: list[dict[str, Any]],
    extracted: list[dict[str, Any]],
    rules: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    mode = submission.get("mode", "partial")
    parse_complete = submission.get("_parse_complete", True)
    conditions = set(submission.get("conditions", []))
    m_index = material_index(materials)
    e_index = extracted_index(extracted)
    results: list[dict[str, Any]] = []
    subject_structure = submission.get("_subject_structure", {})
    results.extend(subject_structure.get("findings", []))
    if submission.get("_unmatched_material_assignments"):
        results.append(
            result(
                "MR-MATERIAL-ASSIGNMENT",
                "manual_review",
                "材料归属配置匹配",
                f"存在{len(submission['_unmatched_material_assignments'])}条材料归属配置未匹配到识别材料",
            )
        )

    if not parse_complete:
        incomplete = [
            item for item in submission.get("_parse_details", [])
            if item.get("parse_status") != "success"
        ]
        details = "；".join(
            f'{Path(item["path"]).name}: {item.get("parse_status")}'
            + (f'，空白页{item["empty_pages"]}' if item.get("empty_pages") else "")
            for item in incomplete
        )
        results.append(
            result(
                "MR-PARSE-QUALITY",
                "manual_review",
                "输入文件解析完整性",
                f"存在未完整解析文件，不能据此判定材料缺失：{details}",
            )
        )

    for material_type, material_policy in policy["materials"].items():
        requirement = material_policy.get("requirement")
        applicable = requirement == "required" or (
            requirement == "conditional_required"
            and material_policy.get("condition") in conditions
        )
        if not applicable:
            continue
        if subject_structure.get("provided") and material_type == "分支机构专项授权文件":
            # Structured entity checks validate one authorization per
            # non-independent applicant and its parent relationship.
            continue
        rule_id = rules["material_rule_ids"].get(material_type, f"MAT-{material_type}")
        candidates = m_index.get(material_type, [])
        confirmed = [
            item for item in candidates
            if item.get("presence_assessment", {}).get("eligible_for_required_presence")
        ]
        if confirmed:
            pages = sorted({page for item in confirmed for page in item["pages"]})
            results.append(
                result(
                    rule_id,
                    "pass",
                    f"检查{material_type}",
                    "已通过强标题证据确认对应材料",
                    pages,
                )
            )
        elif candidates:
            pages = sorted({page for item in candidates for page in item["pages"]})
            results.append(
                result(
                    rule_id,
                    "manual_review",
                    f"检查{material_type}",
                    "发现疑似材料，但仅命中通用关键词，不能确认已提交或据此判缺",
                    pages,
                )
            )
        elif mode == "complete" and parse_complete:
            results.append(result(rule_id, "fail", f"检查{material_type}", "完整材料包中未识别到必要材料"))
        else:
            reason = (
                "存在未完成或仅部分解析的文件，不能判定该材料缺失"
                if not parse_complete
                else "当前为局部测试样本，未提供或未覆盖该必要材料，不能判定缺失"
            )
            results.append(
                result(
                    rule_id,
                    "not_assessable",
                    f"检查{material_type}",
                    reason,
                )
            )

    results.extend(entity_material_findings(submission, materials))

    for material_type, found in m_index.items():
        material_policy = policy["materials"].get(
            material_type,
            {"requirement": "unknown", "missing_action": "ignore"},
        )
        requirement = material_policy.get("requirement", "unknown")
        pages = sorted({page for item in found for page in item["pages"]})
        if requirement in {"recommended", "auxiliary"}:
            results.append(
                result(
                    f"INFO-{material_type}",
                    "pass",
                    f"识别到非必要材料：{material_type}",
                    f"该材料属于{requirement}，仅记录和抽取，不参与缺失打回判断",
                    pages,
                )
            )
        elif requirement in {"irrelevant", "unknown"}:
            results.append(
                result(
                    f"MR-{material_type}",
                    "manual_review",
                    f"识别到{material_type}",
                    "该材料不参与必要性判断，建议人工确认其相关性或有效性",
                    pages,
                )
            )

    commitment_items = e_index.get("申报材料真实性承诺书", [])
    if commitment_items:
        commitment = commitment_items[0]
        pages = commitment.get("fields", {}).get("contact_person", {}).get("evidence_pages", [])
        contact = field_value(commitment, "contact_person")
        phone = field_value(commitment, "contact_phone")
        year = field_value(commitment, "year")
        contact_complete = bool(contact and phone)
        results.append(
            result(
                "HR-6.1",
                "pass" if contact_complete else "manual_review",
                "承诺书联系人和电话不得为空",
                (
                    "联系人和电话已提取"
                    if contact_complete
                    else "未提取到联系人或电话；当前无法区分字段空白与OCR漏识别"
                ),
                pages,
            )
        )
        if year == "2026":
            year_status = "pass"
            year_reason = "识别年份：2026"
        elif year:
            year_status = "manual_review"
            year_reason = f"识别年份：{year}；需核对原页，排除OCR误识别"
        else:
            year_status = "manual_review"
            year_reason = "未识别到承诺书日期年份；当前无法确认原页是否空白"
        results.append(
            result(
                "HR-6.3",
                year_status,
                "承诺书日期年份应为2026",
                year_reason,
                pages,
            )
        )
        commitment_material = m_index["申报材料真实性承诺书"][0]
        missing_phrases = [
            phrase for phrase in rules["commitment_required_phrases"]
            if phrase not in commitment_material["full_text"]
        ]
        results.append(
            result(
                "MR-COMMITMENT-CONTENT",
                "pass" if len(missing_phrases) <= 1 else "manual_review",
                "承诺书关键内容完整性",
                "关键内容基本完整" if len(missing_phrases) <= 1 else f"可能缺少：{', '.join(missing_phrases)}",
                commitment_material["pages"],
            )
        )
        results.append(
            result(
                "MR-COMMITMENT-VISUAL",
                "manual_review",
                "承诺书签字和公章视觉复核",
                "MinerU文字结果只能识别签字/盖章标签，不能确认实际签字、公章存在及真伪",
                commitment_material["pages"],
            )
        )

    low_confidence = [
        item for item in materials
        if item["confidence"] < rules.get("low_confidence_threshold", 0.75)
    ]
    for item in low_confidence:
        results.append(
            result(
                "MR-CLASSIFICATION",
                "manual_review",
                "低置信度材料分类",
                f'{item["document_type"]}分类置信度为{item["confidence"]}',
                item["pages"],
            )
        )

    confirmed_license_materials = [
        material for material in m_index.get("营业执照", [])
        if material.get("presence_assessment", {}).get("eligible_for_required_presence")
    ]
    if confirmed_license_materials:
        license_type_material = confirmed_license_materials[0]
        license_text = license_type_material["full_text"]
        if re.search(r"国有独资|国有控股|全民所有制", license_text):
            status, reason = "pass", "营业执照文字中出现明确国资辅助信号"
        else:
            status, reason = "manual_review", "营业执照未直接体现明确国资性质，需结合股权或上级单位材料判断"
        results.append(result("MR-ENTITY-TYPE", status, "企业国资身份综合判断", reason, license_type_material["pages"]))

    company_names: dict[str, set[str]] = {}
    for item in extracted:
        value = field_value(item, "company_name")
        if value:
            company_names.setdefault(value, set()).add(item["document_type"])
    if len(company_names) > 1:
        results.append(
            result(
                "MR-COMPANY-CONSISTENCY",
                "manual_review",
                "跨材料单位名称一致性",
                "识别到多个单位名称：" + "；".join(company_names),
            )
        )
    elif company_names:
        results.append(
            result(
                "MR-COMPANY-CONSISTENCY",
                "pass",
                "跨材料单位名称一致性",
                "已提取材料中的单位名称一致",
            )
        )

    status_order = {"fail": 4, "manual_review": 3, "warning": 2, "not_assessable": 1, "pass": 0}
    max_status = max((status_order[item["status"]] for item in results), default=0)
    if max_status == 4:
        overall = "预审不通过"
    elif max_status == 3:
        overall = "待人工复核"
    elif max_status == 2:
        overall = "建议补正"
    elif mode == "partial":
        overall = "局部样本验证完成"
    else:
        overall = "预审通过"

    return {
        "submission_id": submission["submission_id"],
        "mode": mode,
        "declaration_type": subject_structure.get("declaration_type", "unspecified"),
        "overall_status": overall,
        "results": results,
        "counts": {
            status: sum(item["status"] == status for item in results)
            for status in status_order
        },
    }
