from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_review"))

from review_mvp.qualification_profile import (
    classify_filename,
    load_qualification_rules,
    normalize_filename,
    detect_ai_relevance,
    scan_qualification_batch,
    scan_qualification_folder,
)


RULES = load_qualification_rules(
    ROOT / "local_review" / "config" / "qualification_profile_rules.json"
)


class QualificationProfileTests(unittest.TestCase):
    def test_normalize_filename_removes_copy_markers(self) -> None:
        normalized = normalize_filename("电子证书--一种鱼类检测方法_A4(2).pdf")
        self.assertEqual(normalized["display_title"], "一种鱼类检测方法")

    def test_explicit_patent_is_high_confidence(self) -> None:
        result = classify_filename("发明专利证书 一种基于人工智能的检测方法.pdf", RULES)
        self.assertEqual(result["qualification_type"], "patent")
        self.assertGreaterEqual(result["classification_confidence"], 0.9)

    def test_generic_electronic_certificate_is_unverified_patent_candidate(self) -> None:
        result = classify_filename("电子证书--一种用于评价鱼类饲料的方法.pdf", RULES)
        self.assertEqual(result["qualification_type"], "patent")
        self.assertLess(result["classification_confidence"], 0.9)
        self.assertIn("需打开正文确认", result["classification_note"])

    def test_long_english_title_is_paper(self) -> None:
        result = classify_filename(
            "A method for custom measurement using an improved YOLO framework.pdf",
            RULES,
        )
        self.assertEqual(result["qualification_type"], "paper")

    def test_award_takes_precedence_over_paper(self) -> None:
        result = classify_filename("2023年湖北省科技期刊百篇优秀论文奖.pdf", RULES)
        self.assertEqual(result["qualification_type"], "award")

    def test_ai_short_keyword_does_not_match_inside_ordinary_words(self) -> None:
        result = detect_ai_relevance("Fish training and evaluation paper.pdf", RULES)
        self.assertFalse(result["ai_related"])

    def test_folder_scan_deduplicates_and_detects_ai(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            filenames = [
                "发明专利证书 一种基于人工智能的检测方法.pdf",
                "发明专利证书 一种基于人工智能的检测方法(2).pdf",
                "企业软件著作权证书.pdf",
                "某项目科技进步奖.pdf",
            ]
            for filename in filenames:
                (root / filename).touch()

            profile = scan_qualification_folder(root, RULES, "测试企业")
            self.assertEqual(profile["stats"]["raw_file_count"], 4)
            self.assertEqual(profile["stats"]["deduplicated_item_count"], 3)
            self.assertEqual(profile["stats"]["duplicate_file_count"], 1)
            self.assertEqual(profile["stats"]["ai_related_candidate_count"], 1)
            self.assertIn("待核验", profile["summary"])

    def test_real_filename_only_folder(self) -> None:
        root = Path.home() / "Downloads" / "材料预审验证260608" / "水生生物研究所"
        if not root.exists():
            self.skipTest("Local filename-only validation folder is unavailable")

        profile = scan_qualification_folder(root, RULES, "水生生物研究所")
        counts = profile["stats"]["deduplicated_category_counts"]
        self.assertEqual(profile["stats"]["raw_file_count"], 22)
        self.assertEqual(counts["paper"], 14)
        self.assertEqual(counts["patent"], 6)
        self.assertEqual(counts["award"], 1)
        self.assertEqual(counts["platform_approval"], 1)
        self.assertGreaterEqual(profile["stats"]["ai_related_candidate_count"], 3)

    def test_batch_groups_immediate_child_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            company_a = root / "企业甲"
            company_b = root / "企业乙"
            company_a.mkdir()
            company_b.mkdir()
            (company_a / "某发明专利证书.pdf").touch()
            (company_b / "某软件著作权证书.pdf").touch()
            (root / "无法归属的奖项.pdf").touch()

            batch = scan_qualification_batch(root, RULES)
            self.assertEqual(batch["entity_count"], 3)
            names = {profile["entity_name"] for profile in batch["profiles"]}
            self.assertEqual(names, {"企业甲", "企业乙", "未归属文件"})
            self.assertTrue(
                all(profile["stats"]["raw_file_count"] == 1 for profile in batch["profiles"])
            )


if __name__ == "__main__":
    unittest.main()
