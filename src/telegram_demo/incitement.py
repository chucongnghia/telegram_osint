"""Phát hiện nội dung kêu gọi / kích động hành vi phạm pháp.

Mô-đun này là nguồn chân lý cho *cách chấm* của project sau khi chuyển trọng tâm
từ "lừa đảo" sang "kêu gọi hành vi phạm pháp". Nguyên tắc:

* Máy chỉ **xếp hạng và trích bằng chứng**; kết luận pháp lý do con người quyết.
* Nhãn hẹp: phải có **hành động** (đốt, đập, tấn công...) hoặc **đe doạ** rõ ràng,
  không gắn cờ cho phê bình/chính kiến thông thường.
* Câu mang tính tường thuật/cảnh báo ("lên án việc đốt...", "tin giả nói rằng sẽ
  tấn công...") bị hạ điểm và đánh dấu `context="reporting"`.
* Mọi kết quả kèm `evidence` (câu trích) và `requires_review` để người duyệt xem lại.

Điểm 0-100 dùng lại ba mức cũ (`Normal` / `Suspicious` / `High-risk`) để dashboard,
SQLite và báo cáo không phải đổi schema.
"""
from __future__ import annotations

import re
import unicodedata

RUBRIC_VERSION = "incitement-v1:2026-11-21"

THRESHOLD_SUSPICIOUS, THRESHOLD_HIGH_RISK = 25, 60

#: Nhãn cho bộ phân loại Jev (giữ đúng thứ tự để rubric ổn định).
LABELS = (
    "call_to_violence",
    "call_to_property_damage",
    "obstruct_officials",
    "call_against_state",
    "threat",
    "criticism",
    "insufficient_information",
)

LABELS_VI = {
    "call_to_violence": "Kêu gọi bạo lực",
    "call_to_property_damage": "Kêu gọi phá hoại tài sản",
    "obstruct_officials": "Chống người thi hành công vụ",
    "call_against_state": "Kêu gọi chống chính quyền",
    "threat": "Đe doạ",
    "criticism": "Phê bình / chính kiến (không phải kêu gọi)",
    "insufficient_information": "Không đủ thông tin",
}

#: Hướng dẫn cho Jev: nhãn nào, loại trừ cái gì, không suy đoán.
RUBRIC_INSTRUCTIONS = (
    "Bạn phân loại một tin nhắn công khai. Chỉ chọn nhãn khi văn bản thể hiện rõ HÀNH ĐỘNG "
    "hoặc LỜI KÊU GỌI hành động, không suy đoán từ cảm xúc hay quan điểm. "
    "call_to_violence: kêu gọi/xúi giục tấn công, đánh, giết, đốt người hoặc dùng vũ khí. "
    "call_to_property_damage: kêu gọi đập phá, đốt tài sản, cướp, phá hoại công trình. "
    "obstruct_officials: kêu gọi chặn, bao vây, tấn công người thi hành công vụ hoặc cản trở "
    "hoạt động của cơ quan nhà nước. "
    "call_against_state: kêu gọi lật đổ chính quyền, đảo chính, chiếm cơ quan nhà nước. "
    "threat: đe doạ trực tiếp một người/tổ chức (giết, đánh, trả thù, biết nhà...). "
    "criticism: phê bình chính sách, bày tỏ quan điểm, tranh luận, phản đối ôn hoà. "
    "insufficient_information: thiếu ngữ cảnh để biết có phải kêu gọi hay không. "
    "Nếu câu chỉ tường thuật, cảnh báo, trích dẫn hoặc lên án hành vi thì chọn criticism. "
    "Không thêm giải thích dài; chỉ chọn nhãn."
)

# ---------------------------------------------------------------------------
# Bộ từ điển
# ---------------------------------------------------------------------------

