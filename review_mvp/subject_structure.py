"""Normalize and validate declaration entities and material ownership."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


APPLICANT_ROLES = {"applicant", "lead", "member"}

JOINT_MATERIAL_LABELS = {
    "stamped_project_cooperation_agreement": "盖章项目合作协议",
    "stamped_joint_declaration_agreement": "盖章联合申报协议",
    "stamped_lead_declaration": "盖章的牵头方申报声明",
}


def finding(rule_id: str, status: str, description: str, reason: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "status": status,
        "description": description,
        "reason": reason,
        "evidence_pages": [],
    }


def subject_structure_from_form_answers(form_answers: dict[str, Any]) -> dict[str, Any]:
    """Convert platform form selections into the internal subject model."""
    is_joint = form_answers.get("is_joint_declaration")
    declaration_type = "joint" if is_joint is True else "independent" if is_joint is False else "unspecified"
    entities: list[dict[str, Any]] = []
    known_ids: set[str] = set()

    for index, applicant in enumerate(form_answers.get("applicants", []), start=1):
        entity_id = applicant.get("entity_id") or f"E{index:02d}"
        role = "applicant"
        if declaration_type == "joint":
            role = "lead" if applicant.get("is_lead") else "member"
        entity = {
            "entity_id": entity_id,
            "entity_name": applicant.get("entity_name"),
            "declaration_role": role,
            "entity_type": applicant.get("entity_type"),
            "is_independent_legal_person": applicant.get("is_independent_legal_person"),
        }
        if applicant.get("required_materials") is not None:
            entity["required_materials"] = applicant["required_materials"]

        parent = applicant.get("parent_entity")
        if applicant.get("is_independent_legal_person") is False and parent:
            parent_id = parent.get("entity_id") or f"{entity_id}-PARENT"
            entity["parent_entity_id"] = parent_id
        entities.append(entity)
        known_ids.add(entity_id)

        if applicant.get("is_independent_legal_person") is False and parent:
            if parent_id not in known_ids:
                entities.append(
                    {
                        "entity_id": parent_id,
                        "entity_name": parent.get("entity_name"),
                        "declaration_role": "authorizing_parent",
                        "entity_type": parent.get("entity_type"),
                        "is_independent_legal_person": parent.get(
                            "is_independent_legal_person",
                            True,
                        ),
                    }
                )
                known_ids.add(parent_id)

    return {
        "declaration_type": declaration_type,
        "joint_declaration_material_type": form_answers.get("joint_declaration_material_type"),
        "project_stage": form_answers.get("project_stage"),
        "entities": entities,
    }


def build_upload_requirements(structure: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe dynamic upload zones implied by confirmed form selections."""
    requirements: list[dict[str, Any]] = []
    if structure.get("declaration_type") == "joint":
        joint_material_type = structure.get("joint_declaration_material_type")
        document_type = JOINT_MATERIAL_LABELS.get(joint_material_type, "联合申报支持材料（三选一）")
        requirements.append(
            {
                "upload_key": "joint_declaration_support_material",
                "document_type": document_type,
                "owner_entity_id": None,
                "supports_entity_id": None,
                "reason": "表单选择联合申报，需从三类支持材料中选择一类上传",
            }
        )
    entities = structure.get("entities", [])
    entity_index = {entity.get("entity_id"): entity for entity in entities}
    for entity in entities:
        if entity.get("declaration_role") not in APPLICANT_ROLES:
            continue
        required_materials = entity.get("required_materials", [])
        if not isinstance(required_materials, list):
            required_materials = []
        for document_type in required_materials:
            requirements.append(
                {
                    "upload_key": f'{entity["entity_id"]}_{document_type}',
                    "document_type": document_type,
                    "owner_entity_id": entity["entity_id"],
                    "supports_entity_id": None,
                    "reason": "该申报主体配置的必需材料",
                }
            )
        if entity.get("is_independent_legal_person") is not False:
            continue
        parent = entity_index.get(entity.get("parent_entity_id"), {})
        requirements.extend(
            [
                {
                    "upload_key": f'{entity["entity_id"]}_parent_license',
                    "document_type": "营业执照",
                    "owner_entity_id": parent.get("entity_id"),
                    "supports_entity_id": entity["entity_id"],
                    "reason": "表单选择该申报单位不是独立法人",
                },
                {
                    "upload_key": f'{entity["entity_id"]}_parent_authorization',
                    "document_type": "分支机构专项授权文件",
                    "owner_entity_id": parent.get("entity_id"),
                    "supports_entity_id": entity["entity_id"],
                    "reason": "表单选择该申报单位不是独立法人",
                },
            ]
        )
    return requirements


