from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_review"))

from review_mvp.folder_review import (
    build_folder_manifest,
    collect_form_answers,
    infer_original_file,
    scan_sample_folder,
)


class FolderReviewTests(unittest.TestCase):
    def test_infer_original_file_from_mineru_name(self) -> None:
        path = Path("MinerU_测试材料__20260609014655.json")
        self.assertEqual(infer_original_file(path), "测试材料.pdf")

    def test_scan_folder_only_accepts_mineru_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "MinerU_材料__20260609014655.json").write_text(
                json.dumps({"pdf_info": []}),
                encoding="utf-8",
            )
            (root / "普通配置.json").write_text(json.dumps({"hello": "world"}), encoding="utf-8")
            (root / "原始材料.pdf").touch()
            result = scan_sample_folder(root)
            self.assertEqual(len(result["mineru_files"]), 1)
            self.assertEqual(len(result["ignored_files"]), 2)

    def test_interactive_independent_non_legal_person(self) -> None:
        answers = iter(
            [
                "2",
                "n",
                "测试分公司",
                "2",
                "2",
            ]
        )
        mode, form_answers = collect_form_answers(
            input_fn=lambda _: next(answers),
            output_fn=lambda _: None,
        )
        self.assertEqual(mode, "complete")
        self.assertFalse(form_answers["is_joint_declaration"])
        applicant = form_answers["applicants"][0]
        self.assertFalse(applicant["is_independent_legal_person"])
        self.assertEqual(applicant["entity_name"], "测试分公司")
        self.assertEqual(applicant["parent_entity"]["entity_id"], "E01-PARENT")
        self.assertNotIn("entity_type", applicant)
        self.assertEqual(form_answers["project_stage"], "planned")

    def test_interactive_joint_two_applicants(self) -> None:
        answers = iter(
            [
                "1",
                "y",
                "2",
                "1",
                "3",
                "牵头单位",
                "1",
                "联合成员",
                "1",
                "0",
            ]
        )
        mode, form_answers = collect_form_answers(
            input_fn=lambda _: next(answers),
            output_fn=lambda _: None,
        )
        self.assertEqual(mode, "partial")
        self.assertTrue(form_answers["is_joint_declaration"])
        self.assertEqual(len(form_answers["applicants"]), 2)
        self.assertTrue(form_answers["applicants"][0]["is_lead"])
        self.assertFalse(form_answers["applicants"][1]["is_lead"])
        self.assertEqual(
            form_answers["joint_declaration_material_type"],
            "stamped_lead_declaration",
        )

    def test_interactive_other_project_stage(self) -> None:
        answers = iter(["1", "n", "测试单位", "1", "3"])
        _, form_answers = collect_form_answers(
            input_fn=lambda _: next(answers),
            output_fn=lambda _: None,
        )
        self.assertEqual(form_answers["project_stage"], "other")

    def test_build_manifest_uses_folder_name(self) -> None:
        scan_result = {
            "folder": "/tmp/测试样本2",
            "sample_name": "测试样本2",
            "mineru_files": [{"path": "/tmp/a.json", "original_file": "a.pdf", "parse_status": "success"}],
            "ignored_files": ["a.pdf"],
        }
        manifest = build_folder_manifest(
            scan_result,
            "partial",
            {"is_joint_declaration": False, "applicants": []},
        )
        submission = manifest["submissions"][0]
        self.assertEqual(submission["name"], "测试样本2")
        self.assertEqual(submission["folder_scan"]["mineru_json_count"], 1)


if __name__ == "__main__":
    unittest.main()