#: Nhóm hành động: (tên tiếng Việt, trọng số, từ khoá).
ACTION_GROUPS = {
    "call_to_violence": ("Kêu gọi bạo lực", 42, [
        "đốt", "đốt nhà", "đốt xe", "thiêu", "đập", "đập phá", "đánh", "đánh người",
        "tấn công", "giết", "chém", "chém giết", "bắn", "bắn giết",
        "ám sát", "hành hung", "ném bom xăng", "bom xăng", "đâm", "chặt",
        "xông vào", "truy sát", "trả thù", "thanh toán", "xử lý",
    ]),
    "call_to_property_damage": ("Kêu gọi phá hoại tài sản", 34, [
        "đập phá", "phá hoại", "đốt xe", "châm lửa", "cướp", "cướp giật",
        "ném đá", "đập cửa", "phá cổng", "chiếm đoạt", "cắt phá",
    ]),
    "obstruct_officials": ("Chống người thi hành công vụ", 30, [
        "chặn đường", "chặn xe", "bao vây", "vây", "cản trở", "chiếm giữ",
        "giải tán", "xô xát", "chống lại", "không cho vào", "phong toả",
        "chiếm trụ sở", "chiếm chốt",
    ]),
    "call_against_state": ("Kêu gọi chống chính quyền", 38, [
        "lật đổ", "lật chính quyền", "đảo chính", "lật chế độ", "lập chính quyền",
        "ly khai", "đả đảo", "chiếm chính quyền", "đánh sập chế độ",
    ]),
}

#: Từ khoá đe doạ; cần thêm cấu trúc "sẽ ..." hoặc "nếu ... thì".
THREAT_KEYWORDS = [
    "sẽ giết", "sẽ đánh", "sẽ trả thù", "biết nhà", "biết mặt", "rồi sẽ biết",
    "đừng để tao gặp", "sẽ cho mày biết", "chờ đấy", "trả thù", "thanh toán",
    "nếu không", "hậu quả", "tự chịu trách nhiệm",
]

#: Lời kêu gọi / hiệu triệu (tăng mức độ "đang kêu gọi", không chỉ bình luận).
CALL_PATTERNS = [
    "hãy", "cùng nhau", "đồng loạt", "kêu gọi", "phát động", "hiệu triệu",
    "tập trung tại", "mang theo", "đi nào", "xung phong", "hẹn gặp tại",
    "đừng đứng ngoài", "ai không đi", "ai không tham gia", "ra tay",
]

#: Dấu hiệu chuẩn bị/hành động cụ thể.
WEAPON_KEYWORDS = [
    "bom xăng", "xăng", "dao", "mã tấu", "gậy", "súng", "vũ khí", "đá",
    "chai", "gạch", "pháo", "khiên", "gậy gộc",
]

TIME_PATTERNS = [
    "hôm nay", "đêm nay", "ngày mai", "sáng mai", "chiều nay", "tối nay",
    "rạng sáng", "thứ bảy", "chủ nhật", "cuối tuần",
]

PLACE_PATTERNS = [
    "trụ sở", "chốt", "doanh trại", "cổng", "quảng trường", "ngã tư",
    "ngã ba", "con đường", "cây cầu", "bến xe", "sân bay", "bệnh viện",
    "trường học", "chợ", "uỷ ban", "toà án", "ngân hàng",
]

OFFICIAL_TARGETS = [
    "công an", "cảnh sát", "bộ đội", "quân đội", "dân quân", "cán bộ",
    "chính quyền", "uỷ ban", "toà án", "viện kiểm sát", "nhà nước",
]

#: Ngữ cảnh tường thuật/cảnh báo: hạ điểm thay vì gắn cờ oan.
NEWS_MARKERS = [
    "theo hãng tin", "theo nguồn tin", "đưa tin", "cho biết", "tuyên bố",
    "phát ngôn viên", "nhấn mạnh rằng", "báo cáo", "phóng viên", "chuyên gia",
    "bộ trưởng", "tổng thống", "thủ tướng", "phát biểu", "chỉ trích", "cáo buộc",
    "hãng tin", "bản tin", "tin tức", "hiện trường", "thiệt hại", "thương vong",
]
AUDIENCE_MARKERS = [
    "chúng ta", "chúng tôi", "anh em", "bà con", "mọi người", "các bạn",
    "đồng bào", "mày", "tao", "chúng mày",
]
REPORTING_PATTERNS = [
    "cảnh báo", "lên án", "phản đối", "tin giả", "thông tin sai", "bịa đặt",
    "không nên", "đừng", "hãy bình tĩnh", "cơ quan chức năng",
    "bắt giữ", "khởi tố", "xét xử", "điều tra", "theo quy định", "bài học",
    "tuyên truyền", "kêu gọi mọi người cảnh giác", "hậu quả pháp lý",
]

