"""DingTalk custom robot delivery with optional HMAC signing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .config import Settings
from .models import DailyBrief


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


class DingTalkNotifier:
    def __init__(self, settings: Settings) -> None:
        self.webhook = settings.dingtalk_webhook
        self.secret = settings.dingtalk_secret

    async def send_brief(self, brief: DailyBrief) -> DeliveryResult:
        return await self.send_markdown(
            f"商品与风控情报晨报｜{brief.report_date.isoformat()}", brief.to_markdown()
        )

    async def send_fault(self, message: str) -> DeliveryResult:
        text = f"# 商品与风控情报晨报｜任务异常\n\n{message}\n\n请检查服务日志与数据源状态。"
        return await self.send_markdown("商品与风控情报晨报｜任务异常", text)

    async def send_markdown(self, title: str, markdown: str) -> DeliveryResult:
        if not self.webhook:
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
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
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
