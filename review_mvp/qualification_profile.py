"""Build a filename-only qualification profile without opening document contents."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


CATEGORY_ORDER = [
    "patent",
    "software_copyright",
    "paper",
    "award",
    "platform_approval",
    "certification",
    "license",
    "other",
]

CATEGORY_LABELS = {
    "patent": "专利候选",
    "software_copyright": "软件著作权候选",
    "paper": "论文或研究成果候选",
    "award": "奖项或荣誉候选",
    "platform_approval": "平台或项目获批候选",
    "certification": "认证或认定候选",
    "license": "许可证候选",
    "other": "待分类文件",
}

SUMMARY_LABELS = {
    "patent": "专利候选",
    "software_copyright": "软件著作权候选",
    "paper": "论文或研究成果",
    "award": "奖项或荣誉",
    "platform_approval": "平台或项目获批",
    "certification": "认证或认定",
    "license": "许可证",
    "other": "待分类文件",
}

IGNORED_FILENAMES = {".DS_Store", "Thumbs.db"}


def load_qualification_rules(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_filename(filename: str) -> dict[str, str]:
    """Return a display title and a conservative key used for deduplication."""
    stem = unicodedata.normalize("NFKC", Path(filename).stem).strip()
    stem = re.sub(r"^(?:副本|扫描件|复印件)[-_—\s]*", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^(?:电子证书|发明专利证书|专利证书)[-_—:\s]*", "", stem, flags=re.IGNORECASE)

    previous = None
    while previous != stem:
        previous = stem
        stem = re.sub(r"[\s_-]*(?:\(\d+\)|（\d+）)$", "", stem)
        stem = re.sub(r"[\s_-]*A4$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[\s_-]*(?:扫描件|副本|复印件)$", "", stem, flags=re.IGNORECASE)

    display_title = re.sub(r"\s+", " ", stem).strip(" _-—")
    key = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", display_title.lower())
    return {"display_title": display_title, "key": key}


def keyword_hits(filename: str, keywords: list[str]) -> list[str]:
    lowered = filename.lower()
    hits: list[str] = []
    for keyword in keywords:
        lowered_keyword = keyword.lower()
        if lowered_keyword == "ai":
            if re.search(r"(?<![a-z0-9])ai(?![a-z0-9])", lowered):
                hits.append(keyword)
        elif lowered_keyword in lowered:
            hits.append(keyword)
    return hits


def _looks_like_paper(filename: str, rules: dict[str, Any]) -> tuple[bool, list[str]]:
    paper_rules = rules["paper_filename"]
    hits = keyword_hits(filename, paper_rules.get("keywords", []))
    latin_words = re.findall(r"[A-Za-z][A-Za-z0-9γ-]*", filename)
    looks_english_title = len(latin_words) >= paper_rules.get("minimum_latin_words", 6)
    evidence = hits + (["英文长标题"] if looks_english_title else [])
    return bool(evidence), evidence


def classify_filename(filename: str, rules: dict[str, Any]) -> dict[str, Any]:
    """Classify one filename. The result is always an unverified candidate."""
    normalized = normalize_filename(filename)

    for category in rules["categories"]:
        hits = keyword_hits(filename, category.get("keywords", []))
        if hits:
            return {
                "qualification_type": category["id"],
                "type_label": category["label"],
                "classification_confidence": category["confidence"],
                "classification_evidence": hits,
                "classification_note": "文件名包含明确类型关键词",
                **normalized,
            }

    if "电子证书" in filename:
        for pattern in rules.get("electronic_certificate_patent_title_patterns", []):
            if re.search(pattern, normalized["display_title"], re.IGNORECASE):
                return {
                    "qualification_type": "patent",
                    "type_label": CATEGORY_LABELS["patent"],
                    "classification_confidence": 0.72,
                    "classification_evidence": ["电子证书", pattern],
                    "classification_note": "标题形态类似专利，但需打开正文确认电子证书类型",
                    **normalized,
                }

    looks_like_paper, paper_evidence = _looks_like_paper(filename, rules)
    if looks_like_paper:
        return {
            "qualification_type": "paper",
            "type_label": CATEGORY_LABELS["paper"],
            "classification_confidence": rules["paper_filename"]["confidence"],
            "classification_evidence": paper_evidence,
            "classification_note": "文件名形态符合论文或研究成果标题",
            **normalized,
        }

    return {
        "qualification_type": "other",
        "type_label": CATEGORY_LABELS["other"],
        "classification_confidence": 0.0,
        "classification_evidence": [],
        "classification_note": "仅凭文件名无法分类",
        **normalized,
    }


def detect_ai_relevance(filename: str, rules: dict[str, Any]) -> dict[str, Any]:
    hits = keyword_hits(filename, rules.get("ai_keywords", []))
    return {"ai_related": bool(hits), "ai_evidence": hits}


def _is_duplicate(
    item: dict[str, Any],
    canonical: dict[str, Any],
    fuzzy_threshold: float,
) -> bool:
    if item["qualification_type"] != canonical["qualification_type"]:
        return False
    if item["normalized_key"] == canonical["normalized_key"]:
        return True
    if not item["normalized_key"] or not canonical["normalized_key"]:
        return False
    return SequenceMatcher(
        None,
        item["normalized_key"],
        canonical["normalized_key"],
    ).ratio() >= fuzzy_threshold


def deduplicate_items(items: list[dict[str, Any]], fuzzy_threshold: float) -> list[dict[str, Any]]:
    canonical_items: list[dict[str, Any]] = []
    for item in items:
        duplicate_of = None
        for canonical in canonical_items:
            if _is_duplicate(item, canonical, fuzzy_threshold):
                duplicate_of = canonical
                break

        if duplicate_of:
            item["duplicate_group_id"] = duplicate_of["duplicate_group_id"]
            item["duplicate_of"] = duplicate_of["item_id"]
            duplicate_of["duplicate_count"] += 1
            duplicate_of["duplicate_filenames"].append(item["raw_filename"])
        else:
            item["duplicate_group_id"] = f'QG{len(canonical_items) + 1:03d}'
            item["duplicate_of"] = None
            item["duplicate_count"] = 1
            item["duplicate_filenames"] = [item["raw_filename"]]
            canonical_items.append(item)
    return canonical_items


def _category_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(item["qualification_type"] for item in items)
    return {category: counter.get(category, 0) for category in CATEGORY_ORDER}


def build_summary(entity_name: str, stats: dict[str, Any]) -> str:
    counts = stats["deduplicated_category_counts"]
    parts = [
        f"{SUMMARY_LABELS[category]}{counts[category]}项"
        for category in CATEGORY_ORDER
        if counts.get(category)
    ]
    category_text = "、".join(parts) if parts else "暂未识别出明确资质类别"
    sentence_1 = (
        f"按文件名初步识别，{entity_name}共提交{stats['raw_file_count']}个资质或成果文件，"
        f"去重后{stats['deduplicated_item_count']}项，其中{category_text}。"
    )
    sentence_2 = (
        f"初步发现{stats['ai_related_candidate_count']}项与人工智能、智能检测或相关算法有关的成果候选。"
        if stats["ai_related_candidate_count"]
        else "文件名中暂未发现明确的人工智能相关成果关键词。"
    )
    sentence_3 = "以上为文件名级待核验候选画像，权属、有效性、证书类型及实际含金量仍需结合正文或人工核验。"
    return sentence_1 + sentence_2 + sentence_3


def _build_profile(
    root: Path,
    files: list[Path],
    rules: dict[str, Any],
    entity_name: str,
) -> dict[str, Any]:
    files = sorted(
        (path for path in files if path.name not in IGNORED_FILENAMES),
        key=lambda path: str(path.relative_to(root)).lower(),
    )

    items: list[dict[str, Any]] = []
    for index, path in enumerate(files, start=1):
        classification = classify_filename(path.name, rules)
        ai_relevance = detect_ai_relevance(path.name, rules)
        items.append(
            {
                "item_id": f"Q{index:03d}",
                "raw_filename": path.name,
                "relative_path": str(path.relative_to(root)),
                "normalized_title": classification.pop("display_title"),
                "normalized_key": classification.pop("key"),
                **classification,
                **ai_relevance,
                "verification_status": "filename_inferred",
                "needs_content_or_manual_verification": True,
            }
        )

    canonical_items = deduplicate_items(
        items,
        float(rules.get("deduplication", {}).get("fuzzy_threshold", 0.96)),
    )
    stats = {
        "raw_file_count": len(items),
        "deduplicated_item_count": len(canonical_items),
        "duplicate_file_count": len(items) - len(canonical_items),
        "verified_item_count": 0,
        "raw_category_counts": _category_counts(items),
        "deduplicated_category_counts": _category_counts(canonical_items),
        "ai_related_candidate_count": sum(item["ai_related"] for item in canonical_items),
        "unclassified_candidate_count": sum(
            item["qualification_type"] == "other" for item in canonical_items
        ),
        "items_requiring_content_or_manual_verification": len(canonical_items),
    }
    return {
        "profile_version": "1.0",
        "source_mode": "filename_only",
        "entity_name": entity_name,
        "source_root": str(root),
        "disclaimer": "本画像只读取文件名和目录结构，不读取文档正文；所有分类均为待核验候选。",
        "stats": stats,
        "summary": build_summary(entity_name, stats),
        "canonical_items": canonical_items,
        "all_files": items,
    }


def scan_qualification_folder(
    root: Path,
    rules: dict[str, Any],
    entity_name: str | None = None,
) -> dict[str, Any]:
    """Scan one entity folder recursively; document contents are never opened."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Qualification folder does not exist: {root}")
    files = [path for path in root.rglob("*") if path.is_file()]
    return _build_profile(root, files, rules, entity_name or root.name)