NEGATION_PATTERNS = ("đừng", "chớ có", "không được", "không nên")

RE_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")

# ---------------------------------------------------------------------------
# Chuẩn hoá + so khớp
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def no_accent(text: str) -> str:
    text = text or ""
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in unicodedata.normalize("NFD", text)
                   if unicodedata.category(ch) != "Mn")


def _fold(text: str) -> str:
    """Bản không dấu, chữ thường, dùng để so khớp từ khoá."""
    return no_accent(normalize(text))


def _regex(keyword: str, *, fold: bool) -> re.Pattern:
    body = _fold(keyword) if fold else normalize(keyword)
    pattern = r"(?<!\w)" + re.escape(body) + r"(?!\w)"
    return re.compile(pattern, re.I | re.U)


def hits(keywords, text: str) -> list[str]:
    """Khớp từ khoá có phân biệt dấu cho từ ngắn.

    Bỏ dấu giúp bắt văn bản gõ không dấu, nhưng làm "đập"/"đạp", "đánh"/"danh",
    "đốt"/"dốt" trùng nhau. Vì vậy: khớp nguyên dấu trước; chỉ khi từ khoá đủ dài
    (có khoảng trắng hoặc ≥5 ký tự sau khi bỏ dấu) mới thử bản không dấu.
    """
    accented = normalize(text)
    plain = no_accent(accented)
    found = []
    for keyword in keywords:
        if _regex(keyword, fold=False).search(accented):
            found.append(keyword)
            continue
        folded = no_accent(normalize(keyword))
        if (" " in folded or len(folded) >= 5) and _regex(keyword, fold=True).search(plain):
            found.append(keyword)
    return found


def incitement_first_word(keywords) -> tuple:
    """Các từ khoá có thể đứng đầu câu như mệnh lệnh ("đập phá...", "chặn xe...")."""
    return tuple(keyword for keyword in keywords if " " in keyword or len(keyword) >= 3)


def sentences(text: str) -> list[str]:
    parts = [part.strip() for part in RE_SENTENCE.split(text or "") if part and part.strip()]
    return parts[:40]


def evidence_for(text: str, keywords) -> list[str]:
    """Câu chứa từ khoá hành động — dùng làm bằng chứng trích dẫn."""
    found = []
    for sentence in sentences(text):
        if hits(keywords, sentence):
            found.append(sentence[:400])
        if len(found) >= 2:
            break
    return found

# ---------------------------------------------------------------------------
# Chấm điểm
# ---------------------------------------------------------------------------


