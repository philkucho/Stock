"""Gmail SMTP HTML 메일 발송.

환경변수 (.env):
  GMAIL_USER          — 발신자 Gmail 주소 (예: choym92@gmail.com)
  GMAIL_APP_PASSWORD  — Google 계정 → 보안 → 2단계 인증 → 앱 비밀번호 (16자, 공백 제거)
  EMAIL_TO            — 수신자 (콤마 구분 다중 허용). 미지정 시 GMAIL_USER 본인 발송.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Sequence


class EmailConfigError(RuntimeError):
    """필수 SMTP 환경변수가 누락된 경우."""


SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL


def _resolve_recipients(to: str | Sequence[str] | None) -> list[str]:
    if to is None:
        env_to = os.environ.get("EMAIL_TO", "").strip()
        if not env_to:
            user = os.environ.get("GMAIL_USER", "").strip()
            if not user:
                raise EmailConfigError("EMAIL_TO 또는 GMAIL_USER 중 하나는 .env에 설정되어야 합니다.")
            return [user]
        return [addr.strip() for addr in env_to.split(",") if addr.strip()]
    if isinstance(to, str):
        return [addr.strip() for addr in to.split(",") if addr.strip()]
    return [a.strip() for a in to if a.strip()]


def send_email(
    subject: str,
    html_body: str,
    text_body: str | None = None,
    to: str | Sequence[str] | None = None,
) -> None:
    """Gmail SMTP로 HTML 이메일 발송.

    text_body 미지정 시 자동으로 fallback 텍스트 생성 (HTML 태그 제거).
    """
    # Google 앱 비밀번호는 표시상 "xxxx xxxx xxxx xxxx" 형태로 공백 포함 → 제거.
    # USER도 .env 편집 중 들어간 공백/탭 안전 제거.
    user = "".join(os.environ.get("GMAIL_USER", "").split())
    password = "".join(os.environ.get("GMAIL_APP_PASSWORD", "").split())
    if not user or not password:
        raise EmailConfigError(
            "GMAIL_USER / GMAIL_APP_PASSWORD 가 .env에 없습니다. "
            "Google 계정 → 보안 → 2단계 인증 → 앱 비밀번호에서 발급 후 등록하세요."
        )

    recipients = _resolve_recipients(to)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="stock-bot.local")

    if text_body is None:
        # 매우 단순한 fallback — 태그 제거.
        import re
        text_body = re.sub(r"<[^>]+>", "", html_body)
        text_body = re.sub(r"\n{3,}", "\n\n", text_body).strip()

    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
