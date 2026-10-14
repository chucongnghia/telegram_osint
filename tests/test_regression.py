#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test hồi quy cho project demo Telegram.

Chạy (không cần mạng, không cần đăng nhập, không cần Telethon):

    python3 -m unittest discover -s tests -v

Bộ test khoá lại những hành vi đã kiểm chứng, để sau này sửa từ điển/trọng số mà
vô tình làm hỏng kết quả thì biết ngay:

  1. Chấm điểm 17 bài demo phải khớp CHÍNH XÁC `posts_tg.json` đã lưu.
  2. Điểm rủi ro theo group/kênh phải khớp `posts_tg.json` (8 nhóm) và
     `nhom1.json` (nhóm aiquochientrang, 1000 bài, có `repeat_top_text`).
  3. Khớp từ khoá theo RANH GIỚI TỪ: `ib` trong "hermibooks", `stk` trong chuỗi
     base64 `fbclid`, `công an` trong "phân công anh ta" KHÔNG được gắn cờ.
  4. Tin tức thật (nhom1.json, 992 bài kênh @aiquochientrang) phải có ít bài bị
     gắn cờ (trước khi sửa là 8 bài — toàn bộ là báo động giả).
  5. Bộ đọc HTML của bản web (`demo_telegram.py`) tách đúng bài/views/media/link.
  6. Hai backend ghi JSON cùng schema, có khóa `groups`.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analyzer  # noqa: E402
import demo_telegram as web  # noqa: E402
from analyzer import (SCAM_GROUPS, analyze, demo_posts, group_summary,  # noqa: E402
                      level_of, no_accent, normalize)

POSTS_TG = json.loads((ROOT / "posts_tg.json").read_text(encoding="utf-8"))
NHOM1 = json.loads((ROOT / "nhom1.json").read_text(encoding="utf-8"))


def without_analysis(post: dict) -> dict:
    return {k: v for k, v in post.items() if k != "analysis"}


