"""Classify and segment normalized MinerU pages into business materials."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def load_material_types(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return sorted(data["types"], key=lambda item: item.get("priority", 0), reverse=True)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def pattern_hits(text: str, patterns: list[str]) -> list[str]:
    value = compact(text)
    return [pattern for pattern in patterns if re.search(pattern, value, re.IGNORECASE)]


def classify_text(text: str, material_types: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for item in material_types:
        starts = pattern_hits(text[:1000], item.get("start_patterns", []))
        keywords = [keyword for keyword in item.get("keywords", []) if keyword.lower() in text.lower()]
        if not starts and not keywords:
            continue
        score = 0.25 + min(len(keywords) * 0.08, 0.32)
        if starts:
            score += 0.42
        score += min(item.get("priority", 0) / 1000, 0.1)
        candidates.append(
            {
                "id": item["id"],
                "document_type": item["name"],
                "category": item["category"],
                "confidence": round(min(score, 0.99), 2),
                "start_hits": starts,
                "keyword_hits": keywords,
                "filename_hits": [],
                "priority": item.get("priority", 0),
            }
        )
    if not candidates:
        return {
            "id": "unclassified",
            "document_type": "待分类材料",
            "category": "待分类",
            "confidence": 0.0,
            "start_hits": [],
            "keyword_hits": [],
            "filename_hits": [],
            "priority": 0,
        }
    return max(candidates, key=lambda item: (item["confidence"], item["priority"]))


FILENAME_HINTS = [
    (r"信用中国|信用报告|政府采购网.*信用", "信用记录证明"),
    (r"联合(?:申报|申请).*协议", "联合申报协议"),
    (r"法定代表人.*无重大违法", "法定代表人无重大违法记录声明函"),
    (r"申报材料真实性承诺|真实性承诺书", "申报材料真实性承诺书"),
    (r"营业执照", "营业执照"),
    (r"研发能力|研发资质|专利|软著|软件著作权", "研发资质证明"),
    (r"研发证明函", "行业能力或项目经验说明"),
    (r"相关荣誉|荣誉证明|获奖", "荣誉资质证明"),
]


def classify_filename(filename: str, material_types: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a conservative single-purpose filename hint."""
    if re.search(r"样本\d|测试(?:文件|材料|样例)?", filename, re.IGNORECASE):
        return None
    matches = [
        (pattern, document_type)
        for pattern, document_type in FILENAME_HINTS
        if re.search(pattern, filename, re.IGNORECASE)
    ]
    distinct_types = {document_type for _, document_type in matches}
    if len(distinct_types) != 1:
        return None
    document_type = distinct_types.pop()
    material_type = next(
        (item for item in material_types if item["name"] == document_type),
        None,
    )
    if not material_type:
        return None
    return {
        "id": material_type["id"],
        "document_type": material_type["name"],
        "category": material_type["category"],
        "confidence": 0.98,
        "start_hits": [],
        "keyword_hits": [],
        "filename_hits": [pattern for pattern, _ in matches],
        "priority": material_type.get("priority", 0),
    }


def apply_filename_hint(
    classification: dict[str, Any],
    filename_hint: dict[str, Any] | None,
) -> dict[str, Any]:
    if not filename_hint:
        classification.setdefault("filename_hits", [])
        return classification
    if classification["document_type"] == filename_hint["document_type"]:
        classification["filename_hits"] = filename_hint["filename_hits"]
        classification["confidence"] = max(
            classification["confidence"],
            filename_hint["confidence"],
        )
        return classification
    if (
        classification["document_type"] == "待分类材料"
        or classification["confidence"] < filename_hint["confidence"]
    ):
        return filename_hint.copy()
    classification.setdefault("filename_hits", [])
    return classification


def classify_block(block: dict[str, Any], material_types: list[dict[str, Any]]) -> dict[str, Any]:
    return classify_text(block.get("text", ""), material_types)


