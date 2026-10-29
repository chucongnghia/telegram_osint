#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyzer.py — "nguồn chân lý" duy nhất cho bộ từ điển + chấm điểm lừa đảo.

Module này gom phần phân tích đã từng bị **copy 2 lần** trong `demo_telegram.py`
(bản web) và `demo_telegram_api.py` (bản API), để hai bản không lệch nhau nữa:

    1. Từ điển  : SCAM_GROUPS (6 nhóm lừa đảo) + ACTION_SIGNALS (4 nhóm tín hiệu)
    2. Ngưỡng   : THRESHOLD_SUSPICIOUS / THRESHOLD_HIGH_RISK (+ ngưỡng cho group)
    3. Regex    : URL, SĐT, số tài khoản, hashtag, mention, link mời nhóm
    4. Chuẩn hoá : normalize() (chữ thường) và no_accent() (bỏ dấu)
    5. Chấm 1 bài: analyze(post, ocr_text="") -> điểm 0-100 + mức + nhóm + tín hiệu
    6. Chấm group: group_summary(records, noun=...) -> điểm rủi ro cho cả nhóm/kênh
    7. Trình bày : print_group_section() in mục [5] của báo cáo

Điểm số chỉ là **TÍN HIỆU HỖ TRỢ RÀ SOÁT**, không phải kết luận.

Lịch sử: bản gốc khớp từ khoá bằng phép `k in haystack` (khớp chuỗi con) nên báo
động giả rất nặng — `ib` khớp trong "herm**ib**ooks"/"respons**ib**le",
`công an` khớp trong "phân công anh ta", `stk` khớp trong một chuỗi base64 của
Facebook. Từ bản này, từ khoá được khớp theo **ranh giới từ**
(`(?<!\\w)kw(?!\\w)`) — xem `hits()`.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone

from .storage import persist_payload

# ---------------------------------------------------------------------------
# 1. Bộ từ điển
# ---------------------------------------------------------------------------

SCAM_GROUPS = {
    "Investment": ("Đầu tư / lợi nhuận cao", 22, [
        "đầu tư", "lợi nhuận", "sinh lời", "cam kết lợi nhuận", "làm giàu",
        "kiếm tiền online", "thu nhập thụ động", "sàn đầu tư", "forex",
        "tiền ảo", "usdt", "gấp 3 lần", "gấp 5 lần", "x2 tài khoản",
        "kèo thơm", "bao lời", "cam kết hoàn vốn", "phiên tín hiệu",
    ]),
    "Job": ("Việc làm online giả", 18, [
        "việc làm online", "việc nhẹ lương cao", "tuyển cộng tác viên", "ctv",
        "làm tại nhà", "chốt đơn", "hoàn tiền", "làm nhiệm vụ",
        "không cần kinh nghiệm", "thu nhập 20 triệu", "tuyển gấp", "đa cấp",
        "tuyển đại lý", "cộng tác viên chốt đơn",
    ]),
    "Loan": ("Cho vay trực tuyến", 20, [
        "cho vay", "vay vốn", "vay nhanh", "vay tiền online", "không thế chấp",
        "giải ngân trong ngày", "chỉ cần cccd", "app vay", "lãi suất 0%",
        "vay tín chấp", "tín dụng đen", "bùng nợ", "hỗ trợ nợ xấu",
    ]),
    "Giveaway": ("Trúng thưởng / quà tặng", 16, [
        "trúng thưởng", "nhận quà", "quà tặng miễn phí", "tri ân khách hàng",
        "minigame", "quay số", "nhận thưởng", "phần thưởng", "airdrop",
        "chỉ cần bình luận", "chỉ cần chia sẻ", "like và share", "tặng code",
    ]),
    "Impersonation": ("Giả mạo tổ chức / cá nhân", 20, [
        "giả mạo", "chính chủ", "hotline chính thức", "công an", "tòa án",
        "cơ quan thuế", "cán bộ thuế", "nhân viên thuế",
        "shipper", "nhân viên ngân hàng", "chuyên gia tài chính",
        "mentor", "fanpage chính thức", "tổng đài", "admin chính thức",
    ]),
    "Shopping": ("Mua sắm / bán hàng đáng ngờ", 14, [
        "sale sốc", "giảm giá 90%", "thanh lý", "xả kho", "cọc trước",
        "chuyển khoản trước", "không ship cod", "sỉ giá gốc",
    ]),
}

ACTION_SIGNALS = {
    "money_transfer": ("Yêu cầu chuyển tiền", 20, [
        "chuyển khoản", "cọc trước", "nạp tiền", "nộp phí", "phí xác nhận",
        "stk", "số tài khoản", "momo", "zalopay", "ví điện tử", "nạp usdt",
    ]),
    "commission": ("Hứa hẹn hoa hồng", 14, [
        "hoa hồng", "chiết khấu cao", "thưởng nóng", "giới thiệu người", "ref",
    ]),
    "offplatform": ("Điều hướng sang kênh/nhóm khác", 18, [
        "link bio", "telegram", "zalo", "whatsapp", "inbox", "ib",
        "link đăng ký", "truy cập link", "tải app", "nhóm kín", "sđt",
        "số điện thoại", "join nhóm", "vào nhóm", "add tele",
    ]),
    "urgency": ("Thúc ép / khan hiếm giả tạo", 10, [
        "duy nhất hôm nay", "chỉ còn", "nhanh tay", "số lượng có hạn",
        "cơ hội cuối", "đừng bỏ lỡ", "trong 24h", "slot cuối",
    ]),
}

# ---------------------------------------------------------------------------
# 2. Ngưỡng + quy ước
# ---------------------------------------------------------------------------

THRESHOLD_SUSPICIOUS, THRESHOLD_HIGH_RISK = 25, 60

#: Điểm rủi ro ở mức group dùng lại đúng hai ngưỡng trên.
GROUP_THRESHOLD_SUSPICIOUS, GROUP_THRESHOLD_HIGH_RISK = THRESHOLD_SUSPICIOUS, THRESHOLD_HIGH_RISK

#: Danh từ dùng trong câu "lý do": bản web gọi là "bài", bản API gọi là "tin".
NOUN_WEB, NOUN_API = "bài", "tin"

#: Tỷ lệ bài bị gắn cờ tối thiểu để thêm lý do "N/M ... cần rà soát".
#: Suy ra từ dữ liệu lịch sử: posts_tg.json có tỷ lệ 1.0 → CÓ lý do này;
#: nhom1.json có tỷ lệ 0.008 → KHÔNG có. Mọi ngưỡng trong khoảng (0.008, 1.0]
#: đều khớp dữ liệu cũ; chọn 0.5 (nửa số bài trở lên) cho dễ hiểu.
REASON_DENSITY_MIN = 0.5

#: Bật khớp từ khoá theo ranh giới từ (xem `hits()`). Đặt False để quay lại
#: hành vi cũ (khớp chuỗi con) khi cần so sánh.
MATCH_WORD_BOUNDARY = True