class TestScoring(unittest.TestCase):
    """Chấm điểm bài đăng."""

    def test_demo_posts_match_saved_analysis(self):
        """17 bài demo: chấm lại phải ra y hệt `analysis` đã lưu trong posts_tg.json."""
        for post in POSTS_TG["posts"]:
            with self.subTest(msg=post["message_id"]):
                self.assertEqual(analyze(without_analysis(post)), post["analysis"])

    def test_demo_level_distribution(self):
        """Đúng như README: 7 Normal / 8 Suspicious / 2 High-risk."""
        levels = [p["analysis"]["level"] for p in POSTS_TG["posts"]]
        self.assertEqual(levels.count("Normal"), 7)
        self.assertEqual(levels.count("Suspicious"), 8)
        self.assertEqual(levels.count("High-risk"), 2)
        flagged = sum(1 for lv in levels if lv != "Normal")
        self.assertEqual(flagged, 10)
        self.assertEqual(round(flagged / len(levels) * 100, 1), 58.8)

    def test_level_thresholds(self):
        for score, expected in [(0, "Normal"), (24, "Normal"), (25, "Suspicious"),
                                (59, "Suspicious"), (60, "High-risk"), (100, "High-risk")]:
            with self.subTest(score=score):
                self.assertEqual(level_of(score), expected)

    def test_no_accent(self):
        self.assertEqual(no_accent("Đầu tư lợi nhuận"), "Dau tu loi nhuan")
        self.assertEqual(no_accent("công an"), "cong an")

    def test_normalize_collapses_whitespace(self):
        self.assertEqual(normalize("  Xin\u200b chào\n\nViệt   Nam  "), "xin chào việt nam")

    def test_word_boundary_blocks_substring_false_positives(self):
        """Báo động giả nổi tiếng: 'ib', 'stk', 'công an', 'thuế' nằm trong từ khác.

        Vài mẫu có URL nên vẫn nhận đúng tín hiệu "Có đường link ngoài nền tảng"
        (+12) — điều KHÔNG được phép là bị gắn thêm nhóm lừa đảo nào khác.
        """
        clean_channel = {"channel": "tintuc", "channel_title": "Tin tức"}
        samples = [
            "ban tai lieu o facebook.com/hermibooks xem rat hay",       # 'ib' trong hermibooks
            "xem them example.com/?fbclid=IwARmEstkkkjvi73xyz",         # 'stk' trong base64
            "anh ta duoc phan cong anh trai phu trach",                 # 'công an' trong 'phân công anh'
            "nhung nguoi nop thue dang cho doi",                        # 'thuế' trong 'nộp thuế'
            "muc thue 1-2 do la my moi thung dau",                      # 'thuế' trong 'mức thuế'
        ]
        for text in samples:
            with self.subTest(text=text):
                result = analyze({**clean_channel, "text": text})
                labels = {s["label_vi"] for s in result["signals"]}
                labels -= {"Có đường link ngoài nền tảng"}
                self.assertEqual(labels, set(), f"báo động giả với: {text} -> {result['signals']}")
                self.assertLessEqual(result["scam_score"], 12)

    def test_real_scam_still_detected(self):
        """Không được 'chữa' báo động giả bằng cách làm mất khả năng phát hiện."""
        post = {
            "channel": "vieclam.ctv.hoantien", "channel_title": "Tuyển CTV",
            "text": ("Tuyển cộng tác viên chốt đơn tại nhà, không cần kinh nghiệm, "
                     "hoàn tiền 100%, làm nhiệm vụ nhận hoa hồng. "
                     "Đăng ký ngay: https://t.me/vieclam_bot hoặc ib zalo 0987654321"),
            "has_media": False,
        }
        result = analyze(post)
        self.assertGreaterEqual(result["scam_score"], 60)
        self.assertEqual(result["level"], "High-risk")
        self.assertEqual(result["scam_category"], "Job")
        self.assertTrue(result["entities"]["urls"])

    def test_substring_bug_fixed_for_bot_keyword(self):
        """`bot` trong 'vieclam_bot' không còn bị tính là 'điều hướng sang kênh khác'."""
        result = analyze({"channel": "vieclam.ctv.hoantien", "channel_title": "Tuyển CTV",
                          "text": "Đăng ký: https://t.me/vieclam_bot"})
        labels = [s["label_vi"] for s in result["signals"]]
        self.assertNotIn("Điều hướng sang kênh/nhóm khác", labels)

    def test_bank_account_regex_rejects_amounts(self):
        """Số tiền có dấu chấm/phẩy không được coi là số tài khoản."""
        ent = analyzer.extract_entities("Giá 1.500.000đ, doanh thu 12,345,678 đồng")
        self.assertEqual(ent["bank_accounts"], [])
        ent2 = analyzer.extract_entities("Chuyển vào STK 1234567890123 (Vietcombank)")
        self.assertEqual(ent2["bank_accounts"], ["1234567890123"])


