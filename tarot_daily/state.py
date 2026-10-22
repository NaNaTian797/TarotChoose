"""单用户、单持久目录中的按日抽牌和发送状态。"""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .deck import make_snapshot


class StateError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "tarot.sqlite3"
        with closing(self.connect()) as db:
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

    def claim(self, local_date, recipient_hash, message_id):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM draws WHERE local_date=?", (local_date,)).fetchone()
            if row is None:
                raise StateError("当天尚未抽牌")
            if row["recipient_hash"] and row["recipient_hash"] != recipient_hash:
                raise StateError("当天记录已绑定其他收件地址，请人工核对")
            if row["status"] == "sent":
                return False
            if row["status"] in ("sending", "uncertain"):
                raise StateError("发送中或发送结果不确定；先核对收件箱，再执行 resolve")
            if row["attempts"] >= 3:
                raise StateError("当天已达到三次发送尝试上限，请人工核对")
            db.execute("""UPDATE draws SET status='sending', attempts=attempts+1,
                       recipient_hash=?, message_id=?, error_code=NULL, updated_at=?
                       WHERE local_date=?""", (recipient_hash, message_id, utc_now(), local_date))
            return True

    def finish(self, local_date, status, error_code=None):
        if status not in ("sent", "failed", "uncertain"):
            raise StateError("非法投递状态")
        with self.transaction() as db:
            changed = db.execute("""UPDATE draws SET status=?,error_code=?,updated_at=?
                                    WHERE local_date=? AND status='sending'""",
                                 (status, error_code, utc_now(), local_date)).rowcount
            if changed != 1:
                raise StateError("投递状态已改变，停止处理")

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
