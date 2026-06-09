"""Generate human-readable Markdown reports."""

from __future__ import annotations

from typing import Any


STATUS_LABELS = {
    "pass": "通过",
    "fail": "不通过",
    "manual_review": "待人工复核",
    "warning": "建议补正",
    "not_assessable": "无法判断",
}


def render_report(
    submission: dict[str, Any],
    materials: list[dict[str, Any]],
    extracted: list[dict[str, Any]],
    rule_results: dict[str, Any],
) -> str:
    lines = [
        f'# {submission["name"]} - 本地AI预审报告',
        "",
        f'- 审查模式：`{submission.get("mode", "partial")}`',
        f'- 申报方式：`{submission.get("_subject_structure", {}).get("declaration_type", "unspecified")}`',
        f'- 主体信息来源：`{submission.get("_subject_structure", {}).get("input_source", "none")}`',
        f'- 综合状态：**{rule_results["overall_status"]}**',
        f'- 输入文件数：{len(submission["files"])}',
        f'- 识别材料数：{len(materials)}',
        "",
        "## 申报主体结构",
        "",
        "| 主体编号 | 主体名称 | 申报角色 | 单位性质 | 是否独立法人 | 上级单位编号 |",
        "|---|---|---|---|---|---|",
    ]
    entities = submission.get("_subject_structure", {}).get("entities", [])
    if entities:
        for entity in entities:
            lines.append(
                f'| {entity.get("entity_id", "")} | {entity.get("entity_name", "")} | '
                f'{entity.get("declaration_role", "")} | {entity.get("entity_type", "")} | '
                f'{entity.get("is_independent_legal_person", "")} | {entity.get("parent_entity_id", "") or ""} |'
            )
    else:
        lines.append("|  | 未提供结构化主体信息 |  |  |  |  |")
    lines.extend(
        [
            "",
            "## 表单触发的动态上传要求",
            "",
            "| 上传区域 | 材料类型 | 材料所属主体 | 支持的申报主体 | 触发原因 |",
            "|---|---|---|---|---|",
        ]
    )
    upload_requirements = submission.get("_subject_structure", {}).get("upload_requirements", [])
    if upload_requirements:
        for item in upload_requirements:
            lines.append(
                f'| {item.get("upload_key", "")} | {item.get("document_type", "")} | '
                f'{item.get("owner_entity_id", "") or ""} | {item.get("supports_entity_id", "") or ""} | '
                f'{item.get("reason", "")} |'
            )
    else:
        lines.append("|  | 当前未生成动态上传要求 |  |  |  |")
    folder_scan = submission.get("folder_scan")
    if folder_scan:
        lines.extend(
            [
                "",
                "## 样本文件夹扫描",
                "",
                f'- 来源文件夹：`{folder_scan.get("source_folder", "")}`',
                f'- MinerU JSON数量：{folder_scan.get("mineru_json_count", 0)}',
                f'- 其他未解析文件数量：{len(folder_scan.get("ignored_files", []))}',
            ]
        )
        if folder_scan.get("ignored_files"):
            lines.extend(["", "未解析文件："])
            lines.extend(f'- `{item}`' for item in folder_scan["ignored_files"])
    lines.extend(
        [
            "",
            "## 解析状态",
            "",
            "| 原始文件 | MinerU JSON | 状态 | 总页数 | 已解析页 | 空白页 |",
            "|---|---|---|---:|---|---|",
        ]
    )
    for item in submission.get("_parse_details", []):
        lines.append(
            f'| {item.get("original_file", "")} | {item["path"]} | {item["parse_status"]} | '
            f'{item.get("total_pages") or ""} | {item.get("parsed_pages") or ""} | '
            f'{item.get("empty_pages") or ""} |'
        )
    lines.extend(
        [
        "",
        "## 材料目录",
        "",
        "| 材料类型 | 材料归属 | 必要性 | 存在性判定 | 分类置信度 | 证据页码 | 摘要 |",
        "|---|---|---|---|---:|---|---|",
        ]
    )
    for item in materials:
        preview = item["text_preview"].replace("|", " ")[:120]
        presence = item.get("presence_assessment", {})
        ownership = item.get("ownership") or {}
        ownership_text = ownership.get("owner_entity_id", "")
        if ownership.get("supports_entity_id"):
            ownership_text += f'→支持{ownership["supports_entity_id"]}'
        lines.append(
            f'| {item["document_type"]} | '
            f'{ownership_text} | '
            f'{item.get("requirement", "unknown")} | '
            f'{presence.get("level", "")}：{presence.get("reason", "")} | '
            f'{item["confidence"]:.2f} | '
            f'{"、".join(item["pages"])} | {preview} |'
        )

    lines.extend(["", "## 规则结果", "", "| 规则 | 状态 | 说明 | 原因 | 证据页码 |", "|---|---|---|---|---|"])
    for item in rule_results["results"]:
        lines.append(
            f'| {item["rule_id"]} | {STATUS_LABELS[item["status"]]} | '
            f'{item["description"]} | {item["reason"].replace("|", " ")} | '
            f'{"、".join(item["evidence_pages"])} |'
        )

    lines.extend(
        [
            "",
            "## 结论说明",
            "",
            "- `partial` 模式仅验证现有局部样本，未识别到其他材料不等于材料缺失。",
            "- 专利、软著、奖项、许可证等鼓励或辅助材料，未提交不得判材料缺失或打回。",
            "- 已提交但明显无关的材料仅提示人工确认，不参与硬规则结论。",
            "- MinerU无法确认公章、签字和证书真伪，相关事项需人工复核。",
            "- 所有自动结果应结合证据页码复核后使用。",
            "",
        ]
    )
    return "\n".join(lines)


def render_batch_summary(items: list[dict[str, Any]]) -> str:
    lines = [
        "# AI预审本地MVP批量测试摘要",
        "",
        "| 样本 | 综合状态 | 识别材料数 | 通过 | 不通过 | 待人工复核 | 无法判断 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in items:
        counts = item["rule_results"]["counts"]
        lines.append(
            f'| {item["name"]} | {item["rule_results"]["overall_status"]} | '
            f'{len(item["materials"])} | {counts["pass"]} | {counts["fail"]} | '
            f'{counts["manual_review"]} | {counts["not_assessable"]} |'
        )
    return "\n".join(lines) + "\n"
