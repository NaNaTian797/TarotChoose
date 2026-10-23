"""Gmail 插件的单次发送交接；本模块不联网、不保管 OAuth 令牌。"""

from dataclasses import dataclass
import os
import uuid

from .formatting import render_html
from .mail import ConfigError, mailbox, make_message, recipient_key


@dataclass(frozen=True, repr=False)
class GmailConfig:
    sender: str
    recipient: str

    @classmethod
    def from_environment(cls):
        required = ("TAROT_MAIL_FROM", "TAROT_MAIL_TO")
        if missing := [name for name in required if not os.environ.get(name)]:
            raise ConfigError("缺少环境变量：" + ", ".join(missing))
        return cls(mailbox(os.environ["TAROT_MAIL_FROM"], "TAROT_MAIL_FROM"),
                   mailbox(os.environ["TAROT_MAIL_TO"], "TAROT_MAIL_TO"))


def prepare_gmail(store, snapshot, config):
    # 先提交 sending，再向调用方交出正文；中断后不允许重新领取或自动重发。
    attempt_id = uuid.uuid4().hex
    message_id = str(make_message(snapshot, config.sender, config.recipient)["Message-ID"])
    if not store.claim(snapshot["date"], recipient_key(config.recipient), message_id,
                       channel="gmail", attempt_id=attempt_id):
        return {"date": snapshot["date"], "status": "already_sent"}
    return {
        "date": snapshot["date"], "status": "send_required", "attempt_id": attempt_id,
        "expected_sender": config.sender,
        "message": {
            "to": config.recipient, "subject": snapshot["subject"],
            "text": snapshot["body"],
            "html": snapshot.get("body_html") or render_html(snapshot["body"]),
        },
    }
