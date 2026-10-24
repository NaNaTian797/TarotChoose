"""验证固定牌库，将 156 个等概率样本映射为牌与朝向。"""

import hashlib
import json
from pathlib import Path
import secrets

from .content import CONTENT_VERSION, SUIT_LABELS, make_body, validate_reading
from .formatting import render_html

DECK_PATH = Path(__file__).parent / "data" / "cards.json"


def load_deck(path=DECK_PATH):
    raw = Path(path).read_bytes()
    deck = json.loads(raw)
    cards = deck["cards"]
    if not isinstance(deck.get("version"), str) or not deck["version"]:
        raise ValueError("牌库缺少版本号")
    if deck.get("content_version") != CONTENT_VERSION:
        raise ValueError("牌库与邮件正文版本不匹配")
    if len(cards) != 78 or [c.get("id") for c in cards] != list(range(78)):
        raise ValueError("牌库必须按固定顺序包含编号 0–77 的 78 张牌")
    if len({c.get("name") for c in cards}) != 78:
        raise ValueError("牌名必须唯一")
    expected = {"major": 22, "wands": 14, "cups": 14, "swords": 14, "pentacles": 14}
    if {s: sum(c.get("suit") == s for c in cards) for s in expected} != expected:
        raise ValueError("牌库的大牌与四花色数量不正确")
    for card in cards:
        if card.get("suit") not in SUIT_LABELS:
            raise ValueError("未知牌组")
        for key in ("name", "name_en", "image_description"):
            if not isinstance(card.get(key), str) or not card[key].strip():
                raise ValueError(f"牌库字段为空：{key}")
        for orientation in ("upright", "reversed"):
            state = card.get(orientation)
            if not isinstance(state, dict):
                raise ValueError(f"牌库缺少 {orientation}")
            validate_reading(state)
    deck["sha256"] = hashlib.sha256(raw).hexdigest()
    return deck


def select(deck, outcome=None):
    if outcome is None:
        outcome = secrets.randbelow(156)
    if type(outcome) is not int or not 0 <= outcome < 156:
        raise ValueError("随机样本必须在 0–155 之间")
    orientation = ("upright", "reversed")[outcome % 2]
    return outcome, deck["cards"][outcome // 2], orientation


def make_snapshot(deck, local_date, outcome=None):
    outcome, card, orientation = select(deck, outcome)
    orientation_name = "正位" if orientation == "upright" else "逆位"
    subject = f"每日一张牌｜{local_date}｜{card['name']}（{orientation_name}）"
    body = make_body(card, orientation, local_date)
    return {
        "date": local_date, "outcome": outcome, "card_id": card["id"],
        "card_name": card["name"], "orientation": orientation,
        "deck_version": deck["version"], "deck_sha256": deck["sha256"],
        "content_version": CONTENT_VERSION, "card_name_en": card["name_en"],
        "group_zh": SUIT_LABELS[card["suit"]][0], "group_en": SUIT_LABELS[card["suit"]][1],
        "algorithm": "python.secrets.randbelow(156)",
        "subject": subject, "body": body, "body_html": render_html(body),
    }
