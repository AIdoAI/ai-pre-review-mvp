"""计划书（项目任务书）AI 提要 —— P1。

面向专家：把项目任务书按官方 5 段结构压成一页提要，供评审时快速导读。
不碰资质/合规（那是预审的事），只读项目任务书内容。

设计：抽段 → 组提示词 → 调大模型（Qwen，可插拔）→ summary.json/summary.md。
未接大模型时退回"抽取式"骨架（取每段前几句），保证端到端可跑、可先看形态。

铁律：只总结原文、不补充不评价；带证据页码；AI 提要仅供导读，不替代读原文、不作评分依据。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

# 官方任务书 5 段：(序号, 标题, 用于定位的关键词, 该段重点抽取项)
PLAN_SECTIONS = [
    ("一", "项目背景与预期目标", "项目背景", "痛点与业务价值；量化 KPI、测试场景、验收方式"),
    ("二", "解决方案与创新点", "解决方案", "总体方案/技术路线/大模型选型；关键创新点"),
    ("三", "实施基础与落地计划", "实施基础", "行业地位/数据基础/团队；季度节点、交付物、落地模式"),
    ("四", "风险管控及其他", "风险管控", "技术难点、数据安全合规风险及应对"),
    ("五", "需要支持事项", "需要支持", "具体诉求：需哪类头部企业/大模型底座/行业方案（避免‘需要资金’这类含糊表述）"),
]
SECTION_KEYWORDS = [kw for _, _, kw, _ in PLAN_SECTIONS]

LLMFunction = Callable[[str], str]

DISCLAIMER = "AI 提要，仅供专家导读，不替代阅读计划书原文，不作为评分依据。"


def detect_plan_material(materials: list[dict[str, Any]]) -> dict[str, Any] | None:
    """在材料里找最像‘项目任务书’的那份（命中 5 段关键词最多者，至少 2 段）。"""
    best, best_hits = None, 0
    for material in materials:
        text = material.get("full_text", "")
        hits = sum(1 for kw in SECTION_KEYWORDS if kw in text)
        if hits > best_hits:
            best, best_hits = material, hits
    return best if best_hits >= 2 else None


def split_sections(text: str) -> dict[str, dict[str, str]]:
    """按 5 段标题切分；用每段的distinctive关键词定位，容忍 OCR 的空格/标点。"""
    anchors: list[tuple[int, str, str]] = []
    for sid, title, kw, _ in PLAN_SECTIONS:
        m = re.search(r"[一二三四五]?\s*[、，.．]?\s*" + re.escape(kw), text)
        if m:
            anchors.append((m.start(), sid, title))
    anchors.sort()
    out: dict[str, dict[str, str]] = {}
    for i, (pos, sid, title) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        out[sid] = {"title": title, "text": text[pos:end].strip()}
    return out


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。；！？])", re.sub(r"\s+", " ", text))
    return [p.strip() for p in parts if len(p.strip()) > 6]


def extractive_summary(section_text: str, max_sentences: int = 3) -> str:
    """无大模型时的兜底：去掉标题行，取前几句。"""
    body = re.sub(r"^[一二三四五]\s*[、，.．].*?(?:。|\n)", "", section_text, count=1)
    sents = _sentences(body or section_text)
    return "".join(sents[:max_sentences]) or "（该段未抽到有效内容）"


def build_prompt(meta: dict[str, Any], sections: dict[str, dict[str, str]]) -> str:
    """组装给大模型（Qwen）的提示词；要求输出严格 JSON。"""
    sec_blocks = []
    for sid, title, _, focus in PLAN_SECTIONS:
        body = sections.get(sid, {}).get("text", "（原文缺该段）")
        sec_blocks.append(f"【{sid}、{title}】重点关注：{focus}\n原文：\n{body}\n")
    sections_text = "\n".join(sec_blocks)
    return f"""你是大赛评审辅助助手。请把下面这份"项目任务书"压成给专家看的一页提要。

严格要求：
- 只总结原文已有内容，不得补充、推断或评价；原文没有就写"原文未提及"。
- 每段 2–4 句；key_points 抽 1–4 条最关键的事实点（尤其量化指标、季度节点、结对诉求）。
- 用中文，简洁、客观，不要套话。
- 只输出 JSON，不要任何额外文字。