RE_URL = re.compile(r"(https?://[^\s<>\"]+|www\.[^\s<>\"]+|[a-z0-9\-]+\.(?:com|net|vn|org|io|xyz|top|info|site|online|me|app|pro)(?:/[^\s<>\"]*)?)", re.I)
RE_PHONE = re.compile(r"(?<!\d)(?:\+84|0)(?:3|5|7|8|9)\d{8}(?!\d)")
RE_BANK_ACCOUNT = re.compile(r"(?<![\d\w.,-])\d{9,16}(?![\d\w.,-])")
RE_HASHTAG = re.compile(r"#([\w\u00C0-\u1EF9]{2,})", re.U)
RE_MENTION = re.compile(r"@([A-Za-z][\w]{3,31})", re.U)
RE_TG_INVITE = re.compile(r"(?:https?://)?t\.me/(?:\+[\w-]+|joinchat/[\w-]+)", re.I)

#: Link ngoài nền tảng trong nội dung forward/nguồn — dùng để nhận biết nguồn gốc.
RE_FORWARD_SOURCE = re.compile(r"(?:https?://)?t\.me/(?!\+|joinchat/)([A-Za-z0-9_]{4,})")

# ---------------------------------------------------------------------------
# 3. Chuẩn hoá văn bản
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Chữ thường, gộp khoảng trắng, bỏ ký tự vô hình."""
    text = unicodedata.normalize("NFC", str(text or ""))
    text = text.replace("\u200b", " ").replace("\ufeff", "")
    return re.sub(r"\s+", " ", re.sub(r"[\r\n\t]+", " ", text)).strip().lower()


def no_accent(text: str) -> str:
    """Bỏ dấu tiếng Việt: 'đầu tư' -> 'dau tu' (để bắt nội dung viết không dấu)."""
    t = unicodedata.normalize("NFD", text)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", t.replace("đ", "d").replace("Đ", "D"))


# ---------------------------------------------------------------------------
# 4. Khớp từ khoá (theo ranh giới từ)
# ---------------------------------------------------------------------------

_KW_RE_CACHE: dict[str, re.Pattern] = {}


def kw_regex(keyword: str) -> re.Pattern:
    """Regex khớp `keyword` như một **từ trọn vẹn**, có cache."""
    rx = _KW_RE_CACHE.get(keyword)
    if rx is None:
        rx = re.compile(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)")
        _KW_RE_CACHE[keyword] = rx
    return rx


def hits(keywords, haystack: str, plain: str) -> list[str]:
    """Các từ khoá xuất hiện trong `haystack` (có dấu) hoặc `plain` (không dấu).

    Mặc định khớp theo **ranh giới từ** để tránh báo động giả kiểu
    `ib` ∈ "hermibooks" hay `công an` ∈ "phân công anh ta".
    """
    found = []
    for k in keywords:
        if MATCH_WORD_BOUNDARY:
            if kw_regex(k).search(haystack) or kw_regex(no_accent(k)).search(plain):
                found.append(k)
        elif k in haystack or no_accent(k) in plain:
            found.append(k)
    return found


# ---------------------------------------------------------------------------
# 5. Trích xuất thực thể
# ---------------------------------------------------------------------------


def extract_entities(text: str) -> dict:
    urls = []
    for u in RE_URL.findall(text or ""):
        u = u.rstrip(".,;)")
        if u.lower() not in [x.lower() for x in urls]:
            urls.append(u)
    phones = list(dict.fromkeys(RE_PHONE.findall(text or "")))
    accounts = [a for a in dict.fromkeys(RE_BANK_ACCOUNT.findall(text or "")) if a not in phones]
    return {
        "urls": urls[:6],
        "phones": phones[:5],
        "bank_accounts": accounts[:5],
        "mentions": list(dict.fromkeys(RE_MENTION.findall(text or "")))[:6],
        "invite_links": list(dict.fromkeys(RE_TG_INVITE.findall(text or "")))[:5],
    }


# ---------------------------------------------------------------------------
# 6. Chấm điểm một bài đăng
# ---------------------------------------------------------------------------


def analyze(post: dict, ocr_text: str = "") -> dict:
    """Chấm điểm nghi ngờ 0-100. `ocr_text`: chữ đọc được từ ảnh (nếu bật --ocr)."""
    text = post.get("text") or ""
    haystack = normalize(" ".join([text, ocr_text or "",
                                   post.get("channel_title") or "", post.get("channel") or ""]))
    plain = no_accent(haystack)

    score, signals, group_scores = 0.0, [], {}

    for label, (name_vi, weight, keywords) in SCAM_GROUPS.items():
        found = hits(keywords, haystack, plain)
        if found:
            pts = weight * min(1.0, 0.6 + 0.2 * (len(found) - 1))
            group_scores[label] = pts
            signals.append((name_vi, pts, found[:6]))

    if group_scores:
        ranked = sorted(group_scores.items(), key=lambda kv: -kv[1])
        score += ranked[0][1] + sum(p * 0.5 for _, p in ranked[1:3])

    for name_vi, weight, keywords in ACTION_SIGNALS.values():
        found = hits(keywords, haystack, plain)
        if found:
            pts = weight * min(1.0, 0.6 + 0.2 * (len(found) - 1))
            score += pts
            signals.append((name_vi, pts, found[:6]))

    ent = extract_entities(text)
    if post.get("link_preview"):
        ent["urls"] = list(dict.fromkeys(ent["urls"] + [post["link_preview"]]))
    if ocr_text:
        ocr_ent = extract_entities(ocr_text)
        for key in ("urls", "phones", "bank_accounts", "invite_links"):
            ent[key] = list(dict.fromkeys(ent[key] + ocr_ent[key]))
    if ent["urls"]:
        score += 12
        signals.append(("Có đường link ngoài nền tảng", 12.0, ent["urls"]))
    if ent["phones"] or ent["bank_accounts"]:
        score += 15
        signals.append(("Số điện thoại / số tài khoản trong bài", 15.0,
                        (ent["phones"] + ent["bank_accounts"])[:4]))
    if ent["invite_links"]:
        score += 12
        signals.append(("Link mời vào nhóm/kênh riêng (t.me/+...)", 12.0, ent["invite_links"]))
    if post.get("forwarded_from"):
        score += 6
        signals.append(("Bài đăng forward từ nguồn khác", 6.0, [post["forwarded_from"]]))
    if post.get("has_media") and hits(["kết quả", "lệnh", "lợi nhuận", "biến động"],
                                      haystack, plain):
        score += 5
        signals.append(("Ảnh/video kèm nội dung tài chính", 5.0, ["media + nội dung tài chính"]))
    if ocr_text:
        score += 5
        signals.append(("Có chữ đọc được từ ảnh (OCR)", 5.0, [ocr_text[:80]]))

    score = int(round(min(100.0, score)))
    level = level_of(score)
    category = None
    if group_scores and level != "Normal":
        category = max(group_scores.items(), key=lambda kv: kv[1])[0]

    top = sorted(signals, key=lambda s: -s[1])[:3]
    summary = ("Nhóm nghi ngờ: " + SCAM_GROUPS[category][0] if category
               else "Không có nhóm rõ ràng")
    if top:
        summary += " | Tín hiệu chính: " + "; ".join(n for n, _, _ in top)
    else:
        summary = "Không phát hiện tín hiệu đáng ngờ."

    return {
        "scam_score": score, "level": level,
        "scam_category": category,
        "scam_category_vi": SCAM_GROUPS[category][0] if category else None,
        "signals": [{"label_vi": n, "points": round(p, 1), "matched": m} for n, p, m in
                    sorted(signals, key=lambda s: -s[1])],
        "entities": ent, "summary": summary,
    }


# ---------------------------------------------------------------------------
# 7. Mức rủi ro + lời kết
# ---------------------------------------------------------------------------


def level_of(score: float) -> str:
    """0-24 Normal, 25-59 Suspicious, 60-100 High-risk."""
    return ("High-risk" if score >= THRESHOLD_HIGH_RISK
            else "Suspicious" if score >= THRESHOLD_SUSPICIOUS else "Normal")


def verdict_of(level: str) -> str:
    """Câu kết luận tiếng Việt cho một mức rủi ro."""
    return {
        "High-risk": "Có dấu hiệu lừa đảo — cần kiểm tra kỹ",
        "Suspicious": "Nghi ngờ",
    }.get(level, "Chưa thấy dấu hiệu bất thường")


def icon_of(level: str) -> str:
    return {"High-risk": "🔴", "Suspicious": "🟠"}.get(level, "🟢")


def _pct(ratio: float) -> str:
    """0.008 -> '0.8', 1.0 -> '100' (bỏ số 0 thừa)."""
    s = f"{ratio * 100:.1f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


# ---------------------------------------------------------------------------
# 8. Chấm điểm rủi ro theo GROUP/KÊNH
# ---------------------------------------------------------------------------
#
# Công thức (0-100), đã dò ngược và kiểm chứng trên 9 nhóm trong
# posts_tg.json + nhom1.json (ra đúng 81/69/67/67/63/6/0/0 và 9):
#
#     group_score = min(100, round( 0.6 * avg_score
#                                 + 25  * flagged_ratio
#                                 + 10  * (số bài của loại hình nổi bật / tổng số bài)
#                                 + 15  * (max_score / 100) ))
#
# * `round()` của Python (làm tròn chẵn) chứ không phải cắt thành int:
#   nhóm vayvon.nhanh365 có 0.6*40.5 + 25 + 10 + 15*0.49 = 66.65 -> 67.
# * Mẫu số của "độ tập trung loại hình" là **tổng số bài**, không phải số bài bị
#   gắn cờ: nhóm aiquochientrang (1000 bài, 8 bài gắn cờ, 3 bài Investment) cho
#   0.6*4.9 + 25*0.008 + 10*3/1000 + 15*0.36 = 8.57 -> 9 ✔ (nếu chia cho 8 thì ra 12 ✘).

GROUP_WEIGHT_AVG = 0.6
GROUP_WEIGHT_DENSITY = 25.0
GROUP_WEIGHT_CONCENTRATION = 10.0
GROUP_WEIGHT_PEAK = 15.0


def _group_key(record: dict) -> tuple:
    if record.get("peer_type") and record.get("chat_id") is not None:
        return (record["peer_type"], str(record["chat_id"]))
    return (str(record.get("channel") or record.get("channel_title") or "?"),)


def group_summary(records: list[dict], noun: str = NOUN_WEB) -> list[dict]:
    """Tính điểm rủi ro cho từng group/kênh có trong `records`.

    Trả danh sách đã sắp xếp giảm dần theo `group_score`; mỗi phần tử có các khóa
    `channel, channel_title, total_messages, avg_score, max_score, flagged,
    flagged_ratio, levels, categories, repeat_top_text (nếu có), group_score,
    group_level, verdict, reasons`.
    """
    buckets: dict[tuple, list[dict]] = {}
    for rec in records:
        buckets.setdefault(_group_key(rec), []).append(rec)

    out: list[dict] = []
    for items in buckets.values():
        channel = str(items[0].get("channel") or items[0].get("channel_title") or "?")
        scores = [int((r.get("analysis") or {}).get("scam_score", 0)) for r in items]
        total = len(items)
        flagged_posts = [r for r in items if (r.get("analysis") or {}).get("level") != "Normal"]
        flagged = len(flagged_posts)
        ratio = flagged / total if total else 0.0
        avg = round(sum(scores) / total, 1) if total else 0.0
        peak = max(scores) if scores else 0

        levels = Counter((r.get("analysis") or {}).get("level", "Normal") for r in items)
        categories = Counter((r.get("analysis") or {}).get("scam_category_vi")
                             for r in flagged_posts
                             if (r.get("analysis") or {}).get("scam_category_vi"))
        top_cat = categories.most_common(1)[0] if categories else None

        group_score = int(round(min(100.0,
                                    GROUP_WEIGHT_AVG * avg
                                    + GROUP_WEIGHT_DENSITY * ratio
                                    + GROUP_WEIGHT_CONCENTRATION * ((top_cat[1] / total) if top_cat else 0)
                                    + GROUP_WEIGHT_PEAK * (peak / 100.0))))
        group_level = level_of(group_score)

        # --- lý do ---------------------------------------------------------
        reasons: list[str] = []
        if flagged and ratio >= REASON_DENSITY_MIN:
            reasons.append(f"{flagged}/{total} {noun} ({_pct(ratio)}%) cần rà soát")
        if top_cat:
            reasons.append(f"loại hình nổi bật: {top_cat[0]} ({top_cat[1]} {noun})")
        if peak >= THRESHOLD_HIGH_RISK:
            reasons.append(f"có {noun} đạt {peak}/100 (High-risk)")
        if group_level != "Normal":
            # Làm tròn về số nguyên cho câu lý do (đúng như dữ liệu lịch sử:
            # 59.7 -> 60, 40.5 -> 40, 45.5 -> 46).
            reasons.append(f"điểm trung bình {round(avg)}/100")

        record = {
            "channel": channel,
            "channel_title": items[0].get("channel_title") or channel,
            "total_messages": total,
            "avg_score": avg,
            "max_score": peak,
            "flagged": flagged,
            "flagged_ratio": round(ratio, 3),
            "levels": dict(levels),
            "categories": dict(categories),
        }
        if items[0].get("peer_type") and items[0].get("chat_id") is not None:
            record.update({key: items[0][key] for key in ("chat_id", "peer_type", "entity_kind") if key in items[0]})
        # `repeat_top_text`: số nội dung khác nhau bị **spam lặp từ 3 lần trở lên**.
        # Ngữ nghĩa này dò ra từ mẫu lịch sử duy nhất còn lưu (nhom1.json:
        # giá trị 2, trong khi nhóm có 3 nội dung lặp >= 2 lần và 1 nội dung lặp 4 lần).
        repeat = sum(1 for text, n in Counter(r.get("text") or "" for r in items).items()
                     if text and n >= 3)
        if repeat:
            record["repeat_top_text"] = repeat
        record.update({
            "group_score": group_score,
            "group_level": group_level,
            "verdict": verdict_of(group_level),
            "reasons": reasons,
        })
        out.append(record)

    out.sort(key=lambda g: -g["group_score"])
    return out


#: Tên gọi khác cho dễ đọc ở nơi khác trong code.
groups_payload = group_summary


def group_risk_for_channel(records: list[dict], channel: str, noun: str = NOUN_WEB) -> dict | None:
    """Điểm rủi ro của **một** kênh/nhóm (None nếu không có bài nào)."""
    for g in group_summary(records, noun=noun):
        if g["channel"] == channel:
            return g
    return None


# ---------------------------------------------------------------------------
# 9. Trình bày mục [5] của báo cáo
# ---------------------------------------------------------------------------


def group_lines(group: dict, noun: str = NOUN_WEB, width: int = 100) -> list[str]:
    """Các dòng mô tả một group (đã xuống dòng, thụt lề) cho mục [5]."""
    head = (f"    {icon_of(group['group_level'])} [ {group['group_score']:>3}/100 | "
            f"{group['group_level']} ] {group['channel_title']} (@{group['channel']})")
    stats = [f"{group['verdict']} — {group['total_messages']} {noun}",
             f"điểm TB {group['avg_score']:g}",
             f"cao nhất {group['max_score']}"]
    if group["flagged"]:
        stats.append(f"{group['flagged']} {noun} cần rà soát ({_pct(group['flagged_ratio'])}%)")
    lines = [head, _wrap("        " + ", ".join(stats), width, 8)]

    reasons = []
    for r in group["reasons"]:
        # Bản in màn hình dùng "ở mức cần rà soát" (file JSON ghi ngắn hơn).
        reasons.append(r.replace(" cần rà soát", " ở mức cần rà soát", 1)
                       if r.endswith("cần rà soát") else r)
    if reasons:
        lines.append(_wrap("        Lý do: " + "; ".join(reasons), width, 15))
    return lines


def _wrap(text: str, width: int, indent: int) -> str:
    """Ngắt dòng đơn giản, các dòng sau thụt lề `indent` khoảng trắng."""
    lead = text[:len(text) - len(text.lstrip())]
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = " " * indent + w
        else:
            cur = (cur + " " + w) if cur else lead + w
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def print_group_section(groups: list[dict], noun: str = NOUN_WEB,
                        title: str = "[5] ĐIỂM RỦI RO THEO GROUP/KÊNH",
                        width: int = 100, limit: int = 10) -> None:
    """In mục [5]: điểm rủi ro của từng group/kênh (nặng nhất trước)."""
    print(f"\n{title}")
    if not groups:
        print("    (không có dữ liệu)")
        return
    for g in groups[:limit]:
        for line in group_lines(g, noun=noun, width=width):
            print(line)
    if len(groups) > limit:
        print(f"    ... và {len(groups) - limit} group khác (xem khóa `groups` trong file JSON)")


# ---------------------------------------------------------------------------
# 8. Trình bày báo cáo + ghi JSON (dùng chung cho cả hai backend)
# ---------------------------------------------------------------------------

def _public(record: dict) -> dict:
    """Bỏ tham chiếu nội bộ (message của Telethon) nhưng GIỮ `_note` của dữ liệu mẫu."""
    return {k: v for k, v in record.items() if k != "_msg_ref"}


def print_report(records: list[dict], sources: list[str] | None = None,
                 title: str = "BÁO CÁO THU THẬP TELEGRAM", noun: str = NOUN_WEB,
                 width: int = 74) -> None:
    """In báo cáo [1]–[5]: tổng quan, nhóm lừa đảo, top kênh, top bài, điểm theo group."""
    total = len(records)
    levels = Counter((r.get("analysis") or {}).get("level", "Normal") for r in records)
    cats = Counter((r.get("analysis") or {}).get("scam_category_vi") for r in records
                   if (r.get("analysis") or {}).get("scam_category_vi"))
    channels = Counter(r["channel"] for r in records)
    groups = group_summary(records, noun=noun)
    line = "=" * width

    print("\n" + line)
    print(title.center(width))
    if sources:
        print(("Nguồn: " + ", ".join(sources))[:width].center(width))
    print(line)
    print(f"\n[1] TỔNG QUAN: {total} bài đăng")
    for lv in ("Normal", "Suspicious", "High-risk"):
        print(f"    {lv:<12}: {levels.get(lv, 0)}")
    if total:
        flagged = sum(levels.get(l, 0) for l in ("Suspicious", "High-risk"))
        print(f"    Cần rà soát : {flagged / total * 100:.1f}%")

    print("\n[2] NHÓM LỪA ĐẢO (mức 2):")
    for name, n in (cats.most_common() or [("(không có)", 0)]):
        print(f"    {name:<34} {n:>3}  {'█' * n}")

    print("\n[3] TOP KÊNH:")
    for ch, n in channels.most_common(8):
        print(f"    {('@' + ch):<30} {n:>3}  {'█' * n}")

    print("\n[4] TOP BÀI ĐĂNG NGHI NGỜ CAO NHẤT:")
    for r in sorted(records, key=lambda x: -((x.get("analysis") or {}).get("scam_score", 0)))[:5]:
        a = r.get("analysis") or {}
        extra = " | OCR" if r.get("ocr_text") else ""
        print(f"    [{a.get('scam_score', 0):>3}/100 | {a.get('level', '?'):<10}] @{r.get('channel', '?')} "
              f"({a.get('scam_category_vi') or 'không rõ nhóm'}){extra}")
        if r.get("url"):
            print(f"        {r['url']}")
        print(f"        {(a.get('summary') or '')[:160]}")

    print_group_section(groups, noun=noun)
    print("\nLưu ý: điểm số là TÍN HIỆU HỖ TRỢ RÀ SOÁT, không phải kết luận.")
    print(line)


def print_history(records: list[dict], sources: list[str] | None = None,
                  noun: str = NOUN_WEB) -> None:
    """In từng tin nhắn đã thu thập: id, thời gian, lượt xem, media, nội dung."""
    if sources:
        print("\n[i] Nguồn: " + ", ".join(sources))
    print(f"[i] Tổng: {len(records)} {noun} nhắn")
    print("-" * 78)
    for i, r in enumerate(records, 1):
        when = (r.get("posted_at") or "")[:19].replace("T", " ")
        media = ""
        if r.get("media_count"):
            kinds = list(dict.fromkeys(r.get("media_types") or []))
            label = ",".join(kinds) or "media"
            media = f" [{r['media_count']} {label}]" if r["media_count"] > 1 else f" [{label}]"
        extra = f" fwd={r['forwards']}" if r.get("forwards") else ""
        print(f"{i:>4}. {r.get('channel', '?')}/{r.get('message_id', '?')}  {when}  "
              f"views={r.get('views', 0)}{extra}{media}")
        if r.get("url"):
            print(f"      {r['url']}")
        text = (r.get("text") or "").strip()
        print(f"      {text[:400]}{' ...' if len(text) > 400 else ''}" if text
              else "      (không có nội dung chữ)")
    print("-" * 78)


def json_payload(records: list[dict], backend: str, noun: str = NOUN_WEB,
                 extra: dict | None = None) -> dict:
    """Build the shared JSON/storage payload without writing or persisting it."""
    out = [_public(r) for r in records]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(out),
        "backend": backend,
        "groups": group_summary(out, noun=noun),
        "posts": out,
    }
    if extra:
        payload.update(extra)
    return payload


def write_json(path: str, records: list[dict], backend: str, noun: str = NOUN_WEB,
               extra: dict | None = None) -> list[dict]:
    """Ghi {generated_at, total, backend, groups, posts} ra JSON.

    `groups` chính là mục [5] ĐIỂM RỦI RO THEO GROUP/KÊNH (xem group_summary).
    `extra` là các khóa ghi thêm (ví dụ `usernames` của chức năng tra theo từ khoá).
    """
    payload = json_payload(records, backend, noun=noun, extra=extra)
    with open(_ensure_dir(path), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    persist_payload(payload, source=path)
    out = payload["posts"]
    print(f"[i] Đã lưu {len(out)} bài đăng vào {path}")
    return out


# ---------------------------------------------------------------------------
# 9. Dữ liệu mẫu offline cho --demo (không cần mạng, không cần tài khoản)
# ---------------------------------------------------------------------------
# Cấu trúc: (username, tên hiển thị, số người đăng ký, [tin nhắn]).
# Mỗi tin: (nội dung, số media, có forward?, lượt xem).
# 5 kênh đầu là kênh lừa đảo, 3 kênh cuối là kênh sạch (để kiểm chứng công cụ
# không báo động giả).
# dialect="web" (bản t.me/s) KHÔNG có `forwards`/`replies`; dialect="api" thì có.

DEMO_ITEMS = [
    ("dautu.sieuloinhuan", "Đầu tư tài chính - Lợi nhuận 30%/tháng", 48200, [
        ("Cam kết lợi nhuận 30%/tháng, đầu tư chỉ từ 2 triệu, rút tiền trong ngày! "
         "Anh em vào nhóm kín để nhận kèo: https://t.me/+AbCdEf123 #dautu #loinhuan", 2, True, 1446),
        ("Kết quả tuần này: 12 lệnh thắng liên tiếp, tài khoản x2 sau 7 ngày. "
         "Nạp tối thiểu 5 triệu, hoa hồng giới thiệu 20%. Inbox @admin_dautu_pro", 3, False, 3856),
        ("Chỉ còn 5 slot cuối cho gói VIP tháng 11, cam kết hoàn vốn 100%. "
         "Chuyển khoản STK 1234567890 (Vietcombank) rồi báo admin để kích hoạt.", 1, False, 6266),
    ]),
    ("vieclam.ctv.hoantien", "Tuyển CTV chốt đơn - Hoàn tiền 100%", 96400, [
        ("Tuyển cộng tác viên chốt đơn tại nhà, không cần kinh nghiệm, thu nhập 20 triệu/tháng. "
         "Làm nhiệm vụ hoàn tiền 100% đơn hàng. Đăng ký: https://t.me/vieclam_bot", 1, True, 2892),
        ("Hướng dẫn làm nhiệm vụ: nạp 500k vào ví, chốt 3 đơn là rút được cả gốc lẫn lãi. "
         "Liên hệ zalo 0987654321 để được hỗ trợ.", 1, False, 7712),
    ]),
    ("vayvon.nhanh365", "Hỗ trợ vay vốn online", 27300, [
        ("Vay tiền online chỉ cần CCCD, không thế chấp, giải ngân trong ngày, lãi suất 0% tháng đầu. "
         "Đăng ký app vay: https://vay365.example.com/dangky", 2, False, 819),
        ("Hỗ trợ nợ xấu, bùng nợ vẫn vay được. Hồ sơ đơn giản, duyệt trong 5 phút. "
         "SĐT 0912345678 (call/zalo).", 1, False, 2184),
    ]),
    ("quatang.trian2026", "Sự kiện tri ân - Trúng thưởng", 143000, [
        ("Chúc mừng 100 khách hàng may mắn trúng thưởng iPhone 16 Pro Max! "
         "Chỉ cần chia sẻ bài viết và bình luận. Nhận quà miễn phí, số lượng có hạn.", 3, True, 4290),
        ("Danh sách trúng thưởng đã có, ai có tên thì chuyển phí vận chuyển 150k "
         "để nhận quà. STK: 9876543210 - Momo: 0987654321", 1, False, 11440),
    ]),
    ("hotro.nganhang.vn", "Hỗ trợ tài khoản ngân hàng", 8900, [
        ("Thông báo: tài khoản của bạn có giao dịch bất thường. Vui lòng xác nhận "
         "số tài khoản và chuyển phí xác nhận 500.000đ để mở lại dịch vụ. "
         "Hotline chính thức 0912345678.", 1, False, 267),
    ]),
    ("tintuc.congnghe", "Tin tức công nghệ", 245000, [
        ("Telegram ra mắt tính năng mới cho phép tìm kiếm trong kênh và nhóm, "
         "cùng nhiều cải tiến về bảo mật.", 1, False, 7350),
        ("Tổng hợp các mẫu điện thoại tầm trung đáng mua trong tháng này.", 2, False, 19600),
        ("Cách bật xác thực hai lớp để bảo vệ tài khoản Telegram của bạn.", 1, False, 31850),
    ]),
    ("hocpython.mienphi", "Học lập trình Python miễn phí", 31200, [
        ("Bài 1: Biến và kiểu dữ liệu trong Python. Tài liệu tham khảo ở phần bình luận.", 2, False, 936),
        ("Bài 2: Vòng lặp for/while, bài tập cuối bài có đáp án.", 1, False, 2496),
    ]),
    ("amthuc.mebap", "Ẩm thực mỗi ngày", 58700, [
        ("Công thức phở bò nước dùng trong 8 tiếng, chi tiết từng bước.", 3, False, 1761),
        ("Mẹo chọn thịt tươi ngon khi đi chợ.", 2, False, 4696),
    ]),
]


def demo_posts(limit: int, dialect: str = "web") -> list[dict]:
    """Sinh `limit` bài mẫu offline (mặc định theo dạng của bản web t.me/s)."""
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for ci, (channel, title, subs, messages) in enumerate(DEMO_ITEMS):
        for mi, (text, media_count, forwarded, views) in enumerate(messages):
            if len(out) >= limit:
                return out
            posted = now - timedelta(days=ci, hours=mi * 3 + 1)
            text = normalize(text)
            rec = {
                "message_id": str(1000 + ci * 100 + mi),
                "url": f"https://t.me/{channel}/{1000 + ci * 100 + mi}",
                "channel": channel, "channel_title": title, "subscribers": subs,
                "text": text,
                "hashtags": list(dict.fromkeys(h.lower() for h in RE_HASHTAG.findall(text))),
                "posted_at": posted.isoformat(), "views": views, "author": title,
                "forwarded_from": ("kênh ẩn danh" if forwarded else None),
                "has_media": media_count > 0, "media_types": ["photo"] * media_count,
                "media_count": media_count, "link_preview": "",
                "source": "demo", "collected_at": now.isoformat(),
                "_note": "DỮ LIỆU MẪU offline - không phải bài đăng thật",
                "pipeline_status": {"collected": True, "downloaded": False, "audio_extracted": False,
                                    "transcribed": False, "frames_extracted": False, "ocr_done": False},
            }
            if dialect == "api":
                rec["forwards"] = views // 20
                rec["replies"] = views // 40
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# 10. Tra USERNAME liên quan tới từ khoá ("nhập keyword -> liệt kê username")
# ---------------------------------------------------------------------------
#
# Telegram KHÔNG cho tìm username theo từ khoá trên trang công khai, nên cách làm
# là đi từ nội dung: lọc những bài ĐÃ THU THẬP có chứa từ khoá, rồi rút ra mọi
# username "dính" tới các bài đó:
#
#   * `@mention` trong nội dung                        -> mentions
#   * link `t.me/<username>` trong bài / link preview  -> links
#   * username của chính kênh đăng bài                 -> channels
#   * nguồn forward dạng `t.me/<username>` hoặc `@x`   -> forwards
#   * (chỉ ở bản API) chat trả về từ tìm kiếm chat     -> searches
#
# Link mời `t.me/+hash` KHÔNG có username nên được liệt kê riêng ở `invites`.

#: Username Telegram: 4–32 ký tự, bắt đầu bằng chữ; cho phép cả dấu `.` vì một số
#: link/`data-post` dùng dạng `ten.kenh` (ví dụ dữ liệu mẫu `dautu.sieuloinhuan`).
RE_USERNAME = re.compile(r"(?:https?://)?t\.me/(?!\+|joinchat/|s/)([A-Za-z][A-Za-z0-9_.]{3,31})",
                         re.I)
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{3,31}$")

ROLE_VI = {
    "channel": "kênh có bài chứa từ khoá",
    "mention": "được @nhắc trong bài",
    "link": "có link t.me/… trong bài",
    "forward": "là nguồn forward của bài",
    "search": "kết quả tìm kiếm chat (API)",
}


def keywords_in(text: str, keywords) -> list[str]:
    """Các từ khoá có trong `text` (khớp ranh giới từ, hiểu cả chữ không dấu)."""
    hay = normalize(text or "")
    return hits(list(keywords), hay, no_accent(hay))


def _username_of(value: str, strict: bool = False) -> str:
    """Rút username từ `t.me/x`, `@x`, hoặc `x`.

    `strict=True` chỉ nhận dạng có tiền tố rõ ràng (`t.me/`, `@`) — dùng cho
    trường `forwarded_from`, vì trường đó thường là **tên hiển thị** chứ không
    phải username.
    """
    value = (value or "").strip()
    if not value:
        return ""
    m = RE_USERNAME.search(value)
    if m:
        return m.group(1)
    if value.startswith("@"):
        value = value[1:]
    if strict:
        return ""
    return value if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", value) else ""


def _record_text(record: dict, only_text: bool = False) -> str:
    """Nội dung dùng để lọc từ khoá: text + link preview + tên/mã kênh.

    `only_text=True` chỉ xét **nội dung bài** (text + link preview), bỏ tên/mã kênh —
    nhờ vậy từ khoá trùng tên kênh không làm mọi bài của kênh đều khớp.
    """
    parts = [record.get("text") or "", record.get("link_preview") or ""]
    if not only_text:
        parts += [record.get("channel_title") or "", record.get("channel") or ""]
    return " ".join(parts)


def related_usernames(records: list[dict], keywords=None, include_channels: bool = True,
                      limit_examples: int = 3, extra_usernames=None,
                      only_text: bool = False) -> list[dict]:
    """Danh sách username liên quan tới `keywords` (rỗng = xét mọi bài).

    `extra_usernames`: các dòng bổ sung đã ở dạng `{"username", "keywords", "source"}`
    (dùng cho kết quả tìm kiếm chat của bản API).

    `only_text=True`: chỉ khớp từ khoá trong **nội dung bài** và bỏ luôn vai trò
    "bài đăng trong kênh" (kênh chỉ xuất hiện khi được nhắc/link thật trong bài).

    Trả về list đã sắp xếp: điểm cao nhất → số bài → tên.
    """
    kws = [k.strip() for k in (keywords or []) if k and k.strip()]
    rows: dict[str, dict] = {}

    def row_of(username: str) -> dict:
        key = username.lower()
        row = rows.get(key)
        if row is None:
            row = rows[key] = {
                "username": username, "profile": f"https://t.me/{username}",
                "mentions": 0, "links": 0, "forwards": 0, "channels": 0, "searches": 0,
                "max_score": 0, "level": "Normal",
                "_seen": set(), "_keywords": set(), "_roles": set(), "examples": [],
            }
        return row

    def add(username: str, kind: str, record: dict | None, kw_list) -> None:
        if not username:
            return
        row = row_of(username)
        row[f"{kind}s" if kind != "search" else "searches"] += 1
        row["_roles"].add(ROLE_VI[kind])
        row["_keywords"].update(kw_list)
        if record is not None:
            key = f"{record.get('channel')}/{record.get('message_id')}"
            row["_seen"].add(key)
            score = int((record.get("analysis") or {}).get("scam_score", 0))
            if score >= row["max_score"]:
                row["max_score"] = score
                row["level"] = (record.get("analysis") or {}).get("level", "Normal")
            url = record.get("url")
            if url and url not in row["examples"] and len(row["examples"]) < limit_examples:
                row["examples"].append(url)

    matched = 0
    for rec in records:
        kw_list = keywords_in(_record_text(rec, only_text), kws) if kws else []
        if kws and not kw_list:
            continue
        matched += 1
        text = rec.get("text") or ""
        for name in RE_MENTION.findall(text):
            add(name, "mention", rec, kw_list)
        sources = [text, rec.get("link_preview") or ""]
        if not only_text:
            sources.append(rec.get("url") or "")
        for source in sources:
            for name in dict.fromkeys(RE_USERNAME.findall(source or "")):
                add(name, "link", rec, kw_list)
        fwd = _username_of(rec.get("forwarded_from"), strict=True)
        if fwd:
            add(fwd, "forward", rec, kw_list)
        if include_channels and not only_text:
            add(_username_of(rec.get("channel")), "channel", rec, kw_list)

    for item in (extra_usernames or []):
        username = _username_of(item.get("username"))
        add(username, "search", None, item.get("keywords") or kws)
        if username:                    # kết quả tìm chat có sẵn tên/loại/quy mô -> in kèm
            row = row_of(username)
            for key in ("name", "kind", "scale"):
                if item.get(key) and not row.get(key):
                    row[key] = item[key]

    out: list[dict] = []
    for row in rows.values():
        row["posts"] = len(row.pop("_seen"))
        row["keywords"] = sorted(row.pop("_keywords"))
        row["roles"] = sorted(row.pop("_roles"))
        if row["posts"] == 0:
            row["posts"] = row["searches"]
        out.append(row)

    def rank(row: dict) -> tuple:
        """Điểm cao trước; cùng điểm thì tên khớp NGAY ĐẦU từ khoá lên trước.

        Nhờ vậy `--keyword "hathanh"` xếp `@hathanh`, `@hathanh7`... trước
        `@NoloveShopbot`, thay vì thuần theo alphabet.
        """
        user = row["username"].lower()
        plain = no_accent(user)
        prefix = 1
        for kw in row.get("keywords") or []:
            kw = (kw or "").lower().strip()
            if kw and (user.startswith(kw) or plain.startswith(no_accent(kw))):
                prefix = 0
                break
        return (-row["max_score"], prefix, -row["posts"], user)

    out.sort(key=rank)
    return out


def related_invite_links(records: list[dict], keywords=None,
                         only_text: bool = False) -> list[dict]:
    """Link mời `t.me/+hash` trong các bài chứa từ khoá (không có username để tra)."""
    kws = [k.strip() for k in (keywords or []) if k and k.strip()]
    rows: dict[str, dict] = {}
    for rec in records:
        kw_list = keywords_in(_record_text(rec, only_text), kws) if kws else []
        if kws and not kw_list:
            continue
        for link in extract_entities(rec.get("text") or "")["invite_links"]:
            row = rows.setdefault(link, {"link": link, "count": 0, "keywords": set(),
                                         "examples": []})
            row["count"] += 1
            row["keywords"].update(kw_list)
            url = rec.get("url")
            if url and len(row["examples"]) < 3:
                row["examples"].append(url)
    out = []
    for row in rows.values():
        row["keywords"] = sorted(row.pop("keywords"))
        out.append(row)
    out.sort(key=lambda r: (-r["count"], r["link"]))
    return out


def _entry_label(row: dict) -> str:
    """Hậu tố '— Tên (Loại, quy mô)' cho một dòng username, rỗng nếu không biết."""
    name = (row.get("name") or "").strip()
    kind = PROFILE_KIND_VI.get(row.get("kind") or "", "")
    scale = (row.get("scale") or "").strip()
    if not (name or kind or scale):
        return ""
    inside = ", ".join(x for x in (kind, scale) if x)
    if name and inside:
        return f"  —  {name}  ({inside})"
    if name:
        return f"  —  {name}"
    return f"  ({inside})"


def print_username_section(rows: list[dict], keywords=None, invites=None, noun: str = NOUN_WEB,
                           width: int = 100, limit: int = 50, compact: bool = False,
                           title: str = "[6] USERNAME LIÊN QUAN TỚI TỪ KHOÁ") -> None:
    """In mục [6]: danh sách username có thể liên quan tới từ khoá đã nhập.

    `compact=True` in mỗi username một dòng gọn `@username — Tên (Loại)`; dùng khi
    chỉ có kết quả tìm kiếm nên không có bài nào để chấm điểm.
    """
    line = "-" * width
    print(f"\n{title}")
    if keywords:
        print(f"    Từ khoá: {', '.join(keywords)}")
    if not rows:
        print("    (không tìm thấy username nào liên quan)")
    for i, row in enumerate(rows[:limit], 1):
        icon = icon_of(row.get("level", "Normal"))
        score = row.get("max_score", 0)
        label = _entry_label(row)
        if compact:
            grade = f"  [ {score}/100 {row.get('level', '?')} ]" if score else ""
            print(f"  {i:>3}. @{row['username']}{label}{grade}")
            continue
        print(f"  {i:>3}. {icon} [ {score:>3}/100 | {row.get('level', '?'):<10} ] "
              f"@{row['username']}{label}")
        print(f"       {row.get('profile', '')}")
        bits = []
        for key, label in (("mentions", "@nhắc"), ("links", "link t.me"), ("forwards", "nguồn forward"),
                           ("channels", "bài đăng trong kênh")):
            if row.get(key):
                bits.append(f"{label} {row[key]}")
        if row.get("searches"):
            bits.append(f"kết quả tìm chat {row['searches']}")
        detail = f"       gặp trong {row.get('posts', 0)} {noun}"
        if bits:
            detail += " — " + ", ".join(bits)
        print(detail)
        if row.get("keywords"):
            print(f"       từ khoá khớp: {', '.join(row['keywords'])}")
        for url in row.get("examples") or []:
            print(f"       ví dụ: {url}")
    if invites:
        print(f"\n    Link mời nhóm kín gặp trong các bài đó ({len(invites)}):")
        for row in invites[:limit]:
            kws = f"  [từ khoá: {', '.join(row['keywords'])}]" if row.get("keywords") else ""
            print(f"      {row['link']}  ×{row['count']}{kws}")
    print(line)


def username_payload(records: list[dict], keywords=None, extra_usernames=None,
                     only_text: bool = False) -> dict:
    """Gói kết quả "nhập keyword -> liệt kê username" để vừa in vừa ghi JSON."""
    kws = [k.strip() for k in (keywords or []) if k and k.strip()]
    return {
        "keywords": kws,
        "usernames": related_usernames(records, kws, extra_usernames=extra_usernames,
                                       only_text=only_text),
        "invite_links": related_invite_links(records, kws, only_text=only_text),
    }


def print_username_payload(payload: dict, noun: str = NOUN_WEB, **kwargs) -> None:
    """In mục [6] từ gói dữ liệu do `username_payload()` tạo."""
    print_username_section(payload.get("usernames") or [], payload.get("keywords"),
                           payload.get("invite_links"), noun=noun, **kwargs)


# ---------------------------------------------------------------------------
# 11. Tra cứu một username: đây là ai / kênh nào? ("tìm người bằng username")
# ---------------------------------------------------------------------------
#
# Dùng chung cho cả hai backend. Hai backend điền được các trường khác nhau:
#   * bản web đọc trang công khai `https://t.me/<username>` -> kind, title, about,
#     số người đăng ký/thành viên, "bản xem trước" có hay không;
#   * bản API dùng Telethon `get_entity()` -> thêm `id`, `verified`, `is_bot`,
#     `participants_count`, ngày tạo, v.v.
# Hàm `make_profile()` là nơi chuẩn hoá để hai bên ra cùng một cấu trúc.

PROFILE_KIND_VI = {
    "channel": "Kênh",
    "group": "Nhóm",
    "bot": "Bot",
    "user": "Người dùng",
    "unknown": "Không rõ",
}


def make_profile(username: str, kind: str = "unknown", **fields) -> dict:
    """Chuẩn hoá thông tin một tài khoản/kênh/nhóm về cùng một cấu trúc."""
    username = (username or "").lstrip("@").strip()
    profile = {
        "username": username,
        "profile_url": f"https://t.me/{username}" if username else "",
        "kind": kind if kind in PROFILE_KIND_VI else "unknown",
        "kind_vi": PROFILE_KIND_VI.get(kind, PROFILE_KIND_VI["unknown"]),
        "exists": bool(fields.pop("exists", True)),
        "title": "", "about": "", "subscribers": None, "members": None,
        "monthly_users": None, "account_id": None, "verified": None,
        "is_bot": None, "source": "", "fetched_at": datetime.now(timezone.utc).isoformat(),
        # --- chi tiết thêm (điền được tuỳ backend) ---
        "phone": "", "photo_url": "", "photo_path": "", "has_photo": None,
        "last_seen": "", "created_at": "", "lang_code": "", "usernames": [],
        "premium": None, "scam": None, "fake": None, "restricted": None,
        "mutual_contact": None, "is_contact": None, "bot_commands": [],
        "common_groups": [], "common_chats_count": None,
    }
    profile.update({k: v for k, v in fields.items() if v not in (None, "")})
    return profile


def _format_count(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def profile_scale(profile: dict) -> str:
    """Câu mô tả quy mô: '10 397 035 người đăng ký', '107 thành viên'…"""
    if profile.get("subscribers") is not None:
        return f"{_format_count(profile['subscribers'])} người đăng ký"
    if profile.get("members") is not None:
        return f"{_format_count(profile['members'])} thành viên"
    if profile.get("monthly_users") is not None:
        return f"{_format_count(profile['monthly_users'])} người dùng/tháng"
    return ""


def _phone_line(profile: dict) -> str:
    """Câu về số điện thoại: Telegram chỉ trả khi là danh bạ hoặc họ để công khai."""
    if profile.get("phone"):
        return f"{profile['phone']}  (danh bạ/công khai — không phải lúc nào cũng thấy)"
    if profile.get("source") == "telethon":
        return "(không có — Telegram chỉ hiện khi là danh bạ, hoặc họ để công khai)"
    return "(cần --api: bản web không thấy được số điện thoại)"


def _photo_line(profile: dict) -> str:
    if profile.get("photo_path"):
        return f"đã lưu: {profile['photo_path']}"
    if profile.get("has_photo"):
        return "có ảnh đại diện (thêm --photo-dir DIR để tải về)"
    if profile.get("photo_url"):
        return profile["photo_url"]
    if profile.get("has_photo") is False:
        return "(không có ảnh đại diện)"
    return ""


def _flags_line(profile: dict) -> str:
    bits = []
    for key, label in (("is_bot", "bot"), ("premium", "Premium"), ("scam", "bị gắn cờ lừa đảo"),
                       ("fake", "bị gắn cờ giả mạo"), ("restricted", "bị hạn chế"),
                       ("mutual_contact", "liên hệ hai chiều"), ("is_contact", "trong danh bạ")):
        if profile.get(key):
            bits.append(label)
    return ", ".join(bits)


def _other_usernames(profile: dict) -> str:
    names = [u for u in (profile.get("usernames") or []) if u and u != profile.get("username")]
    return ", ".join("@" + u for u in names)


def print_profile(profile: dict, records: list[dict] | None = None, noun: str = NOUN_WEB,
                  width: int = 100, title: str = "[0] THÔNG TIN TÀI KHOẢN TRA CỨU",
                  common_limit: int = 20) -> None:
    """In thông tin một username vừa tra, kèm thống kê điểm nếu có bài đã thu thập."""
    line = "-" * width
    who = "@" + profile.get("username", "?")
    print(f"\n{title}")
    print(line)
    if not profile.get("exists"):
        print(f"  {who}: KHÔNG tra được (không tồn tại, riêng tư, hoặc không có bản xem trước).")
        print(line)
        return
    print(f"  {who}  —  {profile.get('kind_vi', 'Không rõ')}"
          + ("  ✔ đã xác minh" if profile.get("verified") else ""))
    rows = [
        ("Tên hiển thị", profile.get("title")),
        ("Giới thiệu", profile.get("about")),
        ("Quy mô", profile_scale(profile)),
        ("ID", profile.get("account_id")),
        ("Số điện thoại", _phone_line(profile)),
        ("Ảnh đại diện", _photo_line(profile)),
        ("Hoạt động", profile.get("last_seen")),
        ("Ngày tạo", profile.get("created_at")),
        ("Username khác", _other_usernames(profile)),
        ("Cờ tài khoản", _flags_line(profile)),
        ("Ngôn ngữ", profile.get("lang_code")),
        ("Nguồn", profile.get("source")),
        ("Liên kết", profile.get("profile_url")),
    ]
    for label, value in rows:
        if value not in (None, ""):
            print(f"    {label:<14}: {value}")
    commands = profile.get("bot_commands") or []
    if commands:
        print(f"    {'Lệnh bot':<14}: " + ", ".join(
            f"/{c.get('command')}" for c in commands[:12] if c.get("command")))
    groups = profile.get("common_groups") or []
    total_groups = profile.get("common_chats_count")
    if groups or total_groups:
        head = f"{total_groups if total_groups is not None else len(groups)} nhóm/kênh dùng chung"
        print(f"    {'Nhóm chung':<14}: {head}")
        for g in groups[:common_limit]:
            name = g.get("title") or ("@" + (g.get("username") or "")) or str(g.get("id"))
            bits = [PROFILE_KIND_VI.get(g.get("kind") or "", "")]
            if g.get("members"):
                bits.append(f"{_format_count(g['members'])} thành viên")
            detail = ", ".join(b for b in bits if b)
            handle = f"  @{g['username']}" if g.get("username") else ""
            print(f"        - {name}  ({detail}){handle}")
    stats = None
    if records:
        stats = group_risk_for_channel(records, profile.get("username", ""), noun=noun)
    if stats:
        print(f"    {'Bài đã lấy':<12}: {stats['total_messages']} {noun} — điểm TB "
              f"{stats['avg_score']}, cao nhất {stats['max_score']}/100, "
              f"{stats['flagged']} {noun} cần rà soát")
        print(f"    {'Mức rủi ro':<12}: {icon_of(stats['group_level'])} "
              f"{stats['group_score']}/100 {stats['group_level']} — {stats['verdict']}")
    print(line)

# ---------------------------------------------------------------------------
# 13. Đường dẫn JSON — MỌI file .json của project nằm trong json_folder/
# ---------------------------------------------------------------------------
#
# Quy ước (m01591): không rải file .json ra thư mục gốc nữa. Cả dữ liệu mẫu
# (nhom1.json, posts_tg.json, real_tg*.json, found*.json) lẫn kết quả chạy
# (posts_web.json, posts_api.json, found_groups.json) và cả tg_api.json đều nằm
# trong json_folder/. Các script nhận `--out`/`--report`/`--json`/`--in` với TÊN
# TRẦN thì tự hiểu là file trong json_folder/.
#
# Đường dẫn mặc định được neo vào gốc project, không phải thư mục mã nguồn.
import os  # noqa: E402
from .paths import PROJECT_ROOT  # noqa: E402

SCRIPT_DIR = str(PROJECT_ROOT)
JSON_DIR_NAME = "json_folder"
JSON_DIR = os.path.join(SCRIPT_DIR, JSON_DIR_NAME)


def _ensure_dir(path: str) -> str:
    """Tạo thư mục cha của `path` nếu cần rồi trả lại chính `path` (cho `write_json`)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    return path


