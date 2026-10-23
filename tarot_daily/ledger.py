"""可恢复的私有状态格式；Library 条件更新由云端调用方负责。"""

from datetime import date
import json
from pathlib import Path
import re
import tempfile


COLUMNS = ("local_date", "snapshot", "status", "attempts", "recipient_hash", "message_id",
           "error_code", "updated_at", "delivery_channel", "attempt_id", "provider_message_id")


def validate_ledger(ledger, expected_id):
    if (not isinstance(ledger, dict) or type(ledger.get("schema_version")) is not int
            or ledger["schema_version"] != 1 or not isinstance(expected_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", expected_id)
            or ledger.get("ledger_id") != expected_id or not isinstance(ledger.get("draws"), list)):
        raise ValueError("状态格式或账本标识不匹配")
    seen = set()
    for row in ledger["draws"]:
        if not isinstance(row, dict) or set(row) != set(COLUMNS):
            raise ValueError("状态记录字段不匹配")
        local_date = row["local_date"]
        if date.fromisoformat(local_date).isoformat() != local_date or local_date in seen:
            raise ValueError("状态日期非法或重复")
        seen.add(local_date)
        if (row["status"] not in ("ready", "sending", "sent", "failed", "uncertain")
                or type(row["attempts"]) is not int or not 0 <= row["attempts"] <= 3
                or row["delivery_channel"] not in (None, "smtp", "gmail")):
            raise ValueError("发送状态非法")
        if row["recipient_hash"] is not None and not re.fullmatch(r"[a-f0-9]{64}", row["recipient_hash"]):
            raise ValueError("收件身份摘要非法")
        for name in ("message_id", "error_code", "updated_at", "attempt_id", "provider_message_id"):
            value = row[name]
            if value is not None and (not isinstance(value, str) or not value or len(value) > 512
                                      or any(c in value for c in "\r\n")):
                raise ValueError("状态字段非法")
        if row["status"] != "ready" and (row["attempts"] < 1 or row["delivery_channel"] is None
                                         or not row["recipient_hash"] or not row["message_id"]):
            raise ValueError("发送记录缺少身份或尝试次数")
        if row["delivery_channel"] == "gmail" and (not row["attempt_id"]
                or not re.fullmatch(r"[a-f0-9]{32}", row["attempt_id"])):
            raise ValueError("Gmail 记录缺少合法尝试标识")
        snapshot = json.loads(row["snapshot"])
        if not isinstance(snapshot, dict):
            raise ValueError("牌面快照必须是对象")
        outcome = snapshot.get("outcome")
        if (snapshot.get("date") != local_date or type(outcome) is not int or not 0 <= outcome < 156
                or snapshot.get("card_id") != outcome // 2
                or snapshot.get("orientation") != ("upright", "reversed")[outcome % 2]):
            raise ValueError("牌面快照与日期或朝向不一致")
        for name in ("subject", "body", "card_name", "deck_version", "deck_sha256", "algorithm"):
            if not isinstance(snapshot.get(name), str) or not snapshot[name]:
                raise ValueError("牌面快照字段缺失")
        if "body_html" in snapshot and not isinstance(snapshot["body_html"], str):
            raise ValueError("HTML 快照字段非法")


def read_ledger(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("状态文件不存在或超过大小上限")
    return json.loads(path.read_text(encoding="utf-8"))


def write_ledger(path, ledger, database_path):
    path = Path(path).resolve()
    if path.suffix.lower() != ".json" or path == Path(database_path).resolve():
        raise ValueError("状态导出目标必须是独立 JSON 文件")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ledger-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write((json.dumps(ledger, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
