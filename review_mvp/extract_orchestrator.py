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
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

# 文件名 → 角色（粗判，决定是否值得花重 OCR）。辅助材料不参与缺失判定。
AUXILIARY_FILENAME_HINTS = (
    "荣誉", "研发能力", "专利", "软著", "软件著作权", "奖", "资质证书", "证书",
)
TEXT_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
XLSX_EXTS = {".xlsx", ".xlsm"}


def xlsx_to_content_list(path: Path, max_rows: int = 400) -> list[dict[str, Any]] | None:
    """读 Excel（openpyxl）为 content_list：每个 sheet 一页，含表名 + 单元格文本。

    缺 openpyxl / 加密 / 读取失败 → None（上层转人工）。data_only=True 取公式缓存值。
    """
    try:
        import openpyxl
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception:
        return None
    items: list[dict[str, Any]] = []
    for idx, ws in enumerate(wb.worksheets):
        lines = [str(ws.title)]
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r >= max_rows:
                break
            cells = [("" if c is None else str(c)).strip() for c in row]
            line = " ".join(c for c in cells if c)
            if line:
                lines.append(line)
        text = "\n".join(lines).strip()
        if len(text) > len(str(ws.title)):
            items.append({"page_idx": idx, "type": "text", "text": text})
    return items or None


def _exe(name: str) -> str:
    """解析可执行名为完整路径，跨平台兼容 Windows 的 .cmd/.exe（npm 全局命令为 .cmd）。"""
    return shutil.which(name) or name


def guess_role(filename: str) -> str:
    """auxiliary（仅记录，不值得 OCR）/ required（走完整链）。"""
    if any(hint in filename for hint in AUXILIARY_FILENAME_HINTS):
        return "auxiliary"
    return "required"


def pdf_pages_text(path: Path, timeout: int = 60) -> list[str] | None:
    """本地文字层抽取（poppler pdftotext）。返回逐页文本；不可用时返回 None。"""
    try:
        # -enc UTF-8 强制 UTF-8 输出，避免 Windows 控制台默认 GBK 导致中文乱码
        proc = subprocess.run(
            [_exe("pdftotext"), "-enc", "UTF-8", "-layout", str(path), "-"],
            capture_output=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "ignore").split("\f")  # pdftotext 以换页符分页


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


def _mineru_call(
    path: Path, out_dir: Path, ocr: bool, timeout: int, pages: str | None = None,
) -> list[dict[str, Any]] | None:
    """单次 MinerU 精度抽取，返回 content_list；超时/失败/无产物返回 None。"""
    cmd = [
        _exe("mineru-open-api"), "extract", str(path),
        "-o", str(out_dir), "-f", "json", "--timeout", str(timeout),
    ]
    if ocr:
        cmd.append("--ocr")
    if pages:
        cmd += ["--pages", pages]
    try:
        # 不用 text=True：Windows 控制台默认 GBK 解码 MinerU 输出会抛 UnicodeDecodeError；
        # 这里只取产出的 JSON 文件，stdout 以字节丢弃即可。
        subprocess.run(cmd, capture_output=True, timeout=timeout + 30)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    produced = out_dir / f"{path.stem}.json"
    if not produced.exists():
        return None
    try:
        data = json.loads(produced.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, list) else data.get("content_list", [])


def _mineru_with_retry(
    path: Path, out_dir: Path, ocr: bool, timeout: int, pages: str | None,
    retries: int, backoff: int,
) -> list[dict[str, Any]] | None:
    """带退避重试（多数超时是偶发网络/服务端负载）。"""
    for attempt in range(retries + 1):
        items = _mineru_call(path, out_dir, ocr, timeout, pages)
        if items is not None:
            return items
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    return None


