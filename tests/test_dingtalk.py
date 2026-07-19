from __future__ import annotations

import json
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
import respx
from httpx import Response

from app.dingtalk import DingTalkDeliveryError, DingTalkNotifier, signed_webhook_url


def test_signed_webhook_url_adds_timestamp_and_signature() -> None:
    url = signed_webhook_url(
        "https://oapi.dingtalk.test/robot/send?access_token=token",
        "secret",
        timestamp_ms=1_700_000_000_000,
    )
    query = parse_qs(urlsplit(url).query)
    assert query["access_token"] == ["token"]
    assert query["timestamp"] == ["1700000000000"]
    assert len(query["sign"][0]) > 20


@pytest.mark.asyncio
@respx.mock
async def test_dingtalk_notifier_posts_markdown(settings) -> None:
    route = respx.post(url__regex=r"https://oapi\.dingtalk\.test/robot/send.*").mock(
        return_value=Response(200, json={"errcode": 0, "errmsg": "ok"})
    )
    notifier = DingTalkNotifier(settings)

    result = await notifier.send_markdown("test", "# test")

    assert route.called
    assert result.status_code == 200
    assert json.loads(route.calls[0].request.content)["msgtype"] == "markdown"


@pytest.mark.asyncio
@respx.mock
async def test_dingtalk_rejection_raises(settings) -> None:
    respx.post(url__regex=r"https://oapi\.dingtalk\.test/robot/send.*").mock(
        return_value=Response(200, json={"errcode": 310000, "errmsg": "invalid signature"})
    )
    with pytest.raises(DingTalkDeliveryError, match="invalid signature"):
        await DingTalkNotifier(settings).send_markdown("test", "test")


@pytest.mark.asyncio
@respx.mock
async def test_dingtalk_webhook_can_be_used_without_a_signing_secret(settings) -> None:
    local_settings = replace(settings, dingtalk_secret="")
    route = respx.post("https://oapi.dingtalk.test/robot/send?access_token=test").mock(
        return_value=Response(200, json={"errcode": 0, "errmsg": "ok"})
    )

    await DingTalkNotifier(local_settings).send_markdown("test", "# test")

    assert route.called


@pytest.mark.asyncio
async def test_notifier_prints_when_webhook_is_not_configured(settings, capsys) -> None:
    local_settings = replace(settings, dingtalk_webhook="", dingtalk_secret="")

    result = await DingTalkNotifier(local_settings).send_markdown("test", "# 本地新闻测试")

    assert result.response["mode"] == "stdout"
    assert "本地新闻测试" in capsys.readouterr().out
