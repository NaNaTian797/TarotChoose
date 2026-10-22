"""验证固定牌库，将 156 个等概率样本映射为牌与朝向。"""

import hashlib
import json
from pathlib import Path
import secrets

DECK_PATH = Path(__file__).parent / "data" / "cards.json"


def load_deck(path=DECK_PATH):
    raw = Path(path).read_bytes()
    deck = json.loads(raw)
    cards = deck["cards"]
    if not isinstance(deck.get("version"), str) or not deck["version"]:
        raise ValueError("牌库缺少版本号")
    if len(cards) != 78 or [c.get("id") for c in cards] != list(range(78)):
        raise ValueError("牌库必须按固定顺序包含编号 0–77 的 78 张牌")
    if len({c.get("name") for c in cards}) != 78:
        raise ValueError("牌名必须唯一")
    expected = {"major": 22, "wands": 14, "cups": 14, "swords": 14, "pentacles": 14}
    if {s: sum(c.get("suit") == s for c in cards) for s in expected} != expected:
        raise ValueError("牌库的大牌与四花色数量不正确")
    for card in cards:
        for key in ("name", "image_description"):
            if not isinstance(card.get(key), str) or not card[key].strip():
                raise ValueError(f"牌库字段为空：{key}")
        for orientation in ("upright", "reversed"):
            for key in ("keyword", "meaning"):
                value = card.get(orientation, {}).get(key)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"牌库缺少 {orientation}.{key}")
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
    state = card[orientation]
    orientation_name = "正位" if orientation == "upright" else "逆位"
    subject = f"每日一张牌｜{local_date}｜{card['name']}（{orientation_name}）"
    body = (
        f"{local_date} · 每日一张牌\n\n"
        f"{card['name']}（{orientation_name}）\n"
        f"今日关键词：{state['keyword']}\n\n"
        f"牌面图案（按正向牌面描述）：\n{card['image_description']}\n\n"
        f"本次朝向的固定牌义：\n{state['meaning']}\n\n"
        "随机事件提供思考材料，牌义不是对命运的判定。\n"
    )
    return {
        "date": local_date, "outcome": outcome, "card_id": card["id"],
        "card_name": card["name"], "orientation": orientation,
        "deck_version": deck["version"], "deck_sha256": deck["sha256"],
        "algorithm": "python.secrets.randbelow(156)",
        "subject": subject, "body": body,
    }
