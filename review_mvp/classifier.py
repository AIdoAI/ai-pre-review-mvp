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
            "priority": 0,
        }
    return max(candidates, key=lambda item: (item["confidence"], item["priority"]))


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
    for source_key, target_key in (("start_hits", "start_patterns"), ("keyword_hits", "keywords")):
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
        current = None
        for page in document["pages"]:
            page_class = classify_text(page["full_text"], material_types)
            for block_position, block in enumerate(page["blocks"]):
                block_class = classify_block(block, material_types)
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
