"""Generate human-readable Markdown reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import re
import unicodedata


def normalize_company_name(name: str | None) -> str:
    """归一化单位名用于比对：全角→半角（NFKC，含括号/数字/字母）、去所有空白。"""
    if not name:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name))


# ---- 终端友好排版（按显示宽度对齐；Markdown→纯文本）----

def display_width(text: str) -> int:
    """字符显示宽度：东亚宽/全角字符算 2，其余算 1。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def format_table(headers: list[str], rows: list[list[str]], gap: str = "  ") -> str:
    """等宽对齐的纯文本表格（按显示宽度补空格，列间留 gap）。"""
    cols = len(headers)
    widths = [display_width(headers[i]) for i in range(cols)]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], display_width(row[i] if i < len(row) else ""))
    def line(cells: list[str]) -> str:
        return gap.join(_pad(cells[i] if i < len(cells) else "", widths[i]) for i in range(cols))
    sep = gap.join("-" * widths[i] for i in range(cols))
    return "\n".join([line(headers), sep] + [line(r) for r in rows])


def _md_cells(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") else s
    return [c.strip() for c in s.split("|")]


def _is_md_separator(line: str) -> bool:
    return bool(re.match(r"^\s*\|?[\s:|\-]+\|?\s*$", line)) and "-" in line


def _clean_md_line(s: str) -> str:
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s)   # 去标题 #
    s = re.sub(r"^\s{0,3}>\s?", "", s)         # 去引用 >
    return s.replace("**", "").replace("`", "")


