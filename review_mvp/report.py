"""Generate human-readable Markdown reports."""

from __future__ import annotations

from typing import Any
import re


STATUS_LABELS = {
    "pass": "通过",
    "fail": "不通过",
    "manual_review": "待人工复核",
    "warning": "建议补正",
    "not_assessable": "无法判断",
}


def compact_number_list(values: list[int] | int | None) -> str:
    if isinstance(values, int):
        return str(values)
    numbers = sorted(set(values or []))
    if not numbers:
        return ""
    ranges: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


def compact_evidence_pages(pages: list[str] | None) -> str:
    grouped: dict[str, list[int]] = {}
    ungrouped: list[str] = []
    for page in pages or []:
        match = re.match(r"^(.*)#P(\d+)$", page)
        if not match:
            ungrouped.append(page)
            continue
        grouped.setdefault(match.group(1), []).append(int(match.group(2)))
    compacted = [
        f"{source}#P{compact_number_list(numbers)}"
        for source, numbers in grouped.items()
    ]
    return "、".join(compacted + ungrouped)


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
            f'| {entity.get("entity_id", "")} | {entity.get("entity_name") or ""} | '
            f'{entity.get("declaration_role", "")} | {entity.get("entity_type") or ""} | '
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
            f'{item.get("total_pages") or ""} | {compact_number_list(item.get("parsed_pages"))} | '
            f'{compact_number_list(item.get("empty_pages"))} |'
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
            f'{compact_evidence_pages(item["pages"])} | {preview} |'
        )

    lines.extend(["", "## 规则结果", "", "| 规则 | 状态 | 说明 | 原因 | 证据页码 |", "|---|---|---|---|---|"])
    for item in rule_results["results"]:
        lines.append(
            f'| {item["rule_id"]} | {STATUS_LABELS[item["status"]]} | '
            f'{item["description"]} | {item["reason"].replace("|", " ")} | '
            f'{compact_evidence_pages(item["evidence_pages"])} |'
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


VERDICT_LABELS = {
    "预审不通过": ("❌ 预审不通过", "存在硬性缺失或不合规项，须整改后才能进入下一环节"),
    "待人工复核": ("⚠️ 待人工复核", "未触发硬性不通过，但有需要人工确认的事项"),
    "建议补正": ("🟡 建议补正", "材料基本齐全，有少量需补正项"),
    "预审通过": ("✅ 预审通过", "形式审查未发现问题"),
    "局部样本验证完成": ("❓ 局部样本（无法终判）", "仅为局部样本，未覆盖全部材料，不作最终结论"),
}

# 针对 fail 项的补正建议；未命中走通用兜底。
FIX_SUGGESTIONS = {
    "HR-2.1-LICENSE": "补交企业法人营业执照",
    "HR-2.1-CREDIT": "补交信用中国/政府采购网征信材料（近三年无不良信用记录）",
    "HR-2.1-DECLARATION": "补交法定代表人无重大违法记录声明函（加盖公章）",
    "HR-2.1-COMMITMENT": "补交申报材料真实性承诺书（公章+法定代表人签字）",
    "HR-2.1-FINANCIAL": "补交上一年度主营业务收入或财务证明",
    "HR-2.1-RD-INVESTMENT": "补交上一年度研发投入证明（研发投入财务报表或上级单位研发费用归集证明）",
    "HR-2.3-JOINT": "补交联合申报支持材料三选一（盖章项目合作协议/盖章联合申报协议/盖章的牵头方申报声明）任一份",
    "HR-2.4-BRANCH": "补交总公司专项授权申报证明文件及总公司营业执照复印件",
    "HR-2.5-BUILDING": "补交“正在建设”阶段证明（当前性能指标及应用进展、项目投资及实施进度）",
    "HR-2.5-PLANNED": "补交“计划实施”阶段证明（前期准备及技术可行性、立项与启动条件）",
    "HR-3.1-APPLICATION": "补交申报书（含基本信息表、项目任务书、附件证明）",
}


def conclusion_label(description: str) -> str:
    return description.replace("检查", "").replace("（三选一）", "").strip()


def render_conclusion(rule_results: dict[str, Any]) -> str:
    """Render one user-friendly, format-unified conclusion from rule results."""
    counts = rule_results.get("counts", {})
    buckets: dict[str, list[dict[str, Any]]] = {
        "fail": [], "manual_review": [], "not_assessable": [], "warning": [], "pass": [],
    }
    for item in rule_results.get("results", []):
        buckets.get(item["status"], buckets["pass"]).append(item)

    head, note = VERDICT_LABELS.get(
        rule_results.get("overall_status", ""),
        (rule_results.get("overall_status", "未知"), ""),
    )
    lines = [
        f"## 预审结论：{head}",
        f"> {note}",
        (
            f"> 汇总：通过 {counts.get('pass', 0)} · 不通过 {counts.get('fail', 0)} · "
            f"待人工 {counts.get('manual_review', 0)} · 无法判断 {counts.get('not_assessable', 0)}"
        ),
        "",
    ]
    if buckets["fail"]:
        lines.append("### ❌ 缺什么 / 不符合（必须整改）")
        for item in buckets["fail"]:
            lines.append(f"- **{conclusion_label(item['description'])}**：{item['reason']}")
        lines.append("")
        lines.append("### 🔧 需要补什么（补正建议）")
        for item in buckets["fail"]:
            lines.append(
                f"- {FIX_SUGGESTIONS.get(item['rule_id'], '补正：' + conclusion_label(item['description']))}"
            )
        lines.append("")
    if buckets["warning"]:
        lines.append("### 🟡 建议补正（不阻断）")
        for item in buckets["warning"]:
            lines.append(f"- **{conclusion_label(item['description'])}**：{item['reason']}")
        lines.append("")
    if buckets["manual_review"]:
        lines.append("### ⚠️ 需要人工审查")
        for item in buckets["manual_review"]:
            reason = re.sub(r"\s+", " ", item["reason"]).strip()
            pages = compact_evidence_pages(item.get("evidence_pages"))
            suffix = f"（证据：{pages}）" if pages else ""
            lines.append(f"- **{conclusion_label(item['description'])}**：{reason}{suffix}")
        lines.append("")
    if buckets["not_assessable"]:
        lines.append("### ❓ 暂无法判断（局部样本/解析不全，不等于材料缺失）")
        for item in buckets["not_assessable"]:
            lines.append(f"- {conclusion_label(item['description'])}：{item['reason']}")
        lines.append("")
    lines.append(f"### ✅ 已通过（{len(buckets['pass'])} 项，已确认）")
    lines.append("、".join(conclusion_label(item["description"]) for item in buckets["pass"]) or "（无）")
    lines.append("")
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