def mineru_extract(
    path: Path, work_dir: Path, *, ocr: bool, timeout: int, page_count: int | None = None,
    chunk_pages: int = 8, retries: int = 2, backoff: int = 5,
) -> tuple[list[dict[str, Any]] | None, bool]:
    """MinerU 精度抽取（含重试 + 大文件分块）。

    返回 (content_list 或 None, complete)。分块时部分块失败 → 返回已成功块且
    complete=False（partial）；全失败 → (None, False)。
    """
    # 小文件：单次（含重试）
    if not page_count or page_count <= chunk_pages:
        items = _mineru_with_retry(path, work_dir, ocr, timeout, None, retries, backoff)
        return (items, items is not None)

    # 大文件：按页分块，逐块重试，部分成功也保留
    merged: list[dict[str, Any]] = []
    global_page = 0
    any_ok = False
    all_ok = True
    for start in range(1, page_count + 1, chunk_pages):
        end = min(start + chunk_pages - 1, page_count)
        chunk_dir = work_dir / f"_chunk_{path.stem}_{start}_{end}"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        items = _mineru_with_retry(
            path, chunk_dir, ocr, timeout, f"{start}-{end}", retries, backoff,
        )
        shutil.rmtree(chunk_dir, ignore_errors=True)
        if items is None:
            all_ok = False
            continue
        any_ok = True
        by_local: dict[int, list[dict[str, Any]]] = {}
        for item in items:
            by_local.setdefault(item.get("page_idx", 0), []).append(item)
        for local in sorted(by_local):
            for item in by_local[local]:
                remapped = dict(item)
                remapped["page_idx"] = global_page
                merged.append(remapped)
            global_page += 1
    if not any_ok:
        return (None, False)
    return (merged, all_ok)


def _write_content_list(out_json: Path, items: list[dict[str, Any]]) -> None:
    out_json.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def extract_file(
    path: Path,
    cache_dir: Path,
    *,
    local_timeout: int = 60,
    mineru_timeout: int = 180,
    min_coverage: float = 0.6,
    retries: int = 2,
    backoff: int = 5,
    chunk_pages: int = 8,
) -> dict[str, Any]:
    """单文件分层抽取，返回带 parse_status 的清单条目。"""
    original = path.name
    role = guess_role(original)
    out_json = cache_dir / f"{path.stem}.json"
    suffix = path.suffix.lower()
    partial_items: list[dict[str, Any]] = []
    page_count = 1  # 图片/未知默认 1 页

    # Excel：本地读取（openpyxl），不走网络/OCR
    if suffix in XLSX_EXTS:
        items = xlsx_to_content_list(path)
        if items:
            _write_content_list(out_json, items)
            return _entry(out_json, original, "success", "local_xlsx", "本地读取 Excel（openpyxl）")
        return _entry(path, original, "failed", "xlsx_failed->manual",
                      "Excel 读取失败或缺 openpyxl，转人工")

    # Tier 0：本地文字层（仅 PDF）
    if suffix in TEXT_EXTS:
        pages = pdf_pages_text(path, timeout=local_timeout)
        if pages is not None:
            page_count = len(pages)
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

    # Tier 2：MinerU 精度（图片，或 required 低覆盖的扫描件）；含重试 + 大文件分块
    items, complete = mineru_extract(
        path, cache_dir, ocr=True, timeout=mineru_timeout, page_count=page_count,
        chunk_pages=chunk_pages, retries=retries, backoff=backoff,
    )
    if items:
        _write_content_list(out_json, items)
        if complete:
            return _entry(out_json, original, "success", "mineru_ocr",
                          "MinerU 精度抽取（OCR，含重试/分块）")
        return _entry(out_json, original, "partial", "mineru_ocr_partial",
                      "OCR 分块部分成功，其余转人工")

    # Tier 3：失败/超时 → 转人工（保留已抽到的本地部分文字）
    if partial_items:
        _write_content_list(out_json, partial_items)
        return _entry(out_json, original, "partial", "local_partial+mineru_failed",
                      "OCR 重试仍失败，保留本地部分文字，转人工")
    return _entry(path, original, "failed", "all_failed->manual",
                  "本地无文字层且 OCR 重试失败，转人工")


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
        if path.suffix.lower() not in TEXT_EXTS | IMAGE_EXTS | XLSX_EXTS:
            continue
        entry = extract_file(path, cache_dir, **budgets)
        entries.append(entry)
        log.append(
            f'  [{entry["parse_status"]:7}] {entry["extract_tier"]:28} {entry["original_file"]}'
            f'  —— {entry["extract_note"]}'
        )
    return entries, log
