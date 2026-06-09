"""Read MinerU middle.json-style output into normalized pages and blocks."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def collect_text(node: Any) -> list[str]:
    result: list[str] = []
    if isinstance(node, dict):
        content = node.get("content")
        if isinstance(content, str) and content.strip():
            result.append(content.strip())
        table_html = node.get("html")
        if isinstance(table_html, str) and table_html.strip():
            result.append(strip_html(table_html))
        for key in ("blocks", "lines", "spans"):
            if key in node:
                result.extend(collect_text(node[key]))
    elif isinstance(node, list):
        for item in node:
            result.extend(collect_text(item))
    return result


def collect_image_paths(node: Any) -> list[str]:
    result: list[str] = []
    if isinstance(node, dict):
        image_path = node.get("image_path")
        if isinstance(image_path, str):
            result.append(image_path)
        for key in ("blocks", "lines", "spans"):
            if key in node:
                result.extend(collect_image_paths(node[key]))
    elif isinstance(node, list):
        for item in node:
            result.extend(collect_image_paths(item))
    return result


def normalize_mineru_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pages: list[dict[str, Any]] = []
    for page in data.get("pdf_info", []):
        page_no = int(page.get("page_idx", 0)) + 1
        source_blocks = page.get("para_blocks") or page.get("preproc_blocks") or []
        blocks: list[dict[str, Any]] = []
        for fallback_index, block in enumerate(source_blocks):
            texts = list(dict.fromkeys(collect_text(block)))
            text = "\n".join(texts).strip()
            image_paths = list(dict.fromkeys(collect_image_paths(block)))
            if not text and not image_paths:
                continue
            blocks.append(
                {
                    "block_index": block.get("index", fallback_index),
                    "type": block.get("type", "unknown"),
                    "bbox": block.get("bbox"),
                    "text": text,
                    "image_paths": image_paths,
                }
            )
        full_text = "\n".join(block["text"] for block in blocks if block["text"])
        pages.append(
            {
                "page": page_no,
                "page_size": page.get("page_size"),
                "blocks": blocks,
                "full_text": full_text,
                "has_content": bool(full_text or any(block["image_paths"] for block in blocks)),
            }
        )
    return {
        "source_file": str(path),
        "metadata": {
            "backend": data.get("_backend"),
            "version": data.get("_version_name"),
            "ocr_enabled": data.get("_ocr_enable"),
            "vlm_ocr_enabled": data.get("_vlm_ocr_enable"),
            "page_count": len(pages),
        },
        "pages": pages,
    }