def prepare_subject_structure(submission: dict[str, Any]) -> dict[str, Any]:
    """Validate an optional structured subject model and derive legacy conditions."""
    legacy_conditions = set(submission.get("conditions", []))
    form_answers = submission.get("form_answers")
    raw = subject_structure_from_form_answers(form_answers) if form_answers else submission.get("subject_structure")
    if not raw:
        return {
            "provided": False,
            "input_source": "none",
            "declaration_type": "unspecified",
            "joint_declaration_material_type": None,
            "project_stage": submission.get("project_stage"),
            "entities": [],
            "applicant_entity_ids": [],
            "upload_requirements": [],
            "derived_conditions": sorted(legacy_conditions),
            "findings": [],
        }

    structure = deepcopy(raw)
    input_source = "form_answers" if form_answers else "subject_structure"
    declaration_type = structure.get("declaration_type", "unspecified")
    joint_material_type = structure.get("joint_declaration_material_type")
    project_stage = structure.get("project_stage") or submission.get("project_stage")
    entities = structure.get("entities", [])
    findings: list[dict[str, Any]] = []
    if form_answers and submission.get("subject_structure"):
        findings.append(
            finding(
                "INFO-FORM-SOURCE",
                "pass",
                "主体结构输入来源",
                "同时收到form_answers和subject_structure，已按表单选项form_answers生成主体结构",
            )
        )
    entity_ids = [entity.get("entity_id") for entity in entities if entity.get("entity_id")]
    duplicate_ids = sorted({entity_id for entity_id in entity_ids if entity_ids.count(entity_id) > 1})
    entity_index = {entity.get("entity_id"): entity for entity in entities if entity.get("entity_id")}
    applicant_roles = {"lead", "member"} if declaration_type == "joint" else APPLICANT_ROLES
    applicants = [entity for entity in entities if entity.get("declaration_role") in applicant_roles]
    leads = [entity for entity in applicants if entity.get("declaration_role") == "lead"]

    missing_id_entities = [
        entity.get("entity_name") or f"第{index + 1}个主体"
        for index, entity in enumerate(entities)
        if not entity.get("entity_id")
    ]
    if missing_id_entities:
        findings.append(
            finding(
                "HR-SUBJECT-IDS",
                "fail",
                "申报主体编号完整性",
                f"以下主体缺少主体编号，无法关联材料：{', '.join(missing_id_entities)}",
            )
        )
    if duplicate_ids:
        findings.append(
            finding(
                "HR-SUBJECT-IDS",
                "fail",
                "申报主体编号唯一性",
                f"存在重复主体编号：{', '.join(duplicate_ids)}",
            )
        )
    if declaration_type not in {"independent", "joint"}:
        findings.append(
            finding(
                "HR-SUBJECT-TYPE",
                "manual_review",
                "申报方式识别",
                "未明确申报方式为独立申报或联合申报",
            )
        )
    elif declaration_type == "independent":
        if len(applicants) != 1:
            findings.append(
                finding(
                    "HR-SUBJECT-INDEPENDENT-COUNT",
                    "fail",
                    "独立申报单位数量",
                    f"独立申报应有1家申报单位，当前结构化数据为{len(applicants)}家",
                )
            )
        else:
            findings.append(
                finding(
                    "HR-SUBJECT-INDEPENDENT-COUNT",
                    "pass",
                    "独立申报单位数量",
                    "独立申报单位数量为1家",
                )
            )
    elif declaration_type == "joint":
        legacy_conditions.add("joint_declaration")
        if joint_material_type in JOINT_MATERIAL_LABELS:
            extra_note = (
                "；选择牵头方申报声明时，还需检查“联合申报单位简介”是否补充"
                "对知识产权无异议表述"
                if joint_material_type == "stamped_lead_declaration"
                else ""
            )
            findings.append(
                finding(
                    "MR-JOINT-SUPPORT-MATERIAL",
                    "manual_review",
                    "联合申报支持材料",
                    f"表单选择：{JOINT_MATERIAL_LABELS[joint_material_type]}；"
                    f"材料存在性由HR-2.3-JOINT三选一规则自动核验，"
                    f"盖章真伪及相关表述转人工复核{extra_note}",
                )
            )
        else:
            findings.append(
                finding(
                    "MR-JOINT-SUPPORT-MATERIAL",
                    "manual_review",
                    "联合申报支持材料",
                    "未明确选择三类联合申报支持材料中的哪一类，需人工复核",
                )
            )
        if 2 <= len(applicants) <= 3:
            findings.append(
                finding(
                    "HR-1.2-COUNT",
                    "pass",
                    "联合申报单位数量",
                    f"联合申报单位总数为{len(applicants)}家",
                )
            )
        else:
            findings.append(
                finding(
                    "HR-1.2-COUNT",
                    "fail",
                    "联合申报单位数量",
                    f"联合申报单位应为2至3家，当前结构化数据为{len(applicants)}家",
                )
            )
        if len(leads) == 1:
            findings.append(
                finding("HR-1.2-LEAD", "pass", "联合申报牵头单位", "已明确1家牵头单位")
            )
            lead_type = leads[0].get("entity_type")
            if lead_type == "state_owned":
                findings.append(
                    finding("HR-1.2-LEAD-TYPE", "pass", "牵头单位国有属性", "牵头单位标记为国有企业")
                )
            elif lead_type:
                findings.append(
                    finding(
                        "HR-1.2-LEAD-TYPE",
                        "fail",
                        "牵头单位国有属性",
                        f"牵头单位类型为{lead_type}，不符合牵头单位应为国有企业的结构化要求",
                    )
                )
            else:
                findings.append(
                    finding(
                        "HR-1.2-LEAD-TYPE",
                        "manual_review",
                        "牵头单位国有属性",
                        "牵头单位未填写单位性质，无法确认国有属性",
                    )
                )
        else:
            findings.append(
                finding(
                    "HR-1.2-LEAD",
                    "fail",
                    "联合申报牵头单位",
                    f"联合申报应明确1家牵头单位，当前识别到{len(leads)}家",
                )
            )

    for entity in applicants:
        entity_id = entity.get("entity_id") or "未编号主体"
        entity_name = entity.get("entity_name") or entity_id
        independent = entity.get("is_independent_legal_person")
        if entity.get("required_materials") is not None and not isinstance(
            entity.get("required_materials"), list
        ):
            findings.append(
                finding(
                    f"MR-ENTITY-MATERIAL-CONFIG-{entity_id}",
                    "manual_review",
                    f"{entity_name}主体材料配置",
                    "required_materials应为材料类型数组",
                )
            )
        if independent is None:
            findings.append(
                finding(
                    f"MR-LEGAL-STATUS-{entity_id}",
                    "manual_review",
                    f"{entity_name}独立法人状态",
                    "未填写是否独立法人，无法生成完整动态材料清单",
                )
            )
            continue
        if independent:
            continue

        legacy_conditions.add("branch_office")
        parent_id = entity.get("parent_entity_id")
        parent = entity_index.get(parent_id)
        if not parent_id:
            findings.append(
                finding(
                    f"HR-2.4-PARENT-{entity_id}",
                    "fail",
                    f"{entity_name}上级单位关联",
                    "非独立法人申报单位未绑定具有独立法人资格的上级单位",
                )
            )
        elif not parent:
            findings.append(
                finding(
                    f"HR-2.4-PARENT-{entity_id}",
                    "fail",
                    f"{entity_name}上级单位关联",
                    f"上级单位编号{parent_id}不存在于主体清单",
                )
            )
        elif parent.get("is_independent_legal_person") is not True:
            findings.append(
                finding(
                    f"HR-2.4-PARENT-{entity_id}",
                    "fail",
                    f"{entity_name}上级单位资格",
                    "绑定的上级单位未明确为独立法人",
                )
            )
        else:
            findings.append(
                finding(
                    f"HR-2.4-PARENT-{entity_id}",
                    "pass",
                    f"{entity_name}上级单位关联",
                    f"已绑定独立法人上级单位：{parent.get('entity_name') or parent_id}",
                )
            )

    for assignment in submission.get("material_assignments", []):
        for field_name, label in (
            ("owner_entity_id", "材料所属主体"),
            ("supports_entity_id", "材料支持的申报主体"),
        ):
            referenced_id = assignment.get(field_name)
            if referenced_id and referenced_id not in entity_index:
                findings.append(
                    finding(
                        "MR-MATERIAL-ENTITY-REFERENCE",
                        "manual_review",
                        "材料归属主体引用",
                        f"{label}编号{referenced_id}不存在于主体清单",
                    )
                )

    if project_stage == "building":
        legacy_conditions.add("project_stage_building")
    elif project_stage == "planned":
        legacy_conditions.add("project_stage_planned")
    elif project_stage == "other":
        findings.append(
            finding(
                "MR-PROJECT-STAGE-OTHER",
                "manual_review",
                "项目当前进展",
                "表单选择“其他”；原则上项目应处于正在建设或计划实施阶段，需人工复核",
            )
        )

    return {
        "provided": True,
        "input_source": input_source,
        "declaration_type": declaration_type,
        "joint_declaration_material_type": joint_material_type,
        "project_stage": project_stage,
        "entities": entities,
        "applicant_entity_ids": [entity["entity_id"] for entity in applicants if entity.get("entity_id")],
        "upload_requirements": build_upload_requirements(structure),
        "derived_conditions": sorted(legacy_conditions),
        "findings": findings,
    }


