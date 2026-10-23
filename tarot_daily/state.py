"""单用户、单持久目录中的按日抽牌和发送状态。"""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import uuid

from .deck import make_snapshot
from .ledger import COLUMNS, validate_ledger


class StateError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, directory, *, require_existing=False):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "tarot.sqlite3"
        if require_existing:
            if not self.path.is_file():
                raise StateError("所需状态库缺失；先从持久账本恢复，禁止重新初始化或抽牌")
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='draws'").fetchone():
                    raise StateError("所需状态库不完整；禁止重新初始化或抽牌")
        self.directory.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS draws (
                local_date TEXT PRIMARY KEY,
                snapshot TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready'
                    CHECK(status IN ('ready','sending','sent','failed','uncertain')),
                attempts INTEGER NOT NULL DEFAULT 0,
                recipient_hash TEXT,
                message_id TEXT,
                error_code TEXT,
                updated_at TEXT NOT NULL
            )""")
            # 增量迁移保留全部历史牌面；并发进程在同一写事务内检查和迁移。
            columns = {row["name"] for row in db.execute("PRAGMA table_info(draws)")}
            for name in ("delivery_channel", "attempt_id", "provider_message_id"):
                if name not in columns:
                    db.execute(f"ALTER TABLE draws ADD COLUMN {name} TEXT")
            db.execute("""UPDATE draws SET delivery_channel='smtp'
                          WHERE delivery_channel IS NULL AND recipient_hash IS NOT NULL""")
            db.execute("CREATE TABLE IF NOT EXISTS state_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO state_info(key,value) VALUES('ledger_id',?)", (uuid.uuid4().hex,))
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get_or_create(self, deck, local_date):
        with self.transaction() as db:
            row = db.execute("SELECT snapshot FROM draws WHERE local_date=?", (local_date,)).fetchone()
            if row:
                return json.loads(row["snapshot"])
            snapshot = make_snapshot(deck, local_date)
            db.execute("INSERT INTO draws(local_date,snapshot,updated_at) VALUES(?,?,?)",
                       (local_date, json.dumps(snapshot, ensure_ascii=False), utc_now()))
            return snapshot

    def get(self, local_date):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM draws WHERE local_date=?", (local_date,)).fetchone()
            return dict(row) if row else None

    def export_ledger(self):
        with self.transaction() as db:
            ledger_id = db.execute("SELECT value FROM state_info WHERE key='ledger_id'").fetchone()[0]
            rows = [dict(row) for row in db.execute("SELECT " + ",".join(COLUMNS) + " FROM draws ORDER BY local_date")]
        return {"schema_version": 1, "ledger_id": ledger_id, "draws": rows}

    def import_ledger(self, ledger, expected_id):
        validate_ledger(ledger, expected_id)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM draws LIMIT 1").fetchone():
                raise StateError("恢复目标必须为空，不覆盖已有抽牌或发送记录")
            placeholders = ",".join("?" for _ in COLUMNS)
            db.executemany("INSERT INTO draws(" + ",".join(COLUMNS) + ") VALUES(" + placeholders + ")",
                           [tuple(row[name] for name in COLUMNS) for row in ledger["draws"]])
            db.execute("UPDATE state_info SET value=? WHERE key='ledger_id'", (expected_id,))

    def claim(self, local_date, recipient_hash, message_id, *, channel="smtp", attempt_id=None):
        if channel not in ("smtp", "gmail") or (channel == "gmail" and not attempt_id):
            raise StateError("非法发送渠道或缺少尝试标识")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM draws WHERE local_date=?", (local_date,)).fetchone()
            if row is None:
                raise StateError("当天尚未抽牌")
            if row["recipient_hash"] and row["recipient_hash"] != recipient_hash:
                raise StateError("当天记录已绑定其他收件地址，请人工核对")
            if row["status"] == "sent":
                return False
            if row["delivery_channel"] and row["delivery_channel"] != channel:
                raise StateError("当天记录已绑定其他发件渠道，请人工核对")
            if row["status"] in ("sending", "uncertain"):
                raise StateError("发送中或发送结果不确定；先核对收件箱，再执行 resolve")
            if row["attempts"] >= 3:
                raise StateError("当天已达到三次发送尝试上限，请人工核对")
            db.execute("""UPDATE draws SET status='sending', attempts=attempts+1,
                       recipient_hash=?, message_id=?, delivery_channel=?, attempt_id=?,
                       provider_message_id=NULL, error_code=NULL, updated_at=?
                       WHERE local_date=?""",
                       (recipient_hash, message_id, channel, attempt_id, utc_now(), local_date))
            return True

    def finish(self, local_date, status, error_code=None):
        if status not in ("sent", "failed", "uncertain"):
            raise StateError("非法投递状态")
        with self.transaction() as db:
            changed = db.execute("""UPDATE draws SET status=?,error_code=?,updated_at=?
                                    WHERE local_date=? AND status='sending'
                                    AND delivery_channel='smtp'""",
                                 (status, error_code, utc_now(), local_date)).rowcount
            if changed != 1:
                raise StateError("投递状态已改变，停止处理")

    def finish_gmail(self, local_date, attempt_id, result, provider_message_id=None):
        if result not in ("accepted", "failed", "uncertain"):
            raise StateError("非法 Gmail 投递结果")
        if result == "accepted":
            if not isinstance(provider_message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", provider_message_id):
                raise StateError("发送已接受时必须提供 Gmail 返回的邮件 ID")
        elif provider_message_id is not None:
            raise StateError("未确认发送时不能记录邮件 ID")
        status = "sent" if result == "accepted" else result
        with self.transaction() as db:
            row = db.execute("SELECT * FROM draws WHERE local_date=?", (local_date,)).fetchone()
            if (row is None or row["delivery_channel"] != "gmail" or not attempt_id
                    or row["attempt_id"] != attempt_id):
                raise StateError("日期、发件渠道或尝试标识不匹配，停止记账")
            if row["status"] == "sent" and status == "sent" and row["provider_message_id"] == provider_message_id:
                return False
            if row["status"] != "sending":
                raise StateError("发送状态已改变；不确定记录需人工核对，停止记账")
            error_code = None if status == "sent" else f"gmail_{result}"
            db.execute("""UPDATE draws SET status=?, provider_message_id=?, error_code=?, updated_at=?
                          WHERE local_date=?""",
                       (status, provider_message_id, error_code, utc_now(), local_date))
            return True

    def resolve(self, local_date, confirmed_sent):
        with self.transaction() as db:
            row = db.execute("SELECT status FROM draws WHERE local_date=?", (local_date,)).fetchone()
            if row is None or row["status"] not in ("sending", "uncertain", "failed"):
                raise StateError("只有异常发送记录需要人工恢复")
            status = "sent" if confirmed_sent else "ready"
            # 仅人工确认未发送后重置限额；牌面快照仍然不变。
            db.execute("""UPDATE draws SET status=?, attempts=CASE WHEN ? THEN attempts ELSE 0 END,
                       error_code='manually_resolved',updated_at=? WHERE local_date=?""",
                       (status, confirmed_sent, utc_now(), local_date))
