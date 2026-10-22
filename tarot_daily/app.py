"""命令行装配；默认只生成私有本地预览。"""

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from .deck import load_deck, make_snapshot
from .mail import ConfigError, DeliveryError, MailConfig, make_message, recipient_key, send
from .state import StateError, Store

ROOT = Path(__file__).resolve().parent.parent
BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")


def today():
    return datetime.now(BEIJING).date().isoformat()


def date_argument(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("日期必须使用 YYYY-MM-DD")
    return value


def export_preview(directory, snapshot):
    directory = Path(directory) / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    for suffix, content in (("txt", snapshot["body"].encode("utf-8")),
                            ("eml", make_message(snapshot).as_bytes())):
        target = directory / f"{snapshot['date']}.{suffix}"
        # 相同日期的并发导出可产生同样的内容；各自使用唯一临时文件。
        import tempfile
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".preview-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def deliver(store, snapshot, config):
    message = make_message(snapshot, config.sender, config.recipient)
    local_date = snapshot["date"]
    while True:
        if not store.claim(local_date, recipient_key(config.recipient), message["Message-ID"]):
            return "already_sent"
        try:
            send(message, config)
        except DeliveryError as exc:
            store.finish(local_date, "uncertain" if exc.uncertain else "failed", exc.code)
            attempts = store.get(local_date)["attempts"]
            if exc.retryable and attempts < 3:
                time.sleep(2 ** attempts)
                continue
            raise
        store.finish(local_date, "sent")
        return "smtp_accepted"


def parser():
    result = argparse.ArgumentParser(description="每天一张牌：固定牌义、私有记录与 TLS 邮件")
    result.add_argument("--state-dir", type=Path, default=Path(os.environ.get("TAROT_STATE_DIR", ROOT / ".local")))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="验证 78 张牌和正逆位资料")
    commands.add_parser("preview", help="临时抽取一个示例，不保存正式当天记录，也不发送")
    run = commands.add_parser("run", help="获取当天唯一牌面；默认仅导出预览")
    run.add_argument("--delivery", choices=("file", "stdout", "smtp"), default="file")
    status = commands.add_parser("status", help="查看指定日期的最小状态摘要")
    status.add_argument("--date", type=date_argument, default=today())
    resolve = commands.add_parser("resolve", help="人工核对后恢复异常发送记录，不重抽")
    resolve.add_argument("--date", required=True, type=date_argument)
    outcomes = resolve.add_mutually_exclusive_group(required=True)
    outcomes.add_argument("--confirmed-sent", action="store_true")
    outcomes.add_argument("--confirmed-not-sent", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        deck = load_deck()
        if args.command == "validate":
            print(json.dumps({"valid": True, "cards": 78, "outcomes": 156,
                              "deck_version": deck["version"], "deck_sha256": deck["sha256"]}))
            return 0
        if args.command == "preview":
            print("示例预览，未保存为当天正式抽牌。\n")
            print(make_snapshot(deck, today())["body"])
            return 0
        # 真实发送的配置先校验；配置不全不创建正式抽牌。
        config = MailConfig.from_environment() if args.command == "run" and args.delivery == "smtp" else None
        store = Store(args.state_dir)
        if args.command == "resolve":
            store.resolve(args.date, args.confirmed_sent)
            print("人工核对结果已记录；牌面未改变。")
            return 0
        if args.command == "status":
            row = store.get(args.date)
            if row is None:
                print(json.dumps({"date": args.date, "status": "not_drawn"}))
            else:
                snapshot = json.loads(row["snapshot"])
                print(json.dumps({"date": args.date, "card": snapshot["card_name"],
                                  "orientation": snapshot["orientation"], "status": row["status"],
                                  "attempts": row["attempts"], "error_code": row["error_code"]}, ensure_ascii=False))
            return 0
        snapshot = store.get_or_create(deck, today())
        export_preview(store.directory, snapshot)
        status = deliver(store, snapshot, config) if config else "preview_saved"
        if args.delivery == "stdout":
            # 将原样正文交给云端任务作为最终回复；本程序不声称通知邮件已发送。
            print(snapshot["body"], end="")
            return 0
        print(json.dumps({"date": snapshot["date"], "status": status,
                          "card": snapshot["card_name"], "orientation": snapshot["orientation"]}, ensure_ascii=False))
        return 0
    except (ConfigError, StateError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except DeliveryError as exc:
        print(f"邮件投递未确认：{exc.code}；查看 status，不要盲目重发。", file=sys.stderr)
        return 3
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        # 不输出第三方错误正文、邮件地址或凭据。
        print(f"运行失败：{type(exc).__name__}；检查文件权限、牌库和配置。", file=sys.stderr)
        return 4