def assess(text: str, ocr_text: str = "", *, channel: str = "", has_media: bool = False,
           forwarded: bool = False) -> dict:
    """Chấm 0-100 và trả về nhãn, tín hiệu, câu bằng chứng.

    `channel`, `has_media`, `forwarded` chỉ dùng để ghi ngữ cảnh, không cộng điểm
    (tránh phạt oan kênh tin tức đăng lại nhiều).
    """
    body = normalize(" ".join([text or "", ocr_text or ""]))
    plain = _fold(body)

    reporting = bool(hits(REPORTING_PATTERNS, body))
    if not reporting:
        news = hits(NEWS_MARKERS, body)
        audience = hits(AUDIENCE_MARKERS, body)
        if news and not audience and not hits(["hãy", "đồng loạt"], body):
            reporting = True
    negated = bool(hits(NEGATION_PATTERNS, body))
    signals: list[dict] = []
    score = 0.0

    # 0) Lời kêu gọi thật phải hướng tới người đọc: câu chứa cả từ khoá hành động
    #    lẫn dấu hiệu hiệu triệu. Câu chỉ tường thuật ("Iran tấn công...") bị hạ
    #    trọng số để bản tin chiến sự không bị gắn cờ như lời kêu gọi.
    imperative = hits(["hãy", "cùng nhau", "đồng loạt", "đi nào", "xung phong",
                       "đừng đứng ngoài", "ai không", "ra tay", "toàn thể",
                       "cần lắm", "mau lên"], body)
    call_cue = bool(imperative) or ("kêu gọi" in plain and bool(
        hits(["mọi người", "anh em", "bà con", "đồng bào", "người dân"], body)))
    strong_call = False
    for sentence in sentences(body):
        if not any(hits(keywords, sentence) for _, _, keywords in ACTION_GROUPS.values()):
            continue
        head = sentence.strip().lower()[:16]
        negated_sentence = head.startswith(("đừng", "không", "chớ", "chẳng"))
        starts_with_action = (not negated_sentence) and any(
            hits(keywords, sentence) and
            sentence.strip().lower().startswith(incitement_first_word(keywords))
            for _, _, keywords in ACTION_GROUPS.values())
        if starts_with_action or (call_cue and hits(imperative, sentence)):
            strong_call = True
            break

    # 1) Nhóm hành động
    group_scores: dict[str, float] = {}
    group_matches: dict[str, list[str]] = {}
    for label, (name_vi, weight, keywords) in ACTION_GROUPS.items():
        found = hits(keywords, body)
        if not found:
            continue
        points = weight * min(1.0, 0.85 + 0.15 * (len(found) - 1))
        if not strong_call:
            points *= 0.2
        group_scores[label] = points
        group_matches[label] = found
        signals.append({"label_vi": name_vi, "points": round(points, 1), "matched": found[:6]})

    # 2) Đe doạ có cấu trúc
    threat_hits = hits(THREAT_KEYWORDS, body)
    conditional = bool(hits(["nếu", "hễ", "khi nào"], body)) and bool(hits(["sẽ"], body))
    if threat_hits and (conditional or bool(hits(["sẽ"], body))):
        points = 32 * min(1.0, 0.9 + 0.1 * len(threat_hits))
        group_scores["threat"] = points
        group_matches["threat"] = threat_hits
        signals.append({"label_vi": "Đe doạ", "points": round(points, 1), "matched": threat_hits[:6]})

    if group_scores:
        ranked = sorted(group_scores.items(), key=lambda kv: -kv[1])
        score += ranked[0][1] + sum(p * 0.4 for _, p in ranked[1:3])

    # 3) Ngữ cảnh hành động: kêu gọi, địa điểm, thời gian, vũ khí, mục tiêu
    calls = hits(CALL_PATTERNS, body)
    if calls and group_scores and strong_call:
        score += 14
        signals.append({"label_vi": "Lời kêu gọi/hiệu triệu", "points": 14.0, "matched": calls[:4]})

    weapons = hits(WEAPON_KEYWORDS, body)
    if weapons and group_scores and strong_call:
        score += 16
        signals.append({"label_vi": "Dấu hiệu chuẩn bị vũ khí/công cụ", "points": 16.0,
                        "matched": weapons[:4]})

    times = hits(TIME_PATTERNS, body) + re.findall(r"\b\d{1,2}\s*(?:h|giờ)\b", plain)
    if times and group_scores and strong_call:
        score += 8
        signals.append({"label_vi": "Thời điểm cụ thể", "points": 8.0, "matched": times[:4]})

    places = hits(PLACE_PATTERNS, body)
    if places and group_scores and strong_call:
        score += 8
        signals.append({"label_vi": "Địa điểm cụ thể", "points": 8.0, "matched": places[:4]})

    targets = hits(OFFICIAL_TARGETS, body)
    if targets and group_scores and strong_call:
        score += 12
        signals.append({"label_vi": "Nhắm tới lực lượng/cơ quan cụ thể", "points": 12.0,
                        "matched": targets[:4]})

    # 4) Hạ điểm khi là tường thuật/cảnh báo hoặc bị phủ định
    reporting_hits = hits(REPORTING_PATTERNS, body)
    if reporting_hits and score:
        # Càng nhiều dấu hiệu tường thuật (cảnh báo, tin giả, điều tra...) càng hạ mạnh,
        # tối đa -55, để bài phân tích về hành vi không bị gắn cờ như lời kêu gọi thật.
        penalty = min(55.0, 30.0 + 10.0 * (len(reporting_hits) - 1))
        score = max(0.0, score - penalty)
        signals.append({"label_vi": "Ngữ cảnh tường thuật/cảnh báo (giảm điểm)",
                        "points": -penalty, "matched": reporting_hits[:4]})
    if negated and score:
        score = max(0.0, score - 15)
        signals.append({"label_vi": "Có phủ định hành vi (giảm điểm)", "points": -15.0,
                        "matched": hits(NEGATION_PATTERNS, body)[:3]})

    score = int(round(min(100.0, score)))
    level = level_of(score)
    category = max(group_scores.items(), key=lambda kv: kv[1])[0] if group_scores else None
    # Đe doạ nhắm vào một người cụ thể (mày/tao...) nhưng không có vũ khí, thời điểm,
    # địa điểm hay mục tiêu công quyền: phân loại "threat" thay vì "call_to_violence".
    personal = hits(["mày", "tao", "mi", "ngươi", "nó", "chúng mày"], body)
    if "threat" in group_scores and category == "call_to_violence" and personal \
            and not (weapons or places or targets):
        category = "threat"

    top = sorted((signal for signal in signals if signal["points"] > 0),
                 key=lambda item: -item["points"])[:3]
    if top:
        summary = "Nhóm chính: " + LABELS_VI.get(category, "không rõ") + " | Tín hiệu: " + \
                  "; ".join(signal["label_vi"] for signal in top)
    else:
        summary = "Không phát hiện lời kêu gọi/đe doạ rõ ràng."

    action_keywords = []
    for matched in group_matches.values():
        action_keywords.extend(matched)
    context = "reporting" if reporting else "neutral"

    return {
        "score": score,
        "level": level,
        "category": category,
        "category_vi": LABELS_VI.get(category) if category else None,
        "signals": signals,
        "summary": summary,
        "evidence": evidence_for(text or ocr_text, action_keywords) if action_keywords else [],
        "context": context,
        "requires_review": score >= THRESHOLD_SUSPICIOUS,
        "rubric_version": RUBRIC_VERSION,
        "meta": {"channel": channel, "has_media": bool(has_media), "forwarded": bool(forwarded)},
    }


