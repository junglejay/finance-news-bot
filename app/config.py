"""Environment-backed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _value(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _integer(name: str, default: int) -> int:
    value = _value(name)
    return int(value) if value else default


@dataclass(frozen=True, slots=True)
class Settings:
    timezone: str
    ai_api_key: str
    ai_base_url: str
    ai_model: str
    dingtalk_webhook: str
    dingtalk_secret: str
    ft_feed_url: str
    ft_email_sender: str
    scholar_imap_host: str
    scholar_imap_port: int
    scholar_imap_username: str
    scholar_imap_password: str
    scholar_imap_folder: str
    scholar_sender: str
    sec_user_agent: str

    @property
    def scholar_enabled(self) -> bool:
        return bool(
            self.scholar_imap_host
            and self.scholar_imap_username
            and self.scholar_imap_password
        )

    @property
    def ft_enabled(self) -> bool:
        return bool(self.ft_feed_url or (self.ft_email_sender and self.scholar_enabled))

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            timezone=_value("TIMEZONE", "Asia/Shanghai"),
            ai_api_key=_value("AI_API_KEY"),
            ai_base_url=_value("AI_BASE_URL", "https://minitoken.top/v1").rstrip("/"),
            ai_model=_value("AI_MODEL", "deepseek-v4-flash"),
            dingtalk_webhook=_value("DINGTALK_WEBHOOK"),
            dingtalk_secret=_value("DINGTALK_SECRET"),
            ft_feed_url=_value("FT_FEED_URL"),
            ft_email_sender=_value("FT_EMAIL_SENDER"),
            scholar_imap_host=_value("SCHOLAR_IMAP_HOST"),
            scholar_imap_port=_integer("SCHOLAR_IMAP_PORT", 993),
            scholar_imap_username=_value("SCHOLAR_IMAP_USERNAME"),
            scholar_imap_password=_value("SCHOLAR_IMAP_PASSWORD"),
            scholar_imap_folder=_value("SCHOLAR_IMAP_FOLDER", "INBOX"),
            scholar_sender=_value("SCHOLAR_SENDER", "scholaralerts-noreply@google.com"),
            sec_user_agent=_value(
                "SEC_USER_AGENT", "commodity-risk-intel-bot/1.0 contact=you@example.com"
            ),
        )
