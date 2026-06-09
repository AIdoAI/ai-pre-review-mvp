from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_review"))

from review_mvp.pipeline import run_manifest
from review_mvp.presence import assess_material_presence


class PipelineTests(unittest.TestCase):
    def test_required_material_keyword_only_is_suspected(self) -> None:
        assessment = assess_material_presence(
            {
                "classification_evidence": {
                    "start_patterns": [],
                    "keywords": ["统一社会信用代码"],
                }
            },
            {"requirement": "required"},
        )
        self.assertEqual(assessment["level"], "suspected")
        self.assertFalse(assessment["eligible_for_required_presence"])

    def test_current_samples(self) -> None:
        manifest_path = ROOT / "local_review" / "config" / "sample_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {
            item["submission_id"]: set(item["expected_materials"])
            for item in manifest["submissions"]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_manifest(manifest_path, Path(temp_dir))
            for item in results:
                detected = {material["document_type"] for material in item["materials"]}
                self.assertTrue(
                    expected[item["submission_id"]].issubset(detected),
                    f'{item["submission_id"]} missing {expected[item["submission_id"]] - detected}',
                )

    def test_partial_samples_do_not_fail_for_absent_materials(self) -> None:
        manifest_path = ROOT / "local_review" / "config" / "sample_manifest.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_manifest(manifest_path, Path(temp_dir))
            for item in results:
                absent_material_rules = [
                    result for result in item["rule_results"]["results"]
                    if result["rule_id"].startswith(("HR-2.1", "HR-3.1"))
                    and result["status"] != "pass"
                ]
                self.assertTrue(absent_material_rules)
                self.assertTrue(all(rule["status"] == "not_assessable" for rule in absent_material_rules))

    def test_optional_materials_are_not_missing_rules(self) -> None:
        manifest_path = ROOT / "local_review" / "config" / "sample_manifest.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_manifest(manifest_path, Path(temp_dir))
            for item in results:
                descriptions = [rule["description"] for rule in item["rule_results"]["results"]]
                self.assertFalse(any("检查研发资质证明" in value for value in descriptions))
                self.assertFalse(any("检查政府荣誉或认定名单" in value for value in descriptions))

    def test_multi_certificate_pdf_boundaries(self) -> None:
        manifest_path = ROOT / "local_review" / "config" / "sample_manifest.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_manifest(manifest_path, Path(temp_dir))
            item = next(result for result in results if result["submission_id"] == "sample-planned-implementation-3")
            materials = {material["document_type"]: material for material in item["materials"]}
            self.assertTrue(all(page.endswith("#P1") for page in materials["营业执照"]["pages"]))
            self.assertTrue(all(not page.endswith("#P5") for page in materials["食品生产许可证"]["pages"]))
            self.assertTrue(all(not page.endswith("#P7") for page in materials["食品经营许可证"]["pages"]))

    def test_material_catalog_contains_original_file(self) -> None:
        manifest_path = ROOT / "local_review" / "config" / "sample_manifest.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_manifest(manifest_path, Path(temp_dir))
            item = next(
                result for result in results
                if result["submission_id"] == "sample-planned-implementation-3"
            )
            license_item = next(
                material for material in item["material_catalog"]
                if material["document_type"] == "营业执照"
            )
            self.assertEqual(
                license_item["sources"][0]["original_file"],
                "样本5（计划实施3）：单一pdf多文件.pdf",
            )
            self.assertEqual(license_item["sources"][0]["pages"], [1])
            self.assertTrue(
                license_item["presence_assessment"]["eligible_for_required_presence"]
            )

    def test_sample3_tax_material_does_not_confirm_business_license(self) -> None:
        manifest_path = ROOT / "local_review" / "config" / "sample3_manifest.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_manifest(manifest_path, Path(temp_dir))
            item = results[0]
            license_rule = next(
                result for result in item["rule_results"]["results"]
                if result["rule_id"] == "HR-2.1-LICENSE"
            )
            detected = {material["document_type"] for material in item["materials"]}
            self.assertNotEqual(license_rule["status"], "pass")
            self.assertIn("纳税或税务材料", detected)
            self.assertIn("主营业务收入或财务证明", detected)

    def test_incomplete_parse_never_proves_material_missing(self) -> None:
        sample_manifest = json.loads(
            (ROOT / "local_review" / "config" / "sample_manifest.json").read_text(encoding="utf-8")
        )
        source_item = sample_manifest["submissions"][0]["files"][0]
        source_file = source_item["path"] if isinstance(source_item, dict) else source_item
        source_path = (
            ROOT / "local_review" / "config" / source_file
        ).resolve()
        manifest = {
            "submissions": [
                {
                    "submission_id": "incomplete-parse-test",
                    "name": "长文件部分解析测试",
                    "mode": "complete",
                    "conditions": [],
                    "files": [
                        {
                            "path": str(source_path),
                            "parse_status": "partial",
                            "total_pages": 40,
                            "parsed_pages": 9,
                        }
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            results = run_manifest(manifest_path, Path(temp_dir) / "output")
            material_rules = [
                item for item in results[0]["rule_results"]["results"]
                if item["rule_id"].startswith(("HR-2.1", "HR-3.1"))
            ]
            self.assertTrue(material_rules)
            self.assertFalse(any(item["status"] == "fail" for item in material_rules))
            self.assertTrue(any(item["status"] == "not_assessable" for item in material_rules))

    def test_empty_mineru_pages_mark_parse_partial(self) -> None:
        manifest_path = ROOT / "local_review" / "config" / "sample_manifest.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_manifest(manifest_path, Path(temp_dir))
            item = next(
                result for result in results
                if result["submission_id"] == "sample-rd-qualification-2"
            )
            parse_rule = next(
                result for result in item["rule_results"]["results"]
                if result["rule_id"] == "MR-PARSE-QUALITY"
            )
            self.assertEqual(parse_rule["status"], "manual_review")
            self.assertIn("空白页[5, 6, 7, 8]", parse_rule["reason"])

    def test_complete_successful_parse_can_fail_missing_required_material(self) -> None:
        sample_manifest = json.loads(
            (ROOT / "local_review" / "config" / "sample_manifest.json").read_text(encoding="utf-8")
        )
        source_item = sample_manifest["submissions"][0]["files"][0]
        source_file = source_item["path"] if isinstance(source_item, dict) else source_item
        source_path = (ROOT / "local_review" / "config" / source_file).resolve()
        manifest = {
            "submissions": [
                {
                    "submission_id": "complete-missing-test",
                    "name": "完整包缺件测试",
                    "mode": "complete",
                    "conditions": [],
                    "files": [{"path": str(source_path), "parse_status": "success"}],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            results = run_manifest(manifest_path, Path(temp_dir) / "output")
            missing_credit = next(
                item for item in results[0]["rule_results"]["results"]
                if item["rule_id"] == "HR-2.1-CREDIT"
            )
            self.assertEqual(missing_credit["status"], "fail")


if __name__ == "__main__":
    unittest.main()
