"""TLS 邮件发送；明确区分失败与投递结果不确定。"""

from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
import hashlib
import os
import smtplib
import ssl


class ConfigError(ValueError):
    pass


class DeliveryError(RuntimeError):
    def __init__(self, code, uncertain=False, retryable=False):
        self.code = code
        self.uncertain = uncertain
        self.retryable = retryable
        super().__init__(code)


def mailbox(value, key):
    if not value or any(c in value for c in "\r\n,"):
        raise ConfigError(f"{key} 必须是一个纯邮箱地址")
    try:
        parsed = Address(addr_spec=value)
        if not parsed.username or not parsed.domain or not value.isascii():
            raise ValueError
    except (ValueError, IndexError):
        raise ConfigError(f"{key} 格式不正确") from None
    return value


@dataclass(frozen=True, repr=False)
class MailConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipient: str
    mode: str = "ssl"

    @classmethod
    def from_environment(cls):
        required = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "TAROT_MAIL_FROM", "TAROT_MAIL_TO")
        if missing := [key for key in required if not os.environ.get(key)]:
            raise ConfigError("缺少环境变量：" + ", ".join(missing))
        mode = os.environ.get("SMTP_MODE", "ssl")
        if mode not in ("ssl", "starttls"):
            raise ConfigError("SMTP_MODE 只支持 ssl 或 starttls")
        try:
            port = int(os.environ.get("SMTP_PORT", "465" if mode == "ssl" else "587"))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ConfigError("SMTP_PORT 格式不正确") from None
        host = os.environ["SMTP_HOST"].strip()
        if not host or any(c in host for c in "\r\n/:"):
            raise ConfigError("SMTP_HOST 必须是不含协议和端口的主机名")
        return cls(host, port, os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"],
                   mailbox(os.environ["TAROT_MAIL_FROM"], "TAROT_MAIL_FROM"),
                   mailbox(os.environ["TAROT_MAIL_TO"], "TAROT_MAIL_TO"), mode)


def recipient_key(recipient):
    # 本地部分可能区分大小写，只归一化域名。
    local, domain = recipient.rsplit("@", 1)
    return hashlib.sha256(f"{local}@{domain.lower()}".encode()).hexdigest()


def make_message(snapshot, sender="preview@example.invalid", recipient="preview@example.invalid"):
    message = EmailMessage(policy=SMTP)
    message["Subject"] = snapshot["subject"]
    message["From"] = sender
    message["To"] = recipient
    identity = f"{snapshot['date']}|{snapshot['outcome']}|{recipient_key(recipient)}"
    message["Message-ID"] = f"<{hashlib.sha256(identity.encode()).hexdigest()}@tarotchoose.local>"
    message.set_content(snapshot["body"])
    return message


def send(message, config):
    client = None
    started_delivery = False
    accepted = False
    try:
        context = ssl.create_default_context()
        if config.mode == "ssl":
            client = smtplib.SMTP_SSL(config.host, config.port, timeout=25, context=context)
        else:
            client = smtplib.SMTP(config.host, config.port, timeout=25)
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(config.user, config.password)
        started_delivery = True
        refused = client.send_message(message, from_addr=config.sender, to_addrs=[config.recipient])
        if refused:
            raise DeliveryError("recipient_refused")
        accepted = True
    except DeliveryError:
        raise
    except smtplib.SMTPAuthenticationError:
        raise DeliveryError("authentication_failed") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
        raise DeliveryError("address_refused") from None
    except smtplib.SMTPResponseException as exc:
        # 服务器明确拒绝 DATA 时，邮件未被接受；可安全有限重试 4xx。
        raise DeliveryError("smtp_rejected", retryable=400 <= exc.smtp_code < 500) from None
    except (OSError, smtplib.SMTPException):
        raise DeliveryError("delivery_unknown" if started_delivery else "connection_failed",
                            uncertain=started_delivery, retryable=not started_delivery) from None
    finally:
        if client is not None:
            try:
                if accepted:
                    client.quit()
                else:
                    client.close()
            except (OSError, smtplib.SMTPException):
                # DATA 已被接受后，QUIT 的错误不能触发重复投递。
                pass
