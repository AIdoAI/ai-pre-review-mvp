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
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

# 角色粗判（决定是否值得花重 OCR + 是否可折叠）。辅助材料不参与缺失判定。
# 按"完整相对路径"判（含文件夹名），但只用**纯辅助类**特征词：避免把"综合实力与研发资质"
# 这种混合目录（内含审计报告=财务必传）整体误判为辅助。
AUXILIARY_FILENAME_HINTS = (
    "荣誉", "获奖", "奖项", "研发能力", "专利", "软著", "软件著作权", "资质证书", "证书",
)
TEXT_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
XLSX_EXTS = {".xlsx", ".xlsm"}
DOCX_EXTS = {".docx"}
ZIP_EXTS = {".zip"}


def _processable(suffix: str) -> bool:
    return suffix.lower() in TEXT_EXTS | IMAGE_EXTS | XLSX_EXTS | DOCX_EXTS


def _zip_member_name(zinfo: Any) -> str:
    """修正 zip 内中文文件名：Windows 压缩常用 GBK，zipfile 默认按 cp437 解会乱码。"""
    name = zinfo.filename
    if zinfo.flag_bits & 0x800:  # 已标记 UTF-8
        return name
    try:
        return name.encode("cp437").decode("gbk")
    except Exception:
        return name


def extract_zip(zip_path: Path, dest_dir: Path) -> list[tuple[Path, Path]]:
    """把 zip 里可处理的文件解到 dest_dir，返回 [(解出的真实路径, zip内相对路径)]。

    解到缓存目录、不动用户原 zip；跳过目录/隐藏项/__MACOSX；中文名按 GBK 修正。
    """
    import zipfile
    out: list[tuple[Path, Path]] = []
    try:
        zf = zipfile.ZipFile(zip_path)
    except Exception:
        return out
    with zf:
        for zinfo in zf.infolist():
            if zinfo.is_dir():
                continue
            rel = Path(_zip_member_name(zinfo))
            if any(part.startswith(".") or part == "__MACOSX" for part in rel.parts):
                continue
            if not _processable(rel.suffix):
                continue
            target = dest_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(zinfo) as src, open(target, "wb") as dst:
                    dst.write(src.read())
            except Exception:
                continue
            out.append((target, rel))
    return out


def docx_to_content_list(path: Path) -> list[dict[str, Any]] | None:
    """读 .docx 为 content_list（stdlib zipfile，无依赖、跨平台）：按段落取文本（含表格单元格）。"""
    import html
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        return None
    lines: list[str] = []
    for para in re.split(r"</w:p>", xml):
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S)
        text = html.unescape(re.sub(r"<[^>]+>", "", "".join(runs))).strip()
        if text:
            lines.append(text)
    body = "\n".join(lines).strip()
    return [{"page_idx": 0, "type": "text", "text": body}] if body else None


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