def apply_material_assignments(
    materials: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach manifest-provided ownership to matching parsed materials."""
    unmatched: list[dict[str, Any]] = []
    for assignment in assignments:
        matches = []
        for material in materials:
            original_files = {
                segment.get("original_file")
                for segment in material.get("segments", [])
                if segment.get("original_file")
            }
            if assignment.get("material_id") and assignment["material_id"] != material.get("material_id"):
                continue
            if assignment.get("document_type") and assignment["document_type"] != material.get("document_type"):
                continue
            if assignment.get("original_file") and assignment["original_file"] not in original_files:
                continue
            matches.append(material)

        if not matches:
            unmatched.append(assignment)
            continue
        for material in matches:
            material["ownership"] = {
                "owner_entity_id": assignment.get("owner_entity_id"),
                "supports_entity_id": assignment.get("supports_entity_id"),
                "assignment_source": "manifest",
            }
    return unmatched


def entity_material_findings(
    submission: dict[str, Any],
    materials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Check explicit parent-license and authorization relationships."""
    structure = submission.get("_subject_structure", {})
    if not structure.get("provided"):
        return []

    mode = submission.get("mode", "partial")
    parse_complete = submission.get("_parse_complete", True)
    entities = structure.get("entities", [])
    entity_index = {entity["entity_id"]: entity for entity in entities if entity.get("entity_id")}
    applicant_entity_ids = set(structure.get("applicant_entity_ids", []))
    results: list[dict[str, Any]] = []

    def check_owned_required_material(entity: dict[str, Any], document_type: str) -> None:
        entity_id = entity["entity_id"]
        entity_name = entity.get("entity_name") or entity_id
        candidates = [item for item in materials if item.get("document_type") == document_type]
        assigned = [
            item
            for item in candidates
            if item.get("ownership", {}).get("owner_entity_id") == entity_id
        ]
        confirmed = [
            item
            for item in assigned
            if item.get("presence_assessment", {}).get("eligible_for_required_presence")
        ]
        pages = sorted({page for item in (confirmed or assigned or candidates) for page in item.get("pages", [])})
        if confirmed:
            status = "pass"
            reason = f"已确认{document_type}归属于{entity_name}"
        elif assigned:
            status = "manual_review"
            reason = f"已关联{document_type}，但存在性证据不足"
        elif candidates:
            status = "manual_review"
            reason = f"识别到{document_type}，但未确认归属于{entity_name}"
        elif mode == "complete" and parse_complete:
            status = "fail"
            reason = f"完整材料包中未识别到{entity_name}要求提交的{document_type}"
        else:
            status = "not_assessable"
            reason = f"当前材料包不完整或解析未完成，不能判断{entity_name}是否缺少{document_type}"
        results.append(
            {
                "rule_id": f"ENTITY-MATERIAL-{entity_id}-{document_type}",
                "status": status,
                "description": f"{entity_name}主体材料：{document_type}",
                "reason": reason,
                "evidence_pages": pages,
            }
        )

    def check_related_material(
        entity: dict[str, Any],
        parent: dict[str, Any],
        document_type: str,
        rule_suffix: str,
        description: str,
        require_support_link: bool,
    ) -> None:
        entity_id = entity["entity_id"]
        parent_id = parent["entity_id"]
        candidates = [item for item in materials if item.get("document_type") == document_type]
        assigned = [
            item
            for item in candidates
            if item.get("ownership", {}).get("owner_entity_id") == parent_id
            and (
                not require_support_link
                or item.get("ownership", {}).get("supports_entity_id") == entity_id
            )
        ]
        confirmed = [
            item
            for item in assigned
            if item.get("presence_assessment", {}).get("eligible_for_required_presence")
        ]
        pages = sorted({page for item in (confirmed or assigned or candidates) for page in item.get("pages", [])})
        if confirmed:
            status = "pass"
            reason = f"已确认材料归属于上级单位{parent.get('entity_name') or parent_id}"
        elif assigned:
            status = "manual_review"
            reason = "已建立材料归属，但材料存在性证据不足，需人工确认"
        elif candidates:
            status = "manual_review"
            reason = "识别到同类材料，但未关联到对应上级单位或被授权申报单位"
        elif mode == "complete" and parse_complete:
            status = "fail"
            reason = "完整材料包中未识别到该项上级单位材料"
        else:
            status = "not_assessable"
            reason = "当前材料包不完整或解析未完成，不能判定该项材料缺失"
        results.append(
            {
                "rule_id": f"HR-2.4-{rule_suffix}-{entity_id}",
                "status": status,
                "description": description,
                "reason": reason,
                "evidence_pages": pages,
            }
        )

    for entity in entities:
        if not entity.get("entity_id"):
            continue
        if entity["entity_id"] not in applicant_entity_ids:
            continue
        required_materials = entity.get("required_materials", [])
        if not isinstance(required_materials, list):
            required_materials = []
        for document_type in required_materials:
            check_owned_required_material(entity, document_type)
        if entity.get("is_independent_legal_person") is not False:
            continue
        parent = entity_index.get(entity.get("parent_entity_id"))
        if not parent:
            continue
        check_related_material(
            entity,
            parent,
            "营业执照",
            "PARENT-LICENSE",
            f"{entity.get('entity_name') or entity['entity_id']}的上级单位营业执照",
            False,
        )
        check_related_material(
            entity,
            parent,
            "分支机构专项授权文件",
            "AUTHORIZATION",
            f"{entity.get('entity_name') or entity['entity_id']}的上级单位专项授权",
            True,
        )
    return results
