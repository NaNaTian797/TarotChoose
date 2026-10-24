"""已确认的双语邮件正文；固定文本，不调用模型或发件服务。"""

import re


CONTENT_VERSION = "email-body-0.2.1"
SUIT_LABELS = {
    "major": ("大阿尔卡纳", "Major Arcana"),
    "wands": ("小阿尔卡纳 · 权杖", "Minor Arcana · Wands"),
    "cups": ("小阿尔卡纳 · 圣杯", "Minor Arcana · Cups"),
    "swords": ("小阿尔卡纳 · 宝剑", "Minor Arcana · Swords"),
    "pentacles": ("小阿尔卡纳 · 星币", "Minor Arcana · Pentacles"),
}
ORIENTATION_LABELS = {"upright": ("正位", "Upright"), "reversed": ("逆位", "Reversed")}


def source_words(text):
    # 原文的词与顺序必须完整保留；分条时允许调整句末标点和首字母排版。
    return re.findall(r"\w+", text.casefold())


def validate_reading(state):
    for key in ("keyword", "meaning", "question", "source_text", "source_url"):
        if not isinstance(state.get(key), str) or not state[key].strip():
            raise ValueError(f"牌库缺少 {key}")
    pairs = state.get("fixed_meanings")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("牌库缺少完整中英固定牌义")
    for pair in pairs:
        if (not isinstance(pair, dict) or set(pair) != {"zh", "en"}
                or any(not isinstance(pair[k], str) or not pair[k].strip() for k in ("zh", "en"))
                or any("\n" in pair[k] or "\r" in pair[k] for k in ("zh", "en"))):
            raise ValueError("固定牌义必须逐条中英对应")
    english = " ".join(pair["en"] for pair in pairs)
    if source_words(english) != source_words(state["source_text"]):
        raise ValueError("固定牌义原文被删减、改词或调整顺序")


def make_body(card, orientation, local_date):
    state = card[orientation]
    orientation_zh, orientation_en = ORIENTATION_LABELS[orientation]
    group_zh, group_en = SUIT_LABELS[card["suit"]]
    terms = "\n".join(f"- {p['zh']} — {p['en']}" for p in state["fixed_meanings"])
    return (
        f"🔮 {local_date} · 每日一张牌\n\n"
        f"{card['name']}（{orientation_zh}）｜{card['name_en']} · {orientation_en}\n\n"
        f"所属牌组： {group_zh}｜{group_en}\n\n"
        f"今日关键词： {state['keyword']}\n\n"
        f"🖼️ 牌面\n\n{card['image_description']}\n\n"
        f"📖 固定牌义｜中英对照\n\n{terms}\n\n"
        f"💡 牌义解读\n\n{state['meaning']}\n\n"
        f"❓ 今日一问\n\n{state['question']}\n"
    )