def mineru_flash_extract(path: Path, timeout: int = 180) -> list[dict[str, Any]] | None:
    """MinerU flash-extract（免 token、含 OCR、一次调用无分块）→ content_list（单块 markdown）。

    比精度版快且不依赖 token/配额，作为 OCR 首选；解析其 stdout markdown。失败/空→None。
    """
    try:
        proc = subprocess.run(
            [_exe("mineru-open-api"), "flash-extract", str(path)],
            capture_output=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    out = proc.stdout.decode("utf-8", "ignore")
    out = re.sub(r"^Thinking\.\.\..*?\(flash\)\s*\n?", "", out, flags=re.S)  # 去状态行
    out = re.sub(r"Done\s*$", "", out).strip()                               # 去结尾 Done
    return [{"page_idx": 0, "type": "text", "text": out}] if out else None


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
    rel: Path | None = None,
    local_timeout: int = 60,
    mineru_timeout: int = 180,
    min_coverage: float = 0.6,
    retries: int = 2,
    backoff: int = 5,
    chunk_pages: int = 8,
) -> dict[str, Any]:
    """单文件分层抽取，返回带 parse_status 的清单条目。

    rel：文件相对样本根目录的路径（递归时用），作为展示名与唯一缓存键，避免不同
    子目录里同名文件互相覆盖缓存。
    """
    original = str(rel) if rel is not None else path.name
    role = guess_role(original)  # 按完整相对路径判角色（纯辅助目录如"软件著作权/"也能识别）
    key = re.sub(r"[^\w.\-一-鿿]+", "_", str(rel)) if rel is not None else path.stem
    out_json = cache_dir / f"{key}.json"
    raw_dir = cache_dir / "_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
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

    # Word：本地读取（zipfile 解 XML），不走网络/OCR
    if suffix in DOCX_EXTS:
        items = docx_to_content_list(path)
        if items:
            _write_content_list(out_json, items)
            return _entry(out_json, original, "success", "local_docx", "本地读取 Word（.docx）")
        return _entry(path, original, "failed", "docx_failed->manual", "Word 读取失败，转人工")

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

    # Tier 2a：MinerU flash 优先（免 token、含 OCR、一次调用，快且稳）
    items = mineru_flash_extract(path, timeout=mineru_timeout)
    if items:
        _write_content_list(out_json, items)
        return _entry(out_json, original, "success", "mineru_flash",
                      "MinerU flash 抽取（OCR，免 token）")

    # Tier 2b：精度 OCR 兜底（含重试 + 大文件分块）
    items, complete = mineru_extract(
        path, raw_dir, ocr=True, timeout=mineru_timeout, page_count=page_count,
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
    *,
    progress: bool = True,
    aux_dir_cap: int = 3,
    **budgets: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """递归抽取文件夹内所有 PDF/图片/Word/Excel 到 cache_dir，返回 (清单条目, 日志行)。

    支持嵌套子目录（如 参赛用户N/证明材料X/项目名/文件）；跳过 .DS_Store 等隐藏项。
    progress=True 时**逐文件实时打印**进度（每抽完一个就刷新输出），避免长时间无输出看着像卡死。
    """
    input_folder = Path(input_folder)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    # 收集待抽取的 (真实路径, 展示用相对路径)；zip 先解压再纳入，展示路径保留 zip 名层级。
    todo: list[tuple[Path, Path]] = []
    for p in sorted(input_folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(input_folder)
        if any(part.startswith(".") or part == "__MACOSX" for part in rel.parts):
            continue
        if p.suffix.lower() in ZIP_EXTS:
            unzip_dir = cache_dir / "_unzip" / re.sub(r"[^\w.\-一-鿿]+", "_", str(rel))
            for inner_path, inner_rel in extract_zip(p, unzip_dir):
                todo.append((inner_path, rel / inner_rel))
        elif _processable(p.suffix):
            todo.append((p, rel))

    # 辅助材料按目录折叠：同一目录下辅助件(软著/专利/荣誉等)只抽样若干份代表，其余不逐一处理。
    # 辅助材料不参与缺失判定，无需逐个看；折叠后大幅提速、报告也不被上百份软著刷屏。
    aux_total: dict[str, int] = {}
    aux_kept: dict[str, int] = {}
    capped: list[tuple[Path, Path]] = []
    for path, rel in todo:
        if guess_role(str(rel)) == "auxiliary":
            folder = str(rel.parent)
            aux_total[folder] = aux_total.get(folder, 0) + 1
            if aux_kept.get(folder, 0) >= aux_dir_cap:
                continue
            aux_kept[folder] = aux_kept.get(folder, 0) + 1
        capped.append((path, rel))
    todo = capped
    if progress:
        for folder, total in aux_total.items():
            extra = total - aux_kept.get(folder, 0)
            if extra > 0:
                print(f"  📁 折叠辅助材料：{folder} 共 {total} 份 → 抽样 {aux_kept[folder]} 份代表，"
                      f"其余 {extra} 份不逐一处理（辅助材料不参与缺失判定）", flush=True)
        print(f"  共 {len(todo)} 个文件待抽取（扫描件走 OCR 会慢，逐个显示进度）...", flush=True)
    entries: list[dict[str, Any]] = []
    log: list[str] = []
    for index, (path, rel) in enumerate(todo, start=1):
        entry = extract_file(path, cache_dir, rel=rel, **budgets)
        entries.append(entry)
        line = (
            f'  ({index}/{len(todo)}) [{entry["parse_status"]:7}] {entry["extract_tier"]:18} '
            f'{entry["original_file"]} —— {entry["extract_note"]}'
        )
        log.append(line)
        if progress:
            print(line, flush=True)
    return entries, log
