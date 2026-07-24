from __future__ import annotations

import pytest

from app.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        timezone="Asia/Shanghai",
        ai_api_key="test-key",
        ai_base_url="https://api.deepseek.test/v1",
        ai_model="deepseek-v4-flash",
        dingtalk_webhook="https://oapi.dingtalk.test/robot/send?access_token=test",
        dingtalk_secret="test-secret",
        ft_feed_url="",
        ft_email_sender="",
        scholar_imap_host="",
        scholar_imap_port=993,
        scholar_imap_username="",
        scholar_imap_password="",
        scholar_imap_folder="INBOX",
        scholar_sender="scholaralerts-noreply@google.com",
        sec_user_agent="test-bot contact=test@example.com",
        extra_rss_feeds=(),
        extra_article_domains=(),
        delivery_history_file="",
    )
