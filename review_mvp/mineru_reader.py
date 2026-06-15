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


def _page_from_blocks(page_no: int, page_size: Any, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    full_text = "\n".join(block["text"] for block in blocks if block["text"])
    return {
        "page": page_no,
        "page_size": page_size,
        "blocks": blocks,
        "full_text": full_text,
        "has_content": bool(full_text or any(block["image_paths"] for block in blocks)),
    }


def pages_from_pdf_info(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Read MinerU middle.json (pdf_info / para_blocks)."""
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
        pages.append(_page_from_blocks(page_no, page.get("page_size"), blocks))
    return pages


def pages_from_content_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read the MinerU Open API content_list format (flat list of page_idx blocks)."""
    by_page: dict[int, list[dict[str, Any]]] = {}
    for fallback_index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        page_no = int(item.get("page_idx", 0)) + 1
        texts: list[str] = []
        for key in ("text", "table_caption", "table_footnote", "image_caption"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())
            elif isinstance(value, list):
                texts.extend(str(part).strip() for part in value if str(part).strip())
        table_html = item.get("table_body") or item.get("html")
        if isinstance(table_html, str) and table_html.strip():
            texts.append(strip_html(table_html))
        text = "\n".join(dict.fromkeys(texts)).strip()
        image_paths = [
            item[key]
            for key in ("img_path", "image_path")
            if isinstance(item.get(key), str) and item[key]
        ]
        if not text and not image_paths:
            continue
        by_page.setdefault(page_no, []).append(
            {
                "block_index": item.get("index", fallback_index),
                "type": item.get("type", "unknown"),
                "bbox": item.get("bbox"),
                "text": text,
                "image_paths": image_paths,
            }
        )
    return [_page_from_blocks(page_no, None, by_page[page_no]) for page_no in sorted(by_page)]


def normalize_mineru_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("pdf_info"):
        pages = pages_from_pdf_info(data)
        meta = data
        source_format = "middle_json"
    else:
        if isinstance(data, list):
            content_list = data
        else:
            content_list = data.get("content_list", [])
        pages = pages_from_content_list(content_list)
        meta = data if isinstance(data, dict) else {}
        source_format = "content_list"
    return {
        "source_file": str(path),
        "metadata": {
            "backend": meta.get("_backend"),
            "version": meta.get("_version_name"),
            "ocr_enabled": meta.get("_ocr_enable"),
            "vlm_ocr_enabled": meta.get("_vlm_ocr_enable"),
            "source_format": source_format,
            "page_count": len(pages),
        },
        "pages": pages,
    }
