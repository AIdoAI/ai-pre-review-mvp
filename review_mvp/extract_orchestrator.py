"""Tiered extraction orchestrator: local text-layer → selective MinerU OCR → human.

Decouples extraction cost from the review layer so one slow scan never blocks a
whole submission. Each file gets a tier chain and a per-file time budget; effort
is role-aware (auxiliary materials never trigger heavy OCR). Output is
MinerU-compatible JSON (content_list) that ``mineru_reader`` already understands;
files that cannot be parsed are returned with ``parse_status="failed"`` so the
rule engine routes them to manual review (never "missing").

Tiers
  0. Local text layer  —— poppler ``pdftotext``, no network, instant.
  1. (reserved) MinerU flash-extract —— markdown only, not wired yet.
  2. MinerU precision ``extract -f json``（按需 ``--ocr``）—— scans / images.
  3. Failed / timeout —— 转人工。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# 文件名 → 角色（粗判，决定是否值得花重 OCR）。辅助材料不参与缺失判定。
AUXILIARY_FILENAME_HINTS = (
    "荣誉", "研发能力", "专利", "软著", "软件著作权", "奖", "资质证书", "证书",
)
TEXT_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def guess_role(filename: str) -> str:
    """auxiliary（仅记录，不值得 OCR）/ required（走完整链）。"""
    if any(hint in filename for hint in AUXILIARY_FILENAME_HINTS):
        return "auxiliary"
    return "required"


def pdf_pages_text(path: Path, timeout: int = 60) -> list[str] | None:
    """本地文字层抽取（poppler pdftotext）。返回逐页文本；不可用时返回 None。"""
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.split("\f")  # pdftotext 以换页符分页


def text_pages_to_content_list(pages: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, text in enumerate(pages):
        cleaned = text.strip()
        if cleaned:
            items.append({"page_idx": index, "type": "text", "text": cleaned})
    return items


def text_coverage(pages: list[str]) -> float:
    if not pages:
        return 0.0
    nonempty = sum(1 for page in pages if len(page.strip()) > 20)
    return nonempty / len(pages)


def mineru_extract_json(path: Path, out_dir: Path, ocr: bool, timeout: int) -> Path | None:
    """调用 MinerU 精度抽取并产出 content_list JSON；超时/失败返回 None。"""
    cmd = [
        "mineru-open-api", "extract", str(path),
        "-o", str(out_dir), "-f", "json", "--timeout", str(timeout),
    ]
    if ocr:
        cmd.append("--ocr")
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    produced = out_dir / f"{path.stem}.json"
    return produced if produced.exists() else None


def _write_content_list(out_json: Path, items: list[dict[str, Any]]) -> None:
    out_json.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def extract_file(
    path: Path,
    cache_dir: Path,
    *,
    local_timeout: int = 60,
    mineru_timeout: int = 180,
    min_coverage: float = 0.6,
) -> dict[str, Any]:
    """单文件分层抽取，返回带 parse_status 的清单条目。"""
    original = path.name
    role = guess_role(original)
    out_json = cache_dir / f"{path.stem}.json"
    suffix = path.suffix.lower()
    partial_items: list[dict[str, Any]] = []

    # Tier 0：本地文字层（仅 PDF）
    if suffix in TEXT_EXTS:
        pages = pdf_pages_text(path, timeout=local_timeout)
        if pages is not None:
            coverage = text_coverage(pages)
            items = text_pages_to_content_list(pages)
            if items and coverage >= min_coverage:
                _write_content_list(out_json, items)
                return _entry(out_json, original, "success", "local_text",
                              f"本地文字层，覆盖率{coverage:.0%}")
            if role == "auxiliary":
                # 辅助材料不值得 OCR：用已有文字（可能 partial），否则转人工
                if items:
                    _write_content_list(out_json, items)
                    return _entry(out_json, original, "partial", "local_text(aux,no-ocr)",
                                  f"辅助材料，本地文字覆盖率{coverage:.0%}，不再 OCR")
                return _entry(path, original, "failed", "aux_no_text->manual",
                              "辅助材料且无文字层，转人工查看")
            partial_items = items  # required + 低覆盖 → 下方升级 OCR

    # Tier 2：MinerU 精度（图片，或 required 低覆盖的扫描件）
    produced = mineru_extract_json(path, cache_dir, ocr=True, timeout=mineru_timeout)
    if produced:
        return _entry(produced, original, "success", "mineru_ocr",
                      "MinerU 精度抽取（OCR）")

    # Tier 3：失败/超时 → 转人工（保留已抽到的本地部分文字）
    if partial_items:
        _write_content_list(out_json, partial_items)
        return _entry(out_json, original, "partial", "local_partial+mineru_timeout",
                      "OCR 超时，保留本地部分文字，转人工")
    return _entry(path, original, "failed", "all_failed->manual",
                  "本地无文字层且 OCR 超时/失败，转人工")


def _entry(path: Path, original: str, status: str, tier: str, note: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "original_file": original,
        "parse_status": status,
        "extract_tier": tier,
        "extract_note": note,
    }


def orchestrate_folder(
    input_folder: Path,
    cache_dir: Path,
    **budgets: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """对文件夹内所有 PDF/图片分层抽取到 cache_dir，返回 (清单条目, 日志行)。"""
    input_folder = Path(input_folder)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    log: list[str] = []
    for path in sorted(p for p in input_folder.iterdir() if p.is_file()):
        if path.suffix.lower() not in TEXT_EXTS | IMAGE_EXTS:
            continue
        entry = extract_file(path, cache_dir, **budgets)
        entries.append(entry)
        log.append(
            f'  [{entry["parse_status"]:7}] {entry["extract_tier"]:28} {entry["original_file"]}'
            f'  —— {entry["extract_note"]}'
        )
    return entries, log