项目信息：名称={meta.get('project_name') or '未识别'}；阶段={meta.get('project_stage') or '未识别'}；赛道={meta.get('track') or '未识别'}。

按此 JSON 模式输出：
{{
  "overview": "一句话总览",
  "sections": [
    {{"id":"一","title":"项目背景与预期目标","summary":"...","key_points":["..."]}},
    {{"id":"二","title":"解决方案与创新点","summary":"...","key_points":["..."]}},
    {{"id":"三","title":"实施基础与落地计划","summary":"...","key_points":["..."]}},
    {{"id":"四","title":"风险管控及其他","summary":"...","key_points":["..."]}},
    {{"id":"五","title":"需要支持事项","summary":"...","key_points":["..."]}}
  ],
  "flags": ["内容过短/缺段/诉求含糊等需提醒专家的点，没有则空数组"]
}}

任务书原文（按段）：
{sections_text}
"""


def summarize_plan(
    materials: list[dict[str, Any]],
    *,
    llm_fn: LLMFunction | None = None,
    project_name: str | None = None,
    project_stage: str | None = None,
    track: str | None = None,
) -> dict[str, Any] | None:
    """产出提要 dict；找不到计划书返回 None。"""
    plan = detect_plan_material(materials)
    if not plan:
        return None
    sections = split_sections(plan.get("full_text", ""))
    meta = {"project_name": project_name, "project_stage": project_stage, "track": track}

    if llm_fn is not None:
        prompt = build_prompt(meta, sections)
        try:
            body = json.loads(_extract_json(llm_fn(prompt)))
            generated_by = "llm"
        except Exception:
            body, generated_by = _scaffold_body(sections), "scaffold(llm解析失败)"
    else:
        body, generated_by = _scaffold_body(sections), "scaffold(extractive)"

    return {
        "project_name": project_name or "未识别",
        "project_stage": project_stage or "未识别",
        "track": track or "未识别",
        "overview": body.get("overview", ""),
        "sections": body.get("sections", []),
        "flags": body.get("flags", []),
        "evidence_pages": plan.get("pages", []),
        "generated_by": generated_by,
        "disclaimer": DISCLAIMER,
    }


def _scaffold_body(sections: dict[str, dict[str, str]]) -> dict[str, Any]:
    out_sections, flags = [], []
    for sid, title, _, _ in PLAN_SECTIONS:
        sec = sections.get(sid)
        if not sec:
            flags.append(f"缺第「{sid}、{title}」段")
            out_sections.append({"id": sid, "title": title, "summary": "原文未提及", "key_points": []})
            continue
        summary = extractive_summary(sec["text"])
        if len(summary) < 15:
            flags.append(f"第「{sid}、{title}」段内容过短")
        out_sections.append({"id": sid, "title": title, "summary": summary, "key_points": []})
    return {"overview": "（抽取式骨架，接入大模型后由模型生成总览）", "sections": out_sections, "flags": flags}


def _extract_json(text: str) -> str:
    """从大模型返回里抠出 JSON 主体（容忍前后多余文字/代码围栏）。"""
    text = text.strip()
    if "```" in text:
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text.strip("` \n"))
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end != -1 else text


def render_summary_md(summary: dict[str, Any]) -> str:
    lines = [
        "# 计划书 AI 提要",
        f"> {summary['disclaimer']}",
        "",
        f"- 项目名称：{summary.get('project_name', '未识别')}",
        f"- 项目阶段：{summary.get('project_stage', '未识别')}　赛道：{summary.get('track', '未识别')}",
        f"- 一句话总览：{summary.get('overview', '') or '（无）'}",
        "",
    ]
    for sec in summary.get("sections", []):
        lines.append(f"## {sec.get('id', '')}、{sec.get('title', '')}")
        lines.append(sec.get("summary", "") or "（无）")
        for kp in sec.get("key_points", []):
            lines.append(f"- {kp}")
        lines.append("")
    if summary.get("flags"):
        lines.append("## ⚠️ 需提醒专家")
        lines.extend(f"- {f}" for f in summary["flags"])
        lines.append("")
    lines.append(f"_生成方式：{summary.get('generated_by', '')}　证据：{('、'.join(summary.get('evidence_pages', [])) or '—')}_")
    return "\n".join(lines)
