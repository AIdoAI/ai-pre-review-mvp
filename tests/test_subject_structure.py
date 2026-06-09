from __future__ import annotations

import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_review"))

from review_mvp.subject_structure import (
    apply_material_assignments,
    build_upload_requirements,
    entity_material_findings,
    prepare_subject_structure,
    subject_structure_from_form_answers,
)


def statuses(structure: dict, rule_id: str) -> list[str]:
    return [
        item["status"]
        for item in structure["findings"]
        if item["rule_id"] == rule_id
    ]


def material(document_type: str, original_file: str, confirmed: bool = True) -> dict:
    return {
        "material_id": f"M-{document_type}",
        "document_type": document_type,
        "segments": [{"original_file": original_file}],
        "pages": [f"{original_file}#P1"],
        "presence_assessment": {
            "eligible_for_required_presence": confirmed,
        },
    }


class SubjectStructureTests(unittest.TestCase):
    def test_form_answers_independent_legal_person(self) -> None:
        structure = prepare_subject_structure(
            {
                "form_answers": {
                    "is_joint_declaration": False,
                    "applicants": [
                        {
                            "entity_id": "E01",
                            "entity_name": "独立申报单位",
                            "entity_type": "state_owned",
                            "is_independent_legal_person": True,
                        }
                    ],
                }
            }
        )
        self.assertEqual(structure["input_source"], "form_answers")
        self.assertEqual(structure["declaration_type"], "independent")
        self.assertEqual(structure["entities"][0]["declaration_role"], "applicant")
        self.assertEqual(structure["upload_requirements"], [])

    def test_form_answers_non_independent_generates_parent_upload_zones(self) -> None:
        structure = prepare_subject_structure(
            {
                "form_answers": {
                    "is_joint_declaration": False,
                    "applicants": [
                        {
                            "entity_id": "E01",
                            "entity_name": "非独立法人申报单位",
                            "entity_type": "state_owned",
                            "is_independent_legal_person": False,
                            "parent_entity": {
                                "entity_id": "E02",
                                "entity_name": "上级单位",
                                "entity_type": "state_owned",
                            },
                        }
                    ],
                }
            }
        )
        self.assertEqual(structure["applicant_entity_ids"], ["E01"])
        self.assertIn("branch_office", structure["derived_conditions"])
        self.assertEqual(
            {item["document_type"] for item in structure["upload_requirements"]},
            {"营业执照", "分支机构专项授权文件"},
        )
        parent = next(item for item in structure["entities"] if item["entity_id"] == "E02")
        self.assertEqual(parent["declaration_role"], "authorizing_parent")
        self.assertTrue(parent["is_independent_legal_person"])

    def test_form_answers_joint_generates_joint_upload_zone(self) -> None:
        structure = prepare_subject_structure(
            {
                "form_answers": {
                    "is_joint_declaration": True,
                    "joint_declaration_material_type": "stamped_lead_declaration",
                    "applicants": [
                        {
                            "entity_id": "E01",
                            "entity_type": "state_owned",
                            "is_lead": True,
                            "is_independent_legal_person": True,
                        },
                        {
                            "entity_id": "E02",
                            "entity_type": "private",
                            "is_lead": False,
                            "is_independent_legal_person": True,
                        },
                    ],
                }
            }
        )
        self.assertEqual(structure["declaration_type"], "joint")
        self.assertEqual(statuses(structure, "HR-1.2-COUNT"), ["pass"])
        self.assertEqual(
            [item["document_type"] for item in structure["upload_requirements"]],
            ["盖章的牵头方申报声明"],
        )
        self.assertEqual(statuses(structure, "MR-JOINT-SUPPORT-MATERIAL"), ["manual_review"])

    def test_other_project_stage_requires_manual_review_without_stage_condition(self) -> None:
        structure = prepare_subject_structure(
            {
                "form_answers": {
                    "is_joint_declaration": False,
                    "project_stage": "other",
                    "applicants": [
                        {
                            "entity_id": "E01",
                            "is_independent_legal_person": True,
                        }
                    ],
                }
            }
        )
        self.assertEqual(statuses(structure, "MR-PROJECT-STAGE-OTHER"), ["manual_review"])
        self.assertNotIn("project_stage_building", structure["derived_conditions"])
        self.assertNotIn("project_stage_planned", structure["derived_conditions"])

    def test_form_answers_are_authoritative_over_subject_structure(self) -> None:
        structure = prepare_subject_structure(
            {
                "form_answers": {
                    "is_joint_declaration": False,
                    "applicants": [
                        {
                            "entity_id": "E01",
                            "is_independent_legal_person": True,
                        }
                    ],
                },
                "subject_structure": {
                    "declaration_type": "joint",
                    "entities": [],
                },
            }
        )
        self.assertEqual(structure["declaration_type"], "independent")
        self.assertEqual(statuses(structure, "INFO-FORM-SOURCE"), ["pass"])

    def test_independent_application_has_one_applicant(self) -> None:
        structure = prepare_subject_structure(
            {
                "conditions": [],
                "subject_structure": {
                    "declaration_type": "independent",
                    "entities": [
                        {
                            "entity_id": "E01",
                            "entity_name": "独立申报单位",
                            "declaration_role": "applicant",
                            "entity_type": "state_owned",
                            "is_independent_legal_person": True,
                        }
                    ],
                },
            }
        )
        self.assertEqual(statuses(structure, "HR-SUBJECT-INDEPENDENT-COUNT"), ["pass"])
        self.assertNotIn("branch_office", structure["derived_conditions"])

    def test_joint_parent_does_not_count_as_joint_member(self) -> None:
        structure = prepare_subject_structure(
            {
                "conditions": [],
                "subject_structure": {
                    "declaration_type": "joint",
                    "entities": [
                        {
                            "entity_id": "E01",
                            "declaration_role": "lead",
                            "entity_type": "state_owned",
                            "is_independent_legal_person": True,
                        },
                        {
                            "entity_id": "E02",
                            "declaration_role": "member",
                            "entity_type": "private",
                            "is_independent_legal_person": False,
                            "parent_entity_id": "E03",
                        },
                        {
                            "entity_id": "E03",
                            "declaration_role": "authorizing_parent",
                            "entity_type": "private",
                            "is_independent_legal_person": True,
                        },
                    ],
                },
            }
        )
        self.assertEqual(statuses(structure, "HR-1.2-COUNT"), ["pass"])
        self.assertIn("joint_declaration", structure["derived_conditions"])
        self.assertIn("branch_office", structure["derived_conditions"])
        self.assertEqual(structure["applicant_entity_ids"], ["E01", "E02"])

    def test_joint_private_lead_fails_structured_check(self) -> None:
        structure = prepare_subject_structure(
            {
                "subject_structure": {
                    "declaration_type": "joint",
                    "entities": [
                        {
                            "entity_id": "E01",
                            "declaration_role": "lead",
                            "entity_type": "private",
                            "is_independent_legal_person": True,
                        },
                        {
                            "entity_id": "E02",
                            "declaration_role": "member",
                            "entity_type": "state_owned",
                            "is_independent_legal_person": True,
                        },
                    ],
                }
            }
        )
        self.assertEqual(statuses(structure, "HR-1.2-LEAD-TYPE"), ["fail"])

    def test_non_independent_applicant_requires_parent(self) -> None:
        structure = prepare_subject_structure(
            {
                "subject_structure": {
                    "declaration_type": "independent",
                    "entities": [
                        {
                            "entity_id": "E01",
                            "declaration_role": "applicant",
                            "entity_type": "state_owned",
                            "is_independent_legal_person": False,
                        }
                    ],
                }
            }
        )
        self.assertEqual(statuses(structure, "HR-2.4-PARENT-E01"), ["fail"])

    def test_parent_materials_pass_when_explicitly_assigned(self) -> None:
        submission = {
            "mode": "complete",
            "_parse_complete": True,
            "_subject_structure": prepare_subject_structure(
                {
                    "subject_structure": {
                        "declaration_type": "independent",
                        "entities": [
                            {
                                "entity_id": "E01",
                                "entity_name": "申报分支机构",
                                "declaration_role": "applicant",
                                "entity_type": "state_owned",
                                "is_independent_legal_person": False,
                                "parent_entity_id": "E02",
                            },
                            {
                                "entity_id": "E02",
                                "entity_name": "上级单位",
                                "declaration_role": "authorizing_parent",
                                "entity_type": "state_owned",
                                "is_independent_legal_person": True,
                            },
                        ],
                    }
                }
            ),
        }
        materials = [
            material("营业执照", "上级单位营业执照.pdf"),
            material("分支机构专项授权文件", "专项授权.pdf"),
        ]
        unmatched = apply_material_assignments(
            materials,
            [
                {
                    "document_type": "营业执照",
                    "original_file": "上级单位营业执照.pdf",
                    "owner_entity_id": "E02",
                },
                {
                    "document_type": "分支机构专项授权文件",
                    "original_file": "专项授权.pdf",
                    "owner_entity_id": "E02",
                    "supports_entity_id": "E01",
                },
            ],
        )
        self.assertEqual(unmatched, [])
        findings = entity_material_findings(submission, materials)
        self.assertEqual([item["status"] for item in findings], ["pass", "pass"])

    def test_unassigned_parent_materials_require_manual_review(self) -> None:
        submission = {
            "mode": "complete",
            "_parse_complete": True,
            "_subject_structure": prepare_subject_structure(
                {
                    "subject_structure": {
                        "declaration_type": "independent",
                        "entities": [
                            {
                                "entity_id": "E01",
                                "declaration_role": "applicant",
                                "is_independent_legal_person": False,
                                "parent_entity_id": "E02",
                            },
                            {
                                "entity_id": "E02",
                                "declaration_role": "authorizing_parent",
                                "is_independent_legal_person": True,
                            },
                        ],
                    }
                }
            ),
        }
        materials = [
            material("营业执照", "不明归属营业执照.pdf"),
            material("分支机构专项授权文件", "不明归属授权.pdf"),
        ]
        findings = entity_material_findings(submission, materials)
        self.assertEqual([item["status"] for item in findings], ["manual_review", "manual_review"])

    def test_entity_specific_required_material_uses_ownership(self) -> None:
        submission = {
            "mode": "complete",
            "_parse_complete": True,
            "_subject_structure": prepare_subject_structure(
                {
                    "subject_structure": {
                        "declaration_type": "joint",
                        "entities": [
                            {
                                "entity_id": "E01",
                                "entity_name": "牵头单位",
                                "declaration_role": "lead",
                                "entity_type": "state_owned",
                                "is_independent_legal_person": True,
                                "required_materials": ["营业执照"],
                            },
                            {
                                "entity_id": "E02",
                                "entity_name": "联合成员",
                                "declaration_role": "member",
                                "entity_type": "private",
                                "is_independent_legal_person": True,
                                "required_materials": ["营业执照"],
                            },
                        ],
                    }
                }
            ),
        }
        materials = [material("营业执照", "牵头单位营业执照.pdf")]
        apply_material_assignments(
            materials,
            [
                {
                    "document_type": "营业执照",
                    "original_file": "牵头单位营业执照.pdf",
                    "owner_entity_id": "E01",
                }
            ],
        )
        findings = entity_material_findings(submission, materials)
        self.assertEqual(findings[0]["status"], "pass")
        self.assertEqual(findings[1]["status"], "manual_review")


if __name__ == "__main__":
    unittest.main()