class TestGroupScoring(unittest.TestCase):
    """Điểm rủi ro theo group/kênh (mục [5])."""

    def test_posts_tg_groups_reproduced_exactly(self):
        """8 nhóm trong posts_tg.json phải tái tạo y hệt (mọi khóa, cả thứ tự)."""
        self.assertEqual(group_summary(POSTS_TG["posts"]), POSTS_TG["groups"])

    def test_nhom1_groups_reproduced_exactly(self):
        """Nhóm thật 1000 bài (nhom1.json) — gồm cả `repeat_top_text`."""
        records = NHOM1["posts"] if isinstance(NHOM1, dict) else NHOM1
        expected = NHOM1["groups"] if isinstance(NHOM1, dict) else None
        self.assertIsNotNone(expected, "nhom1.json thiếu khóa 'groups'")
        self.assertEqual(group_summary(records, noun=analyzer.NOUN_API), expected)

    def test_group_formula_weights(self):
        """Công thức đã dò ngược: 0.6*avg + 25*density + 10*concentration + 15*peak."""
        records = [
            {"channel": "x", "channel_title": "X", "text": "",
             "analysis": {"scam_score": 100, "level": "High-risk",
                          "scam_category_vi": "Đầu tư"}},
            {"channel": "x", "channel_title": "X", "text": "",
             "analysis": {"scam_score": 0, "level": "Normal", "scam_category_vi": None}},
        ]
        g = group_summary(records)[0]
        # 0.6*50 + 25*0.5 + 10*(1/2) + 15*1.0 = 30 + 12.5 + 5 + 15 = 62.5 -> 62
        self.assertEqual(g["group_score"], 62)
        self.assertEqual(g["flagged_ratio"], 0.5)
        self.assertEqual(g["verdict"], "Có dấu hiệu lừa đảo — cần kiểm tra kỹ")

    def test_real_news_false_positives_within_budget(self):
        """@aiquochientrang (992 bài tin tức thật): chỉ 1 bài bị gắn cờ, 0 High-risk.

        Lịch sử: 8 bài (khớp chuỗi con + từ khoá 'thuế' quá rộng) -> 3 bài (siết
        ranh giới từ) -> 1 bài (thay 'thuế' bằng 'cơ quan thuế/cán bộ thuế').
        """
        records = NHOM1["posts"]
        flagged = []
        for post in records:
            result = analyze(without_analysis(post))
            if result["level"] != "Normal":
                flagged.append((post["message_id"], result["scam_score"], result["level"]))
        self.assertEqual(len(flagged), 1, f"số bài bị gắn cờ đã đổi: {flagged}")
        self.assertEqual([f for f in flagged if f[2] == "High-risk"], [])