def to_terminal(md: str, max_cell: int = 30) -> str:
    """把含 Markdown 表格的报告文本转成终端友好排版：
    短表→等宽对齐表；含长文字的表→清单式（标题 + 缩进键值）；其余去掉 #/**/> 记号。
    """
    lines = md.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].lstrip().startswith("|") and i + 1 < n and _is_md_separator(lines[i + 1]):
            headers = _md_cells(lines[i])
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].lstrip().startswith("|") and not _is_md_separator(lines[i]):
                rows.append(_md_cells(lines[i]))
                i += 1
            maxw = max([display_width(c) for c in headers]
                       + [display_width(c) for r in rows for c in r] + [0])
            if maxw <= max_cell:
                out.append(format_table(headers, rows))
            else:  # 有长单元格 → 清单式
                for r in rows:
                    start, title = 1, (r[0] if r else "")
                    if len(r) > 1 and display_width(r[0]) <= 4:  # 如"序号"列
                        title, start = f"{r[0]}  {r[1]}", 2
                    out.append(f"• {title}")
                    for j in range(start, len(headers)):
                        val = r[j] if j < len(r) else ""
                        if val and val != "—":
                            out.append(f"    {headers[j]}：{val}")
                    out.append("")
        else:
            out.append(_clean_md_line(lines[i]))
            i += 1
    return "\n".join(out)


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
    applicant_header: str | None = None,
) -> str:
    if applicant_header is None:
        applicant_header = applicant_headline(submission.get("_subject_structure", {}), extracted)
    lines = [
        f'# {submission["name"]} - 本地AI预审报告',
        "",
        f'## 🏷️ {applicant_header}',
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


def render_conclusion(rule_results: dict[str, Any], applicant_header: str | None = None) -> str:
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
    lines = [f"## 预审结论：{head}"]
    if applicant_header:
        lines.append(f"### 🏷️ {applicant_header}")
    lines += [
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


STATUS_ICON = {
    "pass": "✅",
    "fail": "❌",
    "manual_review": "⚠️",
    "warning": "🟡",
    "not_assessable": "❓",
}
STATUS_ORDER = {"fail": 4, "manual_review": 3, "warning": 2, "not_assessable": 1, "pass": 0}
FILE_VERDICT = {
    "fail": "❌ 不合规",
    "manual_review": "⚠️ 待人工",
    "warning": "🟡 建议补正",
    "not_assessable": "❓ 无法判断",
    "pass": "✅ 合规",
}

# 规则 → 该规则归属的材料类型（用于无证据页时的归属与“未提供材料”归集）。
RULE_DOCTYPE = {
    "HR-2.1-LICENSE": "营业执照",
    "HR-2.1-CREDIT": "信用记录证明",
    "HR-2.1-DECLARATION": "法定代表人无重大违法记录声明函",
    "HR-2.1-COMMITMENT": "申报材料真实性承诺书",
    "HR-2.1-FINANCIAL": "主营业务收入或财务证明",
    "HR-2.1-RD-INVESTMENT": "研发投入证明",
    "HR-3.1-APPLICATION": "申报书",
    "HR-2.5-BUILDING": "正在建设阶段证明",
    "HR-2.5-PLANNED": "计划实施阶段证明",
    "HR-2.4-BRANCH": "分支机构专项授权文件",
    "HR-2.3-JOINT": "联合申报支持材料",
    "HR-6.1": "申报材料真实性承诺书",
    "HR-6.3": "申报材料真实性承诺书",
    "MR-COMMITMENT-CONTENT": "申报材料真实性承诺书",
    "MR-COMMITMENT-VISUAL": "申报材料真实性承诺书",
    "MR-ENTITY-TYPE": "营业执照",
}
GROUP_MEMBERS = {"联合申报支持材料": ["项目合作协议", "联合申报协议", "牵头方申报声明"]}
# 审核项标准（简述）。
STANDARD_HINTS = {
    "HR-2.1-LICENSE": "企业法人营业执照（主体资格）",
    "HR-2.1-CREDIT": "信用中国/政府采购网征信，近三年无不良记录",
    "HR-2.1-DECLARATION": "法定代表人无重大违法记录声明函（公章）",
    "HR-2.1-COMMITMENT": "申报材料真实性承诺书（公章+签字）",
    "HR-2.1-FINANCIAL": "上一年度主营业务收入/财务证明",
    "HR-2.1-RD-INVESTMENT": "上一年度研发投入证明（独立成证）",
    "HR-3.1-APPLICATION": "申报书（基本信息表+任务书+附件）",
    "HR-2.5-BUILDING": "正在建设阶段证明",
    "HR-2.5-PLANNED": "计划实施阶段证明",
    "HR-2.4-BRANCH": "总公司专项授权及营业执照复印件",
    "HR-2.3-JOINT": "联合申报支持材料三选一（均须盖章）",
    "HR-6.1": "承诺书联系人/电话非空",
    "HR-6.3": "承诺书日期年份为2026",
    "MR-COMMITMENT-CONTENT": "承诺书关键要素完整",
    "MR-COMMITMENT-VISUAL": "承诺书签字/公章真伪",
    "MR-ENTITY-TYPE": "国资身份（股权穿透/上级单位）",
    "MR-CLASSIFICATION": "材料分类置信度",
}
CATEGORY_GROUP = {
    "主体资格证明": "资格合规",
    "合规证明": "资格合规",
    "财务证明": "财务证明",
    "财税辅助证明": "财务证明",
    "研发能力证明": "研发资质",
    "荣誉资质证明": "研发资质",
    "行业资质证明": "研发资质",
    "特殊情形证明": "联合/特殊情形",
    "申报主体材料": "申报主体",
    "项目阶段证明": "阶段证明",
}


def _clean_desc(description: str) -> str:
    return description.replace("检查", "").replace("（三选一）", "").strip()


# 用于抽取"申报主体名称"的可信材料类型（与 rule_engine 跨文件一致性同源）。
TRUSTED_NAME_TYPES = {
    "营业执照", "申报材料真实性承诺书", "申报书",
    "法定代表人无重大违法记录声明函", "信用记录证明",
}
_PLACEHOLDER_HINTS = ("待核", "待确认", "待补", "示例", "申报单位一", "申报单位二", "联合单位一", "联合单位二")


def _looks_placeholder(name: str | None) -> bool:
    return (not name) or any(hint in name for hint in _PLACEHOLDER_HINTS) or name.strip() == "申报单位"


def _resolve_main_unit(
    subject_structure: dict[str, Any], extracted: list[dict[str, Any]] | None,
) -> tuple[str, str, str, str]:
    """返回 (role, name, note, declaration_type)；名称优先取材料抽取，其次表单。"""
    entities = subject_structure.get("entities", []) if subject_structure else []
    declaration_type = (subject_structure or {}).get("declaration_type", "unspecified")
    if declaration_type == "joint":
        role = "牵头单位"
        main = next((e for e in entities if e.get("declaration_role") == "lead"), None)
    elif declaration_type == "independent":
        role = "申报单位"
        main = next((e for e in entities if e.get("declaration_role") in {"applicant", "lead"}), None)
    else:
        role = "申报主体"
        main = entities[0] if entities else None
    form_name = main.get("entity_name") if main else None

    counts: dict[str, int] = {}
    for item in extracted or []:
        if item.get("document_type") in TRUSTED_NAME_TYPES:
            value = item.get("fields", {}).get("company_name", {}).get("value")
            if value:
                counts[value] = counts.get(value, 0) + 1
    extracted_name = max(counts, key=counts.get) if counts else None

    if not _looks_placeholder(form_name):
        name, note = form_name, ""
        if extracted_name and normalize_company_name(extracted_name) != normalize_company_name(form_name):
            note = f"（材料中识别为「{extracted_name}」，需核对）"
    elif extracted_name:
        name = extracted_name
        note = "（取自材料抽取）" if len(counts) <= 1 else "（材料中存在多个名称，需人工确认）"
    else:
        name, note = "未能自动确定", "（需人工 / 补充基础资料）"
    return role, name, note, declaration_type


def applicant_unit_name(subject_structure: dict[str, Any], extracted: list[dict[str, Any]] | None) -> str:
    """仅返回主体单位名称（含必要标注），用于批量摘要列。"""
    _, name, note, _ = _resolve_main_unit(subject_structure, extracted)
    return f"{name}{note}"


def applicant_headline(subject_structure: dict[str, Any], extracted: list[dict[str, Any]] | None) -> str:
    """生成"牵头单位/申报主体"醒目行；名称优先取材料抽取，其次表单。"""
    role, name, note, declaration_type = _resolve_main_unit(subject_structure, extracted)
    suffix = "（独立申报）" if declaration_type == "independent" else (
        "（联合申报）" if declaration_type == "joint" else "")
    return f"{role}：{name}{note}{suffix}"


def _source_to_original(materials: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for material in materials:
        for segment in material.get("segments", []):
            source = segment.get("source_file")
            if source:
                mapping[source] = segment.get("original_file") or Path(source).name
    return mapping


def _rule_files(rule: dict[str, Any], source_to_original: dict[str, str]) -> list[str]:
    files: list[str] = []
    for token in rule.get("evidence_pages", []):
        source = token.rsplit("#P", 1)[0]
        original = source_to_original.get(source)
        if original and original not in files:
            files.append(original)
    return files


def _file_category(file_mats: list[dict[str, Any]]) -> str:
    if not file_mats:
        return "—"
    priority = {
        "required": 3, "conditional_required": 3, "group_member": 3,
        "recommended": 2, "auxiliary": 1,
    }
    best = max(file_mats, key=lambda m: priority.get(m.get("requirement", "unknown"), 0))
    return CATEGORY_GROUP.get(best["category"], best["category"])


def render_per_file_report(
    submission: dict[str, Any],
    materials: list[dict[str, Any]],
    rule_results: dict[str, Any],
    applicant_header: str | None = None,
) -> str:
    """Auto-generate a per-file review (detailed tables + overview)."""
    source_to_original = _source_to_original(materials)

    files: list[str] = []
    for item in submission.get("_parse_details", []):
        original = item.get("original_file") or Path(item["path"]).name
        if original not in files:
            files.append(original)
    for material in materials:
        for segment in material.get("segments", []):
            original = segment.get("original_file")
            if original and original not in files:
                files.append(original)

    file_materials: dict[str, list[dict[str, Any]]] = {original: [] for original in files}
    for material in materials:
        for original in {
            seg.get("original_file") for seg in material.get("segments", []) if seg.get("original_file")
        }:
            file_materials.setdefault(original, []).append(material)

    parse_status = {
        (item.get("original_file") or Path(item["path"]).name): item.get("parse_status", "success")
        for item in submission.get("_parse_details", [])
    }

    file_rules: dict[str, list[dict[str, Any]]] = {original: [] for original in files}
    missing_rules: list[dict[str, Any]] = []
    global_rules: list[dict[str, Any]] = []
    for rule in rule_results.get("results", []):
        targets = _rule_files(rule, source_to_original)
        if not targets:
            doctype = RULE_DOCTYPE.get(rule["rule_id"])
            if doctype:
                members = GROUP_MEMBERS.get(doctype, [doctype])
                targets = [
                    original for original in files
                    if any(m["document_type"] in members for m in file_materials.get(original, []))
                ]
                if not targets:
                    missing_rules.append(rule)
                    continue
            else:
                global_rules.append(rule)
                continue
        for original in targets:
            file_rules.setdefault(original, []).append(rule)

    def verdict(original: str) -> tuple[str, int]:
        rules = file_rules.get(original, [])
        mats = file_materials.get(original, [])
        if parse_status.get(original, "success") != "success":
            return "⚠️ 解析未完整（转人工）", 3
        if mats and all(m.get("requirement") in {"recommended", "auxiliary"} for m in mats):
            return "✅ 识别（辅助）", 0
        if not rules:
            return ("❓ 无法判断", 1) if not mats else ("✅ 合规", 0)
        worst = max(STATUS_ORDER[r["status"]] for r in rules)
        label = next(k for k, v in STATUS_ORDER.items() if v == worst)
        return FILE_VERDICT[label], worst

    lines = ["# 逐文件审查", ""]
    if applicant_header:
        lines += [f"### 🏷️ {applicant_header}", ""]
    overview: list[tuple[int, str, str, str, str]] = []
    for index, original in enumerate(files, start=1):
        mats = file_materials.get(original, [])
        rules = file_rules.get(original, [])
        v_label, _ = verdict(original)
        category = _file_category(mats)
        types = "、".join(dict.fromkeys(m["document_type"] for m in mats)) or "（未识别到材料）"
        lines.append(f"## {index:02d} {original} → {v_label}")
        lines.append(f"- 识别材料：{types}")
        if parse_status.get(original, "success") != "success":
            lines.append(f"- ⚠️ 解析状态：{parse_status.get(original)}，相关判断转人工，不据此判缺失。")
        if rules:
            lines.append("")
            lines.append("| 审核项 | 标准 | 审核结果 |")
            lines.append("|---|---|---|")
            for rule in rules:
                standard = STANDARD_HINTS.get(rule["rule_id"], "—")
                reason = re.sub(r"\s+", " ", rule["reason"]).replace("|", " ").strip()
                lines.append(
                    f"| {_clean_desc(rule['description'])} | {standard} | "
                    f"{STATUS_ICON[rule['status']]} {reason} |"
                )
        lines.append("")
        # 关键问题（用于总览）
        nonpass = [r for r in rules if r["status"] != "pass"]
        if parse_status.get(original, "success") != "success":
            issue = "解析未完整，转人工复核（不据此判缺失）"
        elif nonpass:
            issue = "；".join(_clean_desc(r["description"]) for r in nonpass[:3])
        elif mats and all(m.get("requirement") in {"recommended", "auxiliary"} for m in mats):
            issue = "辅助材料，已识别，不参与缺失"
        elif mats:
            issue = "必要项已确认"
        else:
            issue = "未识别到材料"
        overview.append((index, original, category, v_label, issue))

    if missing_rules:
        lines.append("## 未提供 / 无法判断的材料")
        lines.append("")
        lines.append("| 材料 | 标准 | 结论 |")
        lines.append("|---|---|---|")
        for rule in missing_rules:
            standard = STANDARD_HINTS.get(rule["rule_id"], "—")
            reason = re.sub(r"\s+", " ", rule["reason"]).replace("|", " ").strip()
            lines.append(
                f"| {_clean_desc(rule['description'])} | {standard} | "
                f"{STATUS_ICON[rule['status']]} {reason} |"
            )
        lines.append("")

    if global_rules:
        lines.append("## 跨文件 / 主体结构检查")
        lines.append("")
        lines.append("| 检查项 | 结果 |")
        lines.append("|---|---|")
        for rule in global_rules:
            reason = re.sub(r"\s+", " ", rule["reason"]).replace("|", " ").strip()
            lines.append(f"| {_clean_desc(rule['description'])} | {STATUS_ICON[rule['status']]} {reason} |")
        lines.append("")

    lines.append("## 📊 总览")
    lines.append("")
    lines.append("| 序号 | 文件 | 类别 | 审核结果 | 关键问题 |")
    lines.append("|---|---|---|---|---|")
    for index, original, category, v_label, issue in overview:
        lines.append(f"| {index:02d} | {original} | {category} | {v_label} | {issue} |")
    lines.append("")
    return "\n".join(lines)


def render_batch_summary(items: list[dict[str, Any]]) -> str:
    lines = [
        "# AI预审本地MVP批量测试摘要",
        "",
        "| 样本 | 牵头单位/申报主体 | 综合状态 | 识别材料数 | 通过 | 不通过 | 待人工复核 | 无法判断 |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in items:
        counts = item["rule_results"]["counts"]
        unit = applicant_unit_name(
            item.get("subject_structure", {}), item.get("extracted", [])
        ).replace("|", " ")
        lines.append(
            f'| {item["name"]} | {unit} | {item["rule_results"]["overall_status"]} | '
            f'{len(item["materials"])} | {counts["pass"]} | {counts["fail"]} | '
            f'{counts["manual_review"]} | {counts["not_assessable"]} |'
        )
    return "\n".join(lines) + "\n"