def level_of(score: float) -> str:
    return ("High-risk" if score >= THRESHOLD_HIGH_RISK
            else "Suspicious" if score >= THRESHOLD_SUSPICIOUS else "Normal")


def verdict_of(level: str) -> str:
    return {
        "High-risk": "Có lời kêu gọi/kích động hành vi phạm pháp — cần xử lý ngay",
        "Suspicious": "Cần xem xét: có dấu hiệu kêu gọi hành vi phạm pháp",
    }.get(level, "Chưa thấy dấu hiệu kêu gọi hành vi phạm pháp")


#: Câu hỏi cho Jev (cùng dạng với text_classifier để tái dùng cầu nối).
QUESTIONS = {
    "incitement": {
        "type": "choice",
        "instructions": RUBRIC_INSTRUCTIONS,
        "criteria": {
            "call_to_violence": "Kêu gọi/xúi giục tấn công, đánh, giết, đốt người hoặc dùng vũ khí.",
            "call_to_property_damage": "Kêu gọi đập phá, đốt tài sản, cướp, phá hoại công trình.",
            "obstruct_officials": "Kêu gọi chặn, bao vây, tấn công người thi hành công vụ; cản trở cơ quan nhà nước.",
            "call_against_state": "Kêu gọi lật đổ chính quyền, đảo chính, chiếm cơ quan nhà nước.",
            "threat": "Đe doạ trực tiếp một người/tổ chức: giết, đánh, trả thù, biết nhà.",
            "criticism": "Phê bình chính sách, bày tỏ quan điểm, phản đối ôn hoà, hoặc tường thuật/cảnh báo/lên án.",
            "insufficient_information": "Không đủ ngữ cảnh để xác định có phải kêu gọi hay không.",
        },
    }
}