def append_block(
    material: dict[str, Any],
    source_file: str,
    original_file: str,
    page: int,
    block: dict[str, Any],
) -> None:
    material["segments"].append(
        {
            "source_file": source_file,
            "original_file": original_file,
            "page": page,
            "block_index": block["block_index"],
            "bbox": block.get("bbox"),
            "mineru_type": block.get("type"),
        }
    )
    if block.get("text"):
        material["_texts"].append(block["text"])
    material["_last_source"] = source_file
    material["_last_page"] = page


def strengthen_material(material: dict[str, Any], classification: dict[str, Any]) -> None:
    if classification["document_type"] != material["document_type"]:
        return
    material["confidence"] = max(material["confidence"], classification["confidence"])
    for source_key, target_key in (
        ("start_hits", "start_patterns"),
        ("keyword_hits", "keywords"),
        ("filename_hits", "filename_patterns"),
    ):
        current = material["classification_evidence"][target_key]
        material["classification_evidence"][target_key] = list(
            dict.fromkeys(current + classification[source_key])
        )


def new_material(classification: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_id": "",
        "document_type": classification["document_type"],
        "category": classification["category"],
        "confidence": classification["confidence"],
        "classification_evidence": {
            "start_patterns": classification["start_hits"],
            "keywords": classification["keyword_hits"],
            "filename_patterns": classification.get("filename_hits", []),
        },
        "segments": [],
        "_texts": [],
        "_last_source": None,
        "_last_page": None,
    }


def finalize(material: dict[str, Any], index: int) -> dict[str, Any]:
    text = "\n".join(material.pop("_texts")).strip()
    material.pop("_last_source", None)
    material.pop("_last_page", None)
    material["material_id"] = f"M{index:03d}"
    material["pages"] = sorted(
        {
            f'{segment["source_file"]}#P{segment["page"]}'
            for segment in material["segments"]
        }
    )
    material["full_text"] = text
    material["text_preview"] = re.sub(r"\s+", " ", text)[:400]
    return material


def segment_documents(
    normalized_documents: list[dict[str, Any]],
    material_types: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for document in normalized_documents:
        source_file = document["source_file"]
        original_file = document.get("original_file") or Path(source_file).name
        filename_hint = classify_filename(original_file, material_types)
        current = None
        for page in document["pages"]:
            page_class = apply_filename_hint(
                classify_text(page["full_text"], material_types),
                filename_hint,
            )
            for block_position, block in enumerate(page["blocks"]):
                block_class = apply_filename_hint(
                    classify_block(block, material_types),
                    filename_hint,
                )
                strong_start = bool(block_class["start_hits"])

                # Image-only or weak first blocks often precede the visible
                # title. Use the whole-page classification to establish a new
                # material boundary before attaching those blocks.
                if (
                    block_position == 0
                    and current
                    and page_class["document_type"] != "待分类材料"
                    and page_class["document_type"] != current["document_type"]
                    and page_class["confidence"] >= 0.70
                ):
                    materials.append(finalize(current, len(materials) + 1))
                    current = new_material(page_class)

                if block_class["document_type"] == "待分类材料":
                    if current is None:
                        selected = page_class
                        current = new_material(selected)
                    append_block(current, source_file, original_file, page["page"], block)
                    continue

                if (
                    current
                    and current["document_type"] == "无效或测试材料"
                    and current["_last_source"] == source_file
                    and current["_last_page"] == page["page"]
                ):
                    append_block(current, source_file, original_file, page["page"], block)
                    continue

                should_start_new = current is None
                if current and strong_start and block_class["document_type"] != current["document_type"]:
                    should_start_new = True
                if current and block_position == 0 and page_class["document_type"] != current["document_type"]:
                    if page_class["confidence"] >= 0.75:
                        block_class = page_class
                        should_start_new = True

                if should_start_new:
                    if current:
                        materials.append(finalize(current, len(materials) + 1))
                    current = new_material(block_class)
                else:
                    strengthen_material(current, block_class)
                append_block(current, source_file, original_file, page["page"], block)

        if current:
            materials.append(finalize(current, len(materials) + 1))
            current = None

    return materials