def scan_qualification_batch(root: Path, rules: dict[str, Any]) -> dict[str, Any]:
    """Treat each immediate child folder as one entity and keep root files unassigned."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Qualification batch folder does not exist: {root}")

    profiles = [
        scan_qualification_folder(child, rules, child.name)
        for child in sorted(root.iterdir(), key=lambda path: path.name.lower())
        if child.is_dir()
    ]
    root_files = [path for path in root.iterdir() if path.is_file()]
    if any(path.name not in IGNORED_FILENAMES for path in root_files):
        profiles.append(_build_profile(root, root_files, rules, "未归属文件"))

    return {
        "batch_version": "1.0",
        "source_mode": "filename_only_first_level_grouping",
        "source_root": str(root),
        "entity_count": len(profiles),
        "profiles": profiles,
    }


def render_qualification_profile(profile: dict[str, Any]) -> str:
    stats = profile["stats"]
    lines = [
        f'# {profile["entity_name"]} - 文件名资质画像',
        "",
        f'- 数据来源：`{profile["source_mode"]}`',
        f'- 原始文件数：{stats["raw_file_count"]}',
        f'- 去重后候选项：{stats["deduplicated_item_count"]}',
        f'- 重复文件数：{stats["duplicate_file_count"]}',
        f'- AI相关候选项：{stats["ai_related_candidate_count"]}',
        f'- 待正文或人工核验：{stats["items_requiring_content_or_manual_verification"]}',
        "",
        "## 专家摘要",
        "",
        profile["summary"],
        "",
        "## 分类统计",
        "",
        "| 类别 | 原始文件数 | 去重后候选数 |",
        "|---|---:|---:|",
    ]
    for category in CATEGORY_ORDER:
        raw_count = stats["raw_category_counts"].get(category, 0)
        dedup_count = stats["deduplicated_category_counts"].get(category, 0)
        if raw_count or dedup_count:
            lines.append(f"| {CATEGORY_LABELS[category]} | {raw_count} | {dedup_count} |")

    lines.extend(
        [
            "",
            "## 候选明细",
            "",
            "| 文件名 | 初步分类 | 置信度 | AI相关 | 重复数 | 分类依据 |",
            "|---|---|---:|---|---:|---|",
        ]
    )
    for item in profile["canonical_items"]:
        evidence = "、".join(item["classification_evidence"]).replace("|", " ")
        lines.append(
            f'| {item["raw_filename"].replace("|", " ")} | {item["type_label"]} | '
            f'{item["classification_confidence"]:.2f} | '
            f'{"是" if item["ai_related"] else "否"} | {item["duplicate_count"]} | {evidence} |'
        )

    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- 本模块不读取文件正文，不确认资质权属、有效期、证书真伪或含金量。",
            "- 本模块不参与必要材料缺失判断，也不得触发形式审查打回。",
            "- `电子证书`等泛称文件只作为低置信度候选，必须结合正文或人工核验。",
            "",
        ]
    )
    return "\n".join(lines)


def render_qualification_batch(batch: dict[str, Any]) -> str:
    lines = [
        "# 文件名资质画像批量摘要",
        "",
        f'- 企业或分组数：{batch["entity_count"]}',
        "",
        "| 企业或分组 | 原始文件 | 去重后候选 | AI相关候选 | 待分类 |",
        "|---|---:|---:|---:|---:|",
    ]
    for profile in batch["profiles"]:
        stats = profile["stats"]
        lines.append(
            f'| {profile["entity_name"].replace("|", " ")} | {stats["raw_file_count"]} | '
            f'{stats["deduplicated_item_count"]} | {stats["ai_related_candidate_count"]} | '
            f'{stats["unclassified_candidate_count"]} |'
        )
    lines.extend(["", "## 各企业摘要", ""])
    for profile in batch["profiles"]:
        lines.extend([f'### {profile["entity_name"]}', "", profile["summary"], ""])
    return "\n".join(lines)
