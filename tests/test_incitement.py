"""Bộ chấm mức kêu gọi/kích động hành vi phạm pháp.

Kiểm tra nhãn, ngưỡng, bằng chứng, ngữ cảnh (bản tin/cảnh báo/phê bình) và các
bẫy dấu tiếng Việt ("đập" vs "đạp", "đánh" vs "danh").
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from telegram_demo import incitement                                   # noqa: E402
from telegram_demo.analyzer import analyze, demo_posts                 # noqa: E402

VIOLENCE = ("Hãy tập trung tại trụ sở uỷ ban lúc 20h đêm nay, mang theo bom xăng "
            "và đốt sạch chúng nó!")
DAMAGE = "Đập phá hết cửa kính ngân hàng trên phố chính, đốt xe của bọn chúng."
OBSTRUCT = "Chặn xe của cán bộ, không cho họ vào uỷ ban làm việc."
THREAT = "Tao sẽ giết mày nếu mày không xin lỗi trước 10h tối nay."
NEWS = ("Cơ quan chức năng đang điều tra vụ việc một nhóm kêu gọi đốt trụ sở trên mạng. "
        "Cảnh báo người dân không chia sẻ thông tin sai lệch.")
WARNING = "Cảnh báo: tin giả lan truyền kêu gọi đốt trụ sở, cơ quan chức năng đã khởi tố."
OPINION = ("Tôi phản đối chính sách thuế mới vì nó ảnh hưởng tới người lao động. "
           "Đề nghị đối thoại công khai thay vì đàn áp ý kiến.")
BENIGN = "Mình mới mua được cái xe đạp cũ, sửa lại chạy ngon lành."


class RubricShapeTests(unittest.TestCase):
    def test_seven_labels_and_vietnamese_names(self):
        expected = {"call_to_violence", "call_to_property_damage", "obstruct_officials",
                    "call_against_state", "threat", "criticism", "insufficient_information"}
        self.assertEqual(set(incitement.LABELS), expected)
        self.assertEqual(set(incitement.LABELS_VI), expected)
        self.assertEqual(len(incitement.LABELS_VI["call_to_violence"]), len("Kêu gọi bạo lực"))

    def test_questions_use_choice_with_all_labels(self):
        question = incitement.QUESTIONS["incitement"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(set(question["criteria"]), set(incitement.LABELS))
        self.assertIn("không", question["instructions"].lower())
        self.assertIn("HÀNH ĐỘNG", question["instructions"])

    def test_rubric_version_is_pinned(self):
        self.assertTrue(incitement.RUBRIC_VERSION.startswith("incitement-v1:"))
        self.assertEqual(incitement.RUBRIC_VERSION.split(":")[:2], ["incitement-v1", "2026-11-21"])


class AssessTests(unittest.TestCase):
    def test_violence_call_reaches_high_risk(self):
        result = incitement.assess(VIOLENCE)
        self.assertEqual(result["level"], "High-risk")
        self.assertEqual(result["category"], "call_to_violence")
        self.assertTrue(result["requires_review"])
        self.assertGreaterEqual(result["score"], 60)
        matched = " ".join(word for signal in result["signals"] for word in signal["matched"])
        self.assertIn("bom xăng", matched)

    def test_property_damage_has_its_own_category(self):
        result = incitement.assess(DAMAGE)
        self.assertIn(result["category"],
                      {"call_to_property_damage", "call_to_violence"})
        self.assertEqual(result["level"], "High-risk")

    def test_obstruction_and_threat_are_detected(self):
        obstruct = incitement.assess(OBSTRUCT)
        self.assertEqual(obstruct["category"], "obstruct_officials")
        self.assertTrue(obstruct["requires_review"])
        threat = incitement.assess(THREAT)
        self.assertEqual(threat["category"], "threat")
        self.assertGreaterEqual(threat["score"], 25)

    def test_news_and_warnings_are_downgraded(self):
        for text in (NEWS, WARNING):
            result = incitement.assess(text)
            self.assertEqual(result["context"], "reporting", text)
            self.assertEqual(result["level"], "Normal", text)
            self.assertFalse(result["requires_review"], text)

    def test_legal_opinion_is_not_a_call(self):
        result = incitement.assess(OPINION)
        self.assertEqual(result["level"], "Normal")
        self.assertIn(result["category"], (None, "criticism"))

    def test_benign_text_scores_zero_despite_accent_folding_collision(self):
        result = incitement.assess(BENIGN)
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["category"], None)

    def test_evidence_points_at_original_text(self):
        result = incitement.assess(VIOLENCE)
        self.assertTrue(result["evidence"])
        first = result["evidence"][0]
        self.assertTrue(all(isinstance(sentence, str) for sentence in result["evidence"]))
        self.assertTrue(any("bom xăng" in sentence.lower() for sentence in result["evidence"]))

    def test_short_ambiguous_words_need_accents(self):
        # "danh" (tên/danh sách) không được khớp thành "đánh".
        self.assertEqual(incitement.assess("Danh sách lớp đã có trên bảng.")["score"], 0)
        # Nhưng cụm nhiều âm tiết vẫn bắt được văn bản gõ không dấu.
        self.assertGreater(incitement.assess("Hay dot nha no di")["score"], 0)

    def test_empty_and_insufficient_text(self):
        for text in ("", "   ", "ok", "hihi"):
            result = incitement.assess(text)
            self.assertEqual(result["score"], 0)
            self.assertEqual(result["level"], "Normal")

    def test_forwarded_and_media_are_metadata_only(self):
        plain = incitement.assess(VIOLENCE)
        boosted = incitement.assess(VIOLENCE, has_media=True, forwarded=True)
        self.assertEqual(plain["score"], boosted["score"])
        self.assertTrue(boosted["meta"]["has_media"])
        self.assertTrue(boosted["meta"]["forwarded"])

    def test_ocr_text_is_considered(self):
        result = incitement.assess("", ocr_text="ĐỐT TRỤ SỞ NGAY")
        self.assertGreater(result["score"], 0)


class AnalyzerWiringTests(unittest.TestCase):
    def test_analyze_exposes_new_and_legacy_keys(self):
        analysis = analyze({"text": VIOLENCE, "channel": "k", "views": 1, "forwards": 0})
        for key in ("scam_score", "risk_score", "level", "incitement_category",
                    "incitement_category_vi", "evidence", "context", "requires_review",
                    "rubric_version"):
            self.assertIn(key, analysis)
        self.assertEqual(analysis["scam_score"], analysis["risk_score"])
        self.assertIn("Kêu gọi", analysis["incitement_category_vi"])

    def test_demo_fixture_distribution(self):
        posts = demo_posts(40)
        self.assertEqual(len(posts), 17)
        levels = [analyze(post)["level"] for post in posts]
        self.assertEqual(levels.count("High-risk"), 5)
        self.assertEqual(levels.count("Suspicious"), 3)
        self.assertEqual(levels.count("Normal"), 9)
        channels = {post["channel"] for post in posts}
        self.assertIn("kichdong.bienhoa", channels)
        self.assertIn("canhbao.antoan", channels)


if __name__ == "__main__":
    unittest.main()