def json_dir() -> str:
    """Thư mục chứa mọi file JSON; tự tạo nếu chưa có."""
    os.makedirs(JSON_DIR, exist_ok=True)
    return JSON_DIR


def json_path(name: str) -> str:
    """Đường dẫn đầy đủ cho một tên file JSON trong `json_folder/`.

    Tên có sẵn thư mục (chứa `/`) hoặc đường dẫn tuyệt đối thì giữ nguyên —
    người dùng vẫn toàn quyền ghi ra chỗ khác.
    """
    name = (name or "").strip()
    if not name:
        return JSON_DIR
    if os.path.isabs(name) or os.sep in name or "/" in name:
        return name
    return os.path.join(JSON_DIR, name)


def resolve_json(path: str) -> str:
    """Tìm file JSON người dùng gõ: nếu không thấy ở chỗ ghi ra thì thử `json_folder/`.

    Nhờ vậy `--report posts_tg.json`, `--json nhom1.json`, `--in found.json` vẫn
    chạy dù file đã nằm trong `json_folder/`.
    """
    path = (path or "").strip()
    if not path:
        return path
    if os.path.exists(path) or os.path.isabs(path) or os.sep in path or "/" in path:
        return path
    candidate = os.path.join(JSON_DIR, path)
    return candidate if os.path.exists(candidate) else path