class TestWebParser(unittest.TestCase):
    """Bộ đọc HTML t.me/s của demo_telegram.py (không cần mạng)."""

    PAGE = """
    <div class="tgme_channel_info">
      <div class="tgme_channel_info_header_title">Tin tức Việt Nam</div>
      <div class="tgme_channel_info_header_username"><a href="https://t.me/baomoi">@baomoi</a></div>
      <div class="tgme_channel_info_description">Tin nhanh</div>
      <div class="tgme_channel_info_counters">
        <span class="counter_value">10.4M</span><span class="counter_type">subscribers</span>
      </div>
    </div>
    <div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="baomoi/100">
      <div class="tgme_widget_message_text js-message_text">Xin chào <b>Việt Nam</b><br/>Dòng 2</div>
      <span class="tgme_widget_message_views">1.2K</span>
      <time datetime="2026-01-02T03:04:05+00:00"></time>
      <a class="tgme_widget_message_owner_name" href="https://t.me/baomoi"><span dir="auto">Tin tức</span></a>
      <a class="tgme_widget_message_photo_wrap" href="https://t.me/baomoi/100"></a>
    </div>
    <div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="baomoi/101">
      <a class="tgme_widget_message_forwarded_from_name" href="https://t.me/nguon"><span dir="auto">Nguồn ẩn</span></a>
      <div class="tgme_widget_message_text js-message_text">Bản tin số 2 #tintuc</div>
      <span class="tgme_widget_message_views">900</span>
      <time datetime="2026-01-03T03:04:05+00:00"></time>
      <a class="tgme_widget_message_link_preview" href="https://vnexpress.net/bai-viet"></a>
      <a class="tgme_widget_message_photo_wrap" href="1"></a>
      <a class="tgme_widget_message_photo_wrap" href="2"></a>
    </div>
    <a href="/s/baomoi?before=100" data-before="100">Trước</a>
    """

    def test_channel_name(self):
        for raw in ("durov", "@durov", "t.me/durov", "https://t.me/s/durov", "durov/"):
            with self.subTest(raw=raw):
                self.assertEqual(web.channel_name(raw), "durov")

    def test_channel_name_rejects_invite(self):
        with self.assertRaises(RuntimeError):
            web.channel_name("https://t.me/+AbCdEfGhIj")

    def test_parse_count(self):
        self.assertEqual(web.parse_count("10.4M"), 10_400_000)
        self.assertEqual(web.parse_count("1.2K"), 1_200)
        self.assertEqual(web.parse_count("1 234"), 1234)
        self.assertEqual(web.parse_count(""), 0)

    def test_text_of(self):
        self.assertEqual(web._text_of("Xin chào <b>Việt Nam</b><br/>Dòng 2"),
                         "xin chào việt nam dòng 2")
        self.assertEqual(web._text_of("A &amp; B"), "a & b")

    def test_channel_info(self):
        info = web.parse_channel_info(self.PAGE, "baomoi")
        self.assertEqual(info["channel"], "baomoi")
        self.assertEqual(info["channel_title"], "tin tức việt nam")
        self.assertEqual(info["subscribers"], 10_400_000)

    def test_parse_messages(self):
        info = web.parse_channel_info(self.PAGE, "baomoi")
        records = web.parse_messages(self.PAGE, info)
        self.assertEqual(len(records), 2)

        first, second = records
        self.assertEqual(first["message_id"], "100")
        self.assertEqual(first["url"], "https://t.me/baomoi/100")
        self.assertEqual(first["text"], "xin chào việt nam dòng 2")
        self.assertEqual(first["views"], 1200)
        self.assertEqual(first["posted_at"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(first["subscribers"], 10_400_000)
        self.assertEqual(first["media_types"], ["photo"])
        self.assertTrue(first["has_media"])
        self.assertIsNone(first["forwarded_from"])
        self.assertEqual(first["source"], "telegram")

        self.assertEqual(second["message_id"], "101")
        self.assertEqual(second["forwarded_from"], "nguồn ẩn")
        self.assertEqual(second["link_preview"], "https://vnexpress.net/bai-viet")
        self.assertEqual(second["media_types"], ["photo", "photo"])
        self.assertEqual(second["media_count"], 2)
        self.assertEqual(second["hashtags"], ["tintuc"])  # RE_HASHTAG bỏ dấu '#'
        self.assertEqual(web.RE_HASHTAG.findall("bai #Tintuc #vn"), ["Tintuc", "vn"])

    def test_pagination_marker(self):
        self.assertEqual(web.re.findall(r'data-before="(\d+)"', self.PAGE), ["100"])


class TestUsernameFeatures(unittest.TestCase):
    """Tra cứu username (--user) và nhập keyword -> liệt kê username (--keyword)."""

    CHANNEL_PAGE = """
    <meta property="og:title" content="Pavel Durov">
    <meta property="og:description" content="Founder of Telegram.">
    <div class="tgme_page_title"><span dir="auto">Pavel Durov ✔</span></div>
    <div class="tgme_page_extra">10 397 040 subscribers</div>
    <div class="tgme_page_action"><a class="tgme_action_button_new">View in Telegram</a></div>
    """

    GROUP_PAGE = """
    <meta property="og:title" content="Python Чат">
    <meta property="og:description" content="You can view and join @pythonchat right away.">
    <div class="tgme_page_title"><span dir="auto">Python Чат</span></div>
    <div class="tgme_page_extra">107 members</div>
    <div class="tgme_page_action"><a class="tgme_action_button_new">View in Telegram</a></div>
    """

    BOT_PAGE = """
    <meta property="og:title" content="BotFather">
    <meta property="og:description" content="BotFather is the one bot to rule them all.">
    <div class="tgme_page_extra"><a href="https://t.me/BotFather">@BotFather</a></div>
    <div class="tgme_page_extra">7 560 507 monthly users</div>
    <div class="tgme_page_action"><a class="tgme_action_button_new">Start Bot</a></div>
    """

    USER_PAGE = """
    <meta property="og:title" content="Vitalik">
    <meta property="og:description" content="You can contact @vitalikbuterin right away.">
    <div class="tgme_page_extra">@vitalikbuterin</div>
    <div class="tgme_page_action"><a class="tgme_action_button_new">Send Message</a></div>
    """

    MISSING_PAGE = """
    <meta property="og:title" content="Telegram: Contact @khong_ton_tai_xyz_999">
    <meta property="og:description" content="">
    <div class="tgme_page_action"><a class="tgme_action_button_new">Send Message</a></div>
    """

    def test_parse_profile_channel(self):
        prof = web.parse_profile(self.CHANNEL_PAGE, "durov")
        self.assertEqual(prof["kind"], "channel")
        self.assertEqual(prof["kind_vi"], "Kênh")
        self.assertTrue(prof["exists"])
        self.assertTrue(prof["verified"])
        self.assertEqual(prof["title"], "Pavel Durov")      # giữ nguyên chữ hoa
        self.assertEqual(prof["about"], "Founder of Telegram.")
        self.assertEqual(prof["subscribers"], 10_397_040)
        self.assertEqual(prof["profile_url"], "https://t.me/durov")

    def test_parse_profile_group_bot_user(self):
        group = web.parse_profile(self.GROUP_PAGE, "pythonchat")
        self.assertEqual((group["kind"], group["members"]), ("group", 107))
        bot = web.parse_profile(self.BOT_PAGE, "BotFather")
        self.assertEqual(bot["kind"], "bot")
        self.assertEqual(bot["monthly_users"], 7_560_507)
        self.assertIsNone(bot["subscribers"])
        user = web.parse_profile(self.USER_PAGE, "vitalikbuterin")
        self.assertEqual(user["kind"], "user")
        self.assertTrue(user["about"].startswith("You can contact"))

    def test_parse_profile_missing(self):
        prof = web.parse_profile(self.MISSING_PAGE, "khong_ton_tai_xyz_999")
        self.assertFalse(prof["exists"])
        self.assertEqual(prof["kind"], "unknown")
        self.assertEqual(prof["title"], "")

    def test_username_of_and_regex(self):
        self.assertEqual(
            analyzer.RE_USERNAME.findall("https://t.me/dautu.sieuloinhuan/1000"),
            ["dautu.sieuloinhuan"])                      # dấu '.' phải được giữ
        self.assertEqual(analyzer._username_of("@Admin_Group"), "Admin_Group")
        self.assertEqual(analyzer._username_of("https://t.me/vieclam_bot", strict=True),
                         "vieclam_bot")
        # strict=True: tên hiển thị tự do không được coi là username
        self.assertEqual(analyzer._username_of("Some Display Name", strict=True), "")

    def test_keyword_to_usernames_offline(self):
        records = demo_posts(17, dialect="web")
        for rec in records:
            rec["analysis"] = analyze(rec)
        payload = analyzer.username_payload(records, ["đầu tư", "hoàn tiền"])
        self.assertEqual(payload["keywords"], ["đầu tư", "hoàn tiền"])
        rows = {row["username"]: row for row in payload["usernames"]}
        self.assertIn("dautu.sieuloinhuan", rows)
        self.assertEqual(rows["dautu.sieuloinhuan"]["max_score"], 71)
        self.assertEqual(rows["dautu.sieuloinhuan"]["level"], "High-risk")
        self.assertIn(analyzer.ROLE_VI["link"], rows["dautu.sieuloinhuan"]["roles"])
        self.assertIn("admin_dautu_pro", rows)
        self.assertIn(analyzer.ROLE_VI["mention"], rows["admin_dautu_pro"]["roles"])
        self.assertIn("vieclam_bot", rows)
        self.assertEqual(payload["invite_links"][0]["link"], "https://t.me/+abcdef123")
        # kênh sạch không khớp từ khoá nào -> không xuất hiện
        self.assertNotIn("hocpython.mienphi", rows)
        # từ khoá không khớp gì -> danh sách rỗng
        empty = analyzer.username_payload(records, ["zzz_khong_khop_gi"])
        self.assertEqual(empty["usernames"], [])
        self.assertEqual(empty["invite_links"], [])

    def test_cli_exposes_user_and_keyword(self):
        res = subprocess.run([sys.executable, str(ROOT / "demo_telegram.py"), "--help"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("--user", res.stdout)
        self.assertIn("--keyword", res.stdout)

    def test_web_report_replays_stored_usernames(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "kw.json"
            first = subprocess.run(
                [sys.executable, str(ROOT / "demo_telegram.py"), "--demo", "--limit", "17",
                 "--keyword", "hoàn tiền", "--out", str(out), "--no-report"],
                capture_output=True, text=True)
            self.assertEqual(first.returncode, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["keywords"], ["hoàn tiền"])
            self.assertIn("vieclam.ctv.hoantien", [r["username"] for r in payload["usernames"]])
            second = subprocess.run(
                [sys.executable, str(ROOT / "demo_telegram.py"), "--report", str(out)],
                capture_output=True, text=True)
            self.assertEqual(second.returncode, 0)
            self.assertIn("[6] USERNAME LIÊN QUAN", second.stdout)
            self.assertIn("@vieclam.ctv.hoantien", second.stdout)


class TestBackendsEndToEnd(unittest.TestCase):
    """Chạy thật 2 backend ở chế độ --demo (offline) và kiểm tra JSON ghi ra."""

    def _run_demo(self, module, argv):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            old_argv = sys.argv
            sys.argv = [module.__name__] + argv + ["--out", str(out)]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = module.main()
            finally:
                sys.argv = old_argv
            self.assertEqual(code, 0)
            return json.loads(out.read_text(encoding="utf-8"))

    def test_web_backend_demo(self):
        payload = self._run_demo(web, ["--demo", "--limit", "17"])
        self.assertEqual(payload["total"], 17)
        self.assertEqual(sorted(payload.keys()),
                         ["backend", "generated_at", "groups", "posts", "total"])
        self.assertEqual(len(payload["groups"]), 8)
        self.assertEqual(payload["backend"], "demo (offline)")
        self.assertEqual(web.BACKEND, "web (t.me/s)")
        for post in payload["posts"]:
            self.assertIn("analysis", post)

    def test_api_backend_demo(self):
        import demo_telegram_api as api
        payload = self._run_demo(api, ["--demo", "--limit", "17"])
        self.assertEqual(payload["total"], 17)
        self.assertEqual(payload["backend"], "demo (offline)")
        self.assertEqual(len(payload["groups"]), 8)
        # bản API có thêm forwards/replies, bản web thì không
        self.assertIn("forwards", payload["posts"][0])

    def test_web_keyword_end_to_end(self):
        payload = self._run_demo(web, ["--demo", "--limit", "17", "--keyword", "hoàn tiền"])
        self.assertEqual(sorted(payload.keys()),
                         ["backend", "generated_at", "groups", "invite_links", "keywords",
                          "posts", "total", "usernames"])
        self.assertEqual(payload["keywords"], ["hoàn tiền"])
        self.assertIn("vieclam.ctv.hoantien",
                      [row["username"] for row in payload["usernames"]])

    def test_api_demo_keyword_end_to_end(self):
        import demo_telegram_api as api
        payload = self._run_demo(api, ["--demo", "--limit", "17", "--keyword", "đầu tư"])
        self.assertEqual(payload["keywords"], ["đầu tư"])
        self.assertIn("dautu.sieuloinhuan",
                      [row["username"] for row in payload["usernames"]])

    def test_web_demo_matches_saved_posts_tg(self):
        """--demo bản web phải trùng posts_tg.json, chỉ khác mốc thời gian/backend."""
        payload = self._run_demo(web, ["--demo", "--limit", "17"])
        skip = {"collected_at", "posted_at", "backend", "generated_at"}
        cached = {p["channel"] + "/" + str(p["message_id"]): p for p in POSTS_TG["posts"]}
        for post in payload["posts"]:
            key = post["channel"] + "/" + str(post["message_id"])
            with self.subTest(post=key):
                self.assertIn(key, cached)
                for field, value in post.items():
                    if field in skip:
                        continue
                    self.assertEqual(value, cached[key][field], f"{key}.{field}")
        self.assertEqual(payload["groups"], POSTS_TG["groups"])


class TestSplitTools(unittest.TestCase):
    """Hai file tách riêng: `keyword_usernames.py` (chức năng 1) và `username_info.py` (2)."""

    @staticmethod
    def _run(script: str, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / script)] + argv,
                              capture_output=True, text=True, cwd=str(ROOT))

    def test_keyword_tool_demo_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "usernames.json"
            res = self._run("keyword_usernames.py",
                            ["--demo", "--keyword", "hoàn tiền", "--keyword", "đầu tư",
                             "--out", str(out)])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("[6] USERNAME LIÊN QUAN", res.stdout)
            self.assertIn("Username thấy : 4", res.stdout)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["keywords"], ["hoàn tiền", "đầu tư"])
            best = payload["usernames"][0]
            self.assertEqual(best["username"], "dautu.sieuloinhuan")
            self.assertEqual(best["max_score"], 71)
            self.assertEqual(payload["invite_links"][0]["link"], "https://t.me/+abcdef123")

    def test_keyword_tool_json_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "u.json"
            res = self._run("keyword_usernames.py",
                            ["--json", str(ROOT / "posts_tg.json"), "--keyword", "hoàn tiền",
                             "--min-score", "40", "--out", str(out)])
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads(out.read_text(encoding="utf-8"))
            names = [row["username"] for row in payload["usernames"]]
            self.assertEqual(names, ["vieclam.ctv.hoantien"])   # 44 điểm, bot 36 bị lọc
            self.assertEqual(payload["usernames"][0]["posts"], 2)

    def test_keyword_tool_only_text_drops_channel_name_match(self):
        """Từ khoá chỉ có trong TÊN KÊNH: mặc định khớp, `only_text=True` thì không."""
        record = {"channel": "aiquochientrang", "channel_title": "Tin tức chiến sự",
                  "message_id": "1", "url": "https://t.me/aiquochientrang/1",
                  "text": "giá dầu tăng", "link_preview": None, "forwarded_from": None,
                  "analysis": {"scam_score": 0, "level": "Normal"}}
        default = analyzer.username_payload([record], ["chiến sự"])["usernames"]
        self.assertEqual([row["username"] for row in default], ["aiquochientrang"])
        strict = analyzer.username_payload([record], ["chiến sự"], only_text=True)
        self.assertEqual(strict["usernames"], [])

    def test_keyword_tool_loose_then_word_boundary(self):
        record = {"channel": "kenh_test", "channel_title": "", "message_id": "1",
                  "url": "https://t.me/kenh_test/1", "text": "hermibooks la gi",
                  "link_preview": None, "forwarded_from": None,
                  "analysis": {"scam_score": 0, "level": "Normal"}}
        self.assertEqual(analyzer.related_usernames([record], ["ib"]), [])
        old = analyzer.MATCH_WORD_BOUNDARY
        analyzer.MATCH_WORD_BOUNDARY = False
        try:
            rows = analyzer.related_usernames([record], ["ib"])
        finally:
            analyzer.MATCH_WORD_BOUNDARY = old
        self.assertEqual([row["username"] for row in rows], ["kenh_test"])

    def test_keyword_tool_filter_rows(self):
        import keyword_usernames as kw

        rows = [{"username": "a", "max_score": 71}, {"username": "b", "max_score": 30},
                {"username": "c", "max_score": 10}]
        self.assertEqual([r["username"] for r in kw.filter_rows(rows, 25, 0)], ["a", "b"])
        self.assertEqual([r["username"] for r in kw.filter_rows(rows, 0, 2)], ["a", "b"])
        self.assertEqual(kw.filter_rows(rows, 99, 0), [])

    def test_username_info_reads_json_and_help(self):
        import username_info as info

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "u.json"
            path.write_text(json.dumps({
                "usernames": [{"username": "dautu.sieuloinhuan"}, {"username": "@durov"},
                              {"username": "durov"}, "pythonchat"],
            }), encoding="utf-8")
            self.assertEqual(info.usernames_from_json(str(path)),
                             ["dautu.sieuloinhuan", "durov", "pythonchat"])
        for script in ("keyword_usernames.py", "username_info.py"):
            with self.subTest(script=script):
                res = self._run(script, ["--help"])
                self.assertEqual(res.returncode, 0)

    def test_default_source_requires_keyword(self):
        """Không chọn nguồn + không có từ khoá -> báo lỗi rõ ràng (không tự bịa nguồn)."""
        res = self._run("keyword_usernames.py", [])
        self.assertEqual(res.returncode, 1)
        self.assertIn("Cần ít nhất một từ khoá", res.stderr)

    def test_compact_output_shows_name_and_kind(self):
        """Chế độ gọn: `@username  —  Tên  (Loại, quy mô)`, đúng kiểu người dùng yêu cầu."""
        rows = [{"username": "hathanh", "name": "Thanh Hà", "kind": "user", "max_score": 0,
                 "level": "Normal", "posts": 1, "searches": 1, "keywords": ["hathanh"]},
                {"username": "hathanhtrading", "name": "Hà Thành Trading", "kind": "channel",
                 "scale": "213 người đăng ký", "max_score": 0, "level": "Normal",
                 "posts": 1, "searches": 1}]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            analyzer.print_username_section(rows, ["hathanh"], compact=True)
        out = buf.getvalue()
        self.assertIn("@hathanh  —  Thanh Hà  (Người dùng)", out)
        self.assertIn("@hathanhtrading  —  Hà Thành Trading  (Kênh, 213 người đăng ký)", out)

    def test_prefix_match_sorted_before_alphabet(self):
        """Cùng 0 điểm: username khớp ngay đầu từ khoá phải đứng trước (không theo alphabet)."""
        rows = analyzer.related_usernames([], ["hathanh"], extra_usernames=[
            {"username": "NoloveShopbot", "keywords": ["hathanh"]},
            {"username": "thanhnguyen3989", "keywords": ["hathanh"]},
            {"username": "hathanh7", "keywords": ["hathanh"]},
            {"username": "hathanh", "keywords": ["hathanh"]}])
        self.assertEqual([r["username"] for r in rows],
                         ["hathanh", "hathanh7", "NoloveShopbot", "thanhnguyen3989"])

    def test_search_chats_reads_users_too(self):
        """`contacts.Search` trả người dùng trong `.users` — trước đây bị bỏ sót."""
        import asyncio
        from types import SimpleNamespace
        import demo_telegram_api as api
        if getattr(api, "functions", None) is None:     # system Python không có Telethon
            self.skipTest("chưa cài Telethon")

        class FakeClient:
            def __call__(self, request):
                async def go():
                    return SimpleNamespace(
                        chats=[SimpleNamespace(username="hathanhtrading",
                                               title="Hà Thành Trading", broadcast=True,
                                               participants_count=213),
                               SimpleNamespace(username="hathanhcrypto",
                                               title="Hà Thành Trading ( Chat )",
                                               broadcast=False, participants_count=114)],
                        users=[SimpleNamespace(username="hathanh", first_name="Thanh",
                                               last_name="Hà", bot=False),
                               SimpleNamespace(username="hathanhgrbot", first_name="QuanLy",
                                               last_name="", bot=True)])
                return go()

        rows = asyncio.run(api.search_chats(FakeClient(), "hathanh", limit=20))
        got = {r["username"]: r for r in rows}
        self.assertEqual(len(rows), 4)
        self.assertEqual(got["hathanh"]["name"], "Thanh Hà")
        self.assertEqual(got["hathanh"]["kind"], "user")
        self.assertEqual(got["hathanhgrbot"]["kind"], "bot")
        self.assertEqual(got["hathanhtrading"]["kind"], "channel")
        self.assertEqual(got["hathanhtrading"]["scale"], "213 người đăng ký")
        self.assertEqual(got["hathanhcrypto"]["kind"], "group")

    def test_username_info_requires_input(self):
        res = self._run("username_info.py", [])
        self.assertEqual(res.returncode, 1)
        self.assertIn("Chưa nhập username nào", res.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
