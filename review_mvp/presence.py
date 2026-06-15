"""Assess whether a classified material is strong enough to confirm presence."""

from __future__ import annotations

from typing import Any


def assess_material_presence(
    material: dict[str, Any],
    material_policy: dict[str, Any],
) -> dict[str, Any]:
    requirement = material_policy.get("requirement", "unknown")
    confirmation_mode = material_policy.get(
        "presence_confirmation",
        "strong_title"
        if requirement in {"required", "conditional_required", "group_member"}
        else "observed",
    )
    start_patterns = material.get("classification_evidence", {}).get("start_patterns", [])

    if confirmation_mode == "observed":
        return {
            "level": "observed",
            "eligible_for_required_presence": False,
            "reason": "非必要材料仅记录已识别结果，不参与必要材料存在性确认",
        }
    if start_patterns:
        return {
            "level": "confirmed",
            "eligible_for_required_presence": True,
            "reason": "命中材料强标题或起始特征，可确认该类材料已提交",
        }
    return {
        "level": "suspected",
        "eligible_for_required_presence": False,
        "reason": "仅命中通用关键词，不能确认该必要材料已提交",
    }


def annotate_material_presence(
    materials: list[dict[str, Any]],
    policy: dict[str, Any],
) -> None:
    for material in materials:
        material_policy = policy["materials"].get(
            material["document_type"],
            {"requirement": "unknown"},
        )
        material["requirement"] = material_policy.get("requirement", "unknown")
        material["presence_assessment"] = assess_material_presence(material, material_policy)
