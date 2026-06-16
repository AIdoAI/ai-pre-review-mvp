"""End-to-end local review pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .classifier import load_material_types, segment_documents
from .extractor import extract_all
from .mineru_reader import normalize_mineru_json
from .presence import annotate_material_presence
from .report import (
    applicant_headline,
    render_batch_summary,
    render_conclusion,
    render_per_file_report,
    render_report,
)
from .rule_engine import load_policy, load_rules, run_rules
from .subject_structure import apply_material_assignments, prepare_subject_structure


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def compact_material_catalog(
    materials: list[dict[str, Any]],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    catalog = []
    for material in materials:
        sources: dict[tuple[str, str], list[int]] = {}
        for segment in material["segments"]:
            source_key = (
                segment.get("original_file") or Path(segment["source_file"]).name,
                segment["source_file"],
            )
            sources.setdefault(source_key, []).append(segment["page"])
        catalog.append(
            {
                "material_id": material["material_id"],
                "document_type": material["document_type"],
                "ownership": material.get("ownership"),
                "requirement": policy["materials"].get(
                    material["document_type"],
                    {"requirement": "unknown"},
                )["requirement"],
                "confidence": material["confidence"],
                "presence_assessment": material["presence_assessment"],
                "sources": [
                    {
                        "original_file": original_file,
                        "mineru_json": source_file,
                        "pages": sorted(set(pages)),
                    }
                    for (original_file, source_file), pages in sources.items()
                ],
            }
        )
    return catalog


def resolve_files(submission: dict[str, Any], manifest_path: Path) -> list[dict[str, Any]]:
    resolved = []
    for item in submission["files"]:
        if isinstance(item, str):
            item = {"path": item, "parse_status": "success"}
        resolved.append(
            {
                **item,
                "path": (manifest_path.parent / item["path"]).resolve(),
            }
        )
    return resolved


def file_material_assignments(file_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert upload-zone metadata on files into material ownership assignments."""
    assignments = []
    for item in file_entries:
        if not item.get("owner_entity_id") and not item.get("supports_entity_id"):
            continue
        assignments.append(
            {
                "original_file": item.get("original_file") or item["path"].name,
                "document_type": item.get("document_type"),
                "owner_entity_id": item.get("owner_entity_id"),
                "supports_entity_id": item.get("supports_entity_id"),
            }
        )
    return assignments


def run_submission(
    submission: dict[str, Any],
    manifest_path: Path,
    material_types_path: Path,
    rules_path: Path,
    policy_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    file_entries = resolve_files(submission, manifest_path)
    normalized = []
    parse_details = []
    for item in file_entries:
        if item.get("parse_status", "success") in {"success", "partial"} and item["path"].exists():
            document = normalize_mineru_json(item["path"])
            document["original_file"] = item.get("original_file") or item["path"].name
            empty_pages = [
                page["page"] for page in document["pages"]
                if not page["full_text"].strip() and not page["blocks"]
            ]
            effective_status = item.get("parse_status", "success")
            if effective_status == "success" and empty_pages:
                effective_status = "partial"
            parsed_pages = [
                page["page"] for page in document["pages"]
                if page["page"] not in empty_pages
            ]
            document["parse_status"] = effective_status
            document["total_pages"] = item.get("total_pages") or len(document["pages"])
            document["parsed_pages"] = item.get("parsed_pages") or parsed_pages
            document["empty_pages"] = empty_pages
            normalized.append(document)
            parse_details.append(
                {
                    "path": str(item["path"]),
                    "original_file": document["original_file"],
                    "parse_status": effective_status,
                    "total_pages": document["total_pages"],
                    "parsed_pages": document["parsed_pages"],
                    "empty_pages": empty_pages,
                }
            )
        else:
            parse_details.append(
                {
                    "path": str(item["path"]),
                    "original_file": item.get("original_file") or item["path"].name,
                    "parse_status": item.get("parse_status", "failed"),
                    "total_pages": item.get("total_pages"),
                    "parsed_pages": item.get("parsed_pages"),
                    "empty_pages": [],
                }
            )
    subject_structure = prepare_subject_structure(submission)
    review_submission = {
        **submission,
        "conditions": subject_structure["derived_conditions"],
        "_subject_structure": subject_structure,
        "_parse_complete": all(item["parse_status"] == "success" for item in parse_details),
        "_parse_details": parse_details,
    }
    material_types = load_material_types(material_types_path)
    materials = segment_documents(normalized, material_types)
    rules = load_rules(rules_path)
    policy = load_policy(policy_path)
    annotate_material_presence(materials, policy)
    assignments = submission.get("material_assignments", []) + file_material_assignments(file_entries)
    unmatched_assignments = apply_material_assignments(
        materials,
        assignments,
    )
    review_submission["_unmatched_material_assignments"] = unmatched_assignments
    extracted = extract_all(materials)
    rule_results = run_rules(review_submission, materials, extracted, rules, policy)
    material_catalog = compact_material_catalog(materials, policy)
    applicant_header = applicant_headline(subject_structure, extracted)
    conclusion = render_conclusion(rule_results, applicant_header)
    per_file_report = render_per_file_report(review_submission, materials, rule_results, applicant_header)

    target = output_root / submission["submission_id"]
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "normalized_documents.json", normalized)
    write_json(target / "subject_structure.json", subject_structure)
    write_json(target / "material_catalog.json", material_catalog)
    write_json(target / "materials.json", materials)
    write_json(target / "extracted_fields.json", extracted)
    write_json(target / "rule_results.json", rule_results)
    (target / "conclusion.md").write_text(conclusion, encoding="utf-8")
    (target / "per_file_report.md").write_text(per_file_report, encoding="utf-8")
    (target / "review_report.md").write_text(
        conclusion
        + "\n---\n\n" + per_file_report
        + "\n---\n\n" + render_report(review_submission, materials, extracted, rule_results, applicant_header),
        encoding="utf-8",
    )
    return {
        "submission_id": submission["submission_id"],
        "name": submission["name"],
        "subject_structure": subject_structure,
        "material_catalog": material_catalog,
        "materials": materials,
        "extracted": extracted,
        "rule_results": rule_results,
        "conclusion": conclusion,
        "per_file_report": per_file_report,
        "output_dir": str(target),
    }


def run_manifest(
    manifest_path: Path,
    output_root: Path,
    material_types_path: Path | None = None,
    rules_path: Path | None = None,
    policy_path: Path | None = None,
) -> list[dict[str, Any]]:
    base = Path(__file__).resolve().parents[1]
    material_types_path = material_types_path or base / "config" / "material_types.json"
    rules_path = rules_path or base / "config" / "review_rules.json"
    policy_path = policy_path or base / "config" / "material_policy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_root.mkdir(parents=True, exist_ok=True)
    items = [
        run_submission(submission, manifest_path, material_types_path, rules_path, policy_path, output_root)
        for submission in manifest["submissions"]
    ]
    summary = [
        {
            "submission_id": item["submission_id"],
            "name": item["name"],
            "materials": [
                {
                    "document_type": material["document_type"],
                    "confidence": material["confidence"],
                    "pages": material["pages"],
                }
                for material in item["materials"]
            ],
            "rule_results": item["rule_results"],
        }
        for item in items
    ]
    write_json(output_root / "batch_summary.json", summary)
    (output_root / "batch_summary.md").write_text(render_batch_summary(items), encoding="utf-8")
    return items
