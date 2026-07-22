"""DingTalk custom robot delivery with optional HMAC signing.

This module additionally ensures that messages sent to a DingTalk robot
configured with keyword-based security include the required keyword.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .config import Settings
from .models import DeepReadingReport


DEFAULT_KEYWORD = "新闻"


class DingTalkDeliveryError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def signed_webhook_url(webhook: str, secret: str, timestamp_ms: int | None = None) -> str:
    if not secret:
        return webhook
    timestamp = timestamp_ms or int(time.time() * 1000)
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    signature = base64.b64encode(hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()).decode("utf-8")
    split = urlsplit(webhook)
    query = parse_qsl(split.query, keep_blank_values=True)
    query.extend([("timestamp", str(timestamp)), ("sign", signature)])
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status_code: int
    response: dict


def ensure_keyword_in_message(content: str, keyword: str = DEFAULT_KEYWORD) -> str:
    """Ensure the DingTalk keyword is present in the message text/body.

    If the keyword is missing, prepend it on its own line before the content.
    """
    if keyword in content:
        return content
    return f"{keyword}\n\n{content}"


class DingTalkNotifier:
    def __init__(self, settings: Settings) -> None:
        self.webhook = settings.dingtalk_webhook
        self.secret = settings.dingtalk_secret

    async def send_report(self, report: DeepReadingReport) -> DeliveryResult:
        title = f"全球资本市场风控与监管（{report.report_date.isoformat()}）"
        markdown = report.to_markdown()
        return await self.send_markdown(title, markdown)

    async def send_fault(self, message: str) -> DeliveryResult:
        text = f"# 全球资本市场风控与监管：任务异常\n\n{message}\n\n请检查任务日志与数据源状态。"
        return await self.send_markdown("全球资本市场风控与监管：任务异常", text)

    async def send_markdown(self, title: str, markdown: str) -> DeliveryResult:
        # Ensure the required keyword is present so keyword-based robots accept the message
        # Place it at the very beginning, before any Markdown formatting
        markdown = ensure_keyword_in_message(markdown, DEFAULT_KEYWORD)

        if not self.webhook:
            # Print to stdout for local/dev fallback; ensure keyword is present there too
            print(markdown)
            return DeliveryResult(status_code=0, response={"mode": "stdout", "title": title})
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": markdown},
            "at": {"isAtAll": False},
        }
        url = signed_webhook_url(self.webhook, self.secret) if self.secret else self.webhook
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None else None
            raise DingTalkDeliveryError(f"DingTalk webhook request failed: {exc}", status_code) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise DingTalkDeliveryError("DingTalk returned non-JSON response", response.status_code) from exc
        if body.get("errcode", 0) != 0:
            raise DingTalkDeliveryError(
                f"DingTalk rejected message: {body.get('errmsg', body)}", response.status_code
            )
        return DeliveryResult(status_code=response.status_code, response=body)
